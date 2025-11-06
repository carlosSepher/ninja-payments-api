from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Tuple
from uuid import uuid4

import httpx
from transbank.common.integration_type import IntegrationType  # type: ignore[import-untyped]
from transbank.common.options import WebpayOptions  # type: ignore[import-untyped]
from transbank.webpay.webpay_plus.transaction import Transaction  # type: ignore[import-untyped]
from transbank.error.transaction_commit_error import TransactionCommitError  # type: ignore[import-untyped]

from app.config import Settings
from app.domain.models import Payment
from app.domain.statuses import PaymentStatus
from app.repositories.pg_store import PgPaymentStore

from .base import PaymentProvider, ProviderRefundResult

logger = logging.getLogger(__name__)


class TransbankWebpayPlusProvider(PaymentProvider):
    """Transbank Webpay Plus implementation."""

    def __init__(self, settings: Settings):
        self.settings = settings
        options = WebpayOptions(
            settings.tbk_api_key_id, settings.tbk_api_key_secret, IntegrationType.LIVE
        )
        self.transaction = Transaction(options)
        self.store = PgPaymentStore()

    async def create(self, payment: Payment, return_url: str) -> Tuple[str, str]:
        url = f"{self.settings.tbk_host}{self.settings.tbk_api_base}/transactions"
        headers = {
            "Tbk-Api-Key-Id": self.settings.tbk_api_key_id,
            "Tbk-Api-Key-Secret": self.settings.tbk_api_key_secret,
            "Content-Type": "application/json",
        }
        session_id = str(payment.id) if payment.id is not None else uuid4().hex
        amount_integral = int(payment.amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        payload = {
            "buy_order": payment.buy_order,
            "session_id": session_id,
            "amount": amount_integral,
            "return_url": return_url,
        }
        started = time.monotonic()
        response_status: int | None = None
        response_headers: Dict[str, str] | None = None
        response_body: Dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, json=payload)
            response_status = resp.status_code
            response_headers = dict(resp.headers)
            data_raw: Dict[str, Any] | None = None
            try:
                data_raw = resp.json()
            except Exception:  # noqa: BLE001
                if resp.text:
                    data_raw = {"raw": resp.text}
            response_body = data_raw
            if resp.is_error:
                message = None
                if isinstance(data_raw, dict):
                    message = (
                        data_raw.get("detail")
                        or data_raw.get("error_message")
                        or data_raw.get("error")
                        or data_raw.get("message")
                    )
                err_msg = message or f"Transbank create failed ({resp.status_code})"
                latency_ms = int((time.monotonic() - started) * 1000)
                self._log_event(
                    operation="CREATE",
                    request_url=url,
                    request_headers=self._mask_headers(headers),
                    request_body=payload,
                    response_status=response_status,
                    response_headers=response_headers,
                    response_body=response_body,
                    error_message=err_msg,
                    latency_ms=latency_ms,
                )
                raise ValueError(err_msg)
            data = data_raw or {}
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="CREATE",
                request_url=url,
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
        self._log_event(
            operation="CREATE",
            request_url=url,
            request_headers=self._mask_headers(headers),
            request_body=payload,
            response_status=response_status,
            response_headers=response_headers,
            response_body=response_body,
            latency_ms=latency_ms,
        )
        token = data["token"]
        redirect_url = data["url"]
        payment.provider_metadata.update(
            {
                "token_ws": token,
                "session_id": session_id,
                "buy_order": payment.buy_order,
            }
        )
        logger.info(
            "transaction created",
            extra={"buy_order": payment.buy_order, "token": token},
        )
        return redirect_url, token

    async def commit(self, token: str) -> Dict[str, Any]:
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(self.transaction.commit, token)
        except TransactionCommitError as exc:  # type: ignore[misc]
            latency_ms = int((time.monotonic() - started) * 1000)
            response_code = None
            try:
                response_code = int(exc.code) if exc.code is not None else None
            except (TypeError, ValueError):
                response_code = None
            message = getattr(exc, "message", str(exc))
            self._log_event(
                operation="COMMIT",
                request_url="webpay.transaction.commit",
                token=token,
                response_status=response_code,
                error_message=message,
                latency_ms=latency_ms,
            )
            logger.info(
                "transaction commit error",
                extra={"token": token, "response_code": response_code, "event": message},
            )
            return {"response_code": response_code or -1, "authorization_code": None}
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="COMMIT",
                request_url="webpay.transaction.commit",
                token=token,
                response_status=None,
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        response_code = result.get("response_code", -1)
        self._log_event(
            operation="COMMIT",
            request_url="webpay.transaction.commit",
            token=token,
            response_status=int(response_code) if isinstance(response_code, int) else None,
            response_body={
                "response_code": response_code,
                "buy_order": result.get("buy_order"),
                "authorization_code": result.get("authorization_code"),
            },
            latency_ms=latency_ms,
        )
        logger.info(
            "transaction committed",
            extra={
                "buy_order": result.get("buy_order"),
                "token": token,
                "response_code": response_code,
            },
        )
        return {
            "response_code": response_code,
            "authorization_code": result.get("authorization_code"),
        }

    async def status(self, token: str) -> PaymentStatus | None:
        """Read-only status using Webpay SDK.

        Maps TBK status strings to our PaymentStatus without side effects.
        """
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(self.transaction.status, token)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "webpay status error",
                extra={"token": token, "event": str(exc)},
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="STATUS",
                request_url="webpay.transaction.status",
                token=token,
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            return None

        tbk_status = str(result.get("status", "") or "").upper()
        response_code = result.get("response_code", None)
        latency_ms = int((time.monotonic() - started) * 1000)
        self._log_event(
            operation="STATUS",
            request_url="webpay.transaction.status",
            token=token,
            response_status=int(response_code) if isinstance(response_code, int) else None,
            response_body={
                "status": tbk_status,
                "response_code": response_code,
            },
            latency_ms=latency_ms,
        )

        mapping: dict[str, PaymentStatus] = {
            "AUTHORIZED": PaymentStatus.AUTHORIZED,
            "FAILED": PaymentStatus.FAILED,
            # When a transaction is reversed/nullified, treat as REFUNDED
            "REVERSED": PaymentStatus.REFUNDED,
            "NULLIFIED": PaymentStatus.REFUNDED,
            # Transactions still in progress
            "INITIALIZED": PaymentStatus.PENDING,
        }
        mapped = mapping.get(tbk_status)
        logger.info(
            "webpay status read",
            extra={
                "token": token,
                "response_code": (response_code if response_code is not None else -1),
                "status": (mapped.value if mapped else ""),
            },
        )
        return mapped

    async def refund(self, token: str, amount: Decimal | None = None) -> ProviderRefundResult:
        """Issue a refund/nullification for Webpay Plus.

        Uses REST endpoint: POST /transactions/{token}/refunds with JSON { amount }.
        Consider success when response contains response_code == 0 or a
        refund type of REVERSED/NULLIFIED.
        """
        if amount is None or amount <= Decimal("0"):
            # Service is expected to default amount; keep provider safe.
            logger.info(
                "refund amount invalid",
                extra={"token": token, "amount": amount, "response_code": -1},
            )
            return ProviderRefundResult(
                ok=False,
                amount=amount,
                status="INVALID_AMOUNT",
                error="Amount must be positive",
            )

        url = f"{self.settings.tbk_host}{self.settings.tbk_api_base}/transactions/{token}/refunds"
        headers = {
            "Tbk-Api-Key-Id": self.settings.tbk_api_key_id,
            "Tbk-Api-Key-Secret": self.settings.tbk_api_key_secret,
            "Content-Type": "application/json",
        }
        int_amount = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        payload = {"amount": int_amount}
        started = time.monotonic()
        response_status: int | None = None
        response_headers: Dict[str, str] | None = None
        response_body: Dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, json=payload)
            response_status = resp.status_code
            response_headers = dict(resp.headers)
            resp.raise_for_status()
            data = resp.json()
            response_body = {
                "response_code": data.get("response_code"),
                "type": data.get("type"),
            }
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="REFUND",
                request_url=url,
                token=token,
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
        response_code = data.get("response_code")
        refund_type = data.get("type")
        ok = (response_code == 0) or (refund_type in {"REVERSED", "NULLIFIED"})
        self._log_event(
            operation="REFUND",
            request_url=url,
            token=token,
            request_headers=self._mask_headers(headers),
            request_body=payload,
            response_status=response_code if isinstance(response_code, int) else None,
            response_headers=response_headers,
            response_body=response_body,
            error_message=None if ok else "Refund not accepted",
            latency_ms=latency_ms,
        )
        logger.info(
            "webpay refund executed",
            extra={
                "token": token,
                "amount": amount,
                "response_code": (response_code if response_code is not None else -1),
                "refund_type": str(refund_type or ""),
                "accepted": ok,
            },
        )
        return ProviderRefundResult(
            ok=bool(ok),
            amount=amount.quantize(Decimal("0.01")),
            provider_refund_id=str(data.get("authorization_code", "")) or None,
            authorization_code=str(data.get("authorization_code", "")) or None,
            status=str(refund_type or ""),
            payload=data,
            error=None if ok else "Refund not accepted",
        )

    def _mask_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        masked: Dict[str, str] = {}
        for key, value in (headers or {}).items():
            if key.lower() in {"authorization", "tbk-api-key-secret"}:
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
                provider="webpay",
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
                extra={"provider": "webpay", "event": str(exc)},
            )
