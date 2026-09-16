from datetime import timedelta, timezone
from hashlib import sha256
import hmac
import secrets
from types import SimpleNamespace

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request
from sqlalchemy import delete, func, select

from .database import utcnow
from .errors import APIError
from .models import AuthAttempt, LoginSession, Tenant, User

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
# A constant valid dummy hash makes unknown accounts perform the same expensive check.
DUMMY_HASH = PASSWORDS.hash("unusable-account-placeholder")
COOKIE_NAME = "phoenix_session"


def hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return PASSWORDS.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_session(db, user, response, settings):
    token = secrets.token_urlsafe(48)
    session = LoginSession(token_hash=hash_token(token), csrf_token=secrets.token_urlsafe(32), user_id=user.id, expires_at=utcnow() + timedelta(days=settings.session_days))
    db.add(session)
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_days * 86400, path="/")
    return session


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in request.app.state.settings.allowed_origins:
        raise APIError(403, "ORIGIN_REJECTED", "허용되지 않은 주소의 요청입니다.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise APIError(403, "ORIGIN_REJECTED", "다른 사이트에서 보낸 요청은 처리할 수 없습니다.")


def require_auth(request: Request, db, mutate=False, authorize_write=True, enforce_membership=True):
    token = request.cookies.get(COOKIE_NAME, "")
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == hash_token(token))) if token else None
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
        raise APIError(401, "AUTH_REQUIRED", "로그인 후 이용해 주세요.")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise APIError(401, "AUTH_REQUIRED", "다시 로그인해 주세요.")
    if session.active_tenant_id and session.active_tenant_id != user.tenant_id:
        from .feature_models import Membership
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.tenant_id == session.active_tenant_id, Membership.is_active.is_(True)))
        if membership is None:
            if enforce_membership:
                raise APIError(403, "MEMBERSHIP_UNAVAILABLE", "이 작업 공간의 접근 권한이 없습니다. 개인 공간으로 전환해 주세요.")
            # Recovery endpoints (switch home, accept invitation, logout) still
            # authenticate the real account and CSRF token, even after removal.
        else:
            user = SimpleNamespace(id=user.id, name=user.name, email=user.email, tenant_id=membership.tenant_id, role=membership.role, is_admin=user.is_admin, email_verified_at=user.email_verified_at)
    from .billing.payments import enforce_membership_entitlement
    if enforce_membership:
        enforce_membership_entitlement(db, user)
    db.info["principal"] = user
    if mutate:
        check_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(supplied, session.csrf_token):
            raise APIError(403, "CSRF_REJECTED", "보안 확인이 만료되었습니다. 페이지를 새로 열어 주세요.")
        if authorize_write and user.role not in {"owner", "editor"}:
            raise APIError(403, "ROLE_FORBIDDEN", "이 작업을 변경할 권한이 없습니다.")
    return user, session


def auth_payload(db, user, session):
    tenant = db.get(Tenant, user.tenant_id)
    from .feature_models import Membership, WorkspaceMember
    home = db.get(User, user.id)
    memberships = [{"id": home.tenant_id, "name": db.get(Tenant, home.tenant_id).name, "role": home.role}]
    for member in db.scalars(select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))):
        memberships.append({"id": member.tenant_id, "name": db.get(Tenant, member.tenant_id).name, "role": member.role})
    workspaces = list(db.scalars(select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id, WorkspaceMember.tenant_id == user.tenant_id)))
    return {"user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "is_admin": user.is_admin, "email_verified": user.email_verified_at is not None}, "tenant": {"id": tenant.id, "name": tenant.name}, "memberships": memberships, "workspace_ids": workspaces, "csrf_token": session.csrf_token}


def throttle_auth(db, email: str):
    cutoff = utcnow() - timedelta(minutes=15)
    key = hash_token("auth:" + email.lower())
    count = db.scalar(select(func.count()).select_from(AuthAttempt).where(AuthAttempt.key_hash == key, AuthAttempt.created_at >= cutoff))
    if count >= 12:
        raise APIError(429, "LOGIN_RATE_LIMIT", "로그인 시도가 많습니다. 15분 뒤 다시 시도해 주세요.", retryable=True)
    db.execute(delete(AuthAttempt).where(AuthAttempt.created_at < cutoff))
    db.add(AuthAttempt(key_hash=key))
    db.commit()
