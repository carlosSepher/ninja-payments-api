from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)

from app.config import settings


_basic_scheme = HTTPBasic()
_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


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



def verify_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> None:
    """Validate Bearer token matches configured API token."""

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials.strip()
    if not token or not secrets.compare_digest(token, settings.api_bearer_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
