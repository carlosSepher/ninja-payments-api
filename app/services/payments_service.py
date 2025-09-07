from __future__ import annotations

from app.config import Settings, settings
from app.domain.dtos import (
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentStatusResponse,
    RedirectInfo,
)
from app.domain.enums import Currency
from app.domain.models import Payment
from app.domain.statuses import PaymentStatus
from app.providers.factory import get_provider
from app.repositories.memory_store import InMemoryPaymentStore


class PaymentsService:
    """Business logic for payments."""

    def __init__(self, store: InMemoryPaymentStore, cfg: Settings = settings):
        self.store = store
        self.settings = cfg
        self.provider = get_provider(cfg)

    async def create_payment(
        self, request: PaymentCreateRequest, idempotency_key: str | None
    ) -> PaymentCreateResponse:
        if request.currency != Currency.CLP:
            raise ValueError("Unsupported currency")
        if request.amount <= 0:
            raise ValueError("Amount must be positive")

        if idempotency_key:
            existing = self.store.get_by_idempotency(idempotency_key)
            if existing and existing.token and existing.redirect_url:
                redirect = RedirectInfo(
                    url=existing.redirect_url,
                    token=existing.token,
                    form_fields={"token_ws": existing.token},
                )
                return PaymentCreateResponse(status=existing.status, redirect=redirect)

        payment = Payment(
            buy_order=request.buy_order,
            amount=request.amount,
            currency=request.currency,
            success_url=request.success_url,
            failure_url=request.failure_url,
            cancel_url=request.cancel_url,
        )
        redirect_url, token = await self.provider.create(payment, request.return_url)
        payment.token = token
        payment.redirect_url = redirect_url
        self.store.save(payment, token, idempotency_key)
        redirect = RedirectInfo(url=redirect_url, token=token, form_fields={"token_ws": token})
        return PaymentCreateResponse(status=PaymentStatus.PENDING, redirect=redirect)

    async def commit_payment(self, token: str) -> PaymentStatusResponse:
        payment = self.store.get_by_token(token)
        if not payment:
            raise ValueError("Unknown token")
        response_code = await self.provider.commit(token)
        if response_code == 0:
            payment.status = PaymentStatus.AUTHORIZED
        else:
            payment.status = PaymentStatus.FAILED
        return PaymentStatusResponse(status=payment.status)

    def cancel_payment(self, token: str) -> PaymentStatusResponse:
        payment = self.store.get_by_token(token)
        if not payment:
            raise ValueError("Unknown token")
        payment.status = PaymentStatus.CANCELED
        return PaymentStatusResponse(status=payment.status)
