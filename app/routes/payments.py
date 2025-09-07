from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

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
        return await _service.commit_payment(str(token))
    if tbk_token:
        return _service.cancel_payment(str(tbk_token))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid return")
