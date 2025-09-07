from __future__ import annotations

import logging
from typing import Tuple

import httpx

from app.config import Settings
from app.domain.models import Payment

from .base import PaymentProvider

logger = logging.getLogger(__name__)


class PayPalCheckoutProvider(PaymentProvider):
    """PayPal Checkout implementation using Orders v2.

    - create(): creates an order (intent CAPTURE) and returns (approve_url, order_id)
    - commit(): captures the order and returns 0 if COMPLETED, else -1
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = getattr(settings, "paypal_base_url", "https://api-m.sandbox.paypal.com")
        self.client_id = getattr(settings, "paypal_client_id", "")
        self.client_secret = getattr(settings, "paypal_client_secret", "")
        if not self.client_id or not self.client_secret:
            raise ValueError("PayPal credentials not configured")

    async def _get_access_token(self) -> str:
        token_url = f"{self.base_url}/v1/oauth2/token"
        auth = (self.client_id, self.client_secret)
        data = {"grant_type": "client_credentials"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data=data, auth=auth)
            resp.raise_for_status()
            payload = resp.json()
            return str(payload["access_token"])  # type: ignore[index]

    async def create(self, payment: Payment, return_url: str) -> Tuple[str, str]:
        access_token = await self._get_access_token()

        cancel_url = payment.cancel_url or return_url
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": payment.buy_order,
                    "amount": {
                        "currency_code": payment.currency.value,
                        "value": str(payment.amount),
                    },
                }
            ],
            "application_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "PAY_NOW",
            },
        }

        orders_url = f"{self.base_url}/v2/checkout/orders"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(orders_url, headers=headers, json=payload)
            if resp.status_code >= 400:
                # Try to extract detailed error from PayPal
                detail = None
                try:
                    j = resp.json()
                    detail = j.get("details") or j.get("message") or j
                except Exception:
                    detail = resp.text
                logger.info(
                    "paypal order create failed",
                    extra={"response_code": resp.status_code, "token": ""},
                )
                raise ValueError(f"PayPal create error: {detail}")
            data = resp.json()
        order_id = str(data["id"])  # type: ignore[index]
        approve_url = next(
            (link["href"] for link in data.get("links", []) if link.get("rel") == "approve"),
            None,
        )
        if not approve_url:
            raise RuntimeError("PayPal approve URL not found")
        logger.info("paypal order created", extra={"buy_order": payment.buy_order, "token": order_id})
        return approve_url, order_id

    async def commit(self, token: str) -> int:
        """Capture a PayPal order by ID (token)."""
        access_token = await self._get_access_token()
        capture_url = f"{self.base_url}/v2/checkout/orders/{token}/capture"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient() as client:
            resp = await client.post(capture_url, headers=headers, json={})
            if resp.status_code >= 400:
                # Consider non-2xx as failure
                logger.info("paypal capture failed", extra={"token": token, "response_code": -1})
                return -1
            data = resp.json()
        status = str(data.get("status", ""))
        logger.info("paypal capture status", extra={"token": token, "response_code": 0 if status == "COMPLETED" else -1})
        if status == "COMPLETED":
            return 0
        return -1
