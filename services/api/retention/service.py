"""Preservation is independent of paid seats, deletion approval and read access."""
from datetime import timedelta
from hashlib import sha256
import json
from sqlalchemy import select, update
from ..billing.models import CreditBucket, Subscription
from ..billing.policy import aware
from ..database import utcnow
from ..errors import APIError
from ..feature_models import AuditEvent, AiUnit
from ..models import Asset, Job, Project, Tenant
from .models import RetentionAccount, RetentionHold, RetentionNotice

POLICY_VERSION = "retention-v1"
INCOMPLETE = {"queued", "running", "waiting_provider", "validating", "reconciliation_required"}


def lock_tenant(db, tenant_id):
    if db.execute(update(Tenant).where(Tenant.id == tenant_id).values(name=Tenant.name)).rowcount != 1:
        raise APIError(404, "NOT_FOUND", "조직을 찾을 수 없습니다.")


def audit(db, tenant_id, actor_id, action, entity_id, details):
    db.add(AuditEvent(tenant_id=tenant_id, actor_id=actor_id, action=action,
                      entity_id=entity_id, details=details))


def digest(data):
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def target(db, tenant_id, kind, identity):
    model = {"tenant": Tenant, "project": Project, "asset": Asset, "export": Job}.get(kind)
    if model is None:
        raise APIError(422, "TARGET_INVALID", "지원하지 않는 대상입니다.")
    row = db.get(model, str(identity))
    if row is None or (row.id if kind == "tenant" else row.tenant_id) != tenant_id:
        raise APIError(404, "NOT_FOUND", "요청한 자료를 찾을 수 없습니다.")
    if kind == "export" and row.kind not in {"review_export", "production_export", "editable_export"}:
        raise APIError(404, "NOT_FOUND", "요청한 출력물을 찾을 수 없습니다.")
    return row


def effective_holds(db, tenant_id, *, project_id=None, asset_id=None, export_id=None):
    scopes = {("tenant", tenant_id)}
    scopes.update((kind, value) for kind, value in (("project", project_id), ("asset", asset_id), ("export", export_id)) if value)
    return [row for row in db.scalars(select(RetentionHold).where(
        RetentionHold.tenant_id == tenant_id, RetentionHold.released_at.is_(None)))
        if (row.target_kind, row.target_id) in scopes]


def deletion_blockers(db, request):
    blockers = [] if request.target_kind in {"asset", "export"} else ["SCOPE_REQUIRES_MANUAL_ERASURE_PLAN"]
    export = db.get(Job, request.target_id) if request.target_kind == "export" else None
    # The request's own hold prevents GC, but must not disguise another hold.
    scopes = {"project_id": request.target_id if request.target_kind == "project" else export.project_id if export else None,
              "asset_id": request.target_id if request.target_kind == "asset" else None,
              "export_id": request.target_id if request.target_kind == "export" else None}
    if any(h.source_id != request.id for h in effective_holds(db, request.tenant_id, **scopes)):
        blockers.append("PRESERVATION_HOLD")
    elif request.target_kind == "asset" and db.scalar(select(RetentionHold.id).where(
            RetentionHold.tenant_id == request.tenant_id, RetentionHold.target_kind == "project",
            RetentionHold.released_at.is_(None)).limit(1)):
        # Legacy unplaced uploads have no stable source-project FK. Do not
        # infer that an asset is outside a held project merely from the draft.
        blockers.append("PRESERVATION_HOLD")
    if db.scalar(select(Job.id).where(Job.tenant_id == request.tenant_id, Job.status.in_(INCOMPLETE)).limit(1)):
        blockers.append("INCOMPLETE_WORK")
    elif db.scalar(select(AiUnit.id).where(AiUnit.tenant_id == request.tenant_id, AiUnit.status.in_(INCOMPLETE)).limit(1)):
        blockers.append("INCOMPLETE_WORK")
    from .models import BackupRun
    if db.scalar(select(BackupRun.id).where(BackupRun.state.in_(["pinning", "copying", "verifying", "attention_required"])).limit(1)):
        blockers.append("BACKUP_PROTECTION")
    return blockers


def refresh_retention(db, tenant_id, *, now=None):
    now = aware(now or utcnow())
    lock_tenant(db, tenant_id)
    account = db.get(RetentionAccount, tenant_id)
    if account is None:
        account = RetentionAccount(tenant_id=tenant_id)
        db.add(account)
    sub = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    deadlines = [aware(value) for value in db.scalars(select(CreditBucket.expires_at).where(
        CreditBucket.tenant_id == tenant_id, CreditBucket.kind == "purchase"))]
    if sub and sub.paid_until:
        deadlines.append(aware(sub.paid_until) + timedelta(days=90))
    if account.protected_until:
        deadlines.append(aware(account.protected_until))
    deadline = max(deadlines) if deadlines else None
    account.protected_until, account.updated_at = deadline, now
    account.policy_version = POLICY_VERSION
    active = bool(sub and sub.paid_until and aware(sub.paid_until) > now)
    state = "active" if active else "unspecified" if deadline is None else "expired_retained" if deadline <= now else "protected"
    if deadline:
        for notice in db.scalars(select(RetentionNotice).where(RetentionNotice.tenant_id == tenant_id, RetentionNotice.status != "superseded")):
            if aware(notice.due_at) != deadline:
                notice.status = "superseded"
        remaining = (deadline - now).total_seconds() / 86400
        stage = next((days for days in (0, 1, 7, 30) if remaining <= days), None)
        if stage is not None:
            key = f"{POLICY_VERSION}:{deadline.isoformat()}:{stage}"
            if not db.scalar(select(RetentionNotice.id).where(RetentionNotice.tenant_id == tenant_id, RetentionNotice.event_key == key)):
                db.add(RetentionNotice(tenant_id=tenant_id, event_key=key, due_at=deadline, stage_days=stage, created_at=now))
    db.flush()
    return account, state


def refresh_notices(sessions, *, now=None, limit=50):
    now = aware(now or utcnow())
    # Oldest account first also permits newly created tenants to enter the scan.
    with sessions() as db:
        ids = list(db.scalars(select(Tenant.id).outerjoin(RetentionAccount, RetentionAccount.tenant_id == Tenant.id)
                             .order_by(RetentionAccount.updated_at.asc().nulls_first(), Tenant.id).limit(max(1, min(limit, 100)))))
    for tenant_id in ids:
        with sessions() as db:
            refresh_retention(db, tenant_id, now=now)
            db.commit()
    return len(ids)
