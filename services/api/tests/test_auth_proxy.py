from datetime import datetime, timezone
from hashlib import sha256
import hmac

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.requests import Request

from services.api.auth import hash_token
from services.api.auth_proxy import challenge_rate_identity
from services.api.config import Settings
from services.api.database import utcnow
from services.api.errors import APIError
from services.api.main import create_app
from services.api.models import AuthAttempt, GoogleLoginChallenge
from services.api.tests.auth_helpers import TEST_CLIENT_ID

SECRET = "authentication-proxy-test-secret"
PATH = "/v1/auth/google/challenge"


def signed_headers(address="203.0.113.10", timestamp=None, secret=SECRET, method="GET", path=PATH):
    timestamp = str(timestamp if timestamp is not None else int(utcnow().timestamp()))
    key = hmac.new(secret.encode(), f"phoenix-auth-client-v1\n{address}".encode(), sha256).hexdigest()
    signature = hmac.new(secret.encode(), f"phoenix-auth-rate-v1\n{timestamp}\n{method}\n{path}\n{key}".encode(), sha256).hexdigest()
    return {"x-phoenix-auth-rate-key": key, "x-phoenix-auth-rate-timestamp": timestamp, "x-phoenix-auth-rate-signature": signature}


def request(headers, method="GET", path=PATH):
    return Request({"type": "http", "method": method, "path": path, "scheme": "https", "server": ("api.example", 443), "client": ("127.0.0.1", 1234), "headers": [(key.encode(), value.encode()) for key, value in headers.items()]})


def test_signature_protocol_matches_web_and_binds_method_path_and_freshness(monkeypatch):
    monkeypatch.setattr("services.api.auth_proxy.utcnow", lambda: datetime.fromtimestamp(1700000000, timezone.utc))
    settings = Settings(environment="staging", worker_secret=SECRET)
    headers = signed_headers(timestamp=1700000000)
    assert headers["x-phoenix-auth-rate-key"] == "b628faa20c8b03286c5f24e60eea14a4dac0d39ba4d570191881640fe024db51"
    assert headers["x-phoenix-auth-rate-signature"] == "ffe2dedd9dcfdeedafea7ea371c73f71cef37ba616875e330a87c8d0d88ae58d"
    assert challenge_rate_identity(request(headers), settings).startswith("google-challenge-proxy:")
    for bad in [request(headers, method="POST"), request(headers, path="/v1/auth/google"), request(signed_headers(timestamp=1699999939)), request(signed_headers(timestamp=1700000061)), request(signed_headers(timestamp=1700000000, secret="wrong"))]:
        with pytest.raises(APIError) as error:
            challenge_rate_identity(bad, settings)
        assert error.value.code == "AUTH_PROXY_INVALID"


def test_hosted_clients_are_separate_behind_same_proxy_and_spoofing_is_rejected(tmp_path):
    app = create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path / 'proxy.db'}", storage_dir=tmp_path / "files", google_client_id=TEST_CLIENT_ID, worker_secret=SECRET))
    first, second = signed_headers(), signed_headers("203.0.113.11")
    identity = "google-challenge-proxy:" + first["x-phoenix-auth-rate-key"]
    with TestClient(app) as client:
        # Start the isolated SQLite fixture before applying hosted request behavior.
        app.state.settings.environment = "staging"
        with app.state.session_factory.begin() as db:
            db.add_all([AuthAttempt(key_hash=hash_token("auth:" + identity)) for _ in range(120)])
        assert client.get(PATH, headers=first).status_code == 429
        assert client.get(PATH, headers=second).status_code == 200
        assert client.get(PATH, headers={"x-forwarded-for": "203.0.113.12", "x-vercel-forwarded-for": "203.0.113.12"}).status_code == 403
        tampered = {**second, "x-phoenix-auth-rate-key": "a" * 64}
        assert client.get(PATH, headers=tampered).status_code == 403
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(GoogleLoginChallenge)) == 1
        assert db.scalar(select(func.count()).select_from(AuthAttempt)) == 121


def test_missing_hosted_secret_fails_closed_and_local_ignores_forwarding_headers():
    forwarded = request({"x-forwarded-for": "203.0.113.10", **signed_headers()})
    with pytest.raises(APIError) as error:
        challenge_rate_identity(forwarded, Settings(environment="staging", worker_secret=""))
    assert error.value.code == "AUTH_PROXY_UNAVAILABLE"
    assert challenge_rate_identity(forwarded, Settings(environment="development", worker_secret=SECRET)) == "google-challenge-peer:127.0.0.1"
