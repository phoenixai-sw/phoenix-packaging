"""Current public approval information, separate from immutable export snapshots.

Call only after authorizing the supplied jobs. Registry versions are global, so
an administrator's audit tenant need not equal the customer's tenant. Only an
explicitly public revocation reason is projected; internal audit notes stay private.
"""
from collections.abc import Sequence
from datetime import timezone

from sqlalchemy import func, select

from .database import utcnow
from .feature_models import AuditEvent, RegistryVersion
from .models import Job

REVOCATION_NOTICE = "승인이 철회되었습니다. 운영자에게 상세 확인을 요청하세요."
VERSION_KEYS = (("template", "template_version_id"), ("profile", "print_profile_version_id"))


def current_export_approvals(db, jobs: Sequence[Job]) -> dict[str, dict]:
    """At most two queries, independent of the number of authorized jobs."""
    production = [job for job in jobs if job.kind == "production_export"]
    if not production:
        return {}
    identities = {
        identity for job in production for _, key in VERSION_KEYS
        if isinstance(identity := (job.snapshot or {}).get(key), str) and identity
    }
    versions = {row.id: row for row in db.scalars(
        select(RegistryVersion).where(RegistryVersion.id.in_(identities))
    )} if identities else {}
    revoked_ids = [row.id for row in versions.values() if row.status == "revoked"]
    latest = {}
    if revoked_ids:
        ranked = select(
            AuditEvent.entity_id, AuditEvent.created_at, AuditEvent.details,
            func.row_number().over(
                partition_by=AuditEvent.entity_id,
                order_by=(AuditEvent.created_at.desc(), AuditEvent.id.desc()),
            ).label("rank"),
        ).where(AuditEvent.action == "registry_revoked", AuditEvent.entity_id.in_(revoked_ids)).subquery()
        latest = {row.entity_id: row for row in db.execute(select(ranked).where(ranked.c.rank == 1))}
    checked_at = utcnow().isoformat()
    result = {}
    for job in production:
        states = []
        for kind, key in VERSION_KEYS:
            identity = (job.snapshot or {}).get(key)
            identity = identity if isinstance(identity, str) and identity else None
            row = versions.get(identity)
            if row is not None and row.kind != kind:
                row = None
            status = row.status if row is not None else "unavailable"
            if row is not None and row.is_demo and status == "approved":
                status = "unavailable"
            audit = latest.get(identity) if status == "revoked" else None
            public_reason = None
            if status == "revoked":
                # Older notes were internal-only: never fall back to details.reason.
                explicit = (audit.details or {}).get("public_reason") if audit else None
                public_reason = explicit.strip()[:1000] if isinstance(explicit, str) and explicit.strip() else REVOCATION_NOTICE
            states.append({
                "kind": kind, "id": identity,
                "name": row.name if row is not None else ("출력 당시 도면" if kind == "template" else "출력 당시 인쇄 프로필"),
                "status": status,
                "revoked_at": (audit.created_at if audit.created_at.tzinfo else audit.created_at.replace(tzinfo=timezone.utc)).isoformat() if audit else None,
                "public_reason": public_reason,
            })
        statuses = [item["status"] for item in states]
        status = "revoked" if "revoked" in statuses else "approved" if all(value == "approved" for value in statuses) else "unavailable"
        result[job.id] = {"status": status, "checked_at": checked_at, "versions": states}
    return result
