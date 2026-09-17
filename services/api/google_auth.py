"""Google Identity Services ID-token exchange; no password or email fallback."""
from .contracts.base import Envelope, ERROR_RESPONSES
from .contracts import core as C
from datetime import timedelta, timezone
import hmac
import secrets
from typing import Callable

from fastapi import APIRouter, Depends, Request, Response
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from .auth import COOKIE_NAME, auth_payload, check_origin, create_session, hash_token, platform_admin, throttle_auth
from .auth_proxy import challenge_rate_identity
from .database import utcnow
from .errors import APIError
from .models import GoogleLoginChallenge, LoginSession, Tenant, User
from .metrics.schemas import AcquisitionInput

CHALLENGE_COOKIE = "phoenix_google_challenge"
CHALLENGE_SECONDS = 600


class GoogleLoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: str = Field(min_length=20, max_length=16384)
    csrf_token: str = Field(min_length=32, max_length=128)
    acquisition: AcquisitionInput | None = None


class BoundedGoogleRequest(GoogleRequest):
    def __call__(self, url, method="GET", body=None, headers=None, timeout=10, **kwargs):
        return super().__call__(url, method=method, body=body, headers=headers, timeout=min(timeout or 10, 10), **kwargs)


def verify_google_signature(credential: str, client_id: str) -> dict:
    """Official library verifies Google certificates, signature, audience and expiry."""
    try:
        return id_token.verify_oauth2_token(credential, BoundedGoogleRequest(), client_id)
    except TransportError:
        raise APIError(503, "GOOGLE_VERIFICATION_UNAVAILABLE", "Google 계정을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.", retryable=True) from None
    except (GoogleAuthError, ValueError, TypeError):
        raise APIError(401, "GOOGLE_TOKEN_INVALID", "Google 로그인 정보가 유효하지 않습니다. 다시 로그인해 주세요.") from None


def google_verifier() -> Callable[[str, str], dict]:
    # Tests override this dependency in their own FastAPI app, never by environment.
    return verify_google_signature


def validated_identity(claims: dict, client_id: str, nonce: str) -> dict:
    now = utcnow().timestamp()
    invalid = APIError(401, "GOOGLE_TOKEN_INVALID", "Google 로그인 정보가 유효하지 않습니다. 다시 로그인해 주세요.")
    if claims.get("aud") != client_id or claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise invalid
    if claims.get("azp", client_id) != client_id:
        raise invalid
    if type(claims.get("exp")) not in {int, float} or claims["exp"] <= now:
        raise invalid
    if type(claims.get("iat")) not in {int, float} or claims["iat"] > now + 60:
        raise invalid
    if claims.get("email_verified") is not True:
        raise APIError(401, "GOOGLE_EMAIL_UNVERIFIED", "이메일이 확인된 Google 계정으로 로그인해 주세요.")
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(hash_token(claims["nonce"]), hash_token(nonce)):
        raise APIError(401, "GOOGLE_NONCE_INVALID", "로그인 요청이 만료되었습니다. Google 로그인 버튼을 다시 열어 주세요.")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not 1 <= len(sub) <= 255 or not sub.isascii() or not sub.isalnum():
        raise invalid
    try:
        email = str(TypeAdapter(EmailStr).validate_python(claims.get("email"))).lower()
    except ValidationError:
        raise invalid from None
    # Google is authoritative for Gmail and verified Workspace addresses only.
    authoritative = email.endswith("@gmail.com") or bool(isinstance(claims.get("hd"), str) and claims["hd"].strip())
    name = claims.get("name")
    name = name.strip()[:80] if isinstance(name, str) and name.strip() else email.split("@")[0][:80]
    return {"sub": sub, "email": email, "name": name, "authoritative": authoritative}


def resolve_google_user(db, identity):
    by_sub = db.scalar(select(User).where(User.google_sub == identity["sub"]))
    same_email = list(db.scalars(select(User).where(func.lower(User.email) == identity["email"])))
    if len(same_email) > 1:
        raise APIError(409, "GOOGLE_ACCOUNT_CONFLICT", "기존 계정 연결을 확인해야 합니다. 운영자에게 문의해 주세요.")
    by_email = same_email[0] if same_email else None
    if by_sub:
        if by_email is not None and by_email.id != by_sub.id:
            raise APIError(409, "GOOGLE_ACCOUNT_CONFLICT", "이 이메일은 다른 계정과 연결되어 있습니다. 운영자에게 문의해 주세요.")
        user = by_sub
    elif by_email:
        if by_email.google_sub or not identity["authoritative"]:
            raise APIError(409, "GOOGLE_ACCOUNT_LINK_REQUIRED", "기존 계정과 Google 계정을 자동으로 연결할 수 없습니다. 운영자에게 계정 이전을 요청해 주세요.")
        user = by_email
        # Revoke every legacy session when attaching the permanent Google identity.
        db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    else:
        tenant = Tenant(name=f"{identity['name']}의 작업 공간")
        db.add(tenant)
        db.flush()
        user = User(tenant_id=tenant.id, name=identity["name"], email=identity["email"], role="owner", password_hash="")
        db.add(user)
        db.info["metrics_new_account"] = True
    if not user.is_active and user.id is not None:
        raise APIError(403, "ACCOUNT_DISABLED", "이 계정은 현재 사용할 수 없습니다. 운영자에게 문의해 주세요.")
    user.google_sub = identity["sub"]
    user.google_email_authoritative = identity["authoritative"]
    user.email = identity["email"]
    user.name = identity["name"]
    user.email_verified_at = utcnow()
    user.password_hash = ""
    user.is_admin = False  # Only the current server allowlist grants operations access.
    db.flush()
    return user


def install_google_auth(app, db_session):
    router = APIRouter(prefix="/v1/auth")

    @router.get("/google/challenge", response_model=Envelope[C.GoogleChallenge], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def challenge(request: Request, response: Response, db=Depends(db_session)):
        settings = app.state.settings
        check_origin(request)
        if not settings.google_client_id:
            raise APIError(503, "GOOGLE_LOGIN_NOT_CONFIGURED", "Google 로그인 연결을 준비하고 있습니다. 잠시 후 다시 방문해 주세요.")
        # Hosted identity is signed by our Vercel proxy, never raw forwarding headers.
        throttle_auth(db, challenge_rate_identity(request, settings), max_attempts=120)
        old = request.cookies.get(CHALLENGE_COOKIE)
        if old:
            db.execute(delete(GoogleLoginChallenge).where(GoogleLoginChallenge.token_hash == hash_token(old)))
        now = utcnow()
        db.execute(delete(GoogleLoginChallenge).where(GoogleLoginChallenge.expires_at < now))
        secret, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        expires = now + timedelta(seconds=CHALLENGE_SECONDS)
        db.add(GoogleLoginChallenge(token_hash=hash_token(secret), nonce_hash=hash_token(nonce), expires_at=expires))
        db.commit()
        response.set_cookie(CHALLENGE_COOKIE, secret, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=CHALLENGE_SECONDS, path="/")
        return {"data": {"client_id": settings.google_client_id, "nonce": nonce, "csrf_token": nonce, "expires_at": expires.isoformat()}, "request_id": request.state.request_id}

    @router.post("/google", response_model=Envelope[C.SessionData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def login(body: GoogleLoginInput, request: Request, response: Response, verifier=Depends(google_verifier), db=Depends(db_session)):
        settings = app.state.settings
        check_origin(request)
        if not settings.google_client_id:
            raise APIError(503, "GOOGLE_LOGIN_NOT_CONFIGURED", "Google 로그인 연결을 준비하고 있습니다.")
        # The callback uses JSON and our own cookie-bound double-submit challenge.
        # The browser-provided Google profile or email is never accepted as identity.
        secret = request.cookies.get(CHALLENGE_COOKIE, "")
        token_hash = hash_token(secret)
        row = db.get(GoogleLoginChallenge, token_hash) if secret else None
        if row is None or row.consumed_at or row.expires_at.replace(tzinfo=timezone.utc) <= utcnow() or not hmac.compare_digest(row.nonce_hash, hash_token(body.csrf_token)):
            raise APIError(403, "GOOGLE_LOGIN_CSRF", "로그인 요청이 만료되었습니다. Google 로그인 버튼을 다시 열어 주세요.")
        throttle_auth(db, "google-challenge:" + token_hash)
        identity = validated_identity(verifier(body.credential, settings.google_client_id), settings.google_client_id, body.csrf_token)
        throttle_auth(db, "google-account:" + identity["sub"])
        claimed = db.execute(update(GoogleLoginChallenge).where(GoogleLoginChallenge.token_hash == token_hash, GoogleLoginChallenge.consumed_at.is_(None), GoogleLoginChallenge.expires_at > utcnow()).values(consumed_at=utcnow()).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            raise APIError(403, "GOOGLE_LOGIN_CSRF", "이미 사용한 로그인 요청입니다. 다시 시작해 주세요.")
        try:
            user = resolve_google_user(db, identity)
            previous = request.cookies.get(COOKIE_NAME)
            if previous:
                db.execute(delete(LoginSession).where(LoginSession.token_hash == hash_token(previous)))
            session = create_session(db, user, response, settings)
            from .billing.service import ensure_trial
            ensure_trial(db, user.tenant_id)
            from .metrics.service import record_signup, record_trial_granted
            if db.info.get("metrics_new_account"):
                record_signup(db, user, body.acquisition)
            record_trial_granted(db, user.tenant_id)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise APIError(409, "GOOGLE_ACCOUNT_CONFLICT", "계정 연결이 변경되었습니다. 다시 로그인해 주세요.") from None
        # Do not persist allowlist-derived administration as a user-controlled role.
        from types import SimpleNamespace
        principal = SimpleNamespace(id=user.id, tenant_id=user.tenant_id, name=user.name, email=user.email, role=user.role, email_verified_at=user.email_verified_at, is_admin=platform_admin(user, settings))
        response.delete_cookie(CHALLENGE_COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
        return {"data": auth_payload(db, principal, session), "request_id": request.state.request_id}

    # Explicit removal makes old bookmarks/API clients fail without processing credentials.
    @router.post("/register", include_in_schema=False)
    @router.post("/login", include_in_schema=False)
    @router.post("/request-verification", include_in_schema=False)
    @router.post("/verify-email", include_in_schema=False)
    @router.post("/request-password-reset", include_in_schema=False)
    @router.post("/reset-password", include_in_schema=False)
    def retired_login(request: Request):
        check_origin(request)
        raise APIError(410, "GOOGLE_LOGIN_REQUIRED", "Google 로그인으로 전환되었습니다. Google 계정으로 로그인해 주세요.")

    app.include_router(router)
