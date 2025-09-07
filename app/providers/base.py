from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import Payment


class PaymentProvider(ABC):
    """Abstract payment provider."""

    @abstractmethod
    async def create(self, payment: Payment, return_url: str) -> tuple[str, str]:
        """Create a payment and return redirect URL and token."""

    @abstractmethod
    async def commit(self, token: str) -> int:
        """Commit a payment and return provider response code."""
