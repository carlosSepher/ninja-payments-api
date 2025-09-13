from __future__ import annotations

from typing import Optional

from app.db.client import get_conn
from app.domain.enums import Currency
from app.domain.models import Payment
from app.domain.statuses import PaymentStatus


class PgPaymentStore:
    """PostgreSQL-backed store for payments using raw psycopg2.

    Mirrors the minimal interface used by the service and routes.
    """

    def save(self, payment: Payment, token: str, idempotency_key: str | None = None) -> None:
        with get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                # Ensure order exists
                cur.execute(
                    """
                    INSERT INTO payment_order (id, buy_order, environment, currency, amount_expected_minor, status, metadata, created_at, updated_at)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s, 'OPEN', '{}'::jsonb, NOW(), NOW())
                    ON CONFLICT (buy_order) DO UPDATE SET updated_at = EXCLUDED.updated_at
                    RETURNING id
                    """,
                    (payment.buy_order, 'test', payment.currency.value, payment.amount),
                )
                row = cur.fetchone()
                order_id = row[0] if row else None
                # Insert payment attempt
                cur.execute(
                    """
                    INSERT INTO payment (
                        id, payment_order_id, buy_order, amount_minor, currency, provider, environment,
                        status, token, redirect_url, return_url, success_url, failure_url, cancel_url,
                        idempotency_key, provider_metadata, context, created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, '{}'::jsonb, '{}'::jsonb, NOW(), NOW()
                    )
                    """,
                    (
                        order_id,
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

    def get_by_token(self, token: str) -> Optional[Payment]:
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT buy_order, amount_minor, currency, provider, status, token, redirect_url,
                           success_url, failure_url, cancel_url
                      FROM payment
                     WHERE token = %s
                     LIMIT 1
                    """,
                    (token,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                buy_order, amount_minor, currency, provider, status, tok, redirect_url, success_url, failure_url, cancel_url = row
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
                p.token = str(tok)
                p.redirect_url = redirect_url
                return p

    def get_by_idempotency(self, idempotency_key: str) -> Optional[Payment]:
        with get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT buy_order, amount_minor, currency, provider, status, token, redirect_url,
                           success_url, failure_url, cancel_url
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
                buy_order, amount_minor, currency, provider, status, tok, redirect_url, success_url, failure_url, cancel_url = row
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
                p.token = str(tok)
                p.redirect_url = redirect_url
                return p

    def list_pending(self) -> list[Payment]:
        items: list[Payment] = []
        with get_conn() as conn:
            if conn is None:
                return items
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, buy_order, amount_minor, currency, provider, status, token
                      FROM payment
                     WHERE status = 'PENDING'
                     ORDER BY created_at DESC
                     LIMIT 200
                    """,
                )
                for row in cur.fetchall() or []:
                    pid, buy_order, amount_minor, currency, provider, status, tok = row
                    p = Payment(
                        buy_order=str(buy_order),
                        amount=int(amount_minor),
                        currency=Currency(str(currency)),
                        provider=str(provider) if provider else None,
                    )
                    p.id = str(pid)
                    p.status = PaymentStatus(str(status))
                    p.token = str(tok) if tok else None
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
