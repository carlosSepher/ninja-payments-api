from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import Payment
from app.domain.statuses import PaymentStatus


class PaymentProvider(ABC):
    """Abstract payment provider."""

    @abstractmethod
    async def create(self, payment: Payment, return_url: str) -> tuple[str, str]:
        """Create a payment and return redirect URL and token."""

    @abstractmethod
    async def commit(self, token: str) -> int:
        """Commit a payment and return provider response code."""

    @abstractmethod
    async def status(self, token: str) -> PaymentStatus | None:
        """Return current status without side effects when possible.

        Should return one of PaymentStatus if it can be determined without
        mutating state at the provider; otherwise return None.
        Implementations may decide to finalize the transaction (e.g., Webpay)
        when there is no read-only status API.
        """

    @abstractmethod
    async def refund(self, token: str, amount: int | None = None) -> bool:
        """Issue a refund where applicable.

        - token: provider-specific token/identifier (e.g., session_id / order_id).
        - amount: optional refund amount. Units follow provider conventions used on create
          (Stripe minor units like cents for USD; zero-decimal for CLP; PayPal major units).
        Returns True if the refund request was accepted/created.
        """
