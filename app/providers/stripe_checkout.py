from __future__ import annotations

import asyncio
import logging
from typing import Tuple

import stripe  # type: ignore[import-untyped]

from app.config import Settings
from app.domain.models import Payment

from .base import PaymentProvider
from app.domain.statuses import PaymentStatus

logger = logging.getLogger(__name__)


class StripeCheckoutProvider(PaymentProvider):
    """Stripe Checkout implementation.

    create(): creates a Checkout Session and returns (session.url, session.id)
    commit(): retrieves the Session/PaymentIntent and returns 0 if succeeded.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.stripe_secret_key:
            raise ValueError("Stripe secret key not configured")
        stripe.api_key = settings.stripe_secret_key

    async def create(self, payment: Payment, return_url: str) -> Tuple[str, str]:
        success_url = payment.success_url or return_url
        cancel_url = payment.cancel_url or return_url

        currency = payment.currency.value.lower()
        amount = int(payment.amount)

        # Build line item using price_data (one-time payment)
        def _create_session() -> stripe.checkout.Session:  # type: ignore[name-defined]
            return stripe.checkout.Session.create(  # type: ignore[no-any-return]
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_url,
                line_items=[
                    {
                        "price_data": {
                            "currency": currency,
                            "product_data": {"name": payment.buy_order},
                            "unit_amount": amount,
                        },
                        "quantity": 1,
                    }
                ],
                metadata={
                    "buy_order": payment.buy_order,
                    "payment_id": payment.id,
                },
            )

        session = await asyncio.to_thread(_create_session)
        logger.info(
            "stripe session created",
            extra={"buy_order": payment.buy_order, "token": session.id},
        )
        return session.url, session.id

    async def commit(self, token: str) -> int:
        """Poll the session/payment intent and map to response code.

        Note: in Stripe the final state is best handled via webhooks. This
        method is provided for parity and can be used to poll on-demand.
        """

        def _retrieve() -> tuple[str, str]:
            session = stripe.checkout.Session.retrieve(  # type: ignore[call-arg]
                token, expand=["payment_intent"]
            )
            # Prefer PaymentIntent status if present
            pi_status = getattr(getattr(session, "payment_intent", None), "status", None)
            sess_payment_status = getattr(session, "payment_status", None)
            return (str(pi_status or ""), str(sess_payment_status or ""))

        pi_status, sess_payment_status = await asyncio.to_thread(_retrieve)
        logger.info(
            "stripe session status",
            extra={
                "token": token,
                "response_code": 0 if pi_status == "succeeded" or sess_payment_status == "paid" else -1,
            },
        )
        if pi_status == "succeeded" or sess_payment_status == "paid":
            return 0
        return -1

    async def status(self, token: str) -> PaymentStatus | None:
        def _retrieve() -> tuple[str, str]:
            session = stripe.checkout.Session.retrieve(  # type: ignore[call-arg]
                token, expand=["payment_intent"]
            )
            pi_status = getattr(getattr(session, "payment_intent", None), "status", None)
            sess_payment_status = getattr(session, "payment_status", None)
            return (str(pi_status or ""), str(sess_payment_status or ""))

        pi_status, sess_payment_status = await asyncio.to_thread(_retrieve)
        if pi_status == "succeeded" or sess_payment_status == "paid":
            return PaymentStatus.AUTHORIZED
        # Unknown/incomplete; keep as pending
        return PaymentStatus.PENDING

    async def refund(self, token: str, amount: int | None = None) -> bool:
        def _refund() -> bool:
            session = stripe.checkout.Session.retrieve(  # type: ignore[call-arg]
                token, expand=["payment_intent"]
            )
            pi = getattr(session, "payment_intent", None)
            if not pi:
                return False
            args: dict[str, object] = {"payment_intent": pi.id}
            if amount is not None:
                args["amount"] = int(amount)
            refund = stripe.Refund.create(**args)  # type: ignore[arg-type]
            # status can be 'succeeded' or 'pending'
            return bool(refund and getattr(refund, "status", "") in {"succeeded", "pending"})

        return await asyncio.to_thread(_refund)
