from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, Field

from .enums import Currency, ProviderName
from .statuses import PaymentStatus


class PaymentCreateRequest(BaseModel):
    """Request body for creating a payment."""

    buy_order: str
    amount: int
    currency: Currency
    return_url: str = Field(..., description="URL where Webpay will redirect")
    provider: ProviderName | None = Field(
        default=None,
        description="Selected provider (webpay|stripe|paypal). Defaults to config",
    )
    company_id: int = Field(..., description="Authorized company identifier")
    company_token: str = Field(..., description="API token issued to the company")
    # Optional frontend redirects after processing the return
    success_url: str | None = Field(
        default=None, description="Front URL to redirect when authorized"
    )
    failure_url: str | None = Field(
        default=None, description="Front URL to redirect when failed"
    )
    cancel_url: str | None = Field(
        default=None, description="Front URL to redirect when canceled"
    )


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


class PaymentSummary(BaseModel):
    id: int
    buy_order: str
    amount: int
    currency: Currency
    status: PaymentStatus
    token: str | None = None
    provider: str | None = None
    company_id: int | None = None


class RefreshRequest(BaseModel):
    tokens: list[str]


class RefreshResult(BaseModel):
    updated: int
    results: dict[str, PaymentStatus]


class StatusCheckRequest(BaseModel):
    tokens: list[str]


class StatusCheckResult(BaseModel):
    results: dict[str, PaymentStatus | None]


class RefundRequest(BaseModel):
    token: str
    amount: int | None = None
    company_id: int
    company_token: str


class RefundResponse(BaseModel):
    status: PaymentStatus
