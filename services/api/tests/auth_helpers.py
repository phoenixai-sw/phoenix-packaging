"""Network-free tests ONLY: replace Google signature verifier in a specific app.

The normal application never imports this module or exposes an auth bypass.
All nonce, CSRF, account linking and session logic still runs through real routes.
"""
from hashlib import sha256

from services.api.database import utcnow
from services.api.google_auth import google_verifier

TEST_CLIENT_ID = "test-client.apps.googleusercontent.com"


def google_login(client, email="owner@example.com", name="테스트", *, sub=None, claims=None):
    app = client.app
    app.state.settings.google_client_id = TEST_CLIENT_ID
    challenge = client.get("/v1/auth/google/challenge")
    assert challenge.status_code == 200, challenge.text
    value = challenge.json()["data"]
    now = int(utcnow().timestamp())
    identity = {"aud": TEST_CLIENT_ID, "iss": "https://accounts.google.com", "exp": now + 3600, "iat": now,
                "email_verified": True, "email": email, "name": name, "sub": sub or sha256(email.lower().encode()).hexdigest(),
                "nonce": value["nonce"], "hd": email.split("@")[1]}
    identity.update(claims or {})
    previous = app.dependency_overrides.get(google_verifier)
    app.dependency_overrides[google_verifier] = lambda: (lambda *_: identity)
    try:
        response = client.post("/v1/auth/google", json={"credential": "TEST_ONLY_VERIFIED_GOOGLE_ID_TOKEN", "csrf_token": value["csrf_token"]})
    finally:
        if previous is None:
            app.dependency_overrides.pop(google_verifier, None)
        else:
            app.dependency_overrides[google_verifier] = previous
    return response


def register(client, email="owner@example.com"):
    response = google_login(client, email)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    client.headers["X-CSRF-Token"] = data["csrf_token"]
    return data
