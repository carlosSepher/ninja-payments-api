from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Any

from app.db.client import get_conn
from app.domain.enums import Currency, PaymentType
from app.domain.models import Payment
from app.domain.statuses import PaymentStatus
from app.config import settings
from psycopg2.extras import Json


MONEY_QUANT = Decimal("0.01")


def _resolve_payment_environment() -> str:
    raw = (getattr(settings, "ENV_TYPE", None) or "test").lower()
    if raw in {"production", "prod", "live"}:
        return "live"
    return "test"


PAYMENT_ENVIRONMENT = _resolve_payment_environment()


class PgPaymentStore:
    """PostgreSQL-backed store for payments using raw psycopg2.

    Mirrors the minimal interface used by the service and routes.
    """

    @staticmethod
    def _normalize_amount(value: Any | None, *, default: Decimal | None = None) -> Decimal | None:
        if value is None:
            return default
        if isinstance(value, Decimal):
            amount = value
        else:
            try:
                amount = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("Invalid monetary amount") from exc
        return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    def _hydrate_payment(
        self,
        *,
        pid: int,
        buy_order: str,
        amount_minor: Any,
        currency: str,
        provider: str,
        status: str,
        authorization_code: str | None,
        token: str | None,
        company_id: int | None,
        payment_type: str | None,
        commerce_id: str | None,
        product_id: str | None,
        product_name: str | None,
        created_at: datetime | None,
        provider_metadata: Any | None,
        success_url: str | None = None,
        failure_url: str | None = None,
        cancel_url: str | None = None,
        return_url: str | None = None,
        notifica: bool | None = None,
        contrato: int | None = None,
        cuotas: list[int] | None = None,
        tipo_pago: str | None = None,
    ) -> Payment:
        amount_value = self._normalize_amount(amount_minor)
        if amount_value is None:
            raise ValueError("Persisted payment amount cannot be NULL")
        payment = Payment(
            buy_order=str(buy_order),
            amount=amount_value,
            currency=Currency(str(currency)),
            provider=str(provider) if provider else None,
            authorization_code=str(authorization_code) if authorization_code else None,
            payment_type=PaymentType(str(payment_type)) if payment_type else None,
            commerce_id=str(commerce_id) if commerce_id else None,
            product_id=str(product_id) if product_id else None,
            product_name=str(product_name) if product_name else None,
            notifica=bool(notifica) if notifica is not None else False,
            contrato=int(contrato) if contrato is not None else 0,
            cuotas=list(cuotas or []),
            tipo_pago=str(tipo_pago).strip() if tipo_pago is not None else "",
            success_url=success_url,
            failure_url=failure_url,
            cancel_url=cancel_url,
            return_url=return_url,
        )
        payment.status = PaymentStatus(str(status))
        payment.id = int(pid)
        payment.token = str(token) if token else None
        if company_id is not None:
            payment.company_id = int(company_id)
        payment.created_at = created_at
        if provider_metadata:
            try:
                payment.provider_metadata = dict(provider_metadata)
            except Exception:  # noqa: BLE001
                payment.provider_metadata = provider_metadata
        payment.auxiliar_amount = getattr(payment, "auxiliar_amount", None)
        return payment

    def save(self, payment: Payment, token: str, idempotency_key: str | None = None) -> None:
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                if payment.company_id is None:
                    raise ValueError("payment.company_id required")
                amount_value = self._normalize_amount(payment.amount)
                if amount_value is None:
                    raise ValueError("payment.amount required")
                # Ensure order exists (per company)
                cur.execute(
                    """
                    INSERT INTO payment_order (buy_order, company_id, environment, currency, amount_expected_minor, customer_rut, status, metadata, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'OPEN', '{}'::jsonb, NOW(), NOW())
                    ON CONFLICT (company_id, buy_order) DO UPDATE
                        SET currency = EXCLUDED.currency,
                            amount_expected_minor = EXCLUDED.amount_expected_minor,
                            customer_rut = COALESCE(EXCLUDED.customer_rut, payment_order.customer_rut),
                            updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        payment.buy_order,
                        payment.company_id,
                        PAYMENT_ENVIRONMENT,
                        payment.currency.value,
                        amount_value,
                        getattr(payment, 'customer_rut', None),
                    ),
                )
                row = cur.fetchone()
                order_id = row[0] if row else None
                metadata_json = Json(payment.provider_metadata or {})
                context_json = Json(payment.context or {})
                # Insert payment attempt with enriched transaction metadata
                cur.execute(
                    """
                    INSERT INTO payment (
                        payment_order_id, company_id, buy_order, amount_minor, currency, provider,
                        payment_type, commerce_id, product_id, product_name,
                        environment,
                        status, token, redirect_url, return_url, success_url, failure_url, cancel_url,
                        idempotency_key, provider_metadata, context, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, NOW(), NOW()
                    )
                    RETURNING id, created_at
                    """,
                    (
                        order_id,
                        payment.company_id,
                        payment.buy_order,
                        amount_value,
                        payment.currency.value,
                        (payment.provider or ''),
                        payment.payment_type.value if payment.payment_type else None,
                        payment.commerce_id,
                        payment.product_id,
                        payment.product_name,
                        PAYMENT_ENVIRONMENT,
                        payment.status.value,
                        token,
                        payment.redirect_url,
                        payment.return_url,
                        payment.success_url,
                        payment.failure_url,
                        payment.cancel_url,
                        idempotency_key,
                        metadata_json,
                        context_json,
                    ),
                )
                inserted = cur.fetchone()
                if inserted:
                    payment.id = int(inserted[0])
                    payment.created_at = inserted[1]
                    notifica_value = bool(getattr(payment, "notifica", False))
                    contrato_value = int(getattr(payment, "contrato", 0) or 0)
                    cuotas_raw = getattr(payment, "cuotas", None) or []
                    cuotas_value = list(cuotas_raw)
                    tipo_pago_value = str(getattr(payment, "tipo_pago", "") or "").strip()
                    cur.execute(
                        """
                        INSERT INTO payment_contract (payment_id, notifica, contrato, cuotas, tipo_pago, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (payment_id) DO UPDATE
                            SET notifica = EXCLUDED.notifica,
                                contrato = EXCLUDED.contrato,
                                cuotas = EXCLUDED.cuotas,
                                tipo_pago = EXCLUDED.tipo_pago,
                                updated_at = NOW()
                        """,
                        (payment.id, notifica_value, contrato_value, cuotas_value, tipo_pago_value),
                    )
                    nombre_depositante = (getattr(payment, "depositante_nombre", None) or "").strip() or None
                    rut_depositante = getattr(payment, "depositante_rut", None)
                    if nombre_depositante or rut_depositante:
                        cur.execute(
                            """
                            INSERT INTO payment_deposit_info (payment_id, nombre_depositante, rut_depositante, created_at, updated_at)
                            VALUES (%s, %s, %s, NOW(), NOW())
                            ON CONFLICT (payment_id) DO UPDATE
                                SET nombre_depositante = COALESCE(EXCLUDED.nombre_depositante, payment_deposit_info.nombre_depositante),
                                    rut_depositante = COALESCE(EXCLUDED.rut_depositante, payment_deposit_info.rut_depositante),
                                    updated_at = NOW()
                            """,
                            (payment.id, nombre_depositante, rut_depositante),
                        )
                    aux_amount = getattr(payment, "auxiliar_amount", None)
                    if aux_amount is not None and getattr(payment, "currency", None) and payment.currency.value != "CLP":
                        aux_value = self._normalize_amount(aux_amount)
                        base_amount = self._normalize_amount(payment.amount)
                        currency_code = payment.currency.value
                        if aux_value is not None and base_amount is not None:
                            cur.execute(
                                """
                                INSERT INTO payment_aux_amount (payment_id, amount, auxiliar_amount, currency, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, NOW(), NOW())
                                ON CONFLICT (payment_id) DO UPDATE
                                    SET amount = EXCLUDED.amount,
                                        auxiliar_amount = EXCLUDED.auxiliar_amount,
                                        currency = EXCLUDED.currency,
                                        updated_at = NOW()
                                """,
                                (payment.id, base_amount, aux_value, currency_code),
                            )

    def get_by_token(self, token: str) -> Optional[Payment]:
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, p.buy_order, p.amount_minor, p.currency, p.provider, p.status, p.authorization_code,
                           p.token, p.redirect_url, p.return_url,
                           p.success_url, p.failure_url, p.cancel_url, p.company_id,
                           p.payment_type, p.commerce_id, p.product_id, p.product_name,
                           p.created_at, p.provider_metadata, p.context,
                           pc.notifica, pc.contrato, pc.cuotas, pc.tipo_pago
                      FROM payment p
                      LEFT JOIN payment_contract pc ON pc.payment_id = p.id
                     WHERE p.token = %s
                     LIMIT 1
                    """,
                    (token,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                (
                    pid,
                    buy_order,
                    amount_minor,
                    currency,
                    provider,
                    status,
                    authorization_code,
                    tok,
                    redirect_url,
                    return_url,
                    success_url,
                    failure_url,
                    cancel_url,
                    company_id,
                    payment_type,
                    commerce_id,
                    product_id,
                    product_name,
                    created_at,
                    provider_metadata,
                    context,
                    notifica,
                    contrato,
                    cuotas,
                    tipo_pago,
                ) = row
                payment = self._hydrate_payment(
                    pid=int(pid),
                    buy_order=str(buy_order),
                    amount_minor=amount_minor,
                    currency=str(currency),
                    provider=str(provider) if provider else '',
                    status=str(status),
                    authorization_code=authorization_code,
                    token=str(tok) if tok else None,
                    company_id=int(company_id) if company_id is not None else None,
                    payment_type=str(payment_type) if payment_type else None,
                    commerce_id=str(commerce_id) if commerce_id else None,
                    product_id=str(product_id) if product_id else None,
                    product_name=str(product_name) if product_name else None,
                    created_at=created_at,
                    provider_metadata=provider_metadata,
                    success_url=success_url,
                    failure_url=failure_url,
                    cancel_url=cancel_url,
                    return_url=return_url,
                    notifica=notifica,
                    contrato=contrato,
                    cuotas=cuotas,
                    tipo_pago=tipo_pago,
                )
                payment.redirect_url = redirect_url
                payment.return_url = return_url
                if context:
                    try:
                        payment.context = dict(context)
                    except Exception:  # noqa: BLE001
                        payment.context = context
                return payment

    def get_by_idempotency(self, idempotency_key: str, company_id: int | None = None) -> Optional[Payment]:
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                if company_id is not None:
                    cur.execute(
                        """
                        SELECT p.id, p.buy_order, p.amount_minor, p.currency, p.provider, p.status, p.authorization_code,
                               p.token, p.redirect_url, p.return_url,
                               p.success_url, p.failure_url, p.cancel_url, p.company_id,
                               p.payment_type, p.commerce_id, p.product_id, p.product_name,
                               p.created_at, p.provider_metadata, p.context,
                               pc.notifica, pc.contrato, pc.cuotas, pc.tipo_pago
                          FROM payment p
                          LEFT JOIN payment_contract pc ON pc.payment_id = p.id
                         WHERE idempotency_key = %s AND company_id = %s
                         ORDER BY created_at DESC
                         LIMIT 1
                        """,
                        (idempotency_key, company_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT p.id, p.buy_order, p.amount_minor, p.currency, p.provider, p.status, p.authorization_code,
                               p.token, p.redirect_url, p.return_url,
                               p.success_url, p.failure_url, p.cancel_url, p.company_id,
                               p.payment_type, p.commerce_id, p.product_id, p.product_name,
                               p.created_at, p.provider_metadata, p.context,
                               pc.notifica, pc.contrato, pc.cuotas, pc.tipo_pago
                          FROM payment p
                          LEFT JOIN payment_contract pc ON pc.payment_id = p.id
                         WHERE idempotency_key = %s
                         ORDER BY created_at DESC
                         LIMIT 1
                        """,
                        (idempotency_key,),
                    )
                row = cur.fetchone()
                if not row:
                    return None
                (
                    pid,
                    buy_order,
                    amount_minor,
                    currency,
                    provider,
                    status,
                    authorization_code,
                    tok,
                    redirect_url,
                    return_url,
                    success_url,
                    failure_url,
                    cancel_url,
                    comp_id,
                    payment_type,
                    commerce_id,
                    product_id,
                    product_name,
                    created_at,
                    provider_metadata,
                    context,
                    notifica,
                    contrato,
                    cuotas,
                    tipo_pago,
                ) = row
                payment = self._hydrate_payment(
                    pid=int(pid),
                    buy_order=str(buy_order),
                    amount_minor=amount_minor,
                    currency=str(currency),
                    provider=str(provider) if provider else '',
                    status=str(status),
                    authorization_code=authorization_code,
                    token=str(tok) if tok else None,
                    company_id=int(comp_id) if comp_id is not None else None,
                    payment_type=str(payment_type) if payment_type else None,
                    commerce_id=str(commerce_id) if commerce_id else None,
                    product_id=str(product_id) if product_id else None,
                    product_name=str(product_name) if product_name else None,
                    created_at=created_at,
                    provider_metadata=provider_metadata,
                    success_url=success_url,
                    failure_url=failure_url,
                    cancel_url=cancel_url,
                    return_url=return_url,
                    notifica=notifica,
                    contrato=contrato,
                    cuotas=cuotas,
                    tipo_pago=tipo_pago,
                )
                payment.redirect_url = redirect_url
                payment.return_url = return_url
                if context:
                    try:
                        payment.context = dict(context)
                    except Exception:  # noqa: BLE001
                        payment.context = context
                return payment

    def update_provider_metadata(self, *, provider: str, token: str, metadata: dict[str, Any]) -> None:
        if not metadata:
            return
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE payment
                       SET provider_metadata = COALESCE(provider_metadata, '{}'::jsonb) || %s::jsonb,
                           updated_at = NOW()
                     WHERE provider = %s AND token = %s
                    """,
                    (Json(metadata), provider, token),
                )

    def get_token_by_payment_intent(self, payment_intent_id: str) -> Optional[str]:
        if not payment_intent_id:
            return None
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT token
                      FROM payment
                     WHERE provider = 'stripe'
                       AND provider_metadata ->> 'payment_intent_id' = %s
                     ORDER BY created_at DESC
                     LIMIT 1
                    """,
                    (payment_intent_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row and row[0] else None

    def get_token_by_paypal_capture(self, capture_id: str) -> Optional[str]:
        if not capture_id:
            return None
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT token
                      FROM payment
                     WHERE provider = 'paypal'
                       AND provider_metadata ->> 'paypal_capture_id' = %s
                     ORDER BY created_at DESC
                     LIMIT 1
                    """,
                    (capture_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row and row[0] else None

    def get_latest_token_by_buy_order(self, buy_order: str, company_id: int | None = None) -> Optional[str]:
        if not buy_order:
            return None
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                if company_id is not None:
                    cur.execute(
                        """
                        SELECT token
                          FROM payment
                         WHERE provider = 'stripe'
                           AND buy_order = %s
                           AND company_id = %s
                         ORDER BY created_at DESC
                         LIMIT 1
                        """,
                        (buy_order, company_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT token
                          FROM payment
                         WHERE provider = 'stripe'
                           AND buy_order = %s
                         ORDER BY created_at DESC
                         LIMIT 1
                        """,
                        (buy_order,),
                    )
                row = cur.fetchone()
                return str(row[0]) if row and row[0] else None

    def list_pending(self) -> list[Payment]:
        items: list[Payment] = []
        with get_conn() as conn:
            if conn is None:
                return items
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, p.buy_order, p.amount_minor, p.currency, p.provider, p.status, p.authorization_code,
                           p.token, p.company_id,
                           p.payment_type, p.commerce_id, p.product_id, p.product_name, p.created_at, p.provider_metadata,
                           pc.notifica, pc.contrato, pc.cuotas, pc.tipo_pago
                      FROM payment p
                      LEFT JOIN payment_contract pc ON pc.payment_id = p.id
                     WHERE p.status = 'PENDING'
                     ORDER BY p.created_at DESC
                     LIMIT 200
                    """,
                )
                for row in cur.fetchall() or []:
                    (
                        pid,
                        buy_order,
                        amount_minor,
                        currency,
                        provider,
                        status,
                        authorization_code,
                        tok,
                        company_id,
                        payment_type,
                        commerce_id,
                        product_id,
                        product_name,
                        created_at,
                        provider_metadata,
                        notifica,
                        contrato,
                        cuotas,
                        tipo_pago,
                    ) = row
                    payment = self._hydrate_payment(
                        pid=int(pid),
                        buy_order=str(buy_order),
                        amount_minor=amount_minor,
                        currency=str(currency),
                        provider=str(provider) if provider else '',
                        status=str(status),
                        authorization_code=authorization_code,
                        token=str(tok) if tok else None,
                        company_id=int(company_id) if company_id is not None else None,
                        payment_type=str(payment_type) if payment_type else None,
                        commerce_id=str(commerce_id) if commerce_id else None,
                        product_id=str(product_id) if product_id else None,
                        product_name=str(product_name) if product_name else None,
                        created_at=created_at,
                        provider_metadata=provider_metadata,
                        notifica=notifica,
                        contrato=contrato,
                        cuotas=cuotas,
                        tipo_pago=tipo_pago,
                    )
                    items.append(payment)
        return items

    def list_all(self) -> list[Payment]:
        items: list[Payment] = []
        with get_conn() as conn:
            if conn is None:
                return items
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, p.buy_order, p.amount_minor, p.currency, p.provider, p.status, p.authorization_code,
                           p.token, p.company_id,
                           p.payment_type, p.commerce_id, p.product_id, p.product_name, p.created_at, p.provider_metadata,
                           pc.notifica, pc.contrato, pc.cuotas, pc.tipo_pago
                      FROM payment p
                      LEFT JOIN payment_contract pc ON pc.payment_id = p.id
                     ORDER BY p.created_at DESC
                     LIMIT 200
                    """,
                )
                for row in cur.fetchall() or []:
                    (
                        pid,
                        buy_order,
                        amount_minor,
                        currency,
                        provider,
                        status,
                        authorization_code,
                        tok,
                        company_id,
                        payment_type,
                        commerce_id,
                        product_id,
                        product_name,
                        created_at,
                        provider_metadata,
                        notifica,
                        contrato,
                        cuotas,
                        tipo_pago,
                    ) = row
                    payment = self._hydrate_payment(
                        pid=int(pid),
                        buy_order=str(buy_order),
                        amount_minor=amount_minor,
                        currency=str(currency),
                        provider=str(provider) if provider else '',
                        status=str(status),
                        authorization_code=authorization_code,
                        token=str(tok) if tok else None,
                        company_id=int(company_id) if company_id is not None else None,
                        payment_type=str(payment_type) if payment_type else None,
                        commerce_id=str(commerce_id) if commerce_id else None,
                        product_id=str(product_id) if product_id else None,
                        product_name=str(product_name) if product_name else None,
                        created_at=created_at,
                        provider_metadata=provider_metadata,
                        notifica=notifica,
                        contrato=contrato,
                        cuotas=cuotas,
                        tipo_pago=tipo_pago,
                    )
                    items.append(payment)
        return items

    def list_filtered(
        self,
        *,
        provider: str | None = None,
        status: PaymentStatus | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        token: str | None = None,
        limit: int = 200,
    ) -> list[Payment]:
        items: list[Payment] = []
        with get_conn() as conn:
            if conn is None:
                return items
            with conn.cursor() as cur:
                base = (
                    """
                    SELECT p.id, p.buy_order, p.amount_minor, p.currency, p.provider, p.status, p.authorization_code,
                           p.token, p.company_id,
                           p.payment_type, p.commerce_id, p.product_id, p.product_name, p.created_at, p.provider_metadata,
                           pc.notifica, pc.contrato, pc.cuotas, pc.tipo_pago
                      FROM payment p
                      LEFT JOIN payment_contract pc ON pc.payment_id = p.id
                    """
                )
                conditions: list[str] = []
                params: list[Any] = []
                if provider:
                    conditions.append("p.provider = %s")
                    params.append(provider)
                if status:
                    conditions.append("p.status = %s")
                    params.append(status.value)
                if start:
                    conditions.append("p.created_at >= %s")
                    params.append(start)
                if end:
                    conditions.append("p.created_at <= %s")
                    params.append(end)
                if token:
                    conditions.append("p.token = %s")
                    params.append(token)
                if conditions:
                    base += " WHERE " + " AND ".join(conditions)
                base += " ORDER BY p.created_at DESC"
                if limit:
                    base += " LIMIT %s"
                    params.append(limit)
                cur.execute(base, tuple(params))
                for row in cur.fetchall() or []:
                    (
                        pid,
                        buy_order,
                        amount_minor,
                        currency,
                        provider_value,
                        status_value,
                        authorization_code,
                        tok,
                        company_id,
                        payment_type,
                        commerce_id,
                        product_id,
                        product_name,
                        created_at,
                        provider_metadata,
                        notifica,
                        contrato,
                        cuotas,
                        tipo_pago,
                    ) = row
                    payment = self._hydrate_payment(
                        pid=int(pid),
                        buy_order=str(buy_order),
                        amount_minor=amount_minor,
                        currency=str(currency),
                        provider=str(provider_value) if provider_value else '',
                        status=str(status_value),
                        authorization_code=authorization_code,
                        token=str(tok) if tok else None,
                        company_id=int(company_id) if company_id is not None else None,
                        payment_type=str(payment_type) if payment_type else None,
                        commerce_id=str(commerce_id) if commerce_id else None,
                        product_id=str(product_id) if product_id else None,
                        product_name=str(product_name) if product_name else None,
                        created_at=created_at,
                        provider_metadata=provider_metadata,
                        notifica=notifica,
                        contrato=contrato,
                        cuotas=cuotas,
                        tipo_pago=tipo_pago,
                    )
                    items.append(payment)
        return items


    def update_status_by_token(self, *, provider: str, token: str, to_status: PaymentStatus,
                               response_code: int | None = None, reason: str | None = None,
                               authorization_code: str | None = None) -> None:
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE payment
                       SET status = %s,
                           response_code = COALESCE(%s, response_code),
                           status_reason = COALESCE(%s, status_reason),
                           authorization_code = COALESCE(%s, authorization_code),
                           first_authorized_at = CASE WHEN %s = 'AUTHORIZED' AND first_authorized_at IS NULL THEN NOW() ELSE first_authorized_at END,
                           failed_at = CASE WHEN %s = 'FAILED' THEN NOW() ELSE failed_at END,
                           canceled_at = CASE WHEN %s = 'CANCELED' THEN NOW() ELSE canceled_at END,
                           refunded_at = CASE WHEN %s = 'REFUNDED' THEN NOW() ELSE refunded_at END,
                           updated_at = NOW()
                     WHERE provider = %s AND token = %s
                     RETURNING payment_order_id
                    """,
                    (
                        to_status.value,
                        response_code,
                        reason,
                        authorization_code,
                        to_status.value,
                        to_status.value,
                        to_status.value,
                        to_status.value,
                        provider,
                        token,
                    ),
                )
                row = cur.fetchone()
                if row and to_status in {PaymentStatus.AUTHORIZED, PaymentStatus.REFUNDED}:
                    order_id = row[0]
                    if order_id:
                        cur.execute(
                            "UPDATE payment_order SET status='COMPLETED', updated_at=NOW() WHERE id=%s",
                            (order_id,),
                        )

    def log_provider_event(
        self,
        *,
        provider: str,
        operation: str,
        direction: str,
        request_url: str | None = None,
        token: str | None = None,
        response_status: int | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
        request_headers: dict[str, Any] | None = None,
        request_body: dict[str, Any] | None = None,
        response_headers: dict[str, Any] | None = None,
        response_body: dict[str, Any] | None = None,
        ) -> None:
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                pid = None
                if token:
                    cur.execute("SELECT id FROM payment WHERE token=%s LIMIT 1", (token,))
                    r = cur.fetchone()
                    if r:
                        pid = r[0]
                cur.execute(
                    """
                    INSERT INTO provider_event_log (
                        payment_id, provider, direction, operation, request_url,
                        request_headers, request_body,
                        response_status, response_headers, response_body,
                        error_message, latency_ms, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, NOW()
                    )
                    """,
                    (
                        pid,
                        provider,
                        direction,
                        operation,
                        request_url,
                        Json(request_headers or {}),
                        Json(request_body or {}),
                        response_status,
                        Json(response_headers or {}),
                        Json(response_body or {}),
                        error_message,
                        latency_ms,
                    ),
                )

    def record_webhook(
        self,
        *,
        provider: str,
        event_id: str | None,
        event_type: str | None,
        verification_status: str = "UNKNOWN",
        headers: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        related_token: str | None = None,
    ) -> None:
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                related_payment_id = None
                if related_token:
                    cur.execute("SELECT id FROM payment WHERE token=%s LIMIT 1", (related_token,))
                    r = cur.fetchone()
                    if r:
                        related_payment_id = r[0]
                cur.execute(
                    """
                    INSERT INTO webhook_inbox (
                        provider, event_id, event_type, verification_status, headers, payload,
                        related_payment_id, received_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, NOW()
                    ) ON CONFLICT (provider, event_id) DO NOTHING
                    """,
                    (
                        provider,
                        event_id,
                        event_type,
                        verification_status,
                        Json(headers or {}),
                        Json(payload or {}),
                        related_payment_id,
                    ),
                )

    def record_refund(
        self,
        *,
        token: str,
        provider: str,
        amount: Decimal | None,
        status: str,
        provider_refund_id: str | None = None,
        payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        if amount is None:
            return
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM payment WHERE token=%s LIMIT 1", (token,))
                row = cur.fetchone()
                if not row:
                    return
                payment_id = row[0]
                amount_value = self._normalize_amount(amount)
                if amount_value is None or amount_value <= Decimal("0.00"):
                    return
                status_value = (status or "REQUESTED").upper()
                confirmed_at = None
                if status_value in {"SUCCEEDED", "COMPLETED"}:
                    confirmed_at = datetime.now(timezone.utc)
                cur.execute(
                    """
                    INSERT INTO refund (
                        payment_id, provider, amount_minor, status,
                        provider_refund_id, reason, payload, confirmed_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        payment_id,
                        provider,
                        amount_value,
                        status_value,
                        provider_refund_id,
                        reason,
                        Json(payload or {}),
                        confirmed_at,
                    ),
                )

    def record_dispute(
        self,
        *,
        token: str,
        provider: str,
        provider_dispute_id: str | None,
        status: str | None = None,
        amount: Decimal | None = None,
        reason: str | None = None,
        opened_at: datetime | None = None,
        closed_at: datetime | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM payment WHERE token=%s LIMIT 1", (token,))
                row = cur.fetchone()
                if not row:
                    return
                payment_id = row[0]
                amount_minor = self._normalize_amount(amount)
                status_value = status.upper() if status else None
                cur.execute(
                    """
                    INSERT INTO dispute (
                        payment_id, provider, provider_dispute_id, status,
                        amount_minor, reason, opened_at, closed_at, payload
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (provider, provider_dispute_id) DO UPDATE
                        SET payment_id = EXCLUDED.payment_id,
                            status = COALESCE(EXCLUDED.status, dispute.status),
                            amount_minor = COALESCE(EXCLUDED.amount_minor, dispute.amount_minor),
                            reason = COALESCE(EXCLUDED.reason, dispute.reason),
                            opened_at = COALESCE(EXCLUDED.opened_at, dispute.opened_at),
                            closed_at = COALESCE(EXCLUDED.closed_at, dispute.closed_at),
                            payload = COALESCE(EXCLUDED.payload, dispute.payload),
                            updated_at = NOW()
                    """,
                    (
                        payment_id,
                        provider,
                        provider_dispute_id,
                        status_value,
                        amount_minor,
                        reason,
                        opened_at,
                        closed_at,
                        Json(payload or {}),
                    ),
                )
