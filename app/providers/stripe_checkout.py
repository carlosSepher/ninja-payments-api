from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Tuple

import stripe  # type: ignore[import-untyped]

from app.config import Settings
from app.domain.models import Payment
from app.domain.statuses import PaymentStatus
from app.repositories.pg_store import PgPaymentStore

from .base import PaymentProvider, ProviderRefundResult

logger = logging.getLogger(__name__)


class StripeCheckoutProvider(PaymentProvider):
    """Stripe Checkout implementation.

    create(): creates a Checkout Session and returns (session.url, session.id)
    commit(): retrieves the Session/PaymentIntent and returns response metadata.
    """

    ZERO_DECIMAL_CURRENCIES = {"CLP", "JPY", "VND", "KRW"}

    @classmethod
    def _to_minor_units(cls, amount: Decimal, currency: str) -> int:
        currency_upper = currency.upper()
        if currency_upper in cls.ZERO_DECIMAL_CURRENCIES:
            quantized = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            return int(quantized)
        quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        minor_amount = (quantized * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP)
        return int(minor_amount)

    @classmethod
    def _from_minor_units(cls, amount: int, currency: str) -> Decimal:
        currency_upper = currency.upper()
        if currency_upper in cls.ZERO_DECIMAL_CURRENCIES:
            return Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        value = Decimal(amount) / Decimal("100")
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.stripe_secret_key:
            raise ValueError("Stripe secret key not configured")
        stripe.api_key = settings.stripe_secret_key
        self.store = PgPaymentStore()

    async def create(self, payment: Payment, return_url: str) -> Tuple[str, str]:
        success_url = payment.success_url or return_url
        cancel_url = payment.cancel_url or return_url

        currency = payment.currency.value.lower()
        amount = self._to_minor_units(payment.amount, payment.currency.value)

        session_kwargs: Dict[str, Any] = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items": [
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {"name": payment.buy_order},
                        "unit_amount": amount,
                    },
                    "quantity": 1,
                }
            ],
            "metadata": {
                "buy_order": payment.buy_order,
            },
        }
        if payment.company_id is not None:
            session_kwargs["metadata"]["company_id"] = str(payment.company_id)

        def _create_session() -> stripe.checkout.Session:  # type: ignore[name-defined]
            return stripe.checkout.Session.create(  # type: ignore[no-any-return]
                **session_kwargs
            )

        started = time.monotonic()
        try:
            session = await asyncio.to_thread(_create_session)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="CREATE",
                request_url="stripe.checkout.Session.create",
                request_body=self._sanitize_dict(session_kwargs),
                response_status=getattr(exc, "http_status", None),
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        self._log_event(
            operation="CREATE",
            request_url="stripe.checkout.Session.create",
            request_body=self._sanitize_dict(session_kwargs),
            response_status=200,
            response_body={
                "id": session.id,
                "payment_status": getattr(session, "payment_status", None),
            },
            latency_ms=latency_ms,
        )
        payment.provider_metadata.update(
            {
                "checkout_session_id": session.id,
                "buy_order": payment.buy_order,
            }
        )
        logger.info(
            "stripe session created",
            extra={"buy_order": payment.buy_order, "token": session.id},
        )
        return session.url, session.id

    async def commit(self, token: str) -> Dict[str, Any]:
        """Poll the session/payment intent and map to response details."""

        def _retrieve() -> Dict[str, Any]:
            session = stripe.checkout.Session.retrieve(  # type: ignore[call-arg]
                token,
                expand=[
                    "payment_intent",
                    "payment_intent.charges.data.balance_transaction",
                    "payment_intent.latest_charge",
                ],
            )
            pi = getattr(session, "payment_intent", None)
            customer_email = getattr(session, "customer_email", None)
            if not customer_email:
                customer_details = getattr(session, "customer_details", None)
                if customer_details:
                    customer_email = getattr(customer_details, "email", None)
            charge_id = None
            authorization_code = None
            if pi:
                charges = getattr(pi, "charges", None)
                if charges:
                    charge_list = getattr(charges, "data", None)
                    if isinstance(charge_list, list) and charge_list:
                        latest_charge = charge_list[-1]
                        charge_id = getattr(latest_charge, "id", None)
                        authorization_code = getattr(latest_charge, "balance_transaction", None) or charge_id
            if authorization_code is None:
                authorization_code = getattr(pi, "id", None)
            return {
                "session_id": session.id,
                "payment_status": getattr(session, "payment_status", None),
                "payment_intent_status": getattr(pi, "status", None),
                "payment_intent_id": getattr(pi, "id", None),
                "customer_email": customer_email,
                "charge_id": charge_id,
                "authorization_code": authorization_code,
            }

        started = time.monotonic()
        try:
            result = await asyncio.to_thread(_retrieve)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="COMMIT",
                request_url="stripe.checkout.Session.retrieve",
                token=token,
                request_body={"expand": ["payment_intent"]},
                response_status=getattr(exc, "http_status", None),
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        self._log_event(
            operation="COMMIT",
            request_url="stripe.checkout.Session.retrieve",
            token=token,
            request_body={"expand": ["payment_intent"]},
            response_status=200,
            response_body=result,
            latency_ms=latency_ms,
        )
        pi_status = str(result.get("payment_intent_status") or "")
        sess_payment_status = str(result.get("payment_status") or "")
        payment_intent_id = str(result.get("payment_intent_id") or "")
        logger.info(
            "stripe session status",
            extra={
                "token": token,
                "response_code": 0 if pi_status == "succeeded" or sess_payment_status == "paid" else -1,
            },
        )
        if payment_intent_id:
            try:
                self.store.update_provider_metadata(
                    provider="stripe",
                    token=token,
                    metadata={
                        "payment_intent_id": payment_intent_id,
                        "payment_intent_status": pi_status,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "stripe metadata save error",
                    extra={"token": token, "event": str(exc)},
                )
        customer_email = result.get("customer_email")
        if customer_email:
            try:
                self.store.update_provider_metadata(
                    provider="stripe",
                    token=token,
                    metadata={"customer_email": str(customer_email)},
                )
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "stripe metadata email save error",
                    extra={"token": token, "event": str(exc)},
                )
        success = pi_status == "succeeded" or sess_payment_status == "paid"
        response_code = 0 if success else -1
        authorization_code = result.get("authorization_code")
        charge_id = result.get("charge_id")
        if authorization_code is None:
            authorization_code = charge_id or result.get("payment_intent_id")
        return {
            "response_code": response_code,
            "authorization_code": authorization_code,
            "payment_intent_id": result.get("payment_intent_id"),
            "charge_id": charge_id,
        }

    async def status(self, token: str) -> PaymentStatus | None:
        def _retrieve() -> Dict[str, Any]:
            session = stripe.checkout.Session.retrieve(  # type: ignore[call-arg]
                token, expand=["payment_intent"]
            )
            pi = getattr(session, "payment_intent", None)
            return {
                "session_id": session.id,
                "payment_status": getattr(session, "payment_status", None),
                "payment_intent_status": getattr(pi, "status", None),
            }

        started = time.monotonic()
        try:
            result = await asyncio.to_thread(_retrieve)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            self._log_event(
                operation="STATUS",
                request_url="stripe.checkout.Session.retrieve",
                token=token,
                request_body={"expand": ["payment_intent"]},
                response_status=getattr(exc, "http_status", None),
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            return PaymentStatus.PENDING

        latency_ms = int((time.monotonic() - started) * 1000)
        self._log_event(
            operation="STATUS",
            request_url="stripe.checkout.Session.retrieve",
            token=token,
            request_body={"expand": ["payment_intent"]},
            response_status=200,
            response_body=result,
            latency_ms=latency_ms,
        )
        pi_status = str(result.get("payment_intent_status") or "")
        sess_payment_status = str(result.get("payment_status") or "")
        if pi_status == "succeeded" or sess_payment_status == "paid":
            return PaymentStatus.AUTHORIZED
        return PaymentStatus.PENDING

    async def refund(self, token: str, amount: Decimal | None = None) -> ProviderRefundResult:
        payment = self.store.get_by_token(token)
        payment_currency = payment.currency.value if payment and payment.currency else "USD"

        def _refund() -> ProviderRefundResult:
            session = stripe.checkout.Session.retrieve(  # type: ignore[call-arg]
                token, expand=["payment_intent"]
            )
            pi = getattr(session, "payment_intent", None)
            if not pi:
                return ProviderRefundResult(
                    ok=False,
                    amount=amount,
                    status="PAYMENT_INTENT_MISSING",
                    error="Payment intent missing",
                )
            args: Dict[str, object] = {"payment_intent": pi.id}
            currency_code = str(getattr(pi, "currency", "") or "").upper()
            if amount is not None:
                args["amount"] = self._to_minor_units(amount, currency_code or payment_currency)
            refund = stripe.Refund.create(**args)  # type: ignore[arg-type]
            status = str(getattr(refund, "status", ""))
            refund_id = getattr(refund, "id", None)
            refund_amount = getattr(refund, "amount", None)
            payload = {
                "refund_id": refund_id,
                "status": status,
                "payment_intent": pi.id,
            }
            if refund_amount is not None:
                payload["amount"] = refund_amount
            ok = status in {"succeeded", "pending"}
            record_amount: Decimal | None = None
            if refund_amount is not None:
                record_amount = self._from_minor_units(int(refund_amount), currency_code or payment_currency)
            elif amount is not None:
                record_amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return ProviderRefundResult(
                ok=ok,
                amount=record_amount,
                provider_refund_id=str(refund_id or "") or None,
                status=status or None,
                payload=payload,
                error=None if ok else status or "unknown",
            )

        started = time.monotonic()
        try:
            result = await asyncio.to_thread(_refund)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            request_payload: Dict[str, Any] = {"token": token}
            if amount is not None:
                request_payload["amount"] = self._to_minor_units(amount, payment_currency)
            self._log_event(
                operation="REFUND",
                request_url="stripe.Refund.create",
                token=token,
                request_body=request_payload,
                response_status=getattr(exc, "http_status", None),
                error_message=str(exc),
                latency_ms=latency_ms,
            )
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        request_payload: Dict[str, Any] = {"token": token}
        if amount is not None:
            request_payload["amount"] = self._to_minor_units(amount, payment_currency)
        response_body = result.payload or {
            "status": result.status,
            "ok": result.ok,
            "provider_refund_id": result.provider_refund_id,
        }
        self._log_event(
            operation="REFUND",
            request_url="stripe.Refund.create",
            token=token,
            request_body=request_payload,
            response_status=200,
            response_body=response_body,
            error_message=None if result.ok else (result.error or "Refund not accepted"),
            latency_ms=latency_ms,
        )
        return result

    def _sanitize_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                sanitized[key] = self._sanitize_dict(value)
            elif isinstance(value, list):
                sanitized[key] = [self._sanitize_dict(item) if isinstance(item, dict) else item for item in value]
            else:
                sanitized[key] = value
        return sanitized

    def _log_event(
        self,
        *,
        operation: str,
        request_url: str,
        token: str | None = None,
        request_body: Dict[str, Any] | None = None,
        response_status: int | None = None,
        response_body: Dict[str, Any] | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        if not self.settings.log_provider_events:
            return
        try:
            self.store.log_provider_event(
                provider="stripe",
                direction="OUTBOUND",
                operation=operation,
                request_url=request_url,
                token=token,
                response_status=response_status,
                error_message=error_message,
                latency_ms=latency_ms,
                request_body=request_body,
                response_body=response_body,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "provider event log error",
                extra={"provider": "stripe", "event": str(exc)},
            )
