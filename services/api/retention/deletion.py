"""Explicit owner requests only; no expiry-driven deletion of customer content.

An approved immutable object is sealed unavailable in a committed transaction
before storage I/O. This survives a process crash or a lost delete response.
Financial records, scenes, revisions and unknown objects are never removed.
"""
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace
import re

from sqlalchemy import select, update, or_
from ..billing.models import Quote
from ..billing.policy import aware
from ..database import utcnow
from ..errors import APIError
from ..feature_models import AiUnit, Brand, Evidence, Variant, UploadSession, IntakeRecord
from ..models import Asset, Job, Project, Revision
from ..storage import validate_key
from .models import DeletionRequest, RetentionHold
from .service import audit, target, lock_tenant, deletion_blockers
from .storage_lifecycle import gate, _contains, _read_known_object, font_key_referenced

EXECUTABLE_SCOPES = {"asset", "export"}
GRACE = timedelta(days=7)
MAX_BYTES = 256 * 1024 * 1024
SCAN_LIMIT = 10000


def object_state(row):
    metadata = row.metadata_json if isinstance(row, Asset) else row.result
    return (metadata or {}).get("_retention", {})


def available_asset_clause(*, include_deleting=False):
    state = Asset.metadata_json["_retention"]["state"].as_string()
    unavailable = ["deleted"] if include_deleting else ["deleting", "deleted"]
    return or_(state.is_(None), state.not_in(unavailable))


def ensure_object_available(row):
    if isinstance(row, (Asset, Job)) and object_state(row).get("state") in {"deleting", "deleted"}:
        raise APIError(410, "FILE_DELETED_BY_REQUEST", "소유자의 삭제 요청에 따라 이 파일의 접근이 종료되었습니다. 작업·결제 이력은 유지됩니다.")


def lock_asset_access(db, asset):
    """Serialize new references with the seal, including on local SQLite."""
    request = db.info.get("request")
    if request is not None and request.method not in {"GET", "HEAD", "OPTIONS"}:
        db.execute(update(Asset).where(Asset.id == asset.id).values(metadata_json=Asset.metadata_json))
        db.refresh(asset)
    ensure_object_available(asset)


def release_request_hold(db, request, actor, reason, *, now=None):
    for hold in db.scalars(select(RetentionHold).where(RetentionHold.source_id == request.id,
            RetentionHold.reason_code == "deletion_request", RetentionHold.released_at.is_(None))):
        hold.released_at, hold.released_by, hold.release_reason = now or utcnow(), actor, reason
        hold.revision += 1


def current_object(db, request, *, lock=False):
    row = target(db, request.tenant_id, request.target_kind, request.target_id)
    if lock:
        model = Asset if request.target_kind == "asset" else Job
        # UPDATE is an actual write lock in both supported databases.
        column = model.metadata_json if model is Asset else model.result
        db.execute(update(model).where(model.id == row.id).values({column: column}))
        db.refresh(row)
    return row


def object_identity(row):
    if isinstance(row, Asset):
        return row.storage_key, row.byte_size, (row.metadata_json or {}).get("sha256")
    if row.status != "succeeded" or not row.result or not row.result.get("storage_key"):
        raise APIError(409, "EXPORT_NOT_READY", "완료된 출력 파일만 삭제 예약할 수 있습니다.")
    return row.result["storage_key"], row.result.get("byte_size"), row.result.get("sha256")


def reference_blocker(db, request, row, *, now=None):
    key, _, _ = object_identity(row)
    needles = {key, row.id}
    if db.scalar(select(Evidence.id).where(Evidence.storage_key == key).limit(1)):
        return "EVIDENCE_REFERENCE"
    if font_key_referenced(db, key): return "FONT_REFERENCE"
    if isinstance(row, Job) and db.scalar(select(IntakeRecord.id).where(IntakeRecord.job_id == row.id).limit(1)):
        return "MANUFACTURER_INTAKE_REFERENCE"
    if isinstance(row, Asset):
        if db.scalar(select(Brand.id).where(Brand.logo_asset_id == row.id).limit(1)):
            return "BRAND_REFERENCE"
        if db.scalar(select(AiUnit.id).where(AiUnit.asset_id == row.id).limit(1)):
            return "AI_RESULT_REFERENCE"
    total = 0
    roots = ((Project, (Project.scene, Project.structure_snapshot)),
             (Revision, (Revision.scene, Revision.structure_snapshot)),
             (Job, (Job.snapshot, Job.result)), (Asset, (Asset.metadata_json,)),
             (Variant, (Variant.details,)), (Quote, (Quote.input_data,)))
    for model, columns in roots:
        query = select(model.id, *columns).where(model.tenant_id == request.tenant_id)
        if model is Quote: query = query.where(Quote.expires_at > (now or utcnow()))
        for values in db.execute(query):
            total += 1
            if total > SCAN_LIMIT: return "REFERENCE_SCAN_LIMIT"
            # Its own durable record remains as a tombstone and audit anchor.
            if (isinstance(row, Asset) and model is Asset or isinstance(row, Job) and model is Job) and values[0] == row.id:
                continue
            if any(_contains(value, needles) for value in values[1:]): return "ACTIVE_REFERENCE"
    # A completed upload's immutable ticket is bookkeeping, not an active root.
    if db.scalar(select(UploadSession.id).where(UploadSession.storage_key == key,
            UploadSession.status == "pending").limit(1)):
        return "UPLOAD_REFERENCE"
    return None


def prepare_deletion(db, request, storage, *, now=None):
    """Freeze a server-read identity at approval, never accept a client key/hash."""
    if request.target_kind not in EXECUTABLE_SCOPES: return
    row = current_object(db, request, lock=True)
    ensure_object_available(row)
    key, size, expected = object_identity(row)
    try:
        validate_key(key)
        if not key.startswith(request.tenant_id + "/"): raise ValueError()
        raw = storage.get_limited(key, MAX_BYTES)
    except Exception:
        raise APIError(409, "DELETION_IDENTITY_UNCERTAIN", "삭제 대상 원본을 검증하지 못했습니다. 자료를 보존하고 다시 확인해 주세요.") from None
    checksum = sha256(raw).hexdigest()
    if size is not None and size != len(raw) or expected is not None and expected != checksum:
        raise APIError(409, "DELETION_IDENTITY_CHANGED", "등록된 원본과 파일이 달라 삭제 예약을 중단했습니다.")
    request.execution_snapshot = {"version": "object-deletion-v1", "storage_key": key,
        "byte_size": len(raw), "sha256": checksum, "target_kind": request.target_kind, "target_id": request.target_id}
    request.due_at = aware(now or utcnow()) + GRACE


def _sealed_identity_matches(row, request):
    plan = request.execution_snapshot or {}
    try:
        key, size, checksum = object_identity(row)
        validate_key(key)
        return (plan.get("version") == "object-deletion-v1" and key == plan.get("storage_key")
            and key.startswith(request.tenant_id + "/")
            and plan.get("target_kind") == request.target_kind and plan.get("target_id") == request.target_id
            and isinstance(plan.get("byte_size"), int) and 0 < plan["byte_size"] <= MAX_BYTES
            and re.fullmatch(r"[0-9a-f]{64}", plan.get("sha256", "")) is not None
            and (size is None or size == plan["byte_size"]) and (checksum is None or checksum == plan["sha256"]))
    except (ValueError, TypeError, APIError):
        return False


def _mark_object(row, request, state, now):
    marker = {"state": state, "request_id": request.id, "at": now.isoformat()}
    if isinstance(row, Asset): row.metadata_json = {**(row.metadata_json or {}), "_retention": marker}
    else: row.result = {**(row.result or {}), "_retention": marker}


def process_deletion_requests(sessions, storage, settings, *, now=None, limit=5):
    now = aware(now or utcnow()); checked = deleted = 0
    # Automatic expiry alone NEVER creates a deletion request.
    with sessions() as db:
        ids = list(db.scalars(select(DeletionRequest.id).where(DeletionRequest.status.in_(["approved", "executing"]),
            DeletionRequest.due_at <= now).order_by(DeletionRequest.checked_at.asc().nulls_first(),
            DeletionRequest.created_at).limit(max(1, min(limit, 10)))))
    for identity in ids:
        with sessions() as db:
            gate(db)
            req = db.get(DeletionRequest, identity); lock_tenant(db, req.tenant_id); db.refresh(req)
            if req.status not in {"approved", "executing"} or not req.due_at or aware(req.due_at) > now: continue
            checked += 1; req.checked_at = now
            if not settings.retention_customer_delete_enabled:
                req.blocker = "CUSTOMER_DELETION_DISABLED"; db.commit(); continue
            blockers = deletion_blockers(db, req)
            if blockers:
                req.blocker = blockers[0]; db.commit(); continue
            row = current_object(db, req, lock=True)
            blocker = reference_blocker(db, req, row, now=now)
            if blocker or not _sealed_identity_matches(row, req):
                req.blocker = blocker or "OBJECT_IDENTITY_CHANGED"; db.commit(); continue
            if req.status == "approved":
                # Commit the seal before I/O. From here no new reference or
                # download can race a delete, even if this process is killed.
                req.status, req.blocker = "executing", None; req.revision += 1
                _mark_object(row, req, "deleting", now)
                audit(db, req.tenant_id, None, "deletion_execution_started", req.id,
                    {"target_kind": req.target_kind, "target_id": req.target_id, "due_at": req.due_at.isoformat()})
                db.commit()
        # Reacquire the global barrier before touching storage. A backup or
        # dispute hold added between transactions takes precedence.
        with sessions() as db:
            gate(db)
            req = db.get(DeletionRequest, identity); lock_tenant(db, req.tenant_id); db.refresh(req)
            if req.status != "executing": continue
            blockers = deletion_blockers(db, req)
            row = current_object(db, req, lock=True)
            blocker = reference_blocker(db, req, row, now=now)
            if blockers or blocker or not _sealed_identity_matches(row, req):
                req.blocker = (blockers or [blocker or "OBJECT_IDENTITY_CHANGED"])[0]; db.commit(); continue
            req.attempts += 1
            try:
                raw = _read_known_object(storage, SimpleNamespace(**req.execution_snapshot))
                if raw is not None and (len(raw) != req.execution_snapshot["byte_size"] or sha256(raw).hexdigest() != req.execution_snapshot["sha256"]):
                    req.status, req.blocker = "attention_required", "OBJECT_IDENTITY_CHANGED"
                else:
                    if raw is not None: storage.delete(req.execution_snapshot["storage_key"])
                    req.status, req.executed_at, req.updated_at, req.blocker = "executed", now, now, None
                    req.revision += 1; _mark_object(row, req, "deleted", now)
                    release_request_hold(db, req, None, "소유자 요청의 파일 삭제 실행 완료", now=now)
                    audit(db, req.tenant_id, None, "deletion_executed", req.id,
                        {"target_kind": req.target_kind, "target_id": req.target_id, "sha256": req.execution_snapshot["sha256"],
                         "bytes": req.execution_snapshot["byte_size"], "financial_records_preserved": True})
                    deleted += 1
            except ValueError:
                req.status, req.blocker = "attention_required", "OBJECT_IDENTITY_CHANGED"
            except Exception:
                req.blocker = "STORAGE_DELETE_UNCERTAIN"
                if req.attempts >= 3: req.status = "attention_required"
            if req.status == "attention_required":
                req.revision += 1
                req.updated_at = now
            db.commit()
    return {"checked": checked, "deleted": deleted}
