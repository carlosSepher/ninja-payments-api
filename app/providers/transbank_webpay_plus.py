from __future__ import annotations

import asyncio
import logging
from typing import Tuple

import httpx
from transbank.common.integration_type import IntegrationType  # type: ignore[import-untyped]
from transbank.common.options import WebpayOptions  # type: ignore[import-untyped]
from transbank.webpay.webpay_plus.transaction import Transaction  # type: ignore[import-untyped]

from app.config import Settings
from app.domain.models import Payment

from .base import PaymentProvider
from app.domain.statuses import PaymentStatus

logger = logging.getLogger(__name__)


class TransbankWebpayPlusProvider(PaymentProvider):
    """Transbank Webpay Plus implementation."""

    def __init__(self, settings: Settings):
        self.settings = settings
        options = WebpayOptions(
            settings.tbk_api_key_id, settings.tbk_api_key_secret, IntegrationType.TEST
        )
        self.transaction = Transaction(options)

    async def create(self, payment: Payment, return_url: str) -> Tuple[str, str]:
        url = f"{self.settings.tbk_host}{self.settings.tbk_api_base}/transactions"
        headers = {
            "Tbk-Api-Key-Id": self.settings.tbk_api_key_id,
            "Tbk-Api-Key-Secret": self.settings.tbk_api_key_secret,
            "Content-Type": "application/json",
        }
        payload = {
            "buy_order": payment.buy_order,
            "session_id": payment.id,
            "amount": payment.amount,
            "return_url": return_url,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        data = resp.json()
        token = data["token"]
        redirect_url = data["url"]
        logger.info(
            "transaction created",
            extra={"buy_order": payment.buy_order, "token": token},
        )
        return redirect_url, token

    async def commit(self, token: str) -> int:
        result = await asyncio.to_thread(self.transaction.commit, token)
        response_code = result.get("response_code", -1)
        logger.info(
            "transaction committed",
            extra={
                "buy_order": result.get("buy_order"),
                "token": token,
                "response_code": response_code,
            },
        )
        return response_code

    async def status(self, token: str) -> PaymentStatus | None:
        # Webpay (in this simplified flow) has no read-only status endpoint.
        # To keep status checks side-effect free, return None here and leave
        # finalization to the explicit refresh/commit flows.
        return None

    async def refund(self, token: str, amount: int | None = None) -> bool:
        """Not implemented for this demo provider.

        Webpay Plus refunds/nullifications require additional flows not covered
        here. Return False to indicate no refund was executed.
        """
        logger.info(
            "webpay refund not supported",
            extra={"token": token, "response_code": -1},
        )
        return False
