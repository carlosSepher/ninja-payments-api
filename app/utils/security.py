from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import settings


def verify_bearer_token(authorization: str = Header(...)) -> None:
    """Validate Authorization header using a static bearer token."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.api_bearer_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
