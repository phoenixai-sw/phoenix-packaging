"""Referral bonus per the pricing proposal: 100 credits to the referrer after the referred
tenant's first subscription payment survives the cancellation window; 30-day validity,
three grants per calendar month, no self-referral. Bonus credits are kept apart from
purchased credits (kind="bonus", standard-only scope)."""
import secrets
from datetime import timedelta

from sqlalchemy import func, select

from ..database import utcnow
from ..errors import APIError
from ..models import Tenant
from .models import Payment, PaymentOrder, Referral, ReferralCode
from .policy import SEOUL, aware
from .service import grant_credits

POLICY = {
    "version": "referral-2026-09-18-v1",
    "bonus_credits": 100,
    "bonus_valid_days": 30,
    "monthly_grant_limit": 3,
    "cancellation_window_days": 7,
    "claim_window_days": 30,
    "scope": "standard_only",
}
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def referral_code(db, tenant_id, *, now=None):
    row = db.get(ReferralCode, tenant_id)
    if row is None:
        for _ in range(20):
            code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
            if db.scalar(select(ReferralCode).where(ReferralCode.code == code)) is None:
                break
        else:
            raise APIError(503, "REFERRAL_CODE_UNAVAILABLE", "추천 코드를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.")
        row = ReferralCode(tenant_id=tenant_id, code=code, created_at=now or utcnow())
        db.add(row)
        db.flush()
    return row.code


def claim_referral(db, tenant_id, code, *, now=None):
    now = now or utcnow()
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise APIError(404, "NOT_FOUND", "작업 공간을 찾을 수 없습니다.")
    normalized = (code or "").strip().upper()
    owner = db.scalar(select(ReferralCode).where(ReferralCode.code == normalized)) if normalized else None
    if owner is None:
        raise APIError(404, "REFERRAL_CODE_NOT_FOUND", "추천 코드를 확인해 주세요.")
    if owner.tenant_id == tenant_id:
        raise APIError(422, "SELF_REFERRAL", "자기 추천은 등록할 수 없습니다.")
    if db.scalar(select(Referral).where(Referral.referred_tenant_id == tenant_id)) is not None:
        raise APIError(409, "REFERRAL_EXISTS", "이미 추천 코드를 등록한 작업 공간입니다.")
    if aware(tenant.created_at) + timedelta(days=POLICY["claim_window_days"]) < aware(now):
        raise APIError(422, "REFERRAL_CLAIM_EXPIRED", f"추천 코드는 가입 후 {POLICY['claim_window_days']}일 안에만 등록할 수 있습니다.")
    if _first_subscription_payment(db, tenant_id) is not None:
        raise APIError(422, "REFERRAL_AFTER_PAYMENT", "첫 결제 전에만 추천 코드를 등록할 수 있습니다.")
    row = Referral(referrer_tenant_id=owner.tenant_id, referred_tenant_id=tenant_id, code=normalized, status="pending", created_at=now)
    db.add(row)
    db.flush()
    return row


def _first_subscription_payment(db, tenant_id):
    return db.scalar(
        select(Payment).join(PaymentOrder, PaymentOrder.id == Payment.order_id)
        .where(Payment.tenant_id == tenant_id, Payment.status == "DONE", PaymentOrder.kind == "subscription", PaymentOrder.status.in_(("paid", "refunded")))
        .order_by(Payment.approved_at).limit(1)
    )


def _month_bounds(now):
    local = aware(now).astimezone(SEOUL)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def process_referral_bonuses(db, *, now=None, limit=50):
    """Grant due bonuses; a referral past the monthly cap simply waits for the next month."""
    now = now or utcnow()
    granted = 0
    pending = db.scalars(select(Referral).where(Referral.status == "pending").order_by(Referral.created_at).limit(limit)).all()
    for referral in pending:
        payment = _first_subscription_payment(db, referral.referred_tenant_id)
        if payment is None:
            continue
        order = db.get(PaymentOrder, payment.order_id)
        if order is None or order.status != "paid":
            if order is not None and order.status == "refunded":
                referral.status = "void"
                referral.note = "첫 결제가 환불되어 보너스 대상이 아닙니다."
            continue
        if aware(payment.approved_at) + timedelta(days=POLICY["cancellation_window_days"]) > aware(now):
            continue
        start, end = _month_bounds(now)
        used = db.scalar(select(func.count()).select_from(Referral).where(
            Referral.referrer_tenant_id == referral.referrer_tenant_id, Referral.status == "granted",
            Referral.granted_at >= start, Referral.granted_at < end))
        if used >= POLICY["monthly_grant_limit"]:
            continue
        grant_credits(db, referral.referrer_tenant_id, POLICY["bonus_credits"], kind="bonus", scope=POLICY["scope"],
                      expires_at=aware(now) + timedelta(days=POLICY["bonus_valid_days"]), grant_key=f"referral:{referral.id}",
                      reason="추천 보너스 · 추천 고객의 첫 결제 확정", now=now)
        referral.status = "granted"
        referral.granted_at = now
        granted += 1
    return granted


def referral_overview(db, tenant_id, *, now=None):
    now = now or utcnow()
    start, end = _month_bounds(now)
    rows = db.scalars(select(Referral).where(Referral.referrer_tenant_id == tenant_id).order_by(Referral.created_at.desc()).limit(50)).all()
    claimed = db.scalar(select(Referral).where(Referral.referred_tenant_id == tenant_id))
    return {
        "code": referral_code(db, tenant_id, now=now),
        "policy": POLICY,
        "granted_this_month": sum(1 for r in rows if r.status == "granted" and r.granted_at and start <= aware(r.granted_at) < end),
        "claimed_code": claimed.code if claimed else None,
        "referrals": [{"id": r.id, "status": r.status, "created_at": aware(r.created_at).isoformat(),
                       "granted_at": aware(r.granted_at).isoformat() if r.granted_at else None} for r in rows],
    }
