from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse

from app.logging import setup_logging
from app.routes import health, payments
from app.utils.security import require_basic_auth

setup_logging()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(payments.router)


@app.get("/openapi.json", include_in_schema=False)
def custom_openapi(_: None = Depends(require_basic_auth)):
    return JSONResponse(content=app.openapi())


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui(_: None = Depends(require_basic_auth)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Ninja Payments API")


@app.get("/redoc", include_in_schema=False)
def custom_redoc(_: None = Depends(require_basic_auth)):
    return get_redoc_html(openapi_url="/openapi.json", title="Ninja Payments API ReDoc")
