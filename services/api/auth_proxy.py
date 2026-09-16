"""Verify the web proxy's short-lived, pseudonymous login-throttle identity."""
from hashlib import sha256
import hmac
import re

from .database import utcnow
from .errors import APIError


def challenge_rate_identity(request, settings):
    if settings.environment in {"development", "test"}:
        # Local requests use the actual socket peer; all forwarding headers are ignored.
        peer = request.client.host if request.client else "unknown"
        return "google-challenge-peer:" + peer
    if not settings.worker_secret:
        raise APIError(503, "AUTH_PROXY_UNAVAILABLE", "로그인 연결을 준비하고 있습니다. 잠시 후 다시 시도해 주세요.")
    key = request.headers.get("x-phoenix-auth-rate-key", "")
    timestamp = request.headers.get("x-phoenix-auth-rate-timestamp", "")
    signature = request.headers.get("x-phoenix-auth-rate-signature", "")
    invalid = APIError(403, "AUTH_PROXY_INVALID", "서비스의 로그인 화면에서 다시 시작해 주세요.")
    if not re.fullmatch(r"[0-9a-f]{64}", key) or not re.fullmatch(r"[0-9a-f]{64}", signature) or not re.fullmatch(r"[0-9]{10,12}", timestamp):
        raise invalid
    if abs(utcnow().timestamp() - int(timestamp)) > 60:
        raise invalid
    message = f"phoenix-auth-rate-v1\n{timestamp}\n{request.method}\n{request.url.path}\n{key}"
    expected = hmac.new(settings.worker_secret.encode("utf-8"), message.encode("utf-8"), sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise invalid
    return "google-challenge-proxy:" + key
