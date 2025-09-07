from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
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

router = APIRouter(prefix="/api/payments")

_store = InMemoryPaymentStore()
_service = PaymentsService(_store)


@router.post("", response_model=PaymentCreateResponse, dependencies=[Depends(verify_bearer_token)])
async def create_payment(
    request: PaymentCreateRequest,
    idempotency_key: str | None = Depends(get_idempotency_key),
) -> PaymentCreateResponse:
    return await _service.create_payment(request, idempotency_key)


@router.api_route("/tbk/return", methods=["GET", "POST"], response_model=PaymentStatusResponse)
async def tbk_return(request: Request) -> PaymentStatusResponse:
    form = await request.form() if request.method == "POST" else {}
    params = request.query_params
    token = form.get("token_ws") or params.get("token_ws")
    tbk_token = form.get("TBK_TOKEN") or params.get("TBK_TOKEN")
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
                return RedirectResponse(redirect_to, status_code=303)
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
            return RedirectResponse(redirect_to, status_code=303)
        return result
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid return")
