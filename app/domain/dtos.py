from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, Field

from .enums import Currency
from .statuses import PaymentStatus


class PaymentCreateRequest(BaseModel):
    """Request body for creating a payment."""

    buy_order: str
    amount: int
    currency: Currency
    return_url: str = Field(..., description="URL where Webpay will redirect")


class RedirectInfo(BaseModel):
    """Information needed to redirect the user to Webpay."""

    url: str
    token: str
    method: str = "POST"
    form_fields: Dict[str, str]


class PaymentCreateResponse(BaseModel):
    """Response returned when a payment is created."""

    status: PaymentStatus
    redirect: RedirectInfo


class PaymentStatusResponse(BaseModel):
    """Response describing the status of a payment."""

    status: PaymentStatus
