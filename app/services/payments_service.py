from __future__ import annotations

import logging
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
from app.providers.factory import get_provider, get_provider_by_name
from app.repositories.memory_store import InMemoryPaymentStore


class PaymentsService:
    """Business logic for payments."""

    def __init__(self, store: InMemoryPaymentStore, cfg: Settings = settings):
        self.store = store
        self.settings = cfg
        self.provider = get_provider(cfg)
        self.logger = logging.getLogger(__name__)

    async def create_payment(
        self, request: PaymentCreateRequest, idempotency_key: str | None
    ) -> PaymentCreateResponse:
        # Validate currency per provider: Webpay requires CLP, others may allow USD
        provider_name = request.provider.value if getattr(request, "provider", None) else self.settings.provider
        if provider_name in {"webpay", "transbank"} and request.currency != Currency.CLP:
            raise ValueError("Unsupported currency for Webpay; use CLP")
        if request.amount <= 0:
            raise ValueError("Amount must be positive")

        if idempotency_key:
            existing = self.store.get_by_idempotency(idempotency_key)
            if existing and existing.token and existing.redirect_url:
                self.logger.info(
                    "idempotency hit; returning existing redirect",
                    extra={
                        "buy_order": existing.buy_order,
                        "idempotency_key": idempotency_key,
                        "token": existing.token,
                        "status": existing.status.value,
                    },
                )
                # Build redirect info depending on provider
                if (existing.provider or provider_name) in {"webpay", "transbank"}:
                    redirect = RedirectInfo(
                        url=existing.redirect_url,
                        token=existing.token,
                        method="POST",
                        form_fields={"token_ws": existing.token},
                    )
                else:
                    redirect = RedirectInfo(
                        url=existing.redirect_url,
                        token=existing.token,
                        method="GET",
                        form_fields={},
                    )
                return PaymentCreateResponse(status=existing.status, redirect=redirect)

        # Resolve provider per request (fallback to settings)

        payment = Payment(
            buy_order=request.buy_order,
            amount=request.amount,
            currency=request.currency,
            provider=provider_name,
            success_url=request.success_url,
            failure_url=request.failure_url,
            cancel_url=request.cancel_url,
        )
        self.logger.info(
            "creating transaction with provider",
            extra={
                "buy_order": payment.buy_order,
                "amount": payment.amount,
                "currency": payment.currency.value,
                "provider": provider_name,
            },
        )
        provider = get_provider_by_name(self.settings, provider_name)
        redirect_url, token = await provider.create(payment, request.return_url)
        payment.token = token
        payment.redirect_url = redirect_url
        self.store.save(payment, token, idempotency_key)
        self.logger.info(
            "payment stored",
            extra={"buy_order": payment.buy_order, "token": token, "status": PaymentStatus.PENDING.value},
        )
        if provider_name in {"webpay", "transbank"}:
            redirect = RedirectInfo(url=redirect_url, token=token, method="POST", form_fields={"token_ws": token})
        else:
            redirect = RedirectInfo(url=redirect_url, token=token, method="GET", form_fields={})
        return PaymentCreateResponse(status=PaymentStatus.PENDING, redirect=redirect)

    async def commit_payment(self, token: str) -> PaymentStatusResponse:
        payment = self.store.get_by_token(token)
        if not payment:
            raise ValueError("Unknown token")
        provider_name = payment.provider or self.settings.provider
        self.logger.info(
            "commit requested",
            extra={"buy_order": payment.buy_order, "token": token, "provider": provider_name},
        )
        provider = get_provider_by_name(self.settings, provider_name)
        response_code = await provider.commit(token)
        if response_code == 0:
            payment.status = PaymentStatus.AUTHORIZED
        else:
            payment.status = PaymentStatus.FAILED
        self.logger.info(
            "commit completed",
            extra={
                "buy_order": payment.buy_order,
                "token": token,
                "response_code": response_code,
                "status": payment.status.value,
            },
        )
        return PaymentStatusResponse(status=payment.status)

    def cancel_payment(self, token: str) -> PaymentStatusResponse:
        payment = self.store.get_by_token(token)
        if not payment:
            raise ValueError("Unknown token")
        payment.status = PaymentStatus.CANCELED
        self.logger.info(
            "payment canceled",
            extra={"buy_order": payment.buy_order, "token": token, "status": payment.status.value},
        )
        return PaymentStatusResponse(status=payment.status)
