from __future__ import annotations

from enum import Enum


class PaymentStatus(str, Enum):
    """Status of a payment."""

    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    REFUNDED = "REFUNDED"
