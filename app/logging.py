from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, Iterable


class JsonFormatter(logging.Formatter):
    """Format log records as JSON."""

    def _coerce(self, value: Any) -> Any:
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, dict):
            return {str(k): self._coerce(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._coerce(item) for item in value]
        return value

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        data: Dict[str, Any] = {
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        # Whitelisted extra fields to enrich logs
        extra_fields = (
            "buy_order",
            "token",
            "response_code",
            "status",
            "authorization_code",
            "idempotency_key",
            "endpoint",
            "method",
            "redirect_to",
            "event",
            "currency",
            "amount",
            "provider",
        )
        for field in extra_fields:
            if hasattr(record, field):
                data[field] = self._coerce(getattr(record, field))
        return json.dumps(data)


def setup_logging() -> None:
    """Configure root logger to use JSON formatter."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
