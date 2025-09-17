from __future__ import annotations

import pathlib
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from transbank.webpay.webpay_plus.transaction import Transaction  # type: ignore[import-untyped]

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from app.main import app
from app.config import settings


@pytest.fixture(autouse=True)
def mock_external(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock commit via SDK to avoid network
    def fake_commit(self, token: str) -> dict[str, int | str]:  # type: ignore[override]
        return {"buy_order": "order1", "response_code": 0}

    monkeypatch.setattr(Transaction, "commit", fake_commit)

    # Mock HTTPX POST for both create and refund endpoints
    async def fake_post(self, url, headers=None, json=None):  # type: ignore[override]
        class Response:
            def __init__(self, payload: dict[str, object]):
                self._payload = payload
                self.status_code = 200
                self.headers: dict[str, str] = {}
                self.text = ""

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return self._payload

        if str(url).endswith("/transactions"):
            return Response({"url": "https://tbk/pay", "token": "tok_refund_1"})
        if "/refunds" in str(url):
            # Simulate successful refund
            return Response({
                "type": "REVERSED",
                "authorization_code": "abc123",
                "response_code": 0,
            })
        return Response({})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def test_webpay_refund_success() -> None:
    client = TestClient(app)

    # Create payment (Webpay is default provider)
    create_payload = {
        "buy_order": "order1",
        "amount": 2500,
        "currency": "CLP",
        "return_url": "http://example.com/return",
    }
    headers = {"Authorization": f"Bearer {settings.api_bearer_token}"}
    response = client.post("/api/payments", json=create_payload, headers=headers)
    assert response.status_code == 200
    token = response.json()["redirect"]["token"]

    # Request refund without amount (defaults to full amount for TBK)
    refund_payload = {"token": token}
    refund_resp = client.post("/api/payments/refund", json=refund_payload, headers=headers)
    assert refund_resp.status_code == 200
    assert refund_resp.json()["status"] == "REFUNDED"
