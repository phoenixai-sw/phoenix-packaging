"""Read-only workspace summaries and reusable images with ACLs applied before pagination."""
from .contracts.base import Envelope, ERROR_RESPONSES
from .contracts import core as C
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from .auth import require_auth
from .business import owned_record
from .retention.deletion import available_asset_clause
from .feature_models import WorkspaceMember
from .models import Asset, Job, Project, Revision


def visible_workspace(db, user, column):
    if user.role == "owner":
        return True
    spaces = select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.tenant_id == user.tenant_id, WorkspaceMember.user_id == user.id)
    return or_(column.is_(None), column.in_(spaces))


def install_workspace_views(app, db_session, asset_payload):
    router = APIRouter(prefix="/v1")

    @router.get("/workspace/overview", response_model=Envelope[C.WorkspaceOverview], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def overview(request: Request, db=Depends(db_session)):
        user, _ = require_auth(request, db)
        scope = [Project.tenant_id == user.tenant_id, visible_workspace(db, user, Project.workspace_id)]
        counts = db.execute(select(Job.kind, Job.status, func.count(Job.id)).join(Project, Job.project_id == Project.id)
                            .where(*scope, Job.tenant_id == user.tenant_id).group_by(Job.kind, Job.status)).all()
        jobs = db.execute(select(Job, Project.name, Revision.number).join(Project, Job.project_id == Project.id)
                          .join(Revision, Job.revision_id == Revision.id)
                          .where(*scope, Job.tenant_id == user.tenant_id).order_by(Job.updated_at.desc(), Job.id.desc()).limit(16)).all()
        revisions = db.execute(select(Revision, Project.name).join(Project, Revision.project_id == Project.id)
                               .where(*scope, Revision.tenant_id == user.tenant_id)
                               .order_by(Revision.created_at.desc(), Revision.id.desc()).limit(16)).all()
        timeline = [{"id": j.id, "type": j.kind, "project_id": j.project_id, "project_name": name,
                     "revision": number, "status": j.status, "at": j.updated_at.isoformat()} for j, name, number in jobs]
        timeline += [{"id": r.id, "type": "revision", "project_id": r.project_id, "project_name": name,
                      "revision": r.number, "status": "saved", "reason": r.reason, "at": r.created_at.isoformat()} for r, name in revisions]
        timeline.sort(key=lambda item: item["at"], reverse=True)
        return {"data": {"project_count": db.scalar(select(func.count(Project.id)).where(*scope)),
                         "job_counts": [{"kind": k, "status": s, "count": c} for k, s, c in counts],
                         "timeline": timeline[:20]}, "request_id": request.state.request_id}

    @router.get("/assets", response_model=Envelope[C.AssetsData], response_model_exclude_unset=True, responses=ERROR_RESPONSES)
    def assets(request: Request, q: str = Query("", max_length=160),
               source: str | None = Query(None, pattern="^(upload|openai|fixture|image_quality|svg_import)$"),
               project_id: UUID | None = None, offset: int = Query(0, ge=0, le=100000),
               limit: int = Query(24, ge=1, le=60), db=Depends(db_session)):
        user, _ = require_auth(request, db)
        if project_id:
            owned_record(db, Project, project_id, user.tenant_id)
        scope = [Asset.tenant_id == user.tenant_id, visible_workspace(db, user, Asset.workspace_id), available_asset_clause(), Asset.source != "sanitized_svg"]
        if q.strip():
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            scope.append(Asset.original_name.ilike(f"%{escaped}%", escape="\\"))
        if source:
            scope.append(Asset.source.in_(("upload","svg_import")) if source=="upload" else Asset.source == source)
        rows = db.scalars(select(Asset).where(*scope).order_by(Asset.created_at.desc(), Asset.id.desc())
                          .offset(offset).limit(limit + 1)).all()
        return {"data": {"items": [{**asset_payload(a), "created_at": a.created_at.isoformat()} for a in rows[:limit]],
                         "next_offset": offset + limit if len(rows) > limit else None}, "request_id": request.state.request_id}

    app.include_router(router)
