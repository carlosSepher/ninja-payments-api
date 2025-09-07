from __future__ import annotations

import pathlib
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from transbank.webpay.webpay_plus.transaction import Transaction  # type: ignore[import-untyped]

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from app.main import app


@pytest.fixture(autouse=True)
def mock_external(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(self, url, headers=None, json=None):  # type: ignore[override]
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, str]:
                return {"url": "https://tbk/pay", "token": "tok123"}

        return Response()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    def fake_commit(self, token: str) -> dict[str, int | str]:  # type: ignore[override]
        return {"buy_order": "order1", "response_code": 0}

    monkeypatch.setattr(Transaction, "commit", fake_commit)


def test_payment_flow() -> None:
    client = TestClient(app)
    payload = {
        "buy_order": "order1",
        "amount": 1000,
        "currency": "CLP",
        "return_url": "http://example.com/return",
    }
    headers = {"Authorization": "Bearer testtoken", "Idempotency-Key": "123"}
    response = client.post("/api/payments", json=payload, headers=headers)
    assert response.status_code == 200
    token = response.json()["redirect"]["token"]

    return_resp = client.get("/api/payments/tbk/return", params={"token_ws": token})
    assert return_resp.status_code == 200
    assert return_resp.json()["status"] == "AUTHORIZED"
