from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings


_basic_scheme = HTTPBasic()


def require_basic_auth(credentials: HTTPBasicCredentials = Depends(_basic_scheme)) -> None:
    """Validate credentials using HTTP Basic authentication."""

    username_valid = secrets.compare_digest(credentials.username or "", settings.api_basic_username)
    password_valid = secrets.compare_digest(credentials.password or "", settings.api_basic_password)
    if not (username_valid and password_valid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )



def verify_bearer_token(authorization: str | None = Header(None)) -> None:
    """Validate Bearer token matches configured API token."""

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    if not token or not secrets.compare_digest(token, settings.api_bearer_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
