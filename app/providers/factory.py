from __future__ import annotations

from app.config import Settings
from typing import Optional

from .base import PaymentProvider


def get_provider(settings: Settings) -> PaymentProvider:
    """Return the payment provider based on configuration."""
    if settings.provider == "transbank":
        from .transbank_webpay_plus import TransbankWebpayPlusProvider

        return TransbankWebpayPlusProvider(settings)
    msg = f"Unknown provider {settings.provider}"
    raise ValueError(msg)


def get_provider_by_name(settings: Settings, name: Optional[str]) -> PaymentProvider:
    """Return a provider instance by normalized name.

    Supported names:
    - "webpay" or "transbank" -> TransbankWebpayPlusProvider
    - Others currently not implemented (stripe, paypal)
    """
    if not name:
        return get_provider(settings)
    normalized = name.lower()
    if normalized in {"webpay", "transbank"}:
        from .transbank_webpay_plus import TransbankWebpayPlusProvider

        return TransbankWebpayPlusProvider(settings)
    if normalized == "stripe":
        from .stripe_checkout import StripeCheckoutProvider

        return StripeCheckoutProvider(settings)
    if normalized == "paypal":
        from .paypal_checkout import PayPalCheckoutProvider

        return PayPalCheckoutProvider(settings)
    msg = f"Unknown provider {name}"
    raise ValueError(msg)
