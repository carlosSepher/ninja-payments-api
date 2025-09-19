from __future__ import annotations

from dataclasses import dataclass

from .enums import Currency
from .statuses import PaymentStatus


@dataclass
class Payment:
    """Internal representation of a payment."""

    buy_order: str
    amount: int
    currency: Currency
    id: int | None = None
    status: PaymentStatus = PaymentStatus.PENDING
    token: str | None = None
    redirect_url: str | None = None
    provider: str | None = None
    # Optional frontend redirect URLs
    success_url: str | None = None
    failure_url: str | None = None
    cancel_url: str | None = None
    company_id: int | None = None


@dataclass
class Company:
    """Represents a company/merchant authorized to use the API."""

    id: int
    name: str
    contact_email: str | None
    api_token: str
    active: bool = True
