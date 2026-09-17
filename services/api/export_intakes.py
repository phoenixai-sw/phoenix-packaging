"""Read-only intake projections, separate from immutable export results.

Callers must authorize each project/workspace before supplying job IDs. These
queries also constrain tenant, project and print-job identity. Audit
details classify a record; they are never returned as customer-facing content.
"""
from collections.abc import Sequence
from datetime import timezone
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select

from .errors import APIError
from .feature_models import AuditEvent, IntakeRecord
from .models import Job, Project
from .printer_intakes import METRICS_POLICY


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _job_scope(tenant_id, project_id, job_ids):
    query = select(Job.id).join(Project, Project.id == Job.project_id).where(
        Job.tenant_id == tenant_id, Project.tenant_id == tenant_id,
        Job.id.in_(job_ids),
        Job.kind.in_(("review_export", "production_export")),
    )
    return query.where(Job.project_id == project_id) if project_id is not None else query


def _classified(tenant_id, project_id, job_ids):
    scoped = select(IntakeRecord).join(Job, Job.id == IntakeRecord.job_id).where(
        IntakeRecord.tenant_id == tenant_id,
        IntakeRecord.project_id == Job.project_id,
        IntakeRecord.job_id.in_(_job_scope(tenant_id, project_id, job_ids)),
    ).subquery()
    details = AuditEvent.details
    # Even duplicate identical audit records are ambiguous. SQL aggregation avoids
    # transferring arbitrarily large event payloads or loading one query per job.
    audit = select(
        AuditEvent.entity_id, func.count().label("matches"),
        *[func.min(details[key].as_string()).label(key) for key in (
            "metrics_policy", "record_source", "rejection_kind", "job_id", "project_id",
        )],
    ).where(
        AuditEvent.tenant_id == tenant_id,
        AuditEvent.action == "printer_intake_recorded",
        AuditEvent.entity_id.in_(select(scoped.c.id)),
    ).group_by(AuditEvent.entity_id).subquery()
    valid = and_(
        audit.c.matches == 1, audit.c.metrics_policy == METRICS_POLICY,
        audit.c.record_source.in_(("manufacturer", "test")),
        audit.c.job_id == scoped.c.job_id, audit.c.project_id == scoped.c.project_id,
        scoped.c.status.in_(("submitted", "accepted", "rejected")),
        or_(
            and_(scoped.c.status == "rejected", audit.c.rejection_kind.in_(("technical", "aesthetic"))),
            and_(scoped.c.status != "rejected", audit.c.rejection_kind.is_(None)),
        ),
    )
    return select(
        scoped,
        case((valid, audit.c.record_source), else_="legacy").label("record_source"),
        case((valid, audit.c.rejection_kind), else_=None).label("rejection_kind"),
    ).select_from(scoped.outerjoin(audit, audit.c.entity_id == scoped.c.id)).subquery()


def batch_intake_summaries(db, *, tenant_id: str, project_id: str | None,
                           job_ids: Sequence[str]) -> dict[str, dict]:
    """Two bounded queries for up to 100 already-authorized export jobs.

    Latest status is shown except that any historical technical rejection stays
    rejected. This prevents repeated accepted notes from erasing a failure from
    the unique-export-job acceptance policy. Manufacturer and test groups never
    combine; records without exactly one valid matching audit are unclassified.
    """
    identities = list(dict.fromkeys(job_ids))
    if len(identities) > 100:
        raise APIError(422, "INVALID_INTAKE_QUERY", "출력 작업은 한 번에 100개까지 조회할 수 있습니다.")
    if not identities:
        return {}
    eligible = list(db.scalars(_job_scope(tenant_id, project_id, identities)))
    result = {
        job_id: {
            **{source: {"status": None, "last_recorded_at": None,
                        "technical_rejected": False, "record_count": 0}
               for source in ("manufacturer", "test")},
            "unclassified_count": 0, "total_count": 0,
        } for job_id in eligible
    }
    if not eligible:
        return result
    rows = _classified(tenant_id, project_id, eligible)
    ranked = select(rows, func.row_number().over(
        partition_by=(rows.c.job_id, rows.c.record_source),
        order_by=(rows.c.created_at.desc(), rows.c.id.desc()),
    ).label("rank")).subquery()
    groups = select(
        ranked.c.job_id, ranked.c.record_source,
        func.count().label("count"), func.max(ranked.c.created_at).label("latest"),
        func.max(case((ranked.c.rank == 1, ranked.c.status), else_=None)).label("status"),
        func.max(case((and_(ranked.c.status == "rejected", ranked.c.rejection_kind == "technical"), 1), else_=0)).label("technical"),
    ).group_by(ranked.c.job_id, ranked.c.record_source)
    for row in db.execute(groups):
        item = result[row.job_id]
        item["total_count"] += row.count
        if row.record_source == "legacy":
            item["unclassified_count"] += row.count
        else:
            item[row.record_source] = {
                "status": "rejected" if row.technical else row.status,
                "last_recorded_at": _iso(row.latest),
                "technical_rejected": bool(row.technical), "record_count": row.count,
            }
    return result


def list_export_intakes(db, *, tenant_id: str, project_id: str, job_id: str,
                        limit: int = 30, before: str | None = None) -> dict:
    """Keyset pagination by (created_at, id), with a scoped row-ID cursor."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise APIError(422, "INVALID_INTAKE_QUERY", "조회 개수는 1~100 사이 정수여야 합니다.")
    if db.scalar(_job_scope(tenant_id, project_id, [job_id])) is None:
        raise APIError(404, "NOT_FOUND", "출력 작업을 찾을 수 없습니다.")
    rows = _classified(tenant_id, project_id, [job_id])
    query = select(rows)
    if before is not None:
        try:
            before = str(UUID(before))
        except (ValueError, TypeError, AttributeError):
            raise APIError(422, "INVALID_INTAKE_CURSOR", "입고 기록의 이전 페이지 위치를 확인해 주세요.") from None
        anchor = db.execute(select(rows.c.created_at, rows.c.id).where(rows.c.id == before)).first()
        if anchor is None:
            raise APIError(422, "INVALID_INTAKE_CURSOR", "입고 기록의 이전 페이지 위치를 확인해 주세요.")
        query = query.where(or_(rows.c.created_at < anchor.created_at,
                               and_(rows.c.created_at == anchor.created_at, rows.c.id < anchor.id)))
    total = db.scalar(select(func.count()).select_from(rows))
    found = list(db.execute(query.order_by(rows.c.created_at.desc(), rows.c.id.desc()).limit(limit + 1)).mappings())
    items = [{
        "id": row["id"], "status": row["status"], "category": row["category"],
        "manufacturer": row["manufacturer"], "notes": row["notes"],
        "created_at": _iso(row["created_at"]), "record_source": row["record_source"],
        "rejection_kind": row["rejection_kind"], "evidence_attached": row["evidence_id"] is not None,
        "verification": {"manufacturer": "self_reported", "test": "test_record", "legacy": "unclassified"}[row["record_source"]],
    } for row in found[:limit]]
    return {"items": items, "next_cursor": items[-1]["id"] if len(found) > limit else None, "total": total}
