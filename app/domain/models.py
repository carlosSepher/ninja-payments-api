from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .enums import Currency, PaymentType
from .statuses import PaymentStatus


@dataclass
class Payment:
    """Internal representation of a payment."""

    buy_order: str
    amount: Decimal
    currency: Currency
    id: int | None = None
    status: PaymentStatus = PaymentStatus.PENDING
    authorization_code: str | None = None
    token: str | None = None
    redirect_url: str | None = None
    return_url: str | None = None
    provider: str | None = None
    payment_type: PaymentType | None = None
    commerce_id: str | None = None
    product_id: str | None = None
    product_name: str | None = None
    customer_rut: str | None = None
    # Optional frontend redirect URLs
    success_url: str | None = None
    failure_url: str | None = None
    cancel_url: str | None = None
    company_id: int | None = None
    created_at: datetime | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Company:
    """Represents a company/merchant authorized to use the API."""

    id: int
    name: str
    contact_email: str | None
    api_token: str
    active: bool = True
