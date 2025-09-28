from __future__ import annotations

from enum import Enum


class PaymentStatus(str, Enum):
    """Status of a payment."""

    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    REFUNDED = "REFUNDED"
    TO_CONFIRM = "TO_CONFIRM"
    ABANDONED = "ABANDONED"

    @property
    def display_name(self) -> str:
        """Human-friendly label matching business glossary."""

        mapping = {
            self.PENDING: "PENDIENTE",
            self.AUTHORIZED: "APROBADO",
            self.FAILED: "RECHAZADO",
            self.CANCELED: "CANCELADO",
            self.REFUNDED: "REEMBOLSADO",
            self.TO_CONFIRM: "POR_CONFIRMAR",
            self.ABANDONED: "CARRITO_ABANDONADO",
        }
        return mapping.get(self, self.value)
