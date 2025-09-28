from __future__ import annotations

from enum import Enum


class Currency(str, Enum):
    """Supported currencies."""

    CLP = "CLP"
    USD = "USD"


class ProviderName(str, Enum):
    """Supported payment providers (strategy selector)."""

    WEBPAY = "webpay"
    STRIPE = "stripe"
    PAYPAL = "paypal"


class PaymentType(str, Enum):
    """Supported payment instrument types."""

    CREDIT = "credito"
    DEBIT = "debito"
    PREPAID = "prepago"
