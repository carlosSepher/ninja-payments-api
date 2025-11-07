from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict

from pydantic import BaseModel, Field, validator

from .enums import Currency, PaymentType, ProviderName
from .statuses import PaymentStatus


_MONEY_QUANT = Decimal("0.01")


def _normalize_money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Invalid monetary amount") from exc
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return amount.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


class PaymentCreateRequest(BaseModel):
    """Request body for creating a payment."""

    buy_order: str
    amount: Decimal
    currency: Currency
    payment_type: PaymentType = Field(..., description="Tipo de pago: credito|debito|prepago|desconocido")
    commerce_id: str = Field(..., min_length=1, description="Identificador interno del comercio")
    product_id: str = Field(..., min_length=1, description="Identificador del producto asociado")
    product_name: str = Field(..., min_length=1, description="Nombre del producto al momento de la transaccion")
    return_url: str = Field(..., description="URL where Webpay will redirect")
    provider: ProviderName | None = Field(
        default=None,
        description="Selected provider (webpay|stripe|paypal). Defaults to config",
    )
    company_id: int = Field(..., description="Authorized company identifier")
    company_token: str = Field(..., description="API token issued to the company")
    # Optional frontend redirects after processing the return
    success_url: str | None = Field(
        default=None, description="Front URL to redirect when authorized"
    )
    failure_url: str | None = Field(
        default=None, description="Front URL to redirect when failed"
    )
    cancel_url: str | None = Field(
        default=None, description="Front URL to redirect when canceled"
    )

    customer_rut: str | None = Field(
        default=None, description="RUT del cliente asociado a la orden (opcional)"
    )
    notifica: bool = Field(
        default=False, description="Indica si se debe notificar externamente el pago"
    )
    contrato: int = Field(
        default=0, description="Identificador numérico de contrato asociado al pago"
    )

    @validator("amount", pre=True)
    def _validate_amount(cls, value: Any) -> Decimal:  # noqa: D417
        return _normalize_money(value)


class RedirectInfo(BaseModel):
    """Information needed to redirect the user to Webpay."""

    url: str
    token: str
    method: str = "POST"
    form_fields: Dict[str, str]


class PaymentCreateResponse(BaseModel):
    """Response returned when a payment is created."""

    status: PaymentStatus
    redirect: RedirectInfo
    internal_id: int | None = Field(None, description="Identificador interno de la transaccion")
    provider_transaction_id: str | None = Field(
        None, description="Identificador entregado por el proveedor"
    )


class PaymentStatusResponse(BaseModel):
    """Response describing the status of a payment."""

    status: PaymentStatus


class PaymentSummary(BaseModel):
    id: int
    buy_order: str
    amount: Decimal
    currency: Currency
    status: PaymentStatus
    token: str | None = None
    provider_transaction_id: str | None = None
    provider: str | None = None
    company_id: int | None = None
    payment_type: PaymentType | None = None
    commerce_id: str | None = None
    product_id: str | None = None
    product_name: str | None = None
    created_at: datetime | None = None


class RefreshRequest(BaseModel):
    tokens: list[str]


class RefreshResult(BaseModel):
    updated: int
    results: dict[str, PaymentStatus]


class StatusCheckRequest(BaseModel):
    tokens: list[str]


class StatusCheckResult(BaseModel):
    results: dict[str, PaymentStatus | None]


class RefundRequest(BaseModel):
    token: str
    amount: Decimal | None = None
    company_id: int
    company_token: str

    @validator("amount", pre=True, always=True)
    def _validate_optional_amount(cls, value: Any) -> Decimal | None:  # noqa: D417
        if value is None:
            return None
        return _normalize_money(value)


class RefundResponse(BaseModel):
    status: PaymentStatus
    codigoAutorizacion: str | None = None
