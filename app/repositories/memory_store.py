from __future__ import annotations

from typing import Dict, Optional

from app.domain.models import Payment


class InMemoryPaymentStore:
    """Simple in-memory payment repository."""

    def __init__(self) -> None:
        self.by_id: Dict[int, Payment] = {}
        self.by_token: Dict[str, int] = {}
        self.by_idempotency: Dict[str, int] = {}

    def save(self, payment: Payment, token: str, idempotency_key: str | None = None) -> None:
        pid = payment.id or (max(self.by_id.keys(), default=0) + 1)
        payment.id = pid
        self.by_id[pid] = payment
        self.by_token[token] = pid
        if idempotency_key:
            self.by_idempotency[idempotency_key] = pid

    def get_by_token(self, token: str) -> Optional[Payment]:
        payment_id = self.by_token.get(token)
        if payment_id:
            return self.by_id.get(payment_id)
        return None

    def get_by_idempotency(self, key: str, company_id: int | None = None) -> Optional[Payment]:
        payment_id = self.by_idempotency.get(key)
        if payment_id:
            return self.by_id.get(payment_id)
        return None

    def list_pending(self) -> list[Payment]:
        return [p for p in self.by_id.values() if p.status.name == "PENDING"]

    def list_all(self) -> list[Payment]:
        return list(self.by_id.values())
