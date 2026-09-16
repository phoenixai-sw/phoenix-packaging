from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from services.api.config import Settings
from services.api.main import create_app
from services.api.models import User
from services.api.tests.auth_helpers import register
from services.api.billing.payments import MockProvider


@pytest.fixture
def billing_app(tmp_path, monkeypatch):
    provider = MockProvider()
    monkeypatch.setenv("PAYMENT_PROVIDER", "mock")
    monkeypatch.setenv("BILLING_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("services.api.billing.routes.build_provider", lambda _: provider)
    app = create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'routes.db'}", storage_dir=tmp_path / "files"))
    return app, provider


def create(client):
    response = client.post("/v1/billing/orders", headers={"Idempotency-Key": "order"}, json={"kind": "subscription", "plan_id": "starter"})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_authenticated_sync_is_scoped_csrf_guarded_and_never_approves(billing_app):
    app, provider = billing_app
    with TestClient(app) as client:
        auth = register(client)
        order = create(client)
        url = "/v1/billing/orders/" + order["order_id"] + "/sync"
        del client.headers["X-CSRF-Token"]
        assert client.post(url).status_code == 403
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        assert client.post(url, headers={"Origin": "https://attacker.example"}).status_code == 403
        assert client.post(url).status_code == 409
        assert all(call[0] == "query" for call in provider.calls)
        provider.confirm("mock", order["order_id"], order["amount"])
        confirmed = client.post(url)
        assert confirmed.status_code == 200, confirmed.text
        data = confirmed.json()["data"]
        assert data["status"] == "paid" and data["payment"]["provider_status"] == "DONE"
        public = client.get("/v1/billing").json()["data"]["orders"][0]
        assert public["payment"]["total_amount"] == order["amount"]
        with TestClient(app) as stranger:
            register(stranger, "stranger@example.com")
            before = len(provider.calls)
            assert stranger.post(url).status_code == 404
            assert len(provider.calls) == before
        # The owner's ordinary editor role must not grant billing permission.
        with app.state.session_factory.begin() as db:
            db.get(User, auth["user"]["id"]).role = "editor"
        assert client.post(url).status_code == 403


def test_confirm_rejects_coercible_amounts_before_provider_and_refund_returns_safe_summary(billing_app):
    app, provider = billing_app
    with TestClient(app) as client:
        register(client)
        order = create(client)
        for amount in (str(order["amount"]), float(order["amount"]), True):
            response = client.post("/v1/billing/confirm", json={"order_id": order["order_id"], "payment_key": "mock", "amount": amount})
            assert response.status_code == 422
        assert provider.calls == []
        assert client.post("/v1/billing/mock-confirm", json={"order_id": order["order_id"]}).status_code == 200
        url = "/v1/billing/orders/" + order["order_id"] + "/refund"
        assert client.post(url, json={"reason": "x" * 201}).status_code == 422
        refunded = client.post(url, json={"reason": "사용 전 취소"})
        assert refunded.status_code == 200, refunded.text
        data = refunded.json()["data"]
        assert data["status"] == "refunded" and data["payment"]["balance_amount"] == 0
        assert data["payment"]["cancelled_amount"] == order["amount"]
        assert "paymentKey" not in refunded.text and "transaction_key" not in refunded.text


def test_webhook_transport_retries_same_delivery_after_unverified_then_success(billing_app):
    app, provider = billing_app
    with TestClient(app) as client:
        auth = register(client)
        order = create(client)
        settings = app.state.billing_settings
        settings.provider = "toss_test"
        settings.secret_key, settings.client_key, settings.merchant_id = "test_sk_placeholder", "test_ck_placeholder", "merchant"
        # Convert this isolated test fixture's account to the selected PG.
        from services.api.billing.models import BillingAccount
        with app.state.session_factory.begin() as db:
            db.get(BillingAccount, auth["tenant"]["id"]).provider = "toss_test"
        payload = {"eventType": "PAYMENT_STATUS_CHANGED", "data": {"orderId": order["order_id"], "status": "DONE"}}
        headers = {"tosspayments-webhook-transmission-id": "delivery-id"}
        assert client.post("/v1/billing/webhooks/toss", json=payload, headers=headers).status_code == 409
        payment = provider.confirm("mock", order["order_id"], order["amount"])
        provider.payments[order["order_id"]]["mId"] = "merchant"
        response = client.post("/v1/billing/webhooks/toss", json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"received": True, "ignored": False, "status": "paid"}
        before = len(provider.calls)
        assert client.post("/v1/billing/webhooks/toss", json=payload, headers=headers).status_code == 200
        assert len(provider.calls) == before
        unsupported = client.post("/v1/billing/webhooks/toss", json={"eventType": "DEPOSIT_CALLBACK", "data": {}})
        assert unsupported.status_code == 200 and unsupported.json()["data"]["ignored"] is True
        assert client.post("/v1/billing/webhooks/toss", json=[]).status_code == 422
