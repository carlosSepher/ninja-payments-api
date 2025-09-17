from __future__ import annotations

import logging
import time
from typing import Any, Dict, Tuple
from decimal import Decimal, ROUND_HALF_UP

import httpx

from app.config import Settings
from app.domain.models import Payment

from .base import PaymentProvider, ProviderRefundResult
from app.domain.statuses import PaymentStatus
from app.repositories.pg_store import PgPaymentStore

logger = logging.getLogger(__name__)


class PayPalCheckoutProvider(PaymentProvider):
    """PayPal Checkout implementation using Orders v2.

    - create(): creates an order (intent CAPTURE) and returns (approve_url, order_id)
    - commit(): captures the order and returns 0 if COMPLETED, else -1
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = getattr(settings, "paypal_base_url", "https://api-m.sandbox.paypal.com")
        self.client_id = getattr(settings, "paypal_client_id", "")
        self.client_secret = getattr(settings, "paypal_client_secret", "")
        if not self.client_id or not self.client_secret:
            raise ValueError("PayPal credentials not configured")
        self.store = PgPaymentStore()

    async def _get_access_token(self) -> str:
        token_url = f"{self.base_url}/v1/oauth2/token"
        auth = (self.client_id, self.client_secret)
        data = {"grant_type": "client_credentials"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data=data, auth=auth)
            resp.raise_for_status()
            payload = resp.json()
            return str(payload["access_token"])  # type: ignore[index]

    async def create(self, payment: Payment, return_url: str) -> Tuple[str, str]:
        access_token = await self._get_access_token()

        cancel_url = payment.cancel_url or return_url
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": payment.buy_order,
                    "amount": {
                        "currency_code": payment.currency.value,
                        "value": str(payment.amount),
                    },
                }
            ],
            "application_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "PAY_NOW",
            },
        }

        orders_url = f"{self.base_url}/v2/checkout/orders"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        started = time.monotonic()
        response_status: int | None = None
        response_headers: Dict[str, str] | None = None
        response_body: Dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(orders_url, headers=headers, json=payload)
            response_status = resp.status_code
            response_headers = dict(resp.headers)
            if resp.status_code >= 400:
                try:
                    detail_payload = resp.json()
                except Exception:  # noqa: BLE001
                    detail_payload = {"text": resp.text[:512]}
                response_body = detail_payload
                logger.info(
                    "paypal order create failed",
                    extra={"response_code": resp.status_code, "token": ""},
                )
                raise ValueError(f"PayPal create error: {detail_payload}")
            data = resp.json()
            response_body = {"id": data.get("id"), "status": data.get("status")}
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="CREATE",
                request_url=orders_url,
                request_headers=self._mask_headers(headers),
                request_body=payload,
                response_status=response_status,
                response_headers=response_headers,
                response_body=response_body,
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        order_id = str(data["id"])  # type: ignore[index]
        approve_url = next(
            (link["href"] for link in data.get("links", []) if link.get("rel") == "approve"),
            None,
        )
        if not approve_url:
            raise RuntimeError("PayPal approve URL not found")
        self._log_event(
            operation="CREATE",
            request_url=orders_url,
            token=order_id,
            request_headers=self._mask_headers(headers),
            request_body=payload,
            response_status=response_status,
            response_headers=response_headers,
            response_body=response_body,
            latency_ms=latency_ms,
        )
        logger.info("paypal order created", extra={"buy_order": payment.buy_order, "token": order_id})
        return approve_url, order_id

    async def commit(self, token: str) -> int:
        """Capture a PayPal order by ID (token)."""
        access_token = await self._get_access_token()
        capture_url = f"{self.base_url}/v2/checkout/orders/{token}/capture"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        started = time.monotonic()
        response_status: int | None = None
        response_headers: Dict[str, str] | None = None
        response_body: Dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(capture_url, headers=headers, json={})
            response_status = resp.status_code
            response_headers = dict(resp.headers)
            if resp.status_code >= 400:
                try:
                    detail_payload = resp.json()
                except Exception:  # noqa: BLE001
                    detail_payload = {"text": resp.text[:512]}
                response_body = detail_payload
                logger.info("paypal capture failed", extra={"token": token, "response_code": -1})
                self._log_event(
                    operation="COMMIT",
                    request_url=capture_url,
                    token=token,
                    request_headers=self._mask_headers(headers),
                    request_body={},
                    response_status=response_status,
                    response_headers=response_headers,
                    response_body=response_body,
                    error_message="Capture failed",
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                return -1
            data = resp.json()
            response_body = {"status": data.get("status")}
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="COMMIT",
                request_url=capture_url,
                token=token,
                request_headers=self._mask_headers(headers),
                request_body={},
                response_status=response_status,
                response_headers=response_headers,
                response_body=response_body,
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        status = str(data.get("status", ""))
        self._log_event(
            operation="COMMIT",
            request_url=capture_url,
            token=token,
            request_headers=self._mask_headers(headers),
            request_body={},
            response_status=response_status,
            response_headers=response_headers,
            response_body=response_body,
            latency_ms=latency_ms,
        )
        logger.info("paypal capture status", extra={"token": token, "response_code": 0 if status == "COMPLETED" else -1})
        if status == "COMPLETED":
            return 0
        return -1

    async def status(self, token: str) -> PaymentStatus | None:
        access_token = await self._get_access_token()
        order_url = f"{self.base_url}/v2/checkout/orders/{token}"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        started = time.monotonic()
        response_status: int | None = None
        response_headers: Dict[str, str] | None = None
        response_body: Dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(order_url, headers=headers)
            response_status = resp.status_code
            response_headers = dict(resp.headers)
            if resp.status_code >= 400:
                logger.info("paypal status failed", extra={"token": token, "response_code": resp.status_code})
                response_body = {"text": resp.text[:512]}
                self._log_event(
                    operation="STATUS",
                    request_url=order_url,
                    token=token,
                    request_headers=self._mask_headers(headers),
                    request_body=None,
                    response_status=response_status,
                    response_headers=response_headers,
                    response_body=response_body,
                    error_message="Status failed",
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                return None
            data = resp.json()
            response_body = {"status": data.get("status"), "id": data.get("id")}
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="STATUS",
                request_url=order_url,
                token=token,
                request_headers=self._mask_headers(headers),
                request_body=None,
                response_status=response_status,
                response_headers=response_headers,
                response_body=response_body,
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            return None
        latency_ms = int((time.monotonic() - started) * 1000)
        self._log_event(
            operation="STATUS",
            request_url=order_url,
            token=token,
            request_headers=self._mask_headers(headers),
            request_body=None,
            response_status=response_status,
            response_headers=response_headers,
            response_body=response_body,
            latency_ms=latency_ms,
        )
        order_status = str(data.get("status", ""))
        captures = (
            (data.get("purchase_units") or [{}])[0]
            .get("payments", {})
            .get("captures", [])
        )
        # If there are captures with refund markers, prefer that
        cap_statuses = {str(c.get("status", "")) for c in captures}
        if any(s in {"REFUNDED", "PARTIALLY_REFUNDED"} for s in cap_statuses):
            return PaymentStatus.REFUNDED
        if order_status == "COMPLETED":
            return PaymentStatus.AUTHORIZED
        if order_status in {"VOIDED", "CANCELLED"}:
            return PaymentStatus.CANCELED
        # CREATED, APPROVED, PAYER_ACTION_REQUIRED -> pending
        return PaymentStatus.PENDING

    async def refund(self, token: str, amount: int | None = None) -> ProviderRefundResult:
        access_token = await self._get_access_token()
        # Retrieve order to find capture id
        order_url = f"{self.base_url}/v2/checkout/orders/{token}"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        started = time.monotonic()
        async with httpx.AsyncClient() as client:
            r1 = await client.get(order_url, headers=headers)
        latency_status = int((time.monotonic() - started) * 1000)
        if r1.status_code >= 400:
            logger.info("paypal refund: order fetch failed", extra={"token": token, "response_code": r1.status_code})
            self._log_event(
                operation="STATUS",
                request_url=order_url,
                token=token,
                request_headers=self._mask_headers(headers),
                request_body=None,
                response_status=r1.status_code,
                response_headers=dict(r1.headers),
                response_body={"text": r1.text[:512]},
                error_message="Refund order fetch failed",
                latency_ms=latency_status,
            )
            return ProviderRefundResult(
                ok=False,
                amount=amount,
                status="ORDER_FETCH_FAILED",
                error=f"order fetch failed: {r1.status_code}",
            )
        order = r1.json()
        self._log_event(
            operation="STATUS",
            request_url=order_url,
            token=token,
            request_headers=self._mask_headers(headers),
            request_body=None,
            response_status=r1.status_code,
            response_headers=dict(r1.headers),
            response_body={"status": order.get("status"), "id": order.get("id")},
            latency_ms=latency_status,
        )
        captures = (
            (order.get("purchase_units") or [{}])[0]
            .get("payments", {})
            .get("captures", [])
        )
        if not captures:
            logger.info("paypal refund: no captures", extra={"token": token})
            return ProviderRefundResult(
                ok=False,
                amount=amount,
                status="NO_CAPTURES",
                error="No captures available for refund",
            )
        # Prefer a COMPLETED capture (latest)
        completed_caps = [c for c in captures if c.get("status") == "COMPLETED"]
        cap = (completed_caps[-1] if completed_caps else captures[-1])
        capture_id = cap.get("id")
        if not capture_id:
            return ProviderRefundResult(
                ok=False,
                amount=amount,
                status="CAPTURE_MISSING",
                error="Capture ID missing",
            )
        refund_url = f"{self.base_url}/v2/payments/captures/{capture_id}/refund"
        body: dict[str, object] = {}
        if amount is not None:
            currency = (order.get("purchase_units") or [{}])[0].get("amount", {}).get("currency_code", "USD")
            zero_decimal = {"CLP", "JPY", "VND", "KRW"}
            if currency in zero_decimal:
                value_str = str(int(amount))
            else:
                value_str = str(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            body = {"amount": {"currency_code": currency, "value": value_str}}
        started_refund = time.monotonic()
        async with httpx.AsyncClient() as client:
            r2 = await client.post(refund_url, headers=headers, json=body)
        latency_refund = int((time.monotonic() - started_refund) * 1000)
        if r2.status_code >= 400:
            try:
                detail_payload = r2.json()
            except Exception:  # noqa: BLE001
                detail_payload = {"text": r2.text[:512]}
            logger.info("paypal refund failed", extra={"token": token, "response_code": r2.status_code, "event": str(detail_payload)})
            self._log_event(
                operation="REFUND",
                request_url=refund_url,
                token=token,
                request_headers=self._mask_headers(headers),
                request_body=body or {},
                response_status=r2.status_code,
                response_headers=dict(r2.headers),
                response_body=detail_payload,
                error_message="Refund failed",
                latency_ms=latency_refund,
            )
            return ProviderRefundResult(
                ok=False,
                amount=amount,
                status="FAILED",
                payload=detail_payload,
                error="Refund failed",
            )
        data = r2.json()
        status = str(data.get("status", ""))
        payload = data
        self._log_event(
            operation="REFUND",
            request_url=refund_url,
            token=token,
            request_headers=self._mask_headers(headers),
            request_body=body or {},
            response_status=r2.status_code,
            response_headers=dict(r2.headers),
            response_body={"status": status, "capture_id": capture_id},
            error_message=None if status in {"COMPLETED", "PENDING"} else "Refund not accepted",
            latency_ms=latency_refund,
        )
        amount_value = body.get("amount", {}).get("value") if body else None
        try:
            amount_minor = int(amount_value) if amount_value is not None else amount
        except ValueError:
            amount_minor = amount
        return ProviderRefundResult(
            ok=status in {"COMPLETED", "PENDING"},
            amount=amount_minor,
            provider_refund_id=str(data.get("id", "")) or None,
            status=status or None,
            payload=payload,
            error=None if status in {"COMPLETED", "PENDING"} else "Refund not accepted",
        )

    def _mask_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        masked: Dict[str, str] = {}
        for key, value in (headers or {}).items():
            if key.lower() in {"authorization"}:
                masked[key] = "***"
            else:
                masked[key] = value
        return masked

    def _log_event(
        self,
        *,
        operation: str,
        request_url: str,
        token: str | None = None,
        request_headers: Dict[str, str] | None = None,
        request_body: Dict[str, Any] | None = None,
        response_status: int | None = None,
        response_headers: Dict[str, str] | None = None,
        response_body: Dict[str, Any] | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        if not self.settings.log_provider_events:
            return
        try:
            self.store.log_provider_event(
                provider="paypal",
                direction="OUTBOUND",
                operation=operation,
                request_url=request_url,
                token=token,
                response_status=response_status,
                error_message=error_message,
                latency_ms=latency_ms,
                request_headers=request_headers,
                request_body=request_body,
                response_headers=response_headers,
                response_body=response_body,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "provider event log error",
                extra={"provider": "paypal", "event": str(exc)},
            )
