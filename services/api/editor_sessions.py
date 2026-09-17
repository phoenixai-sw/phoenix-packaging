"""Short edit leases and immutable whole-scene history, scoped to a login and tab.

Legacy clients can use CAS without a lease only while no active editor owns the
project. A stale supplied token never silently falls back to legacy writes.
"""
from .contracts.base import Envelope, ERROR_RESPONSES
from .contracts import core as C
from copy import deepcopy
from datetime import timedelta
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import defer

from .auth import COOKIE_NAME, hash_token, require_auth
from .billing.policy import aware
from .billing.service import lock_wallet
from .business import enforce_item_access, owned_record, validate_project_links
from .database import new_id, utcnow
from .errors import APIError
from .feature_models import AuditEvent
from .models import LoginSession, Project, ProjectEditLease, Revision, User
from .schemas import Scene

LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30


class EditorIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    editor_id: UUID


class EditorProof(EditorIdentity):
    lease_token: UUID


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=1, strict=True)


def _lock_project(db, project):
    # Wallet -> project matches paid job publication and member authorization.
    # A write lock also serializes SQLite, where FOR UPDATE is ignored. Never
    # lock project then wallet: a worker can hold wallet while awaiting project.
    lock_wallet(db, project.tenant_id)
    db.execute(update(Project).where(Project.id == project.id, Project.tenant_id == project.tenant_id)
               .values(base_revision=Project.base_revision).execution_options(synchronize_session=False))
    db.refresh(project)
    enforce_item_access(db, project)


def _active(db, lease, now):
    if lease is None or aware(lease.expires_at) <= now:
        return False
    session = db.get(LoginSession, lease.login_session_id)
    return bool(session and session.user_id == lease.user_id and aware(session.expires_at) > now)


def _session(db, request):
    token = request.cookies.get(COOKIE_NAME, "")
    return db.scalar(select(LoginSession).where(LoginSession.token_hash == hash_token(token))) if token else None


def _state(db, lease, user, session, editor_id=None, *, token=False, now=None):
    now = now or utcnow()
    active = _active(db, lease, now)
    mine = bool(active and session and lease.login_session_id == session.id and lease.user_id == user.id
                and editor_id is not None and lease.editor_id == str(editor_id))
    holder = db.get(User, lease.user_id) if active else None
    result = {"status": "active" if active else "available",
              "editable": user.role in {"owner", "editor"} and (not active or mine),
              "holder": {"name": holder.name if holder else "다른 편집자", "is_self": mine} if active else None,
              "expires_at": aware(lease.expires_at).isoformat() if active else None,
              "heartbeat_seconds": HEARTBEAT_SECONDS, "lease_seconds": LEASE_SECONDS}
    if mine and token:
        result["lease_token"] = lease.lease_token
    return result


def _held(db, lease, now):
    holder = db.get(User, lease.user_id)
    raise APIError(423, "EDIT_LEASE_HELD", "다른 편집 화면에서 작업 중입니다. 읽기 전용으로 확인하거나 편집 종료 후 다시 시도해 주세요.",
                   {"edit_session": {"holder": {"name": holder.name if holder else "다른 편집자"},
                                     "expires_at": aware(lease.expires_at).isoformat()}}, retryable=True)


def enforce_edit_lease(db, project, request=None):
    """Call after ACL authorization and before any edit or new paid job.

    Holds the same DB project lock used by claim/renew/release until the caller's
    commit. Queued workers deliberately do not use browser leases.
    """
    request = request or db.info.get("request")
    if request is None:
        raise RuntimeError("An authenticated HTTP request is required for editor lease checks")
    _lock_project(db, project)
    now = utcnow()
    lease = db.get(ProjectEditLease, project.id, populate_existing=True)
    supplied = request.headers.get("x-editor-lease", "")
    if not _active(db, lease, now):
        if supplied:
            raise APIError(423, "EDIT_LEASE_EXPIRED", "편집 권한이 만료되었습니다. 최신 장면을 확인하고 편집 권한을 다시 받아 주세요.", retryable=True)
        return  # Existing clients retain their revision-CAS behavior.
    session = _session(db, request)
    principal = db.info.get("principal")
    if (session is None or principal is None or lease.login_session_id != session.id or lease.user_id != principal.id
            or not supplied or not supplied.isascii() or not hmac.compare_digest(supplied, lease.lease_token)):
        _held(db, lease, now)


def revision_payload(row, *, include_scene=True):
    return {"id": row.id, "project_id": row.project_id, "number": row.number,
            "reason": row.reason, "created_at": aware(row.created_at).isoformat(),
            **({"scene": row.scene} if include_scene else {})}


def install_editor_routes(app, db_session, project_payload, snapshot_revision, normalize_scene):
    router = APIRouter(prefix="/v1/projects", tags=["editor sessions and history"])

    def result(request, data):
        return {"data": data, "request_id": request.state.request_id}

    @router.get("/{project_id}/edit-session", response_model=Envelope[C.EditorLease], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def status(project_id: UUID, request: Request, editor_id: UUID | None = None, db=Depends(db_session)):
        user, session = require_auth(request, db)
        project = owned_record(db, Project, project_id, user.tenant_id)
        return result(request, _state(db, db.get(ProjectEditLease, project.id), user, session, editor_id))

    @router.post("/{project_id}/edit-session", response_model=Envelope[C.EditorLease], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def acquire(project_id: UUID, body: EditorIdentity, request: Request, db=Depends(db_session)):
        user, session = require_auth(request, db, mutate=True)
        project = owned_record(db, Project, project_id, user.tenant_id)
        _lock_project(db, project)
        lease = db.get(ProjectEditLease, project.id, populate_existing=True)
        now = utcnow()
        if _active(db, lease, now):
            if lease.login_session_id != session.id or lease.editor_id != str(body.editor_id) or lease.user_id != user.id:
                _held(db, lease, now)
        else:
            if lease is None:
                lease = ProjectEditLease(project_id=project.id, tenant_id=user.tenant_id)
                db.add(lease)
            lease.user_id, lease.login_session_id = user.id, session.id
            lease.editor_id, lease.lease_token = str(body.editor_id), new_id()
        lease.expires_at, lease.updated_at = now + timedelta(seconds=LEASE_SECONDS), now
        db.commit()
        return result(request, _state(db, lease, user, session, body.editor_id, token=True, now=now))

    def prove(db, project, user, session, body):
        _lock_project(db, project)
        lease = db.get(ProjectEditLease, project.id, populate_existing=True)
        now = utcnow()
        if not _active(db, lease, now):
            return lease, now, False
        if lease.user_id != user.id or lease.login_session_id != session.id or lease.editor_id != str(body.editor_id) or not hmac.compare_digest(lease.lease_token, str(body.lease_token)):
            _held(db, lease, now)
        return lease, now, True

    @router.patch("/{project_id}/edit-session", response_model=Envelope[C.EditorLease], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def heartbeat(project_id: UUID, body: EditorProof, request: Request, db=Depends(db_session)):
        user, session = require_auth(request, db, mutate=True)
        project = owned_record(db, Project, project_id, user.tenant_id)
        lease, now, active = prove(db, project, user, session, body)
        if not active:
            raise APIError(423, "EDIT_LEASE_EXPIRED", "편집 권한이 만료되었습니다. 최신 장면을 확인하고 다시 시작해 주세요.", retryable=True)
        lease.expires_at, lease.updated_at = now + timedelta(seconds=LEASE_SECONDS), now
        db.commit()
        return result(request, _state(db, lease, user, session, body.editor_id, token=True, now=now))

    @router.delete("/{project_id}/edit-session", response_model=Envelope[C.EditorLease], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def release(project_id: UUID, body: EditorProof, request: Request, db=Depends(db_session)):
        user, session = require_auth(request, db, mutate=True, authorize_write=False)
        project = owned_record(db, Project, project_id, user.tenant_id)
        lease, now, active = prove(db, project, user, session, body)
        if active:
            lease.expires_at, lease.updated_at = now, now
        db.commit()
        return result(request, _state(db, lease, user, session, body.editor_id, now=now))

    @router.get("/{project_id}/revisions", response_model=Envelope[C.RevisionsData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def revisions(project_id: UUID, request: Request, limit: int = Query(default=100, ge=1, le=100),
                  before_number: int | None = Query(default=None, ge=1, le=2_147_483_647), include_scene: bool = True, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        project = owned_record(db, Project, project_id, user.tenant_id)
        scope = (Revision.project_id == project.id, Revision.tenant_id == user.tenant_id)
        query = select(Revision).where(*scope)
        if not include_scene:
            query = query.options(defer(Revision.scene))
        if before_number is not None:
            query = query.where(Revision.number < before_number)
        rows = list(db.scalars(query.order_by(Revision.number.desc()).limit(limit + 1)))
        more, rows = len(rows) > limit, rows[:limit]
        return result(request, {"items": [revision_payload(row, include_scene=include_scene) for row in rows], "has_more": more,
                                "next_before_number": rows[-1].number if more else None,
                                "total": db.scalar(select(func.count()).select_from(Revision).where(*scope)),
                                "current_revision": project.base_revision})

    def history(db, project, identity, tenant):
        row = db.scalar(select(Revision).where(Revision.id == str(identity), Revision.project_id == project.id, Revision.tenant_id == tenant))
        if row is None:
            raise APIError(404, "REVISION_NOT_FOUND", "프로젝트의 저장 이력을 찾을 수 없습니다.")
        return row

    @router.get("/{project_id}/revisions/{revision_id}", response_model=Envelope[C.RevisionData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def detail(project_id: UUID, revision_id: UUID, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        project = owned_record(db, Project, project_id, user.tenant_id)
        return result(request, revision_payload(history(db, project, revision_id, user.tenant_id)))

    @router.post("/{project_id}/revisions/{revision_id}/restore", response_model=Envelope[C.RestoredProject], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def restore(project_id: UUID, revision_id: UUID, body: RestoreBody, request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db, mutate=True)
        project = owned_record(db, Project, project_id, user.tenant_id)
        enforce_edit_lease(db, project, request)
        if project.base_revision != body.base_revision:
            raise APIError(409, "REVISION_CONFLICT", "다른 저장 내용이 있습니다. 최신 장면을 확인한 뒤 복원해 주세요.", {"base_revision": {"server_revision": project.base_revision}})
        source = history(db, project, revision_id, user.tenant_id)
        raw = deepcopy(source.scene)
        # Old approval/review checkboxes cannot authorize a newly restored scene.
        raw["confirmed_fields"], raw["reviewed_face_ids"] = [], []
        raw["workspace_id"] = project.workspace_id
        links = {key: raw.get(key) for key in ("brand_id", "product_variant_id")}
        validate_project_links(db, user, {**links, "workspace_id": project.workspace_id})
        try:
            scene = normalize_scene(db, user, project, Scene.model_validate(raw), links=links, restoring=True)
        except ValidationError:
            raise APIError(422, "REVISION_SCENE_INVALID", "이 저장 이력은 현재 장면 규격에 맞지 않아 자동 복원할 수 없습니다. 원본 이력은 보존됩니다.") from None
        previous = snapshot_revision(db, project, "before_restore")
        changed = db.execute(update(Project).where(Project.id == project.id, Project.tenant_id == user.tenant_id, Project.base_revision == body.base_revision)
                             .values(scene=scene, base_revision=body.base_revision + 1, updated_at=utcnow(), **links).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise APIError(409, "REVISION_CONFLICT", "프로젝트가 변경되었습니다. 최신 내용을 확인해 주세요.")
        db.refresh(project)
        restored = snapshot_revision(db, project, f"restore:{source.number}")
        db.add(AuditEvent(tenant_id=user.tenant_id, actor_id=user.id, action="revision_restored", entity_id=project.id,
                          details={"source_revision_id": source.id, "previous_revision_id": previous.id, "revision_id": restored.id}))
        db.commit()
        return result(request, {**project_payload(project), "restored_from_revision": {"id": source.id, "number": source.number}})

    app.include_router(router)
