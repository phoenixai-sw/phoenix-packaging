"""Known write intents only. Unknown files and every published asset are excluded."""
from datetime import timedelta
from hashlib import sha256
from uuid import UUID
import httpx
import re
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from ..billing.policy import aware
from ..database import Base, utcnow
from ..errors import APIError
from ..feature_models import AiUnit, Evidence, Brand, UploadSession
from ..models import Asset, Job, Project, Revision
from ..storage import validate_key
from .models import BackupRun, BackupPin, MaintenanceGate, StorageIntent, GcCandidate
from .service import audit, lock_tenant, effective_holds, INCOMPLETE

ACTIVE_BACKUPS = {"pinning", "copying", "verifying", "attention_required"}
MIN_AGE = timedelta(hours=24)
OBSERVATION_GAP = timedelta(minutes=5)


def gate(db):
    if db.get(MaintenanceGate, 1) is None:
        try:
            with db.begin_nested():
                db.add(MaintenanceGate(id=1, version=0)); db.flush()
        except IntegrityError:
            pass
    db.execute(update(MaintenanceGate).where(MaintenanceGate.id == 1).values(version=MaintenanceGate.version + 1))


def valid_intent_key(row):
    try:
        for value in (row.tenant_id, row.job_id, row.lease_id): UUID(value)
        validate_key(row.storage_key)
        if row.unit_id:
            UUID(row.unit_id)
            return row.storage_key == f"{row.tenant_id}/ai/{row.job_id}/{row.unit_id}/{row.lease_id}.png"
        return row.storage_key in {f"{row.tenant_id}/exports/{row.job_id}/{row.lease_id}.pdf", f"{row.tenant_id}/exports/{row.job_id}/{row.lease_id}.zip"}
    except (ValueError, TypeError):
        return False


def record_write_intent(sessions, tenant_id, job_id, lease_id, key, content, *, unit_id=None):
    """Call before storage.put, outside a caller DB transaction. No file is read."""
    with sessions() as db:
        lock_tenant(db, tenant_id)
        job = db.get(Job, job_id)
        unit = db.get(AiUnit, unit_id) if unit_id else None
        if not job or job.tenant_id != tenant_id or (unit_id and (not unit or unit.job_id != job_id or unit.tenant_id != tenant_id)):
            raise APIError(409, "STORAGE_INTENT_INVALID", "작업 보관 상태를 확인할 수 없습니다.")
        owner = unit or job
        if owner.status != "running" or owner.lease_id != lease_id:
            raise APIError(409, "WORKER_LEASE_LOST", "작업 처리 권한이 만료되었습니다.")
        row = db.scalar(select(StorageIntent).where(StorageIntent.storage_key == key))
        checksum = sha256(content).hexdigest()
        if row:
            if row.sha256 != checksum or row.byte_size != len(content) or row.status != "planned":
                raise APIError(409, "STORAGE_INTENT_CONFLICT", "같은 보관 경로를 변경할 수 없습니다.")
        else:
            row = StorageIntent(tenant_id=tenant_id, job_id=job_id, unit_id=unit_id, lease_id=lease_id,
                                storage_key=key, sha256=checksum, byte_size=len(content))
            if not valid_intent_key(row): raise APIError(422, "STORAGE_INTENT_INVALID", "허용하지 않는 보관 경로입니다.")
            db.add(row)
        db.commit()
        return row.id


def mark_published(db, key):
    """Same transaction as the final asset/result and credit capture."""
    row = db.scalar(select(StorageIntent).where(StorageIntent.storage_key == key).with_for_update())
    if row is None or row.status not in {"planned", "published"}:
        raise APIError(409, "STORAGE_PUBLICATION_BLOCKED", "파일 보관 상태가 변경되어 공개하지 않았습니다.")
    row.status, row.published_at = "published", utcnow()


def begin_backup(sessions, reason):
    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 1000: raise ValueError("Backup reason is required")
    with sessions() as db:
        gate(db)
        row = BackupRun(reason=reason.strip()); db.add(row); db.flush()
        audit(db, None, None, "backup_pin_started", row.id, {"reason": row.reason})
        db.commit(); return row.id


def add_backup_pins(sessions, run_id, keys):
    with sessions() as db:
        gate(db)
        run = db.get(BackupRun, run_id)
        if run is None or run.state not in {"pinning", "copying"}: raise ValueError("Backup is not active")
        for key in set(keys):
            validate_key(key)
            if not db.scalar(select(BackupPin.id).where(BackupPin.run_id == run_id, BackupPin.storage_key == key)):
                db.add(BackupPin(run_id=run_id, storage_key=key))
        db.commit()


def finish_backup(sessions, run_id, *, verified, manifest_hash=None):
    with sessions() as db:
        gate(db)
        run = db.get(BackupRun, run_id)
        if run is None or run.state not in ACTIVE_BACKUPS: raise ValueError("Backup is not active")
        if verified and (not isinstance(manifest_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_hash)): raise ValueError("Verified manifest hash required")
        run.state = "completed" if verified else "attention_required"
        run.verified, run.manifest_hash = verified, manifest_hash
        run.finished_at = utcnow() if verified else None
        run.object_count = len(list(db.scalars(select(BackupPin.id).where(BackupPin.run_id == run_id))))
        audit(db, None, None, "backup_verified" if verified else "backup_attention_required", run.id, {"manifest_hash": manifest_hash, "objects": run.object_count})
        db.commit()


def _contains(value, needles):
    if isinstance(value, dict): return any(_contains(v, needles) for v in value.values())
    if isinstance(value, list): return any(_contains(v, needles) for v in value)
    return isinstance(value, str) and value in needles


def font_key_referenced(db, key):
    # Font records register alongside API metadata; no import cycle with the
    # font upload lifecycle. Unknown canonical font keys never qualify as an
    # intent in the first place, and this also protects an unexpected alias.
    for name in ("font_assets", "font_upload_sessions"):
        table = Base.metadata.tables.get(name)
        if table is not None and db.scalar(select(table.c.id).where(table.c.storage_key == key).limit(1)):
            return True
    return False


def _read_known_object(storage, intent):
    try:
        return storage.get_limited(intent.storage_key, max(intent.byte_size, 1))
    except FileNotFoundError:
        return None
    except httpx.HTTPStatusError as error:
        # A previous delete may have succeeded despite a lost response. A
        # permissions/network failure alone never proves the object is absent.
        if error.response.status_code in {400, 404} and hasattr(storage, "confirm_missing"):
            try:
                body = error.response.json()
                missing = isinstance(body, dict) and (body.get("code") == "NoSuchKey" or str(body.get("message", "")).lower() == "object not found")
                if missing and storage.confirm_missing(intent.storage_key): return None
            except Exception:
                pass
        raise


def protection(db, intent):
    if not valid_intent_key(intent): return "UNKNOWN_KEY"
    if intent.status == "published": return "PUBLISHED"
    job = db.get(Job, intent.job_id)
    if not job or job.tenant_id != intent.tenant_id: return "UNKNOWN_JOB"
    if db.scalar(select(BackupRun.id).where(BackupRun.state.in_(ACTIVE_BACKUPS)).limit(1)):
        # Conservative global barrier also protects a not-yet-enumerated snapshot.
        return "BACKUP_PROTECTION"
    if effective_holds(db, intent.tenant_id, project_id=job.project_id, export_id=job.id): return "PRESERVATION_HOLD"
    if db.scalar(select(Job.id).where(Job.tenant_id == intent.tenant_id, Job.status.in_(INCOMPLETE)).limit(1)): return "INCOMPLETE_WORK"
    if db.scalar(select(AiUnit.id).where(AiUnit.tenant_id == intent.tenant_id, AiUnit.status.in_(INCOMPLETE)).limit(1)): return "INCOMPLETE_WORK"
    if db.scalar(select(Asset.id).where(Asset.storage_key == intent.storage_key).limit(1)): return "ASSET_REFERENCE"
    if db.scalar(select(Evidence.id).where(Evidence.storage_key == intent.storage_key).limit(1)): return "EVIDENCE_REFERENCE"
    if db.scalar(select(UploadSession.id).where(UploadSession.storage_key == intent.storage_key).limit(1)): return "UPLOAD_REFERENCE"
    if font_key_referenced(db, intent.storage_key): return "FONT_REFERENCE"
    # Iterate tenant snapshots only; limits fail closed instead of skipping roots.
    total = 0
    for model, columns in ((Job, (Job.snapshot, Job.result)), (Project, (Project.scene,)), (Revision, (Revision.scene,))):
        for values in db.execute(select(*columns).where(model.tenant_id == intent.tenant_id)):
            total += 1
            if total > 10000: return "REFERENCE_SCAN_LIMIT"
            if any(_contains(v, {intent.storage_key}) for v in values): return "SNAPSHOT_REFERENCE"
    return None


def process_known_orphans(sessions, storage, settings, *, now=None, limit=10):
    """Does not list a bucket. Only server-recorded immutable lease keys qualify."""
    now = aware(now or utcnow()); checked = deleted = 0
    with sessions() as db:
        ids = list(db.scalars(select(StorageIntent.id).outerjoin(GcCandidate, GcCandidate.intent_id == StorageIntent.id)
            .where(StorageIntent.status != "published", StorageIntent.status != "deleted", StorageIntent.created_at <= now - MIN_AGE)
            .order_by(GcCandidate.last_seen_at.asc().nulls_first(), StorageIntent.created_at).limit(max(1, min(limit, 20)))))
    for identity in ids:
        with sessions() as db:
            gate(db)
            intent = db.get(StorageIntent, identity)
            lock_tenant(db, intent.tenant_id)
            intent = db.scalar(select(StorageIntent).where(StorageIntent.id == identity).with_for_update().execution_options(populate_existing=True))
            if intent.status in {"published", "deleted"}:
                continue
            candidate = db.scalar(select(GcCandidate).where(GcCandidate.intent_id == identity))
            if candidate is None:
                candidate = GcCandidate(intent_id=identity, first_seen_at=now, last_seen_at=now)
                db.add(candidate); db.flush()
            blocker = protection(db, intent)
            checked += 1
            if blocker:
                candidate.status, candidate.blocker, candidate.first_seen_at = "blocked", blocker, now
            elif candidate.attempts >= 3:
                candidate.status, candidate.blocker = "attention_required", "DELETE_RETRY_LIMIT"
            elif now - aware(candidate.first_seen_at) < OBSERVATION_GAP:
                candidate.status, candidate.blocker = "observed", None
            elif not getattr(settings, "storage_gc_delete_enabled", False):
                candidate.status, candidate.blocker = "ready", "AUTOMATIC_DELETION_DISABLED"
            else:
                candidate.attempts += 1
                # Hold gate+tenant locks through the bounded delete so neither a
                # backup pin nor permission/hold change can race the final check.
                try:
                    raw = _read_known_object(storage, intent)
                    if raw is not None and (len(raw) != intent.byte_size or sha256(raw).hexdigest() != intent.sha256):
                        candidate.status, candidate.blocker = "attention_required", "OBJECT_IDENTITY_CHANGED"
                    else:
                        if raw is not None:
                            storage.delete(intent.storage_key)
                        intent.status = "deleted"
                        candidate.status, candidate.blocker, candidate.deleted_at = "deleted", None, now
                        audit(db, intent.tenant_id, None, "known_orphan_deleted", intent.id,
                              {"job_id": intent.job_id, "lease_id": intent.lease_id, "sha256": intent.sha256, "bytes": intent.byte_size})
                        deleted += 1
                except ValueError as error:
                    candidate.status, candidate.blocker = ("attention_required", "OBJECT_IDENTITY_CHANGED") if str(error) == "Asset exceeds limit" else ("retry_pending", "STORAGE_DELETE_UNCERTAIN")
                except Exception:
                    candidate.status, candidate.blocker = "retry_pending", "STORAGE_DELETE_UNCERTAIN"
            candidate.last_seen_at = now
            db.commit()
    return {"checked": checked, "deleted": deleted}
