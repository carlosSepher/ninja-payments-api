from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration read from environment variables."""

    api_bearer_token: str = "testtoken"
    tbk_api_key_id: str = "597055555532"
    tbk_api_key_secret: str = "597055555532"
    tbk_host: str = "https://webpay3gint.transbank.cl"
    tbk_api_base: str = "/rswebpaytransaction/api/webpay/v1.2"
    provider: str = "transbank"
    return_url: str = "http://localhost:8000/api/payments/tbk/return"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
