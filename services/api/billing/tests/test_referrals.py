from datetime import timedelta

import pytest
from sqlalchemy import func, select

from services.api.errors import APIError
from services.api.models import Tenant
from services.api.billing.models import CreditBucket, Referral
from services.api.billing.payments import confirm_order
from services.api.billing.referrals import POLICY, claim_referral, process_referral_bonuses, referral_code, referral_overview
from services.api.billing.service import wallet_summary
from services.api.billing.tests.test_payments import subscribe


def _tenant(db, name, created_at):
    row = Tenant(name=name, created_at=created_at)
    db.add(row)
    db.flush()
    return row.id


def test_claim_rules_self_duplicate_window_and_after_payment(billing_db, payment_settings, provider):
    factory, referrer, now = billing_db
    with factory.begin() as db:
        code = referral_code(db, referrer)
        assert code == referral_code(db, referrer) and len(code) == 8
        with pytest.raises(APIError) as exc:
            claim_referral(db, referrer, code, now=now)
        assert exc.value.code == "SELF_REFERRAL"
        friend = _tenant(db, "추천 고객", now)
        with pytest.raises(APIError) as exc:
            claim_referral(db, friend, "NOPE1234", now=now)
        assert exc.value.code == "REFERRAL_CODE_NOT_FOUND"
        claim_referral(db, friend, code.lower(), now=now)
        with pytest.raises(APIError) as exc:
            claim_referral(db, friend, code, now=now)
        assert exc.value.code == "REFERRAL_EXISTS"
        late = _tenant(db, "늦은 가입", now - timedelta(days=POLICY["claim_window_days"] + 1))
        with pytest.raises(APIError) as exc:
            claim_referral(db, late, code, now=now)
        assert exc.value.code == "REFERRAL_CLAIM_EXPIRED"
        paid = _tenant(db, "결제 후", now)
        order = subscribe(db, paid, now, payment_settings, provider)
        confirm_order(db, paid, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=now)
        with pytest.raises(APIError) as exc:
            claim_referral(db, paid, code, now=now)
        assert exc.value.code == "REFERRAL_AFTER_PAYMENT"
        overview = referral_overview(db, referrer, now=now)
        assert overview["code"] == code and [r["status"] for r in overview["referrals"]] == ["pending"]
        assert referral_overview(db, friend, now=now)["claimed_code"] == code


def test_bonus_after_cancellation_window_monthly_cap_and_refund_void(billing_db, payment_settings, provider):
    factory, referrer, now = billing_db
    with factory.begin() as db:
        code = referral_code(db, referrer)
        friends = [_tenant(db, f"고객 {i}", now) for i in range(4)]
        for friend in friends:
            claim_referral(db, friend, code, now=now)
        # Nothing before a payment, nothing inside the cancellation window.
        assert process_referral_bonuses(db, now=now) == 0
        for friend in friends:
            order = subscribe(db, friend, now, payment_settings, provider)
            confirm_order(db, friend, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=now)
        assert process_referral_bonuses(db, now=now + timedelta(days=POLICY["cancellation_window_days"] - 1)) == 0
        later = now + timedelta(days=POLICY["cancellation_window_days"])
        assert process_referral_bonuses(db, now=later) == POLICY["monthly_grant_limit"]
        assert process_referral_bonuses(db, now=later) == 0  # cap reached; the fourth stays pending
        statuses = sorted(r.status for r in db.scalars(select(Referral)))
        assert statuses == ["granted"] * 3 + ["pending"]
        bonus = db.scalars(select(CreditBucket).where(CreditBucket.tenant_id == referrer, CreditBucket.kind == "bonus")).all()
        assert len(bonus) == 3 and all(b.granted == POLICY["bonus_credits"] and b.scope == "standard_only" for b in bonus)
        assert wallet_summary(db, referrer, now=later)["available"] == 30 + 3 * POLICY["bonus_credits"]
        # Bonus credits expire after 30 days and are separate from purchased credits.
        assert wallet_summary(db, referrer, now=later + timedelta(days=POLICY["bonus_valid_days"] + 1))["available"] == 0
        # The next month grants the remaining one; repeated runs never double grant.
        next_month = later + timedelta(days=32)
        assert process_referral_bonuses(db, now=next_month) == 1
        assert process_referral_bonuses(db, now=next_month) == 0
        assert db.scalar(select(func.count()).select_from(CreditBucket).where(CreditBucket.kind == "bonus")) == 4


def test_refunded_first_payment_voids_the_referral(billing_db, payment_settings, provider):
    from services.api.billing.payments import refund_order
    factory, referrer, now = billing_db
    with factory.begin() as db:
        code = referral_code(db, referrer)
        friend = _tenant(db, "환불 고객", now)
        claim_referral(db, friend, code, now=now)
        order = subscribe(db, friend, now, payment_settings, provider)
        confirm_order(db, friend, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=now)
        refund_order(db, friend, order.order_id, "unused", provider=provider, settings=payment_settings, now=now + timedelta(days=1))
        assert process_referral_bonuses(db, now=now + timedelta(days=POLICY["cancellation_window_days"])) == 0
        assert db.scalar(select(Referral)).status == "void"
