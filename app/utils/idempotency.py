from __future__ import annotations

from typing import Optional

from fastapi import Header


async def get_idempotency_key(idempotency_key: Optional[str] = Header(default=None)) -> Optional[str]:
    """Return the provided Idempotency-Key header if any."""
    return idempotency_key
