from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration read from environment variables."""

    # General
    api_bearer_token: str = "testtoken"
    provider: str = "transbank"

    # Transbank config
    tbk_api_key_id: str = "597055555532"
    tbk_api_key_secret: str = "597055555532"
    tbk_host: str = "https://webpay3gint.transbank.cl"
    tbk_api_base: str = "/rswebpaytransaction/api/webpay/v1.2"
    # Default return URL if not provided via env
    return_url: str = "http://localhost:8000/api/payments/tbk/return"

    # Stripe config
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # PayPal config
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""

    # Database (PostgreSQL)
    db_host: str = ""
    db_port: int = 5432
    db_user: str = ""
    db_password: str = ""
    db_name: str = ""
    db_schema: str = "payments"

    @property
    def db_enabled(self) -> bool:
        return bool(self.db_host and self.db_user and self.db_name)

    @property
    def db_dsn(self) -> str:
        if not self.db_enabled:
            return ""
        return f"postgresql+psycopg2://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
    paypal_webhook_id: str = ""  # PayPal Webhook ID for signature verification

    # Pydantic v2 settings config
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",  # allow extra keys in .env like APP_ENV, TBK_RETURN_URL, etc.
    )


settings = Settings()
