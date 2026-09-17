"""Credit mutations never commit: callers publish results and capture atomically."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..database import utcnow
from ..errors import APIError
from ..models import Project, Tenant
from .models import Allocation, BillingOutbox, CreditBucket, LedgerEntry, ProductionEntitlement, Quote, Reservation, Wallet
from .policy import aware, pricing

TRIAL_ACTIONS = {"image.generate.standard", "image.edit.standard", "editor.manual", "preview.all_faces", "export.review"}


def canonical_hash(value):
    try:
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        raise APIError(422, "INVALID_INPUT", "견적 입력 형식을 확인해 주세요.") from None


def lock_wallet(db, tenant_id, now=None):
    now = now or utcnow()
    db.flush()
    dialect = db.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    db.execute(insert(Wallet).values(tenant_id=tenant_id, lock_version=0, ever_paid=False, created_at=now).on_conflict_do_nothing(index_elements=["tenant_id"]))
    # A write lock also serializes local SQLite, where FOR UPDATE is ignored.
    db.execute(update(Wallet).where(Wallet.tenant_id == tenant_id).values(lock_version=Wallet.lock_version + 1).execution_options(synchronize_session=False))
    return db.scalar(select(Wallet).where(Wallet.tenant_id == tenant_id).execution_options(populate_existing=True))


def _event(db, tenant_id, bucket_id, event, amount, key, reason, now, *, operation_key=None, job_id=None, actor_id=None, invoice_id=None):
    db.add(LedgerEntry(tenant_id=tenant_id, bucket_id=bucket_id, event=event, amount=amount, event_key=key, reason=reason[:300], operation_key=operation_key, job_id=job_id, actor_id=actor_id, invoice_id=invoice_id, created_at=now))


def _grant(db, tenant_id, amount, kind, scope, expires_at, grant_key, now, *, source_bucket_id=None, event="GRANT", reason="크레딧 지급", invoice_id=None):
    existing = db.scalar(select(CreditBucket).where(CreditBucket.tenant_id == tenant_id, CreditBucket.grant_key == grant_key))
    if existing:
        if existing.granted != amount or existing.scope != scope:
            raise APIError(409, "GRANT_CONFLICT", "기존 지급 내역과 요청이 다릅니다.")
        return existing
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0 or scope not in {"standard_only", "paid"}:
        raise APIError(422, "INVALID_GRANT", "크레딧 지급 조건을 확인해 주세요.")
    bucket = CreditBucket(tenant_id=tenant_id, kind=kind, scope=scope, grant_key=grant_key, granted=amount, available=amount, expires_at=expires_at, source_bucket_id=source_bucket_id, created_at=now)
    db.add(bucket)
    db.flush()
    _event(db, tenant_id, bucket.id, event, amount, f"grant:{grant_key}", reason, now, invoice_id=invoice_id)
    db.flush()
    return bucket


def grant_credits(db, tenant_id, amount, *, kind, scope, expires_at, grant_key, reason, invoice_id=None, now=None):
    """Trusted server-side grants only; no public endpoint accepts these arguments."""
    now = now or utcnow()
    lock_wallet(db, tenant_id, now)
    return _grant(db, tenant_id, amount, kind, scope, expires_at, grant_key, now, reason=reason, invoice_id=invoice_id)


def ensure_trial(db, tenant_id, *, now=None):
    now = now or utcnow()
    wallet = lock_wallet(db, tenant_id, now)
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise APIError(404, "NOT_FOUND", "작업 공간을 찾을 수 없습니다.")
    trial = pricing()["trial"]
    _grant(db, tenant_id, trial["credits"], "trial", "standard_only", aware(tenant.created_at) + timedelta(days=trial["expires_days"]), "signup-trial", now, reason="가입 체험 크레딧 · 표준 이미지 생성/수정 전용")
    return wallet


def expire_available(db, tenant_id, *, now=None):
    now = now or utcnow()
    lock_wallet(db, tenant_id, now)
    expired = db.scalars(select(CreditBucket).where(CreditBucket.tenant_id == tenant_id, CreditBucket.expires_at <= now, CreditBucket.available > 0).order_by(CreditBucket.id)).all()
    for bucket in expired:
        amount = bucket.available
        bucket.available = 0
        bucket.expired += amount
        _event(db, tenant_id, bucket.id, "EXPIRE", amount, f"expire:{bucket.id}:{bucket.expired}", "유효기간 만료", now)
    db.flush()


def _eligible(bucket, action):
    return bucket.scope == "paid" or action in TRIAL_ACTIONS


def _action_cost(action, units):
    if isinstance(units, bool) or not isinstance(units, int) or not 1 <= units <= 10:
        raise APIError(422, "INVALID_UNITS", "작업 수량은 1~10개로 입력해 주세요.")
    if action not in pricing()["actions"]:
        raise APIError(422, "ACTION_UNAVAILABLE", "지원하지 않는 작업입니다.")
    if action.startswith("export.") and units != 1:
        raise APIError(422, "INVALID_EXPORT_UNITS", "출력 견적은 제작 항목 하나씩 요청해 주세요.")
    if action == "image.generate.high" and os.getenv("AI_HIGH_ENABLED", "false").lower() != "true":
        raise APIError(422, "HIGH_RESOLUTION_DISABLED", "고해상도 생성은 원가 검증 후 제공됩니다.")
    return pricing()["actions"][action]


def production_fingerprint(tenant_id, identity):
    """Physical identity only; naming, layout, print quantity and profiles excluded."""
    if not isinstance(identity, dict) or any(not identity.get(key) for key in ("brand_id", "product_variant_id", "billing_family_key")):
        raise APIError(422, "PRODUCTION_IDENTITY_REQUIRED", "브랜드·상품 변형·구조 정보를 먼저 확정해 주세요.")
    def number(value):
        try:
            result = Decimal(str(value))
            if not result.is_finite() or result < 0 or result > Decimal("1000000000000"):
                raise ValueError()
            return result
        except (InvalidOperation, ValueError):
            raise APIError(422, "PRODUCTION_IDENTITY_INVALID", "내용량과 실제 규격을 확인해 주세요.") from None
    unit = str(identity.get("content_unit", "")).lower()
    units = {"g": ("g", Decimal(1)), "kg": ("g", Decimal(1000)), "ml": ("ml", Decimal(1)), "l": ("ml", Decimal(1000)), "ea": ("ea", Decimal(1))}
    if unit not in units:
        raise APIError(422, "PRODUCTION_IDENTITY_INVALID", "내용량 단위를 g, kg, ml, L, ea 중에서 선택해 주세요.")
    normalized_unit, scale = units[unit]
    measure = lambda value: format(number(value).quantize(Decimal("0.0001")).normalize(), "f")
    dimensions = {key: measure(identity.get(key, 0)) for key in ("width_mm", "height_mm", "bottom_mm", "depth_mm")}
    if number(dimensions["width_mm"]) <= 0 or number(dimensions["height_mm"]) <= 0:
        raise APIError(422, "PRODUCTION_IDENTITY_INVALID", "완성 폭·높이는 양수여야 합니다.")
    holes = [{"face_id": str(hole.get("face_id", "front")), **{key: measure(hole.get(key, 0)) for key in ("x_mm", "y_mm", "diameter_mm")}} for hole in identity.get("holes", [])]
    barcode = identity.get("barcode") or {}
    normalized = {"tenant_id": tenant_id, "brand_id": str(identity["brand_id"]), "product_variant_id": str(identity["product_variant_id"]), "billing_family_key": str(identity["billing_family_key"]), "content": measure(number(identity.get("content_amount", 0)) * scale), "unit": normalized_unit, "barcode": {"symbology": str(barcode.get("symbology", "")).upper(), "data": str(barcode.get("data", ""))}, "dimensions": dimensions, "holes": sorted(holes, key=lambda h: json.dumps(h, sort_keys=True))}
    if identity.get("pouch_features") is not None:
        from ..geometry.pouch_features import normalize_pouch_features, physical_pouch_features
        features=normalize_pouch_features(identity["pouch_features"],"stand-up-pouch",float(dimensions["width_mm"]),float(dimensions["height_mm"]))
        normalized["pouch_features"]={key:measure(value) if not isinstance(value,(bool,str)) else value for key,value in physical_pouch_features(features).items()}
    return canonical_hash(normalized)


def _request(action, units, project_id, base_revision, input_data):
    return {"action": action, "units": units, "project_id": project_id, "base_revision": base_revision, "input_data": input_data or {}}


def _project_revision(db, tenant_id, project_id, base_revision):
    if project_id is None:
        return
    project = db.scalar(select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id))
    if project is None:
        raise APIError(404, "NOT_FOUND", "프로젝트를 찾을 수 없습니다.")
    if project.base_revision != base_revision:
        raise APIError(409, "REVISION_CONFLICT", "프로젝트가 변경되었습니다. 새 견적을 받아 주세요.")


def _production_cost(db, tenant_id, action, input_data, cost):
    fingerprint = None
    if action.startswith("export.production"):
        fingerprint = production_fingerprint(tenant_id, (input_data or {}).get("production_identity"))
        entitled = db.scalar(select(ProductionEntitlement).where(ProductionEntitlement.tenant_id == tenant_id, ProductionEntitlement.fingerprint == fingerprint, ProductionEntitlement.status == "entitled"))
        if entitled:
            cost = 0
        elif action == "export.production.repeat":
            raise APIError(403, "PRODUCTION_ENTITLEMENT_REQUIRED", "최초 제작 출력 권한이 없는 항목입니다.")
    return cost, fingerprint


def create_quote(db, tenant_id, action, units, *, project_id, base_revision, input_data, now=None):
    now = now or utcnow()
    wallet = ensure_trial(db, tenant_id, now=now)
    expire_available(db, tenant_id, now=now)
    _project_revision(db, tenant_id, project_id, base_revision)
    cost, fingerprint = _production_cost(db, tenant_id, action, input_data, _action_cost(action, units))
    if action.startswith("export.production") and not wallet.ever_paid:
        raise APIError(403, "PAID_PRODUCTION_REQUIRED", "체험 계정에서는 검토용 출력만 사용할 수 있습니다.")
    buckets = db.scalars(select(CreditBucket).where(CreditBucket.tenant_id == tenant_id, CreditBucket.expires_at > now)).all()
    balance = sum(bucket.available for bucket in buckets if _eligible(bucket, action))
    if balance < units * cost:
        raise APIError(402, "INSUFFICIENT_CREDITS", "크레딧이 부족합니다.", {"required": units * cost, "available": balance})
    quote = Quote(tenant_id=tenant_id, action=action, units=units, unit_cost=cost, credit_total=units * cost, pricing_version=pricing()["version"], project_id=project_id, base_revision=base_revision, input_hash=canonical_hash(_request(action, units, project_id, base_revision, input_data)), input_data=input_data or {}, fingerprint=fingerprint, balance_before=balance, expires_at=now + timedelta(minutes=5), created_at=now)
    db.add(quote)
    db.flush()
    return quote


def quote_payload(quote):
    return {"id": quote.id, "quote_id": quote.id, "action": quote.action, "requested_units": quote.units, "credit_total": quote.credit_total, "unit_cost": quote.unit_cost, "balance_before": quote.balance_before, "balance_after": quote.balance_before - quote.credit_total, "pricing_version": quote.pricing_version, "project_id": quote.project_id, "base_revision": quote.base_revision, "fingerprint": quote.fingerprint, "expires_at": aware(quote.expires_at).isoformat(), "input_hash": quote.input_hash}


def reserve(db, tenant_id, operation_key, action, units, *, quote_id=None, project_id=None, base_revision=None, input_data=None, job_id=None, actor_id=None, now=None):
    now = now or utcnow()
    if not isinstance(operation_key, str) or not 1 <= len(operation_key) <= 160:
        raise APIError(422, "IDEMPOTENCY_KEY_INVALID", "요청 식별자를 확인해 주세요.")
    wallet = ensure_trial(db, tenant_id, now=now)
    request_hash = canonical_hash(_request(action, units, project_id, base_revision, input_data))
    existing = db.scalar(select(Reservation).where(Reservation.tenant_id == tenant_id, Reservation.operation_key == operation_key))
    if existing:
        if existing.request_hash != request_hash:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "같은 요청 식별자로 다른 작업을 실행할 수 없습니다.")
        return existing
    _project_revision(db, tenant_id, project_id, base_revision)
    cost, fingerprint = _production_cost(db, tenant_id, action, input_data, _action_cost(action, units))
    if action.startswith("export.production") and not wallet.ever_paid:
        raise APIError(403, "PAID_PRODUCTION_REQUIRED", "체험 크레딧으로 제작용 출력을 할 수 없습니다.")
    if quote_id:
        quote = db.scalar(select(Quote).where(Quote.id == quote_id, Quote.tenant_id == tenant_id))
        if quote is None:
            raise APIError(404, "QUOTE_NOT_FOUND", "견적을 찾을 수 없습니다.")
        if aware(quote.expires_at) <= aware(now):
            raise APIError(409, "QUOTE_EXPIRED", "견적 유효시간 5분이 지났습니다. 새 견적을 받아 주세요.")
        if quote.input_hash != request_hash or quote.action != action or quote.units != units or quote.unit_cost != cost:
            raise APIError(409, "QUOTE_CHANGED", "견적 이후 입력이나 출력 권한이 바뀌었습니다.")
        if db.scalar(select(Reservation.id).where(Reservation.quote_id == quote_id)):
            raise APIError(409, "QUOTE_ALREADY_USED", "이미 실행한 견적입니다.")
        cost = quote.unit_cost
    expire_available(db, tenant_id, now=now)
    buckets = [bucket for bucket in db.scalars(select(CreditBucket).where(CreditBucket.tenant_id == tenant_id, CreditBucket.expires_at > now, CreditBucket.available > 0).order_by(CreditBucket.expires_at, CreditBucket.created_at, CreditBucket.id)).all() if _eligible(bucket, action)]
    if sum(bucket.available for bucket in buckets) < cost * units:
        raise APIError(402, "INSUFFICIENT_CREDITS", "다른 작업에서 사용한 크레딧을 확인해 주세요.", {"required": cost * units, "available": sum(b.available for b in buckets)})
    claim = None
    if fingerprint and cost:
        claim = db.scalar(select(ProductionEntitlement).where(ProductionEntitlement.tenant_id == tenant_id, ProductionEntitlement.fingerprint == fingerprint))
        if claim and claim.status == "pending":
            raise APIError(409, "PRODUCTION_IN_PROGRESS", "같은 제작 항목을 이미 준비하고 있습니다.")
        if claim is None:
            claim = ProductionEntitlement(tenant_id=tenant_id, fingerprint=fingerprint, identity_policy="physical-identity-v1")
            db.add(claim)
    reservation = Reservation(tenant_id=tenant_id, operation_key=operation_key, request_hash=request_hash, action=action, units=units, unit_cost=cost, total=cost * units, quote_id=quote_id, job_id=job_id, unit_status={str(i): "held" for i in range(units)}, expires_at=now + timedelta(minutes=40), created_at=now)
    db.add(reservation)
    db.flush()
    if claim is not None:
        claim.reservation_id, claim.status = reservation.id, "pending"
    for index in range(units):
        remaining = cost
        for bucket in buckets:
            if remaining <= 0:
                break
            take = min(bucket.available, remaining)
            if take <= 0:
                continue
            bucket.available -= take
            bucket.reserved += take
            allocation = Allocation(tenant_id=tenant_id, reservation_id=reservation.id, unit_index=index, bucket_id=bucket.id, amount=take)
            db.add(allocation)
            db.flush()
            _event(db, tenant_id, bucket.id, "RESERVE", take, f"reserve:{allocation.id}", "작업 성공분 차감을 위한 예약", now, operation_key=operation_key, job_id=job_id, actor_id=actor_id)
            remaining -= take
    db.add(BillingOutbox(tenant_id=tenant_id, event_key=f"reservation:{reservation.id}", kind="credits.reserved", payload={"reservation_id": reservation.id, "job_id": job_id, "action": action}, created_at=now))
    db.flush()
    return reservation


def _reservation(db, tenant_id, reservation_id, now):
    lock_wallet(db, tenant_id, now)
    reservation = db.scalar(select(Reservation).where(Reservation.id == reservation_id, Reservation.tenant_id == tenant_id).execution_options(populate_existing=True))
    if reservation is None:
        raise APIError(404, "RESERVATION_NOT_FOUND", "크레딧 예약 내역을 찾을 수 없습니다.")
    return reservation


def _unit(reservation, unit_index):
    if isinstance(unit_index, bool) or not isinstance(unit_index, int) or not 0 <= unit_index < reservation.units:
        raise APIError(422, "INVALID_UNIT_INDEX", "작업 결과 번호가 올바르지 않습니다.")
    return reservation.unit_status[str(unit_index)]


def _status(reservation):
    states = set(reservation.unit_status.values())
    reservation.status = "reserved" if "held" in states else "captured" if states == {"captured"} else "released" if states == {"released"} else "partially_captured"


def capture_unit(db, tenant_id, reservation_id, unit_index, *, job_id=None, actor_id=None, now=None):
    now = now or utcnow()
    reservation = _reservation(db, tenant_id, reservation_id, now)
    state = _unit(reservation, unit_index)
    if state != "held":
        return state == "captured"
    if aware(reservation.expires_at) <= aware(now):
        release(db, tenant_id, reservation_id, reason="예약 상한시간 초과", now=now)
        return False
    allocations = db.scalars(select(Allocation).where(Allocation.reservation_id == reservation.id, Allocation.tenant_id == tenant_id, Allocation.unit_index == unit_index)).all()
    for allocation in allocations:
        bucket = db.get(CreditBucket, allocation.bucket_id)
        if bucket.tenant_id != tenant_id or allocation.status != "held":
            raise APIError(409, "ALLOCATION_CONFLICT", "예약 정산 내역을 확인해 주세요.")
        bucket.reserved -= allocation.amount
        bucket.consumed += allocation.amount
        allocation.status = "captured"
        _event(db, tenant_id, bucket.id, "CAPTURE", allocation.amount, f"capture:{allocation.id}", "사용 가능한 작업 결과 제공", now, operation_key=reservation.operation_key, job_id=job_id or reservation.job_id, actor_id=actor_id)
    reservation.unit_status = {**reservation.unit_status, str(unit_index): "captured"}
    _status(reservation)
    entitlement = db.scalar(select(ProductionEntitlement).where(ProductionEntitlement.tenant_id == tenant_id, ProductionEntitlement.reservation_id == reservation.id))
    if entitlement and reservation.status == "captured":
        entitlement.status = "entitled"
    db.flush()
    return True


def release_unit(db, tenant_id, reservation_id, unit_index, *, reason="실패한 작업 예약 반환", now=None):
    now = now or utcnow()
    reservation = _reservation(db, tenant_id, reservation_id, now)
    state = _unit(reservation, unit_index)
    if state != "held":
        return state == "released"
    allocations = db.scalars(select(Allocation).where(Allocation.reservation_id == reservation.id, Allocation.tenant_id == tenant_id, Allocation.unit_index == unit_index)).all()
    for allocation in allocations:
        bucket = db.get(CreditBucket, allocation.bucket_id)
        if bucket.tenant_id != tenant_id or allocation.status != "held":
            raise APIError(409, "ALLOCATION_CONFLICT", "예약 반환 내역을 확인해 주세요.")
        bucket.reserved -= allocation.amount
        allocation.status = "released"
        _event(db, tenant_id, bucket.id, "RELEASE", allocation.amount, f"release:{allocation.id}", reason, now, operation_key=reservation.operation_key, job_id=reservation.job_id)
        if aware(bucket.expires_at) <= aware(now):
            bucket.expired += allocation.amount
            _event(db, tenant_id, bucket.id, "EXPIRE", allocation.amount, f"expire-released:{allocation.id}", "원래 지급분 만료 · 같은 이용 범위의 복원분 별도 지급", now, operation_key=reservation.operation_key)
            _grant(db, tenant_id, allocation.amount, "compensation", bucket.scope, max(aware(bucket.expires_at), aware(now) + timedelta(days=7)), f"restore:{allocation.id}", now, source_bucket_id=bucket.id, event="COMPENSATE", reason=reason)
        else:
            bucket.available += allocation.amount
    reservation.unit_status = {**reservation.unit_status, str(unit_index): "released"}
    _status(reservation)
    if reservation.status in {"released", "partially_captured"}:
        entitlement = db.scalar(select(ProductionEntitlement).where(ProductionEntitlement.tenant_id == tenant_id, ProductionEntitlement.reservation_id == reservation.id, ProductionEntitlement.status == "pending"))
        if entitlement:
            entitlement.status = "failed"
    db.flush()
    return True


def release(db, tenant_id, reservation_id, *, reason="작업 예약 반환", now=None):
    now = now or utcnow()
    reservation = _reservation(db, tenant_id, reservation_id, now)
    for index in range(reservation.units):
        release_unit(db, tenant_id, reservation_id, index, reason=reason, now=now)
    return reservation


def release_overdue(db, tenant_id, *, now=None):
    now = now or utcnow()
    lock_wallet(db, tenant_id, now)
    rows = db.scalars(select(Reservation).where(Reservation.tenant_id == tenant_id, Reservation.status == "reserved", Reservation.expires_at <= now)).all()
    for row in rows:
        release(db, tenant_id, row.id, reason="작업 응답 시간 초과 · 예약 복원", now=now)
    return len(rows)


def compensate_unit(db, tenant_id, reservation_id, unit_index, *, reason, now=None):
    """Restore a previously captured result that is later found inaccessible."""
    now = now or utcnow()
    reservation = _reservation(db, tenant_id, reservation_id, now)
    if _unit(reservation, unit_index) != "captured":
        raise APIError(409, "CAPTURE_REQUIRED", "확정 차감한 결과만 오류 보상할 수 있습니다.")
    if not reason or not reason.strip():
        raise APIError(422, "REASON_REQUIRED", "보상 사유를 기록해 주세요.")
    allocations = db.scalars(select(Allocation).where(Allocation.reservation_id == reservation.id, Allocation.tenant_id == tenant_id, Allocation.unit_index == unit_index)).all()
    for allocation in allocations:
        source = db.get(CreditBucket, allocation.bucket_id)
        _grant(db, tenant_id, allocation.amount, "compensation", source.scope, max(aware(source.expires_at), aware(now) + timedelta(days=7)), f"compensate:{allocation.id}", now, source_bucket_id=source.id, event="COMPENSATE", reason=reason)
    db.flush()
    return reservation


def wallet_summary(db, tenant_id, *, now=None):
    now = now or utcnow()
    ensure_trial(db, tenant_id, now=now)
    release_overdue(db, tenant_id, now=now)
    expire_available(db, tenant_id, now=now)
    buckets = db.scalars(select(CreditBucket).where(CreditBucket.tenant_id == tenant_id).order_by(CreditBucket.expires_at, CreditBucket.created_at, CreditBucket.id)).all()
    ledger = db.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == tenant_id).order_by(LedgerEntry.created_at.desc(), LedgerEntry.id).limit(100)).all()
    trial = next((bucket for bucket in buckets if bucket.kind == "trial"), None)
    # Refunded unused credits are terminal in the bucket accounting, but UI must
    # distinguish explicit adjustments from natural time-based expiry.
    adjustments = dict(db.execute(select(LedgerEntry.bucket_id, func.sum(-LedgerEntry.amount)).where(LedgerEntry.tenant_id == tenant_id, LedgerEntry.event == "ADJUSTMENT", LedgerEntry.amount < 0).group_by(LedgerEntry.bucket_id)).all())
    return {"balance": sum(b.available for b in buckets if aware(b.expires_at) > aware(now)), "available": sum(b.available for b in buckets if aware(b.expires_at) > aware(now)), "reserved": sum(b.reserved for b in buckets), "consumed": sum(b.consumed for b in buckets), "expired": sum(max(0, b.expired - adjustments.get(b.id, 0)) for b in buckets), "adjusted": sum(adjustments.values()), "trial_expires_at": aware(trial.expires_at).isoformat() if trial else None, "buckets": [{"id": b.id, "kind": b.kind, "scope": b.scope, "available": b.available if aware(b.expires_at) > aware(now) else 0, "reserved": b.reserved, "consumed": b.consumed, "expired": max(0, b.expired - adjustments.get(b.id, 0)), "adjusted": adjustments.get(b.id, 0), "expires_at": aware(b.expires_at).isoformat()} for b in buckets], "ledger": [{"id": row.id, "event": row.event, "amount": row.amount, "reason": row.reason, "created_at": aware(row.created_at).isoformat(), "job_id": row.job_id, "operation_key": row.operation_key} for row in ledger]}
