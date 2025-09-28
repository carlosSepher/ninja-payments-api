from __future__ import annotations

import os
import platform
from datetime import datetime, timezone
from typing import Any

import logging

from fastapi import APIRouter

from app.config import settings
from app.db.client import get_conn
from app.domain.statuses import PaymentStatus

router = APIRouter()
logger = logging.getLogger(__name__)

SERVICE_STARTED_AT = datetime.now(timezone.utc)


@router.get("/health")
async def health() -> dict[str, str]:
    """Simple health check endpoint for load balancers."""
    return {"status": "ok"}


def _collect_payment_metrics() -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "connected": False,
        "status_counts": {},
        "status_counts_display": {},
        "pending_by_provider": {},
        "last_24h": {"count": 0, "amount_minor": 0},
    }
    try:
        with get_conn() as conn:
            if conn is None:
                return metrics
            metrics["connected"] = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, COUNT(*)
                      FROM payment
                     GROUP BY status
                    """,
                )
                for status_value, count in cur.fetchall() or []:
                    status_key = str(status_value)
                    metrics["status_counts"][status_key] = int(count)
                    try:
                        status_enum = PaymentStatus(status_key)
                        metrics["status_counts_display"][status_enum.display_name] = int(count)
                    except ValueError:
                        metrics["status_counts_display"][status_key] = int(count)

                cur.execute(
                    """
                    SELECT provider, COUNT(*)
                      FROM payment
                     WHERE status = 'PENDING'
                     GROUP BY provider
                    """,
                )
                metrics["pending_by_provider"] = {
                    str(provider): int(count)
                    for provider, count in (cur.fetchall() or [])
                }

                cur.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(amount_minor), 0)
                      FROM payment
                     WHERE created_at >= NOW() - INTERVAL '1 day'
                    """,
                )
                row = cur.fetchone()
                if row:
                    metrics["last_24h"] = {
                        "count": int(row[0] or 0),
                        "amount_minor": int(row[1] or 0),
                    }
    except Exception as exc:  # noqa: BLE001
        logger.info("health metrics collection failed", extra={"error": str(exc)})
    return metrics


@router.get("/health/metrics")
async def health_metrics() -> dict[str, Any]:
    """Detailed service health endpoint with lightweight operational metrics."""

    captured_at = datetime.now(timezone.utc)
    raw_metrics = _collect_payment_metrics()
    db_connected = bool(raw_metrics.pop("connected", False))
    uptime_seconds = int((captured_at - SERVICE_STARTED_AT).total_seconds())
    status = "ok" if db_connected else "degraded"

    return {
        "status": status,
        "timestamp": captured_at.isoformat(),
        "uptime_seconds": uptime_seconds,
        "service": {
            "default_provider": settings.provider,
            "environment": getattr(settings, "app_env", "local"),
            "version": getattr(settings, "app_version", None),
            "host": platform.node(),
            "pid": os.getpid(),
        },
        "database": {
            "connected": db_connected,
            "schema": settings.db_schema or None,
        },
        "payments": raw_metrics,
    }
