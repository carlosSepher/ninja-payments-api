from __future__ import annotations

from app.config import Settings

from .base import PaymentProvider
from .transbank_webpay_plus import TransbankWebpayPlusProvider


def get_provider(settings: Settings) -> PaymentProvider:
    """Return the payment provider based on configuration."""
    if settings.provider == "transbank":
        return TransbankWebpayPlusProvider(settings)
    msg = f"Unknown provider {settings.provider}"
    raise ValueError(msg)
