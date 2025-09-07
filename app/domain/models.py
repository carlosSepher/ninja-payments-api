from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .enums import Currency
from .statuses import PaymentStatus


@dataclass
class Payment:
    """Internal representation of a payment."""

    buy_order: str
    amount: int
    currency: Currency
    id: str = field(default_factory=lambda: uuid4().hex)
    status: PaymentStatus = PaymentStatus.PENDING
    token: str | None = None
    redirect_url: str | None = None
