from __future__ import annotations

from fastapi import FastAPI

from app.logging import setup_logging
from app.routes import health, payments

setup_logging()

app = FastAPI()
app.include_router(health.router)
app.include_router(payments.router)
