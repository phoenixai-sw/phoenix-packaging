from datetime import timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, Request, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func
from ..auth import require_auth
from ..billing.policy import aware
from ..contracts.base import Envelope, ERROR_RESPONSES, binary_responses
from ..database import utcnow
from ..errors import APIError
from ..feature_models import AuditEvent
from ..models import Asset, Job, Project, Revision
from ..storage import SupabaseStorage
from .models import RetentionHold, DeletionRequest, RetentionNotice, SupportSession, BackupRun, GcCandidate, StorageIntent
from .schemas import (TargetBody, AdminTargetBody, HoldBody, VersionReason, ReviewBody, HoldDTO,
    RequestDTO, NoticeDTO, RetentionDTO, RequestsDTO, NoticesDTO, SupportDTO, SupportProjectDTO,
    OperationsDTO, BackupResolveBody, BackupAbandonBody, BackupDTO, AuditListDTO)
from .service import target, audit, digest, lock_tenant, refresh_retention, deletion_blockers, POLICY_VERSION
from .storage_lifecycle import gate, ACTIVE_BACKUPS
from .deletion import prepare_deletion, release_request_hold, ensure_object_available, EXECUTABLE_SCOPES


def payload(row, fields):
    return {name: getattr(row, name) for name in fields}


def hold_payload(row): return payload(row, HoldDTO.model_fields)
def notice_payload(row): return {**payload(row, ("id", "due_at", "stage_days", "status", "created_at")), "channel": "in_app", "download_path": "/app/projects"}
def support_payload(row): return {**payload(row, ("id", "tenant_id", "target_kind", "target_id", "reason", "actor_id", "created_at", "expires_at", "revoked_at")), "read_only": True}
def backup_payload(row): return payload(row, BackupDTO.model_fields)
def request_payload(db, row):
    blockers = deletion_blockers(db, row) if row.status not in {"executed", "canceled", "rejected"} else []
    if row.blocker and row.blocker not in blockers: blockers.append(row.blocker)
    return {**payload(row, ("id", "tenant_id", "target_kind", "target_id", "reason", "status", "review_reason", "revision", "created_at", "updated_at", "due_at", "executed_at")),
        "blockers": blockers, "execution_supported": row.target_kind in EXECUTABLE_SCOPES,
        "planned_bytes": (row.execution_snapshot or {}).get("byte_size"),
        "cancelable": row.status in {"requested", "approved"}}


def _owner(request, db, mutate=False):
    user, session = require_auth(request, db, mutate=mutate)
    if user.role != "owner": raise APIError(403, "OWNER_REQUIRED", "조직 소유자만 보관·삭제 요청을 관리할 수 있습니다.")
    return user, session


def _admin(request, db, mutate=False):
    user, session = require_auth(request, db, mutate=mutate, authorize_write=False, enforce_membership=False)
    if not user.is_admin: raise APIError(403, "ADMIN_REQUIRED", "플랫폼 관리자 권한이 필요합니다.")
    return user, session


def _version(row, expected):
    if row.revision != expected: raise APIError(409, "REVISION_CONFLICT", "상태가 변경되었습니다. 새로고침 후 다시 확인해 주세요.")


def install_retention_routes(app, db_session):
    router = APIRouter(prefix="/v1", tags=["retention"], responses=ERROR_RESPONSES)
    storage, settings = app.state.storage, app.state.settings
    def result(request, data): return {"data": data, "request_id": request.state.request_id}

    @router.get("/retention", response_model=Envelope[RetentionDTO], response_model_exclude_unset=True)
    def overview(request: Request, db=Depends(db_session)):
        user, _ = _owner(request, db)
        account, state = refresh_retention(db, user.tenant_id)
        holds = list(db.scalars(select(RetentionHold).where(RetentionHold.tenant_id == user.tenant_id, RetentionHold.released_at.is_(None)).order_by(RetentionHold.created_at.desc()).limit(100)))
        notices = list(db.scalars(select(RetentionNotice).where(RetentionNotice.tenant_id == user.tenant_id, RetentionNotice.status != "superseded").order_by(RetentionNotice.created_at.desc()).limit(20)))
        data = {"policy_version": POLICY_VERSION, "protected_until": account.protected_until, "state": state,
                "holds": [hold_payload(r) for r in holds], "notices": [notice_payload(r) for r in notices],
                "automatic_customer_deletion_enabled": False,
                "deletion_capabilities": {"request": True, "customer_data_execution": settings.retention_customer_delete_enabled,
                    "executable_scopes": ["asset", "export"], "grace_days": 7, "known_orphan_execution": settings.storage_gc_delete_enabled}}
        db.commit(); return result(request, data)

    @router.get("/notices", response_model=Envelope[NoticesDTO], response_model_exclude_unset=True)
    def notices(request: Request, offset: int = Query(0, ge=0, le=100000), limit: int = Query(30, ge=1, le=100), db=Depends(db_session)):
        user, _ = _owner(request, db)
        rows = list(db.scalars(select(RetentionNotice).where(RetentionNotice.tenant_id == user.tenant_id).order_by(RetentionNotice.created_at.desc(), RetentionNotice.id).offset(offset).limit(limit+1)))
        return result(request, {"items": [notice_payload(r) for r in rows[:limit]], "next_offset": offset+limit if len(rows)>limit else None})

    @router.get("/retention/access-history", response_model=Envelope[AuditListDTO], response_model_exclude_unset=True)
    def access_history(request: Request, offset: int = Query(0, ge=0, le=100000), limit: int = Query(30, ge=1, le=100), db=Depends(db_session)):
        user, _ = _owner(request, db)
        rows = list(db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == user.tenant_id,
            AuditEvent.action.in_(["support_access_started", "support_access_revoked", "support_project_read", "support_asset_read", "support_export_read"]))
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id).offset(offset).limit(limit+1)))
        return result(request, {"items": [payload(r, ("id", "actor_id", "action", "entity_id", "created_at", "details")) for r in rows[:limit]],
            "next_offset": offset+limit if len(rows)>limit else None})

    @router.post("/notices/{identity}/read", response_model=Envelope[NoticeDTO], response_model_exclude_unset=True)
    def read_notice(identity: UUID, request: Request, db=Depends(db_session)):
        user, _ = _owner(request, db, True)
        row = db.scalar(select(RetentionNotice).where(RetentionNotice.id == str(identity), RetentionNotice.tenant_id == user.tenant_id).with_for_update())
        if not row: raise APIError(404, "NOT_FOUND", "알림을 찾을 수 없습니다.")
        if row.status == "unread": row.status, row.read_at = "read", utcnow()
        db.commit(); return result(request, notice_payload(row))

    @router.post("/deletion-requests", status_code=201, response_model=Envelope[RequestDTO], response_model_exclude_unset=True)
    def create_deletion(body: TargetBody, request: Request, db=Depends(db_session)):
        user, _ = _owner(request, db, True)
        key = request.headers.get("idempotency-key", "")
        if not 1 <= len(key) <= 160: raise APIError(422, "IDEMPOTENCY_KEY_REQUIRED", "삭제 요청 식별자가 필요합니다.")
        lock_tenant(db, user.tenant_id)
        fingerprint = digest(body.model_dump(mode="json"))
        existing = db.scalar(select(DeletionRequest).where(DeletionRequest.tenant_id == user.tenant_id, DeletionRequest.operation_key == key))
        if existing:
            if existing.request_hash != fingerprint: raise APIError(409, "IDEMPOTENCY_CONFLICT", "같은 식별자로 다른 삭제 요청을 보낼 수 없습니다.")
            return result(request, request_payload(db, existing))
        target(db, user.tenant_id, body.target_kind, body.target_id)
        count = db.scalar(select(func.count()).select_from(DeletionRequest).where(DeletionRequest.tenant_id == user.tenant_id, DeletionRequest.created_at > utcnow()-timedelta(days=1)))
        if count >= 20: raise APIError(429, "DELETION_REQUEST_LIMIT", "오늘 접수할 수 있는 요청 수를 초과했습니다.")
        row = DeletionRequest(tenant_id=user.tenant_id, requested_by=user.id, target_kind=body.target_kind, target_id=str(body.target_id), reason=body.reason, operation_key=key, request_hash=fingerprint)
        db.add(row); db.flush()
        db.add(RetentionHold(tenant_id=user.tenant_id, target_kind=row.target_kind, target_id=row.target_id,
            reason_code="deletion_request", reason="삭제 요청 검토가 끝날 때까지 자료를 보존합니다.", source_id=row.id, created_by=user.id))
        audit(db, user.tenant_id, user.id, "deletion_requested", row.id, {"target_kind": row.target_kind, "target_id": row.target_id, "reason": row.reason})
        db.commit(); return result(request, request_payload(db, row))

    @router.get("/deletion-requests", response_model=Envelope[RequestsDTO], response_model_exclude_unset=True)
    def deletions(request: Request, offset: int = Query(0, ge=0, le=100000), limit: int = Query(30, ge=1, le=100), db=Depends(db_session)):
        user, _ = _owner(request, db)
        rows = list(db.scalars(select(DeletionRequest).where(DeletionRequest.tenant_id == user.tenant_id).order_by(DeletionRequest.created_at.desc(), DeletionRequest.id).offset(offset).limit(limit+1)))
        return result(request, {"items": [request_payload(db, r) for r in rows[:limit]], "next_offset": offset+limit if len(rows)>limit else None})

    @router.get("/deletion-requests/{identity}", response_model=Envelope[RequestDTO], response_model_exclude_unset=True)
    def deletion_detail(identity: UUID, request: Request, db=Depends(db_session)):
        user, _ = _owner(request, db)
        row = db.scalar(select(DeletionRequest).where(DeletionRequest.id == str(identity), DeletionRequest.tenant_id == user.tenant_id))
        if not row: raise APIError(404, "NOT_FOUND", "요청을 찾을 수 없습니다.")
        return result(request, request_payload(db, row))

    @router.post("/deletion-requests/{identity}/cancel", response_model=Envelope[RequestDTO], response_model_exclude_unset=True)
    def cancel_deletion(identity: UUID, body: VersionReason, request: Request, db=Depends(db_session)):
        user, _ = _owner(request, db, True); lock_tenant(db, user.tenant_id)
        row = db.scalar(select(DeletionRequest).where(DeletionRequest.id == str(identity), DeletionRequest.tenant_id == user.tenant_id))
        if not row: raise APIError(404, "NOT_FOUND", "요청을 찾을 수 없습니다.")
        if row.status == "canceled": return result(request, request_payload(db, row))
        _version(row, body.revision)
        if row.status not in {"requested", "approved"}: raise APIError(409, "DELETION_STATE_CONFLICT", "이 요청은 취소할 수 없습니다.")
        row.status, row.updated_at, row.blocker = "canceled", utcnow(), None; row.revision += 1
        release_request_hold(db, row, user.id, body.reason)
        audit(db, user.tenant_id, user.id, "deletion_canceled", row.id, {"reason": body.reason})
        db.commit(); return result(request, request_payload(db, row))

    @router.post("/admin/deletion-requests/{identity}/review", response_model=Envelope[RequestDTO], response_model_exclude_unset=True)
    def review_deletion(identity: UUID, body: ReviewBody, request: Request, db=Depends(db_session)):
        user, _ = _admin(request, db, True)
        row = db.get(DeletionRequest, str(identity))
        if not row: raise APIError(404, "NOT_FOUND", "요청을 찾을 수 없습니다.")
        gate(db); lock_tenant(db, row.tenant_id); db.refresh(row); _version(row, body.revision)
        if row.status != "requested": raise APIError(409, "DELETION_STATE_CONFLICT", "검토 가능한 요청 상태가 아닙니다.")
        if body.decision == "approved": prepare_deletion(db, row, storage)
        row.status, row.reviewed_by, row.review_reason, row.updated_at = body.decision, user.id, body.reason, utcnow()
        row.revision += 1
        if body.decision == "rejected": release_request_hold(db, row, user.id, body.reason)
        audit(db, row.tenant_id, user.id, "deletion_reviewed", row.id, {"decision": body.decision, "reason": body.reason,
            "execution_supported": row.target_kind in EXECUTABLE_SCOPES, "due_at": row.due_at.isoformat() if row.due_at else None})
        db.commit(); return result(request, request_payload(db, row))

    @router.post("/admin/retention/holds", status_code=201, response_model=Envelope[HoldDTO], response_model_exclude_unset=True)
    def create_hold(body: HoldBody, request: Request, db=Depends(db_session)):
        user, _ = _admin(request, db, True); tenant_id = str(body.tenant_id)
        lock_tenant(db, tenant_id); target(db, tenant_id, body.target_kind, body.target_id)
        row = RetentionHold(tenant_id=tenant_id, target_kind=body.target_kind, target_id=str(body.target_id), reason_code=body.reason_code, reason=body.reason, created_by=user.id)
        db.add(row); db.flush()
        audit(db, tenant_id, user.id, "retention_hold_created", row.id, {"reason": body.reason, "reason_code": body.reason_code})
        db.commit(); return result(request, hold_payload(row))

    @router.post("/admin/deletion-requests/{identity}/retry", response_model=Envelope[RequestDTO], response_model_exclude_unset=True)
    def retry_deletion(identity: UUID, body: VersionReason, request: Request, db=Depends(db_session)):
        user, _ = _admin(request, db, True); gate(db)
        row = db.get(DeletionRequest, str(identity))
        if not row: raise APIError(404, "NOT_FOUND", "요청을 찾을 수 없습니다.")
        lock_tenant(db, row.tenant_id); db.refresh(row); _version(row, body.revision)
        if row.status != "attention_required" or not row.execution_snapshot:
            raise APIError(409, "DELETION_STATE_CONFLICT", "재확인이 필요한 실행 상태가 아닙니다.")
        row.status, row.attempts, row.blocker = "executing", 0, None
        row.revision += 1; row.updated_at = utcnow()
        audit(db, row.tenant_id, user.id, "deletion_retry_requested", row.id, {"reason": body.reason})
        db.commit(); return result(request, request_payload(db, row))

    @router.post("/admin/retention/holds/{identity}/release", response_model=Envelope[HoldDTO], response_model_exclude_unset=True)
    def release_hold(identity: UUID, body: VersionReason, request: Request, db=Depends(db_session)):
        user, _ = _admin(request, db, True); row = db.get(RetentionHold, str(identity))
        if not row: raise APIError(404, "NOT_FOUND", "보존 상태를 찾을 수 없습니다.")
        lock_tenant(db, row.tenant_id); db.refresh(row)
        if row.released_at: return result(request, hold_payload(row))
        _version(row, body.revision)
        if row.reason_code == "deletion_request": raise APIError(409, "REQUEST_HOLD_REQUIRED", "삭제 요청을 취소하거나 검토 반려하여 해제해 주세요.")
        row.released_at, row.released_by, row.release_reason = utcnow(), user.id, body.reason; row.revision += 1
        audit(db, row.tenant_id, user.id, "retention_hold_released", row.id, {"reason": body.reason})
        db.commit(); return result(request, hold_payload(row))

    @router.post("/admin/support-sessions", status_code=201, response_model=Envelope[SupportDTO], response_model_exclude_unset=True)
    def create_support(body: AdminTargetBody, request: Request, db=Depends(db_session)):
        user, session = _admin(request, db, True); tenant_id = str(body.tenant_id)
        target(db, tenant_id, body.target_kind, body.target_id)
        row = SupportSession(tenant_id=tenant_id, actor_id=user.id, login_session_id=session.id,
            target_kind=body.target_kind, target_id=str(body.target_id), reason=body.reason, expires_at=utcnow()+timedelta(minutes=30))
        db.add(row); db.flush()
        audit(db, tenant_id, user.id, "support_access_started", row.id, {"reason": body.reason, "target_kind": body.target_kind, "target_id": row.target_id})
        db.commit(); return result(request, support_payload(row))

    @router.post("/admin/support-sessions/{identity}/revoke", response_model=Envelope[SupportDTO], response_model_exclude_unset=True)
    def revoke_support(identity: UUID, body: BackupResolveBody, request: Request, db=Depends(db_session)):
        user, _ = _admin(request, db, True)
        row = db.scalar(select(SupportSession).where(SupportSession.id == str(identity)).with_for_update())
        if not row: raise APIError(404, "NOT_FOUND", "지원 접근 기록을 찾을 수 없습니다.")
        if not row.revoked_at:
            row.revoked_at = utcnow()
            audit(db, row.tenant_id, user.id, "support_access_revoked", row.id, {"reason": body.reason})
        db.commit(); return result(request, support_payload(row))

    def support_access(identity, request, db, kind, target_id):
        user, session = _admin(request, db)
        grant = db.scalar(select(SupportSession).where(SupportSession.id == str(identity)).with_for_update())
        now = utcnow()
        if not grant or grant.actor_id != user.id or grant.login_session_id != session.id or grant.revoked_at or aware(grant.expires_at) <= now:
            raise APIError(403, "SUPPORT_ACCESS_EXPIRED", "지원 접근 권한이 없거나 만료되었습니다.")
        row = target(db, grant.tenant_id, kind, target_id)
        allowed = grant.target_kind == "tenant" or (grant.target_kind == kind and grant.target_id == str(target_id))
        if grant.target_kind == "project" and kind == "export": allowed = row.project_id == grant.target_id
        if grant.target_kind == "project" and kind == "asset":
            project = target(db, grant.tenant_id, "project", grant.target_id)
            scenes = [project.scene] + list(db.scalars(select(Revision.scene).where(Revision.project_id == project.id, Revision.tenant_id == grant.tenant_id).limit(10001)))
            if len(scenes) > 10001: raise APIError(409, "SUPPORT_SCOPE_LIMIT", "자산을 직접 지정하여 지원 접근을 시작해 주세요.")
            allowed = any(any(o.get("asset_id") == str(target_id) for f in scene.get("faces", []) for o in f.get("objects", [])) for scene in scenes)
        if not allowed: raise APIError(403, "SUPPORT_SCOPE_FORBIDDEN", "지원 접근 범위 밖의 자료입니다.")
        audit(db, grant.tenant_id, user.id, "support_asset_read" if kind == "asset" else "support_export_read" if kind == "export" else "support_project_read",
              grant.id, {"reason": grant.reason, "target_kind": kind, "target_id": str(target_id)})
        # Fail closed when the audit cannot be durably committed before access.
        db.commit()
        ttl = int((aware(grant.expires_at)-utcnow()).total_seconds())
        if ttl < 1: raise APIError(403, "SUPPORT_ACCESS_EXPIRED", "지원 접근 시간이 만료되었습니다.")
        return row, min(60, ttl)

    @router.get("/admin/support-sessions/{identity}/projects/{project_id}", response_model=Envelope[SupportProjectDTO], response_model_exclude_unset=True)
    def support_project(identity: UUID, project_id: UUID, request: Request, db=Depends(db_session)):
        row, _ = support_access(identity, request, db, "project", project_id)
        return result(request, {"id": row.id, "name": row.name, "base_revision": row.base_revision, "scene": row.scene, "structure_snapshot": row.structure_snapshot})

    @router.get("/admin/support-sessions/{identity}/assets/{asset_id}/content", response_class=Response,
        responses=binary_responses("image/png", "image/jpeg", "image/webp"))
    def support_asset(identity: UUID, asset_id: UUID, request: Request, db=Depends(db_session)):
        row, ttl = support_access(identity, request, db, "asset", asset_id)
        from ..asset_reconciliation import ensure_asset_available
        ensure_asset_available(row)
        if isinstance(storage, SupabaseStorage): return RedirectResponse(storage.signed_url(row.storage_key, ttl=ttl), status_code=307)
        return Response(storage.get(row.storage_key), media_type=row.content_type, headers={"Cache-Control": "no-store"})

    @router.get("/admin/support-sessions/{identity}/exports/{job_id}/download", response_class=Response,
        responses=binary_responses("application/pdf", "application/zip"))
    def support_export(identity: UUID, job_id: UUID, request: Request, db=Depends(db_session)):
        row, ttl = support_access(identity, request, db, "export", job_id)
        ensure_object_available(row)
        if row.status != "succeeded" or not row.result or not row.result.get("storage_key"): raise APIError(409, "EXPORT_NOT_READY", "출력 파일이 준비되지 않았습니다.")
        if isinstance(storage, SupabaseStorage): return RedirectResponse(storage.signed_url(row.result["storage_key"], ttl=ttl), status_code=307)
        return Response(storage.get(row.result["storage_key"]), media_type="application/pdf" if row.kind == "review_export" and row.result.get("format") != "print_engine_zip" else "application/zip", headers={"Cache-Control": "no-store"})

    @router.post("/admin/retention/backups/{identity}/abandon", response_model=Envelope[BackupDTO], response_model_exclude_unset=True)
    def abandon_backup(identity: UUID, body: BackupAbandonBody, request: Request, db=Depends(db_session)):
        user, _ = _admin(request, db, True); gate(db)
        row = db.get(BackupRun, str(identity))
        if not row: raise APIError(404, "NOT_FOUND", "백업 기록을 찾을 수 없습니다.")
        # An operator must first stop the backup process. No TTL auto-releases it.
        crashed = row.state in {"pinning", "copying", "verifying"} and aware(row.created_at) <= utcnow()-timedelta(hours=24) and body.process_stopped
        if row.state != "attention_required" and not crashed: raise APIError(409, "BACKUP_ACTIVE", "실행 중인 백업 보호를 해제할 수 없습니다. 24시간 이상 중단된 작업은 프로세스 종료를 별도 확인해 주세요.")
        row.state, row.finished_at, row.resolved_by = "abandoned", utcnow(), user.id
        audit(db, None, user.id, "backup_abandoned", row.id, {"reason": body.reason, "process_stopped_confirmed": body.process_stopped})
        db.commit(); return result(request, backup_payload(row))

    @router.get("/admin/retention/overview", response_model=Envelope[OperationsDTO], response_model_exclude_unset=True)
    def admin_overview(request: Request, db=Depends(db_session)):
        _admin(request, db)
        requests = list(db.scalars(select(DeletionRequest).order_by(DeletionRequest.created_at.desc()).limit(100)))
        holds = list(db.scalars(select(RetentionHold).order_by(RetentionHold.created_at.desc()).limit(100)))
        sessions = list(db.scalars(select(SupportSession).order_by(SupportSession.created_at.desc()).limit(50)))
        backups = list(db.scalars(select(BackupRun).order_by(BackupRun.created_at.desc()).limit(50)))
        candidates = list(db.execute(select(GcCandidate, StorageIntent).join(StorageIntent).order_by(GcCandidate.last_seen_at.desc()).limit(100)))
        events = list(db.scalars(select(AuditEvent).where(AuditEvent.action.in_([
            "deletion_requested", "deletion_reviewed", "deletion_canceled", "retention_hold_created", "retention_hold_released",
            "support_access_started", "support_access_revoked", "support_asset_read", "support_export_read", "support_project_read",
            "backup_pin_started", "backup_verified", "backup_attention_required", "backup_abandoned", "known_orphan_deleted",
            "deletion_execution_started", "deletion_executed", "deletion_retry_requested"
        ])).order_by(AuditEvent.created_at.desc()).limit(100)))
        return result(request, {"requests": [request_payload(db,r) for r in requests], "holds": [hold_payload(r) for r in holds],
            "support_sessions": [support_payload(r) for r in sessions], "backups": [backup_payload(r) for r in backups],
            "gc_candidates": [{"id":c.id, "job_id":i.job_id, "tenant_id":i.tenant_id, "status":c.status, "blocker":c.blocker,
                "first_seen_at":c.first_seen_at, "last_seen_at":c.last_seen_at, "byte_size":i.byte_size, "attempts":c.attempts} for c,i in candidates],
            "recent_audit": [payload(r, ("id", "actor_id", "action", "entity_id", "created_at", "details")) for r in events],
            "known_orphan_execution": settings.storage_gc_delete_enabled, "customer_data_execution": settings.retention_customer_delete_enabled})

    app.include_router(router)
