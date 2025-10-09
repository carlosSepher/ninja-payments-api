from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import stripe  # type: ignore[import-untyped]
from typing import Any

import httpx

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi import Response
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from app.domain.dtos import (
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentStatusResponse,
    PaymentSummary,
    RedirectInfo,
    RefundRequest,
    RefundResponse,
)
from app.repositories.pg_store import PgPaymentStore
from app.services.payments_service import PaymentsService
from app.utils.idempotency import get_idempotency_key
from app.utils.security import verify_bearer_token
from app.config import settings
from app.domain.enums import ProviderName
from app.domain.models import Payment
from app.domain.statuses import PaymentStatus
from app.providers.paypal_checkout import PayPalCheckoutProvider

router = APIRouter(prefix="/api/payments")

_store = PgPaymentStore()
_service = PaymentsService(_store)
logger = logging.getLogger(__name__)


def _provider_reference(payment: Payment) -> str | None:
    metadata = payment.provider_metadata or {}
    if isinstance(metadata, dict):
        for key in (
            "payment_intent_id",
            "paypal_capture_id",
            "paypal_order_id",
            "token_ws",
            "buy_order",
        ):
            value = metadata.get(key)
            if value:
                return str(value)
    return payment.token


def _payment_to_summary(payment: Payment) -> PaymentSummary:
    return PaymentSummary(
        id=payment.id or 0,
        buy_order=payment.buy_order,
        amount=int(payment.amount),
        currency=payment.currency,
        status=payment.status,
        token=payment.token,
        provider=payment.provider,
        company_id=payment.company_id,
        provider_transaction_id=_provider_reference(payment),
        payment_type=payment.payment_type,
        commerce_id=payment.commerce_id,
        product_id=payment.product_id,
        product_name=payment.product_name,
        created_at=payment.created_at,
    )


STRIPE_REFUND_EVENTS = {
    "charge.refunded",
    "charge.refund.updated",
    "charge.refund.created",
}

STRIPE_DISPUTE_EVENTS = {
    "charge.dispute.created",
    "charge.dispute.updated",
    "charge.dispute.closed",
    "charge.dispute.funds_withdrawn",
    "charge.dispute.funds_reinstated",
}

STRIPE_CANCELLATION_EVENTS = {
    "payment_intent.canceled",
    "payment_intent.payment_failed",
    "checkout.session.expired",
}


def _stripe_epoch_to_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _handle_stripe_refund_event(event_type: str, payload: dict[str, Any]) -> str | None:
    metadata = payload.get("metadata") or {}
    payment_intent_id = payload.get("payment_intent") or metadata.get("payment_intent_id")
    token = None
    company_meta = metadata.get("company_id") or metadata.get("COMPANY_ID")
    company_id: int | None = None
    if company_meta is not None:
        try:
            company_id = int(company_meta)
        except (TypeError, ValueError):
            company_id = None
    logger.info(
        "stripe refund start",
        extra={
            "endpoint": "/api/payments/stripe/webhook",
            "event": event_type,
            "payment_intent": str(payment_intent_id or ""),
            "charge": str(payload.get("charge") or ""),
            "metadata": metadata,
            "company_id": company_id,
        },
    )
    if payment_intent_id:
        token = _store.get_token_by_payment_intent(str(payment_intent_id))
        logger.info(
            "stripe refund lookup by intent",
            extra={
                "endpoint": "/api/payments/stripe/webhook",
                "event": event_type,
                "payment_intent": str(payment_intent_id or ""),
                "token": token or "",
            },
        )
    if not token:
        buy_order_meta = metadata.get("buy_order") or metadata.get("BUY_ORDER")
        logger.info(
            "stripe refund lookup by buy_order",
            extra={
                "endpoint": "/api/payments/stripe/webhook",
                "event": event_type,
                "buy_order": str(buy_order_meta or ""),
            },
        )
        if buy_order_meta:
            token = _store.get_latest_token_by_buy_order(str(buy_order_meta), company_id=company_id)
            logger.info(
                "stripe refund buy_order result",
                extra={
                    "endpoint": "/api/payments/stripe/webhook",
                    "event": event_type,
                    "buy_order": str(buy_order_meta or ""),
                    "token": token or "",
                    "company_id": company_id,
                },
            )
    if not token:
        logger.info(
            "stripe refund token not found",
            extra={
                "endpoint": "/api/payments/stripe/webhook",
                "event": event_type,
                "payment_intent": str(payment_intent_id or ""),
                "buy_order": str(metadata.get("buy_order") or metadata.get("BUY_ORDER") or ""),
            },
        )
        return None

    raw_amount = None
    provider_refund_id: str | None = None
    refund_status = str(payload.get("status") or "")
    if payload.get("object") == "refund":
        provider_refund_id = str(payload.get("id") or "") or None
        raw_amount = payload.get("amount")
    else:
        raw_amount = payload.get("amount_refunded")
        refunds = payload.get("refunds") or {}
        if provider_refund_id is None and isinstance(refunds, dict):
            data_items = refunds.get("data")
            if isinstance(data_items, list) and data_items:
                provider_refund_id = str((data_items[-1] or {}).get("id") or "") or None
        if not refund_status:
            refund_status = "succeeded" if event_type == "charge.refunded" else ""

    amount_minor: int | None = None
    if raw_amount is not None:
        try:
            amount_minor = int(raw_amount)
        except (TypeError, ValueError):
            amount_minor = None

    status_value = (
        refund_status.upper()
        if refund_status
        else ("SUCCEEDED" if event_type == "charge.refunded" else "PENDING")
    )
    logger.info(
        "stripe refund recording",
        extra={
            "endpoint": "/api/payments/stripe/webhook",
            "event": event_type,
            "token": token,
            "amount_minor": amount_minor,
            "provider_refund_id": provider_refund_id or "",
            "status": status_value,
        },
    )
    try:
        _store.record_refund(
            token=token,
            provider="stripe",
            amount=amount_minor,
            status=status_value,
            provider_refund_id=provider_refund_id,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "stripe refund log error",
            extra={
                "endpoint": "/api/payments/stripe/webhook",
                "event": str(exc),
                "token": token,
            },
        )

    payment = _store.get_by_token(token)
    should_mark_refunded = False
    if event_type == "charge.refunded":
        should_mark_refunded = True
    elif amount_minor is not None and payment:
        try:
            should_mark_refunded = amount_minor >= int(payment.amount)
        except (TypeError, ValueError):
            should_mark_refunded = False
    logger.info(
        "stripe refund update decision",
        extra={
            "endpoint": "/api/payments/stripe/webhook",
            "event": event_type,
            "token": token,
            "mark_refunded": should_mark_refunded,
            "amount_minor": amount_minor,
            "payment_amount": getattr(payment, "amount", None) if payment else None,
            "status_value": status_value,
        },
    )
    if should_mark_refunded and payment:
        try:
            _store.update_status_by_token(
                provider="stripe",
                token=token,
                to_status=PaymentStatus.REFUNDED,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "stripe refund status update error",
                extra={
                    "endpoint": "/api/payments/stripe/webhook",
                    "event": str(exc),
                    "token": token,
                },
            )
    logger.info(
        "stripe refund processed",
        extra={
            "endpoint": "/api/payments/stripe/webhook",
            "event": event_type,
            "token": token,
            "refund_amount": amount_minor or 0,
        },
    )
    return token


def _handle_stripe_dispute_event(event_type: str, payload: dict[str, Any]) -> str | None:
    dispute_id = str(payload.get("id") or "") or None
    payment_intent_id = payload.get("payment_intent")
    if not payment_intent_id:
        charge = payload.get("charge")
        if isinstance(charge, dict):
            payment_intent_id = charge.get("payment_intent")
    token: str | None = None
    if payment_intent_id:
        token = _store.get_token_by_payment_intent(str(payment_intent_id))
    if not token:
        metadata = payload.get("metadata") or {}
        buy_order_meta = metadata.get("buy_order") or metadata.get("BUY_ORDER")
        company_meta = metadata.get("company_id") or metadata.get("COMPANY_ID")
        company_id: int | None = None
        if company_meta is not None:
            try:
                company_id = int(company_meta)
            except (TypeError, ValueError):
                company_id = None
        if buy_order_meta:
            token = _store.get_latest_token_by_buy_order(str(buy_order_meta), company_id=company_id)
    if not token:
        return None

    amount_minor: int | None = None
    amount_raw = payload.get("amount")
    if amount_raw is not None:
        try:
            amount_minor = int(amount_raw)
        except (TypeError, ValueError):
            amount_minor = None

    opened_at = _stripe_epoch_to_datetime(payload.get("created"))
    closed_at = None
    if event_type in {"charge.dispute.closed", "charge.dispute.funds_reinstated"}:
        closed_at = _stripe_epoch_to_datetime(payload.get("closed")) or datetime.now(timezone.utc)

    status_value = str(payload.get("status") or "")
    reason = str(payload.get("reason") or "").strip() or None

    try:
        _store.record_dispute(
            token=token,
            provider="stripe",
            provider_dispute_id=dispute_id,
            status=status_value,
            amount=amount_minor,
            reason=reason,
            opened_at=opened_at,
            closed_at=closed_at,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "stripe dispute log error",
            extra={
                "endpoint": "/api/payments/stripe/webhook",
                "event": event_type,
                "token": token,
                "dispute": dispute_id or "",
                "error": str(exc),
            },
        )

    target_status: PaymentStatus | None = None
    normalized_status = status_value.lower()
    if event_type == "charge.dispute.closed":
        if normalized_status in {"won", "warning_closed"}:
            target_status = PaymentStatus.AUTHORIZED
        elif normalized_status in {"lost", "warning_lost"}:
            target_status = PaymentStatus.FAILED
    elif event_type == "charge.dispute.funds_reinstated":
        target_status = PaymentStatus.AUTHORIZED
    elif event_type in {"charge.dispute.created", "charge.dispute.updated", "charge.dispute.funds_withdrawn"}:
        target_status = PaymentStatus.FAILED

    if target_status:
        try:
            _store.update_status_by_token(
                provider="stripe",
                token=token,
                to_status=target_status,
                reason=f"stripe dispute {dispute_id or ''} {normalized_status or event_type}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "stripe dispute status update error",
                extra={
                    "endpoint": "/api/payments/stripe/webhook",
                    "event": event_type,
                    "token": token,
                    "dispute": dispute_id or "",
                    "error": str(exc),
                },
            )

    logger.info(
        "stripe dispute handled",
        extra={
            "endpoint": "/api/payments/stripe/webhook",
            "event": event_type,
            "token": token,
            "dispute": dispute_id or "",
            "status": status_value,
        },
    )
    return token


def _handle_stripe_cancellation_event(event_type: str, payload: dict[str, Any]) -> str | None:
    token: str | None = None
    new_status = PaymentStatus.CANCELED
    reason: str | None = None

    if event_type in {"payment_intent.canceled", "payment_intent.payment_failed"}:
        payment_intent_id = payload.get("id")
        if payment_intent_id:
            token = _store.get_token_by_payment_intent(str(payment_intent_id))
        if not token:
            metadata = payload.get("metadata") or {}
            buy_order_meta = metadata.get("buy_order") or metadata.get("BUY_ORDER")
            company_meta = metadata.get("company_id") or metadata.get("COMPANY_ID")
            company_id: int | None = None
            if company_meta is not None:
                try:
                    company_id = int(company_meta)
                except (TypeError, ValueError):
                    company_id = None
            if buy_order_meta:
                token = _store.get_latest_token_by_buy_order(str(buy_order_meta), company_id=company_id)
        if event_type == "payment_intent.payment_failed":
            new_status = PaymentStatus.FAILED
            last_error = payload.get("last_payment_error") or {}
            reason = str(last_error.get("message") or last_error.get("code") or "").strip() or None
        else:
            new_status = PaymentStatus.CANCELED
            reason = str(payload.get("cancellation_reason") or "").strip() or None
    elif event_type == "checkout.session.expired":
        session_id = payload.get("id")
        if session_id:
            token = str(session_id)
        reason = "stripe checkout.session.expired"
        new_status = PaymentStatus.CANCELED
    else:
        return None

    if not token:
        return None

    try:
        _store.update_status_by_token(
            provider="stripe",
            token=token,
            to_status=new_status,
            reason=reason or f"stripe event {event_type}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "stripe cancellation status update error",
            extra={
                "endpoint": "/api/payments/stripe/webhook",
                "event": event_type,
                "token": token,
                "error": str(exc),
            },
        )
        return token

    logger.info(
        "stripe cancellation handled",
        extra={
            "endpoint": "/api/payments/stripe/webhook",
            "event": event_type,
            "token": token,
            "status": new_status.value,
        },
    )
    return token


def _parse_paypal_time(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _handle_paypal_dispute_event(
    event_type: str,
    resource: dict[str, Any],
    fallback_token: str | None,
) -> str | None:
    dispute_id = str(resource.get("dispute_id") or "") or None
    token: str | None = None

    disputed_transactions = resource.get("disputed_transactions") or []
    for tx in disputed_transactions:
        if not isinstance(tx, dict):
            continue
        capture_id = tx.get("seller_transaction_id") or tx.get("transaction_id")
        if capture_id:
            token = _store.get_token_by_paypal_capture(str(capture_id))
            if token:
                break

    if not token:
        token = fallback_token
    if not token:
        return None

    amount_minor: int | None = None
    amount_info = resource.get("disputed_amount")
    if not isinstance(amount_info, dict):
        amount_info = resource.get("amount") if isinstance(resource.get("amount"), dict) else {}
    value_raw = amount_info.get("value") if isinstance(amount_info, dict) else None
    if value_raw is not None:
        try:
            quantized = Decimal(str(value_raw)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            amount_minor = int(quantized)
        except (InvalidOperation, ValueError):
            amount_minor = None

    status_value = str(resource.get("status") or "")
    reason = str(resource.get("reason") or "").strip() or None
    outcome = str((resource.get("dispute_outcome") or {}).get("outcome_code") or "").upper() or ""

    opened_at = _parse_paypal_time(resource.get("create_time"))
    closed_at = _parse_paypal_time(resource.get("update_time")) if "RESOLVED" in event_type.upper() else None

    try:
        _store.record_dispute(
            token=token,
            provider="paypal",
            provider_dispute_id=dispute_id,
            status=status_value,
            amount=amount_minor,
            reason=reason or outcome or None,
            opened_at=opened_at,
            closed_at=closed_at,
            payload=resource,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "paypal dispute log error",
            extra={
                "endpoint": "/api/payments/paypal/webhook",
                "event": event_type,
                "token": token,
                "dispute": dispute_id or "",
                "error": str(exc),
            },
        )

    target_status: PaymentStatus | None = None
    if event_type.upper().endswith("RESOLVED"):
        if outcome == "RESOLVED_SELLER_FAVOUR":
            target_status = PaymentStatus.AUTHORIZED
        elif outcome:
            target_status = PaymentStatus.FAILED
    else:
        target_status = PaymentStatus.FAILED

    if target_status:
        try:
            _store.update_status_by_token(
                provider="paypal",
                token=token,
                to_status=target_status,
                reason=f"paypal dispute {dispute_id or ''} {outcome or status_value}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "paypal dispute status update error",
                extra={
                    "endpoint": "/api/payments/paypal/webhook",
                    "event": event_type,
                    "token": token,
                    "dispute": dispute_id or "",
                    "error": str(exc),
                },
            )

    logger.info(
        "paypal dispute handled",
        extra={
            "endpoint": "/api/payments/paypal/webhook",
            "event": event_type,
            "token": token,
            "dispute": dispute_id or "",
            "status": status_value,
            "outcome": outcome,
        },
    )
    return token


@router.get("", response_model=list[PaymentSummary], dependencies=[Depends(verify_bearer_token)])
async def list_payments(
    provider: ProviderName | None = Query(None, description="Filtra por proveedor"),
    status: PaymentStatus | None = Query(None, description="Filtra por estado"),
    start_date: datetime | None = Query(None, description="Fecha/hora inicial (inclusive, ISO 8601)"),
    end_date: datetime | None = Query(None, description="Fecha/hora final (inclusive, ISO 8601)"),
    token: str | None = Query(None, description="Filtra por token exacto"),
    limit: int = Query(200, ge=1, le=500, description="Número máximo de registros"),
) -> list[PaymentSummary]:
    items = _service.list_payments(
        provider=provider.value if provider else None,
        status=status,
        start_date=start_date,
        end_date=end_date,
        token=token,
        limit=limit,
    )
    return [_payment_to_summary(p) for p in items]


@router.post("", response_model=PaymentCreateResponse, dependencies=[Depends(verify_bearer_token)])
async def create_payment(
    request: PaymentCreateRequest,
    idempotency_key: str | None = Depends(get_idempotency_key),
) -> PaymentCreateResponse:
    logger.info(
        "create_payment received",
        extra={
            "endpoint": "/api/payments",
            "method": "POST",
            "buy_order": request.buy_order,
            "amount": request.amount,
            "currency": request.currency.value,
            "provider": (request.provider.value if getattr(request, "provider", None) else None),
            "idempotency_key": idempotency_key or "",
        },
    )
    try:
        result = await _service.create_payment(request, idempotency_key)
    except ValueError as exc:
        detail = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "company" in detail.lower():
            status_code = status.HTTP_401_UNAUTHORIZED
        # Surface business errors, invalid credentials, and unknown provider
        raise HTTPException(status_code=status_code, detail=detail) from exc
    logger.info(
        "create_payment responded",
        extra={
            "endpoint": "/api/payments",
            "method": "POST",
            "buy_order": request.buy_order,
            "status": result.status.value,
            "token": result.redirect.token,
            "provider": (request.provider.value if getattr(request, "provider", None) else None),
        },
    )
    return result


@router.api_route("/tbk/return", methods=["GET", "POST"], response_model=PaymentStatusResponse)
async def tbk_return(request: Request) -> PaymentStatusResponse:
    form = await request.form() if request.method == "POST" else {}
    params = request.query_params
    token_ws = form.get("token_ws") or params.get("token_ws")
    token = token_ws or form.get("token") or params.get("token")
    tbk_token = form.get("TBK_TOKEN") or params.get("TBK_TOKEN")
    paypal_cancel = form.get("paypal_cancel") or params.get("paypal_cancel")
    response_format = (form.get("format") or params.get("format") or "").lower()
    logger.info(
        "tbk_return received",
        extra={
            "endpoint": "/api/payments/tbk/return",
            "method": request.method,
            "token": str(token or tbk_token or ""),
        },
    )
    if token and paypal_cancel:
        # Explicit PayPal cancel flow
        result = _service.cancel_payment(str(token))
        payment = _store.get_by_token(str(token))
        if payment and payment.cancel_url:
            url = urlparse(payment.cancel_url)
            q = dict(parse_qsl(url.query))
            q.update({"status": result.status.value, "buy_order": payment.buy_order})
            new_query = urlencode(q)
            redirect_to = urlunparse((url.scheme, url.netloc, url.path, url.params, new_query, url.fragment))
            logger.info(
                "tbk_return redirecting (paypal cancel)",
                extra={
                    "endpoint": "/api/payments/tbk/return",
                    "method": request.method,
                    "buy_order": payment.buy_order,
                    "status": result.status.value,
                    "redirect_to": redirect_to,
                },
            )
            if response_format != "json":
                return RedirectResponse(redirect_to, status_code=303)
            # fallthrough to JSON below
        logger.info(
            "tbk_return returning JSON (paypal cancel)",
            extra={
                "endpoint": "/api/payments/tbk/return",
                "method": request.method,
                "status": result.status.value,
                "token": str(token),
            },
        )
        return result
    if token:
        result = await _service.commit_payment(str(token))
        # If we have frontend URLs saved for this payment, redirect the browser
        payment = _store.get_by_token(str(token))
        if payment:
            target: str | None = None
            if result.status.value == "AUTHORIZED" and payment.success_url:
                target = payment.success_url
            elif result.status.value == "FAILED" and payment.failure_url:
                target = payment.failure_url
            if target:
                # Append status and buy_order as query parameters
                url = urlparse(target)
                q = dict(parse_qsl(url.query))
                q.update({"status": result.status.value, "buy_order": payment.buy_order})
                new_query = urlencode(q)
                redirect_to = urlunparse((url.scheme, url.netloc, url.path, url.params, new_query, url.fragment))
                logger.info(
                    "tbk_return redirecting",
                    extra={
                        "endpoint": "/api/payments/tbk/return",
                        "method": request.method,
                        "buy_order": payment.buy_order,
                        "status": result.status.value,
                        "redirect_to": redirect_to,
                    },
                )
                if response_format != "json":
                    return RedirectResponse(redirect_to, status_code=303)
                # else fallthrough and return JSON below
        logger.info(
            "tbk_return returning JSON",
            extra={
                "endpoint": "/api/payments/tbk/return",
                "method": request.method,
                "status": result.status.value,
                "token": str(token),
            },
        )
        return result
    if tbk_token:
        result = _service.cancel_payment(str(tbk_token))
        payment = _store.get_by_token(str(tbk_token))
        if payment and payment.cancel_url:
            url = urlparse(payment.cancel_url)
            q = dict(parse_qsl(url.query))
            q.update({"status": result.status.value, "buy_order": payment.buy_order})
            new_query = urlencode(q)
            redirect_to = urlunparse((url.scheme, url.netloc, url.path, url.params, new_query, url.fragment))
            logger.info(
                "tbk_return redirecting (cancel)",
                extra={
                    "endpoint": "/api/payments/tbk/return",
                    "method": request.method,
                    "buy_order": payment.buy_order,
                    "status": result.status.value,
                    "redirect_to": redirect_to,
                },
            )
            if response_format != "json":
                return RedirectResponse(redirect_to, status_code=303)
            # else fallthrough and return JSON below
        logger.info(
            "tbk_return returning JSON (cancel)",
            extra={
                "endpoint": "/api/payments/tbk/return",
                "method": request.method,
                "status": result.status.value,
                "token": str(tbk_token),
            },
        )
        return result
    logger.info(
        "tbk_return invalid return",
        extra={
            "endpoint": "/api/payments/tbk/return",
            "method": request.method,
        },
    )
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid return")


@router.get("/pending", response_model=list[PaymentSummary], dependencies=[Depends(verify_bearer_token)])
async def list_pending() -> list[PaymentSummary]:
    items = _store.list_pending()
    return [_payment_to_summary(p) for p in items]

@router.get("/all", response_model=list[PaymentSummary], dependencies=[Depends(verify_bearer_token)])
async def list_all() -> list[PaymentSummary]:
    items = _store.list_all()
    return [_payment_to_summary(p) for p in items]

@router.post("/paypal/webhook")
async def paypal_webhook(request: Request) -> Response:
    """Handle PayPal webhooks with signature verification.

    Primary use: finalize approved orders server-side and reflect refunds.
    - Verifies signature via PayPal Verify Webhook Signature API.
    - On CHECKOUT.ORDER.APPROVED: captures the order (commit) to move to AUTHORIZED.
    - On PAYMENT.CAPTURE.REFUNDED/PARTIALLY_REFUNDED: attempts to mark REFUNDED if related order_id is present.
    """
    if not settings.paypal_webhook_id:
        logger.info(
            "paypal webhook id missing",
            extra={"endpoint": "/api/payments/paypal/webhook"},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="PayPal webhook not configured")

    try:
        event = await request.json()
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "paypal webhook invalid json",
            extra={"endpoint": "/api/payments/paypal/webhook", "event": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload")
    if not isinstance(event, dict):
        logger.info(
            "paypal webhook unexpected payload",
            extra={"endpoint": "/api/payments/paypal/webhook"},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload")

    headers = request.headers
    transmission_id = headers.get("PayPal-Transmission-Id")
    transmission_time = headers.get("PayPal-Transmission-Time")
    cert_url = headers.get("PayPal-Cert-Url")
    auth_algo = headers.get("PayPal-Auth-Algo")
    transmission_sig = headers.get("PayPal-Transmission-Sig")
    webhook_id = settings.paypal_webhook_id

    # Verify signature
    provider = PayPalCheckoutProvider(settings)
    access_token = await provider._get_access_token()  # noqa: SLF001 (intentional internal use)
    verify_url = f"{provider.base_url}/v1/notifications/verify-webhook-signature"
    payload = {
        "transmission_id": transmission_id,
        "transmission_time": transmission_time,
        "cert_url": cert_url,
        "auth_algo": auth_algo,
        "transmission_sig": transmission_sig,
        "webhook_id": webhook_id,
        "webhook_event": event,
    }
    async with httpx.AsyncClient() as client:
        vresp = await client.post(
            verify_url,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )
        try:
            vdata = vresp.json()
        except Exception:  # noqa: BLE001
            vdata = {"verification_status": "FAILURE"}

    if str(vdata.get("verification_status", "")).upper() != "SUCCESS":
        logger.info(
            "paypal webhook verification failed",
            extra={"endpoint": "/api/payments/paypal/webhook", "response_code": vresp.status_code},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature")

    event_type = str(event.get("event_type", ""))
    event_upper = event_type.upper()
    resource = event.get("resource", {}) or {}
    related_ids = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    raw_order_id = str(related_ids.get("order_id") or "")
    capture_id = str(related_ids.get("capture_id") or "")
    if not capture_id:
        for link in resource.get("links", []) or []:
            href = link.get("href")
            rel = link.get("rel")
            if isinstance(href, str) and rel == "up":
                capture_id = href.rstrip("/").split("/")[-1]
                break
    payment = None
    payment_token: str | None = None
    if raw_order_id:
        payment = _store.get_by_token(raw_order_id)
        if payment:
            payment_token = raw_order_id
    if (payment is None or payment_token is None) and capture_id:
        token_by_capture = _store.get_token_by_paypal_capture(capture_id)
        if token_by_capture:
            payment = _store.get_by_token(token_by_capture)
            payment_token = token_by_capture
    if payment_token is None:
        fallback = str(resource.get("custom_id") or resource.get("invoice_id") or "")
        payment_token = payment_token or fallback or raw_order_id or capture_id or None
        if payment_token:
            payment = payment or _store.get_by_token(payment_token)
    order_id = payment_token or ""

    logger.info(
        "paypal webhook received",
        extra={"endpoint": "/api/payments/paypal/webhook", "event": event_type, "token": order_id},
    )
    try:
        _store.record_webhook(
            provider="paypal",
            event_id=str(event.get("id", "")),
            event_type=event_type,
            verification_status="SUCCESS",
            headers=dict(request.headers),
            payload=event,  # type: ignore[arg-type]
            related_token=(order_id or None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "paypal webhook log error",
            extra={"endpoint": "/api/payments/paypal/webhook", "event": str(exc)},
        )

    if event_upper.startswith("CUSTOMER.DISPUTE."):
        handled_token = _handle_paypal_dispute_event(event_upper, resource, order_id or None)
        if not handled_token:
            logger.info(
                "paypal dispute token not found",
                extra={
                    "endpoint": "/api/payments/paypal/webhook",
                    "event": event_type,
                    "order_id": order_id,
                },
            )
        return Response(status_code=200)

    if event_upper in {"CHECKOUT.ORDER.CANCELLED", "CHECKOUT.ORDER.CANCELED", "PAYMENT.CAPTURE.CANCELLED"} and order_id:
        try:
            _store.update_status_by_token(
                provider="paypal",
                token=order_id,
                to_status=PaymentStatus.CANCELED,
                reason="paypal checkout order cancelled via webhook",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "paypal cancel status update error",
                extra={
                    "endpoint": "/api/payments/paypal/webhook",
                    "event": event_type,
                    "token": order_id,
                    "error": str(exc),
                },
            )
        else:
            logger.info(
                "paypal order cancelled",
                extra={
                    "endpoint": "/api/payments/paypal/webhook",
                    "event": event_type,
                    "token": order_id,
                    "status": PaymentStatus.CANCELED.value,
                },
            )
        return Response(status_code=200)

    if event_upper in {"PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.REVERSED"} and order_id:
        try:
            _store.update_status_by_token(
                provider="paypal",
                token=order_id,
                to_status=PaymentStatus.FAILED,
                reason=f"paypal event {event_type}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "paypal capture failure status update error",
                extra={
                    "endpoint": "/api/payments/paypal/webhook",
                    "event": event_type,
                    "token": order_id,
                    "error": str(exc),
                },
            )
        else:
            logger.info(
                "paypal capture failed",
                extra={
                    "endpoint": "/api/payments/paypal/webhook",
                    "event": event_type,
                    "token": order_id,
                    "status": PaymentStatus.FAILED.value,
                },
            )
        return Response(status_code=200)

    # Handle primary event: approved order -> capture (commit)
    if event_upper == "CHECKOUT.ORDER.APPROVED" and order_id:
        try:
            await _service.commit_payment(order_id)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "paypal webhook commit error",
                extra={"endpoint": "/api/payments/paypal/webhook", "event": str(exc)},
            )
        return Response(status_code=200)

    # Handle refunds: mark store as REFUNDED if we can relate order_id
    if event_upper in {"PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.PARTIALLY_REFUNDED"} and order_id:
        payment = _store.get_by_token(order_id)
        amount_minor: int | None = None
        if payment:
            amount_info = resource.get("amount") or {}
            value_str = amount_info.get("value") if isinstance(amount_info, dict) else None
            currency_code = amount_info.get("currency_code") if isinstance(amount_info, dict) else payment.currency.value
            if value_str is not None:
                try:
                    dec = Decimal(str(value_str))
                    amount_minor = int(dec.to_integral_value())
                except (InvalidOperation, ValueError):
                    amount_minor = None
            if amount_minor is None:
                amount_minor = payment.amount
            try:
                _store.update_status_by_token(
                    provider="paypal",
                    token=order_id,
                    to_status=PaymentStatus.REFUNDED,
                )
                payment.status = PaymentStatus.REFUNDED
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "paypal webhook refund status update error",
                    extra={"endpoint": "/api/payments/paypal/webhook", "token": order_id, "event": str(exc)},
                )
            try:
                _store.record_refund(
                    token=order_id,
                    provider="paypal",
                    amount=amount_minor,
                    status="SUCCEEDED",
                    provider_refund_id=(capture_id or str(resource.get("id") or "") or None),
                    payload=resource,
                    reason=str((resource.get("status_details") or {}).get("reason") or "") or None,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "paypal webhook refund log error",
                    extra={"endpoint": "/api/payments/paypal/webhook", "token": order_id, "event": str(exc)},
                )
            logger.info(
                "paypal webhook refund applied",
                extra={"endpoint": "/api/payments/paypal/webhook", "token": order_id, "status": PaymentStatus.REFUNDED.value, "refund_amount": amount_minor},
            )
        return Response(status_code=200)

    return Response(status_code=200)


@router.get("/redirect", response_model=RedirectInfo, dependencies=[Depends(verify_bearer_token)])
async def get_redirect(token: str) -> RedirectInfo:
    """Return the stored redirect info for a given token to resume checkout.

    - Webpay: method POST with form_fields { token_ws }
    - Stripe/PayPal: method GET to provider URL
    """
    payment = _store.get_by_token(token)
    if not payment or not payment.redirect_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown token")
    if (payment.provider or settings.provider) in {"webpay", "transbank"}:
        return RedirectInfo(url=payment.redirect_url, token=token, method="POST", form_fields={"token_ws": token})
    return RedirectInfo(url=payment.redirect_url, token=token, method="GET", form_fields={})


@router.post("/refund", response_model=RefundResponse, dependencies=[Depends(verify_bearer_token)])
async def refund_payment(req: RefundRequest) -> RefundResponse:
    try:
        company = _service.company_store.validate_credentials(req.company_id, req.company_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    payment = _store.get_by_token(req.token)
    if not payment or payment.company_id != company.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown token")

    try:
        status_val = await _service.refund(req.token, req.amount, company_id=company.id)
        return RefundResponse(status=status_val)
    except ValueError as exc:  # unknown token
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> Response:
    """Handle Stripe webhooks (e.g., checkout.session.completed).

    Verifies the signature and commits the payment based on the session id.
    """
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    if not settings.stripe_webhook_secret:
        logger.info(
            "stripe webhook secret missing",
            extra={"endpoint": "/api/payments/stripe/webhook"},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stripe webhook not configured")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=sig_header, secret=settings.stripe_webhook_secret
        )
    except Exception as exc:  # includes JSON parse and signature errors
        logger.info(
            "stripe webhook invalid",
            extra={"endpoint": "/api/payments/stripe/webhook", "event": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook")

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})
    session_id = None
    if event_type.startswith("checkout.session"):
        session_id = data.get("id")
    # Other event types could be mapped if desired (e.g., payment_intent.payment_failed)

    logger.info(
        "stripe webhook received",
        extra={
            "endpoint": "/api/payments/stripe/webhook",
            "event": event_type,
            "token": str(session_id or ""),
        },
    )

    related_token: str | None = None

    if session_id:
        # Delegate to provider commit logic; it will read session status and set AUTHORIZED/FAILED
        try:
            await _service.commit_payment(str(session_id))
        except Exception as exc:  # keep webhook 200 to avoid retries storm during dev
            logger.info(
                "stripe webhook commit error",
                extra={"endpoint": "/api/payments/stripe/webhook", "event": str(exc)},
            )
        related_token = str(session_id)
    elif event_type in STRIPE_REFUND_EVENTS:
        related_token = _handle_stripe_refund_event(event_type, data)
    elif event_type in STRIPE_DISPUTE_EVENTS:
        related_token = _handle_stripe_dispute_event(event_type, data)
    elif event_type in STRIPE_CANCELLATION_EVENTS:
        related_token = _handle_stripe_cancellation_event(event_type, data)

    # Record webhook inbox entry for traceability
    try:
        _store.record_webhook(
            provider="stripe",
            event_id=str(event.get("id", "")),
            event_type=event_type,
            verification_status="SUCCESS",
            headers=dict(request.headers),
            payload=event,  # type: ignore[arg-type]
            related_token=related_token,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "stripe webhook log error",
            extra={"endpoint": "/api/payments/stripe/webhook", "event": str(exc)},
        )
    return Response(status_code=200)
