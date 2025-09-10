from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient
from transbank.webpay.webpay_plus.transaction import Transaction  # type: ignore[import-untyped]

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from app.main import app
from app.config import settings


@pytest.fixture(autouse=True)
def mock_status(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock SDK status to return AUTHORIZED
    def fake_status(self, token: str) -> dict[str, object]:  # type: ignore[override]
        return {"status": "AUTHORIZED", "response_code": 0, "buy_order": "order_st"}

    monkeypatch.setattr(Transaction, "status", fake_status)


def test_tbk_status_authorized() -> None:
    client = TestClient(app)

    # Crear pago para registrar token en store
    payload = {
        "buy_order": "order_st",
        "amount": 1200,
        "currency": "CLP",
        "return_url": "http://example.com/return",
    }
    headers = {"Authorization": f"Bearer {settings.api_bearer_token}"}
    resp = client.post("/api/payments", json=payload, headers=headers)
    assert resp.status_code == 200
    token = resp.json()["redirect"]["token"]

    # Consultar status (read-only) -> AUTHORIZED por mapeo del SDK
    status_body = {"tokens": [token]}
    stat = client.post("/api/payments/status", json=status_body, headers=headers)
    assert stat.status_code == 200
    assert stat.json()["results"][token] == "AUTHORIZED"

