from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from services.api.errors import APIError
from services.api.models import Project
from services.api.billing.models import Allocation, CreditBucket, LedgerEntry, ProductionEntitlement, Reservation, Wallet
from services.api.billing.policy import aware
from services.api.billing.service import capture_unit, compensate_unit, create_quote, ensure_trial, expire_available, grant_credits, lock_wallet, production_fingerprint, release, release_unit, reserve, wallet_summary


IDENTITY = {"brand_id": "brand-1", "product_variant_id": "variant-1", "billing_family_key": "three-side-v1", "content_amount": 500, "content_unit": "g", "width_mm": 160, "height_mm": 230, "barcode": {"symbology": "EAN13", "data": "8801234567893"}}


def paid(db, tenant, now, amount=500):
    ensure_trial(db, tenant, now=now)
    lock_wallet(db, tenant, now).ever_paid = True
    grant_credits(db, tenant, amount, kind="monthly", scope="paid", expires_at=now + timedelta(days=31), grant_key="test-paid", reason="검수용 지급", now=now)


def test_trial_once_14_days_scope_and_no_auto_conversion(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        ensure_trial(db, tenant, now=now)
        ensure_trial(db, tenant, now=now)
        state = wallet_summary(db, tenant, now=now)
        assert state["balance"] == 30 and len(state["buckets"]) == 1
        assert state["buckets"][0]["scope"] == "standard_only"
        with pytest.raises(APIError) as caught:
            reserve(db, tenant, "production", "export.production.first", 1, input_data={"production_identity": IDENTITY}, now=now)
        assert caught.value.code == "PAID_PRODUCTION_REQUIRED"
    with factory.begin() as db:
        assert wallet_summary(db, tenant, now=now + timedelta(days=14))["balance"] == 0
        assert db.get(Wallet, tenant).ever_paid is False


def test_standard_three_edit_and_first_production_cost_80(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        paid(db, tenant, now)
        for key, action, units, data in [("three", "image.generate.standard", 3, {}), ("edit", "image.edit.standard", 1, {}), ("production", "export.production.first", 1, {"production_identity": IDENTITY})]:
            reservation = reserve(db, tenant, key, action, units, input_data=data, now=now)
            for unit in range(units):
                assert capture_unit(db, tenant, reservation.id, unit, now=now)
        summary = wallet_summary(db, tenant, now=now)
        assert summary["consumed"] == 80
        assert summary["balance"] == 450
        assert summary["reserved"] == 0


def test_partial_success_capture20_release10_and_replay(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        reservation = reserve(db, tenant, "three", "image.generate.standard", 3, now=now)
        same = reserve(db, tenant, "three", "image.generate.standard", 3, now=now)
        assert same.id == reservation.id
        assert capture_unit(db, tenant, reservation.id, 0, now=now)
        assert capture_unit(db, tenant, reservation.id, 1, now=now)
        release_unit(db, tenant, reservation.id, 2, now=now)
        assert capture_unit(db, tenant, reservation.id, 2, now=now) is False
        capture_unit(db, tenant, reservation.id, 0, now=now)
        release_unit(db, tenant, reservation.id, 2, now=now)
        summary = wallet_summary(db, tenant, now=now)
        assert (summary["balance"], summary["reserved"], summary["consumed"]) == (10, 0, 20)
        assert reservation.status == "partially_captured"
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event == "CAPTURE")) == 2
        with pytest.raises(APIError) as caught:
            reserve(db, tenant, "three", "image.generate.standard", 2, now=now)
        assert caught.value.code == "IDEMPOTENCY_CONFLICT"


def test_concurrent_reservations_cannot_overdraw_30(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        ensure_trial(db, tenant, now=now)
    def attempt(key):
        try:
            with factory.begin() as db:
                reserve(db, tenant, key, "image.generate.standard", 2, now=now)
                return "reserved"
        except APIError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ["one", "two"]))
    assert sorted(outcomes) == ["INSUFFICIENT_CREDITS", "reserved"]
    with factory.begin() as db:
        state = wallet_summary(db, tenant, now=now)
        assert (state["balance"], state["reserved"]) == (10, 20)


def test_fefo_and_expiration_does_not_expire_reserved_twice(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        paid(db, tenant, now)
        reservation = reserve(db, tenant, "near-expiry", "image.generate.standard", 3, now=now + timedelta(days=14, seconds=-60))
        allocations = db.scalars(select(Allocation).where(Allocation.reservation_id == reservation.id)).all()
        assert {db.get(CreditBucket, allocation.bucket_id).kind for allocation in allocations} == {"trial"}
        after = now + timedelta(days=14, seconds=60)
        expire_available(db, tenant, now=after)
        release_unit(db, tenant, reservation.id, 0, now=after)
        assert capture_unit(db, tenant, reservation.id, 1, now=after)
        release_unit(db, tenant, reservation.id, 2, now=after)
        restored = db.scalars(select(CreditBucket).where(CreditBucket.kind == "compensation")).all()
        assert sum(bucket.available for bucket in restored) == 20
        assert {bucket.scope for bucket in restored} == {"standard_only"}
        assert all(aware(bucket.expires_at) == after + timedelta(days=7) for bucket in restored)
        state = wallet_summary(db, tenant, now=after)
        assert (state["reserved"], state["consumed"], state["expired"], state["balance"]) == (0, 10, 20, 520)


def test_lost_worker_timeout_returns_and_late_result_is_not_charged(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        reservation = reserve(db, tenant, "lost", "image.generate.standard", 3, now=now)
    with factory.begin() as db:
        assert capture_unit(db, tenant, reservation.id, 0, now=now + timedelta(minutes=41)) is False
        state = wallet_summary(db, tenant, now=now + timedelta(minutes=41))
        assert (state["balance"], state["reserved"], state["consumed"]) == (30, 0, 0)


def test_quote_binds_input_revision_and_five_minute_expiry(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        quote = create_quote(db, tenant, "image.generate.standard", 1, project_id=None, base_revision=None, input_data={"prompt": "말차"}, now=now)
        assert quote.input_data == {"prompt": "말차"}
        with pytest.raises(APIError) as changed:
            reserve(db, tenant, "changed", "image.generate.standard", 1, quote_id=quote.id, input_data={"prompt": "커피"}, now=now)
        assert changed.value.code == "QUOTE_CHANGED"
        with pytest.raises(APIError) as expired:
            reserve(db, tenant, "expired", "image.generate.standard", 1, quote_id=quote.id, input_data={"prompt": "말차"}, now=now + timedelta(minutes=5))
        assert expired.value.code == "QUOTE_EXPIRED"
        result = reserve(db, tenant, "valid", "image.generate.standard", 1, quote_id=quote.id, input_data={"prompt": "말차"}, now=now + timedelta(minutes=4))
        assert result.quote_id == quote.id
        with pytest.raises(APIError) as used:
            reserve(db, tenant, "again", "image.generate.standard", 1, quote_id=quote.id, input_data={"prompt": "말차"}, now=now + timedelta(minutes=4))
        assert used.value.code == "QUOTE_ALREADY_USED"


def test_capture_and_result_transaction_rollback_keeps_reservation(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        reservation = reserve(db, tenant, "db-error", "image.generate.standard", 1, now=now)
    with pytest.raises(RuntimeError):
        with factory.begin() as db:
            assert capture_unit(db, tenant, reservation.id, 0, now=now)
            raise RuntimeError("simulated result DB failure")
    with factory.begin() as db:
        assert db.get(Reservation, reservation.id).unit_status["0"] == "held"
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event == "CAPTURE")) == 0
        release(db, tenant, reservation.id, reason="결과 DB 저장 실패", now=now)
        assert wallet_summary(db, tenant, now=now)["balance"] == 30


def test_production_identity_repeats_free_physical_changes_new_and_failed_bundle_not_entitled(billing_db):
    factory, tenant, now = billing_db
    assert production_fingerprint(tenant, IDENTITY) == production_fingerprint(tenant, {**IDENTITY, "content_amount": "0.5", "content_unit": "kg", "title": "제목 수정", "material": "다른 재질명", "print_quantity": 999})
    for change in [{"content_amount": 501}, {"width_mm": 161}, {"billing_family_key": "standup"}, {"barcode": {"symbology": "EAN13", "data": "8801234567800"}}, {"holes": [{"x_mm": 40, "y_mm": 15, "diameter_mm": 5}]}]:
        assert production_fingerprint(tenant, IDENTITY) != production_fingerprint(tenant, {**IDENTITY, **change})
    with factory.begin() as db:
        paid(db, tenant, now)
        first = reserve(db, tenant, "first", "export.production.first", 1, input_data={"production_identity": IDENTITY}, now=now)
        with pytest.raises(APIError) as running:
            reserve(db, tenant, "duplicate", "export.production.first", 1, input_data={"production_identity": IDENTITY}, now=now)
        assert running.value.code == "PRODUCTION_IN_PROGRESS"
        release(db, tenant, first.id, reason="묶음 파일 누락", now=now)
        assert db.scalar(select(ProductionEntitlement)).status == "failed"
        retry = reserve(db, tenant, "retry", "export.production.first", 1, input_data={"production_identity": IDENTITY}, now=now)
        capture_unit(db, tenant, retry.id, 0, now=now)
        quote = create_quote(db, tenant, "export.production.repeat", 1, project_id=None, base_revision=None, input_data={"production_identity": IDENTITY}, now=now)
        assert quote.credit_total == 0
        again = reserve(db, tenant, "repeat", "export.production.repeat", 1, quote_id=quote.id, input_data={"production_identity": IDENTITY}, now=now)
        capture_unit(db, tenant, again.id, 0, now=now)
        assert wallet_summary(db, tenant, now=now)["consumed"] == 40


def test_tenant_cannot_capture_or_use_other_quote(billing_db):
    from services.api.models import Tenant
    factory, tenant, now = billing_db
    with factory.begin() as db:
        other = Tenant(name="다른 조직", created_at=now)
        db.add(other)
        db.flush()
        reservation = reserve(db, tenant, "own", "image.generate.standard", 1, now=now)
        with pytest.raises(APIError) as denied:
            capture_unit(db, other.id, reservation.id, 0, now=now)
        assert denied.value.status == 404


def test_missing_captured_asset_compensates_once_without_rewriting_history(billing_db):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        reservation = reserve(db, tenant, "missing-asset", "image.generate.standard", 1, now=now)
        capture_unit(db, tenant, reservation.id, 0, now=now)
        compensate_unit(db, tenant, reservation.id, 0, reason="결과 파일 접근 불가 확인", now=now)
        compensate_unit(db, tenant, reservation.id, 0, reason="같은 장애 재처리", now=now)
        restored = db.scalars(select(CreditBucket).where(CreditBucket.kind == "compensation")).all()
        assert len(restored) == 1 and restored[0].scope == "standard_only"
        assert wallet_summary(db, tenant, now=now)["balance"] == 30
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event == "CAPTURE")) == 1
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event == "COMPENSATE")) == 1
