from __future__ import annotations

import logging
from app.config import Settings, settings
from app.domain.dtos import (
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentStatusResponse,
    RedirectInfo,
    RefreshResult,
)
from app.domain.enums import Currency
from app.domain.models import Company, Payment
from app.domain.statuses import PaymentStatus
from app.providers.factory import get_provider, get_provider_by_name
from app.repositories.pg_store import PgPaymentStore
from app.repositories.company_store import PgCompanyStore


class PaymentsService:
    """Business logic for payments."""

    def __init__(self, store: PgPaymentStore, cfg: Settings = settings):
        self.store = store
        self.settings = cfg
        self.provider = get_provider(cfg)
        self.logger = logging.getLogger(__name__)
        self.company_store = PgCompanyStore()
        # DB-backed store is the source of truth now

    async def create_payment(
        self, request: PaymentCreateRequest, idempotency_key: str | None
    ) -> PaymentCreateResponse:
        # Validate currency per provider: Webpay requires CLP, others may allow USD
        provider_name = request.provider.value if getattr(request, "provider", None) else self.settings.provider
        if provider_name in {"webpay", "transbank"} and request.currency != Currency.CLP:
            raise ValueError("Unsupported currency for Webpay; use CLP")
        if request.amount <= 0:
            raise ValueError("Amount must be positive")

        try:
            company = self.company_store.validate_credentials(request.company_id, request.company_token)
        except ValueError as exc:
            if not self.settings.db_enabled:
                company = Company(
                    id=request.company_id,
                    name="offline-company",
                    contact_email=None,
                    api_token=request.company_token,
                    active=True,
                )
                self.logger.warning(
                    "company validation skipped (db disabled)",
                    extra={
                        "company_id": request.company_id,
                        "reason": str(exc),
                    },
                )
            else:
                raise

        if idempotency_key:
            existing = self.store.get_by_idempotency(idempotency_key, company.id)
            if existing and existing.token and existing.redirect_url:
                self.logger.info(
                    "idempotency hit; returning existing redirect",
                    extra={
                        "buy_order": existing.buy_order,
                        "idempotency_key": idempotency_key,
                        "token": existing.token,
                        "status": existing.status.value,
                        "company_id": company.id,
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
                return PaymentCreateResponse(
                    status=existing.status,
                    redirect=redirect,
                    internal_id=existing.id,
                    provider_transaction_id=existing.token,
                )

        # Resolve provider per request (fallback to settings)

        payment = Payment(
            buy_order=request.buy_order,
            amount=request.amount,
            currency=request.currency,
            provider=provider_name,
            payment_type=request.payment_type,
            commerce_id=request.commerce_id,
            product_id=request.product_id,
            product_name=request.product_name or request.buy_order,
            success_url=request.success_url,
            failure_url=request.failure_url,
            cancel_url=request.cancel_url,
            company_id=company.id,
        )
        self.logger.info(
            "creating transaction with provider",
            extra={
                "buy_order": payment.buy_order,
                "amount": payment.amount,
                "currency": payment.currency.value,
                "provider": provider_name,
                "payment_type": payment.payment_type.value if payment.payment_type else None,
                "commerce_id": payment.commerce_id,
                "product_id": payment.product_id,
                "company_id": company.id,
            },
        )
        provider = get_provider_by_name(self.settings, provider_name)
        redirect_url, token = await provider.create(payment, request.return_url)
        payment.token = token
        payment.redirect_url = redirect_url
        self.store.save(payment, token, idempotency_key)
        self.logger.info(
            "payment stored",
            extra={
                "buy_order": payment.buy_order,
                "token": token,
                "status": PaymentStatus.PENDING.value,
                "company_id": company.id,
            },
        )
        if provider_name in {"webpay", "transbank"}:
            redirect = RedirectInfo(url=redirect_url, token=token, method="POST", form_fields={"token_ws": token})
        else:
            redirect = RedirectInfo(url=redirect_url, token=token, method="GET", form_fields={})
        return PaymentCreateResponse(
            status=PaymentStatus.PENDING,
            redirect=redirect,
            internal_id=payment.id,
            provider_transaction_id=token,
        )

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
        try:
            self.store.update_status_by_token(
                provider=provider_name,
                token=token,
                to_status=payment.status,
                response_code=response_code,
            )
        except Exception as db_exc:  # noqa: BLE001
            self.logger.info("db commit save error", extra={"token": token, "event": str(db_exc)})
        # DB store reflects status through this service; external reconciler can also update later
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
        try:
            self.store.update_status_by_token(
                provider=payment.provider or self.settings.provider,
                token=token,
                to_status=payment.status,
            )
        except Exception as db_exc:  # noqa: BLE001
            self.logger.info("db cancel save error", extra={"token": token, "event": str(db_exc)})
        self.logger.info(
            "payment canceled",
            extra={"buy_order": payment.buy_order, "token": token, "status": payment.status.value},
        )
        return PaymentStatusResponse(status=payment.status)

    async def refresh_payment(self, token: str) -> PaymentStatus:
        """Check current status without forcing failures when possible.

        - Webpay: commits (finalizes) and maps to AUTHORIZED/FAILED.
        - Stripe: returns AUTHORIZED if paid else keeps PENDING.
        - PayPal: returns AUTHORIZED if COMPLETED, CANCELED if voided, otherwise PENDING.
        """
        payment = self.store.get_by_token(token)
        if not payment:
            raise ValueError("Unknown token")
        provider_name = payment.provider or self.settings.provider
        provider = get_provider_by_name(self.settings, provider_name)
        if provider_name in {"webpay", "transbank"}:
            code = await provider.commit(token)
            status = PaymentStatus.AUTHORIZED if code == 0 else PaymentStatus.FAILED
        else:
            status = await provider.status(token)
        if status and status != payment.status:
            payment.status = status
            self.logger.info(
                "payment refreshed",
                extra={"buy_order": payment.buy_order, "token": token, "status": payment.status.value},
            )
        return payment.status

    async def status_payment(self, token: str) -> PaymentStatus | None:
        """Check current provider-reported status without mutating local store."""
        payment = self.store.get_by_token(token)
        if not payment:
            raise ValueError("Unknown token")
        provider_name = payment.provider or self.settings.provider
        provider = get_provider_by_name(self.settings, provider_name)
        return await provider.status(token)

    async def refund(self, token: str, amount: int | None = None, company_id: int | None = None) -> PaymentStatus:
        payment = self.store.get_by_token(token)
        if not payment:
            raise ValueError("Unknown token")
        if company_id is not None and payment.company_id is not None and payment.company_id != company_id:
            raise ValueError("Invalid company for token")
        provider_name = payment.provider or self.settings.provider
        provider = get_provider_by_name(self.settings, provider_name)
        # For Webpay, default to full refund when amount is omitted
        if provider_name in {"webpay", "transbank"} and (amount is None):
            amount = payment.amount
        result = await provider.refund(token, amount)
        raw_amount = (
            result.amount
            if result.amount is not None
            else (amount if amount is not None else payment.amount)
        )
        refund_amount: int | None
        if raw_amount is None:
            refund_amount = None
        else:
            try:
                refund_amount = int(raw_amount)
            except (TypeError, ValueError):
                try:
                    from decimal import Decimal, InvalidOperation  # local import to avoid global dependency

                    refund_amount = int(Decimal(str(raw_amount)))
                except (InvalidOperation, ValueError):
                    refund_amount = None
        if refund_amount is None:
            fallback_amount = amount if amount is not None else payment.amount
            refund_amount = int(fallback_amount)
        refund_status = "SUCCEEDED" if result.ok else "FAILED"
        try:
            self.store.record_refund(
                token=token,
                provider=provider_name,
                amount=refund_amount,
                status=refund_status,
                provider_refund_id=result.provider_refund_id,
                payload=result.payload,
                reason=result.error,
            )
        except Exception as db_exc:  # noqa: BLE001
            self.logger.info("db refund record error", extra={"token": token, "event": str(db_exc)})

        if result.ok:
            payment.status = PaymentStatus.REFUNDED
            try:
                self.store.update_status_by_token(
                    provider=provider_name,
                    token=token,
                    to_status=payment.status,
                )
            except Exception as db_exc:  # noqa: BLE001
                self.logger.info("db refund save error", extra={"token": token, "event": str(db_exc)})
            self.logger.info(
                "refund completed",
                extra={"buy_order": payment.buy_order, "token": token, "status": payment.status.value, "provider": provider_name},
            )
        else:
            self.logger.info(
                "refund failed",
                extra={"buy_order": payment.buy_order, "token": token, "provider": provider_name},
            )
        # DB updates for refunds can be handled by reconciler/webhooks as needed
        return payment.status
