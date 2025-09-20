from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Any

from app.db.client import get_conn
from app.domain.enums import Currency
from app.domain.models import Payment
from app.domain.statuses import PaymentStatus
from psycopg2.extras import Json


class PgPaymentStore:
    """PostgreSQL-backed store for payments using raw psycopg2.

    Mirrors the minimal interface used by the service and routes.
    """

    def save(self, payment: Payment, token: str, idempotency_key: str | None = None) -> None:
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                if payment.company_id is None:
                    raise ValueError("payment.company_id required")
                # Ensure order exists (per company)
                cur.execute(
                    """
                    INSERT INTO payment_order (buy_order, company_id, environment, currency, amount_expected_minor, status, metadata, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'OPEN', '{}'::jsonb, NOW(), NOW())
                    ON CONFLICT (company_id, buy_order) DO UPDATE
                        SET currency = EXCLUDED.currency,
                            amount_expected_minor = EXCLUDED.amount_expected_minor,
                            updated_at = NOW()
                    RETURNING id
                    """,
                    (payment.buy_order, payment.company_id, 'test', payment.currency.value, payment.amount),
                )
                row = cur.fetchone()
                order_id = row[0] if row else None
                # Insert payment attempt
                cur.execute(
                    """
                    INSERT INTO payment (
                        payment_order_id, company_id, buy_order, amount_minor, currency, provider, environment,
                        status, token, redirect_url, return_url, success_url, failure_url, cancel_url,
                        idempotency_key, provider_metadata, context, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, '{}'::jsonb, '{}'::jsonb, NOW(), NOW()
                    )
                    RETURNING id
                    """,
                    (
                        order_id,
                        payment.company_id,
                        payment.buy_order,
                        int(payment.amount),
                        payment.currency.value,
                        (payment.provider or ''),
                        'test',
                        payment.status.value,
                        token,
                        payment.redirect_url,
                        None,  # return_url stored per request (not on model)
                        payment.success_url,
                        payment.failure_url,
                        payment.cancel_url,
                        idempotency_key,
                    ),
                )
                inserted = cur.fetchone()
                if inserted:
                    payment.id = int(inserted[0])

    def get_by_token(self, token: str) -> Optional[Payment]:
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, buy_order, amount_minor, currency, provider, status, token, redirect_url,
                           success_url, failure_url, cancel_url, company_id
                      FROM payment
                     WHERE token = %s
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
                    tok,
                    redirect_url,
                    success_url,
                    failure_url,
                    cancel_url,
                    company_id,
                ) = row
                p = Payment(
                    buy_order=str(buy_order),
                    amount=int(amount_minor),
                    currency=Currency(str(currency)),
                    provider=str(provider) if provider else None,
                    success_url=success_url,
                    failure_url=failure_url,
                    cancel_url=cancel_url,
                )
                p.status = PaymentStatus(str(status))
                p.id = int(pid)
                p.token = str(tok)
                p.redirect_url = redirect_url
                if company_id is not None:
                    p.company_id = int(company_id)
                return p

    def get_by_idempotency(self, idempotency_key: str, company_id: int | None = None) -> Optional[Payment]:
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                if company_id is not None:
                    cur.execute(
                        """
                        SELECT id, buy_order, amount_minor, currency, provider, status, token, redirect_url,
                               success_url, failure_url, cancel_url, company_id
                          FROM payment
                         WHERE idempotency_key = %s AND company_id = %s
                         ORDER BY created_at DESC
                         LIMIT 1
                        """,
                        (idempotency_key, company_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, buy_order, amount_minor, currency, provider, status, token, redirect_url,
                               success_url, failure_url, cancel_url, company_id
                          FROM payment
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
                    tok,
                    redirect_url,
                    success_url,
                    failure_url,
                    cancel_url,
                    comp_id,
                ) = row
                p = Payment(
                    buy_order=str(buy_order),
                    amount=int(amount_minor),
                    currency=Currency(str(currency)),
                    provider=str(provider) if provider else None,
                    success_url=success_url,
                    failure_url=failure_url,
                    cancel_url=cancel_url,
                )
                p.status = PaymentStatus(str(status))
                p.id = int(pid)
                p.token = str(tok)
                p.redirect_url = redirect_url
                if comp_id is not None:
                    p.company_id = int(comp_id)
                return p

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
                    SELECT id, buy_order, amount_minor, currency, provider, status, token, company_id
                      FROM payment
                     WHERE status = 'PENDING'
                     ORDER BY created_at DESC
                     LIMIT 200
                    """,
                )
                for row in cur.fetchall() or []:
                    pid, buy_order, amount_minor, currency, provider, status, tok, company_id = row
                    p = Payment(
                        buy_order=str(buy_order),
                        amount=int(amount_minor),
                        currency=Currency(str(currency)),
                        provider=str(provider) if provider else None,
                    )
                    p.id = int(pid)
                    p.status = PaymentStatus(str(status))
                    p.token = str(tok) if tok else None
                    if company_id is not None:
                        p.company_id = int(company_id)
                    items.append(p)
        return items

    def list_all(self) -> list[Payment]:
        items: list[Payment] = []
        with get_conn() as conn:
            if conn is None:
                return items
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, buy_order, amount_minor, currency, provider, status, token, company_id
                      FROM payment
                     ORDER BY created_at DESC
                     LIMIT 200
                    """,
                )
                for row in cur.fetchall() or []:
                    pid, buy_order, amount_minor, currency, provider, status, tok, company_id = row
                    p = Payment(
                        buy_order=str(buy_order),
                        amount=int(amount_minor),
                        currency=Currency(str(currency)),
                        provider=str(provider) if provider else None,
                    )
                    p.id = int(pid)
                    p.status = PaymentStatus(str(status))
                    p.token = str(tok) if tok else None
                    if company_id is not None:
                        p.company_id = int(company_id)
                    items.append(p)
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
        amount: int | None,
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
                amount_int = int(amount)
                if amount_int <= 0:
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
                        amount_int,
                        status_value,
                        provider_refund_id,
                        reason,
                        Json(payload or {}),
                        confirmed_at,
                    ),
                )
