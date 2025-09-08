from __future__ import annotations

import logging
import stripe  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi import Response
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from app.domain.dtos import (
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentStatusResponse,
)
from app.repositories.memory_store import InMemoryPaymentStore
from app.services.payments_service import PaymentsService
from app.utils.idempotency import get_idempotency_key
from app.utils.security import verify_bearer_token
from app.config import settings

router = APIRouter(prefix="/api/payments")

_store = InMemoryPaymentStore()
_service = PaymentsService(_store)
logger = logging.getLogger(__name__)


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
        # Surface business errors and unknown provider as 400
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
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

    if session_id:
        # Delegate to provider commit logic; it will read session status and set AUTHORIZED/FAILED
        try:
            await _service.commit_payment(str(session_id))
        except Exception as exc:  # keep webhook 200 to avoid retries storm during dev
            logger.info(
                "stripe webhook commit error",
                extra={"endpoint": "/api/payments/stripe/webhook", "event": str(exc)},
            )
    return Response(status_code=200)
