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
        """Read-only status using Webpay SDK.

        Maps TBK status strings to our PaymentStatus without side effects.
        """
        try:
            result = await asyncio.to_thread(self.transaction.status, token)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "webpay status error",
                extra={"token": token, "event": str(exc)},
            )
            return None

        tbk_status = str(result.get("status", "") or "").upper()
        response_code = result.get("response_code", None)

        mapping: dict[str, PaymentStatus] = {
            "AUTHORIZED": PaymentStatus.AUTHORIZED,
            "FAILED": PaymentStatus.FAILED,
            # When a transaction is reversed/nullified, treat as REFUNDED
            "REVERSED": PaymentStatus.REFUNDED,
            "NULLIFIED": PaymentStatus.REFUNDED,
            # Transactions still in progress
            "INITIALIZED": PaymentStatus.PENDING,
        }
        mapped = mapping.get(tbk_status)
        logger.info(
            "webpay status read",
            extra={
                "token": token,
                "response_code": (response_code if response_code is not None else -1),
                "status": (mapped.value if mapped else ""),
            },
        )
        return mapped

    async def refund(self, token: str, amount: int | None = None) -> bool:
        """Issue a refund/nullification for Webpay Plus.

        Uses REST endpoint: POST /transactions/{token}/refunds with JSON { amount }.
        Consider success when response contains response_code == 0 or a
        refund type of REVERSED/NULLIFIED.
        """
        if amount is None or amount <= 0:
            # Service is expected to default amount; keep provider safe.
            logger.info(
                "refund amount invalid",
                extra={"token": token, "amount": amount, "response_code": -1},
            )
            return False

        url = f"{self.settings.tbk_host}{self.settings.tbk_api_base}/transactions/{token}/refunds"
        headers = {
            "Tbk-Api-Key-Id": self.settings.tbk_api_key_id,
            "Tbk-Api-Key-Secret": self.settings.tbk_api_key_secret,
            "Content-Type": "application/json",
        }
        payload = {"amount": int(amount)}
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        data = resp.json()
        response_code = data.get("response_code")
        refund_type = data.get("type")
        ok = (response_code == 0) or (refund_type in {"REVERSED", "NULLIFIED"})
        logger.info(
            "webpay refund executed",
            extra={
                "token": token,
                "amount": amount,
                "response_code": (response_code if response_code is not None else -1),
                "refund_type": str(refund_type or ""),
                "accepted": ok,
            },
        )
        return bool(ok)
