"""Manufacturer replies are distinct from test records and technical approval.

The immutable export job is the counting unit. Repeated status notes, resubmitted
notes, and multiple manufacturer names cannot inflate the acceptance denominator.
Metadata lives in the existing append-only audit event, so legacy rows remain
explicitly unclassified instead of being promoted into real manufacturer evidence.
"""
from collections import defaultdict

from sqlalchemy import select

from .billing.policy import pricing
from .errors import APIError
from .feature_models import AuditEvent, IntakeRecord


METRICS_POLICY = "manufacturer-intake-v1"


def intake_metadata(body, job, settings):
    if body.record_source == "manufacturer":
        conditions = job.snapshot.get("approved_conditions", {}) if job else {}
        approved = all(
            conditions.get(kind, {}).get("status") == "approved"
            and conditions.get(kind, {}).get("is_demo") is False
            for kind in ("template", "profile")
        )
        if (
            settings.demo_mode
            or not job
            or job.kind != "production_export"
            or job.status != "succeeded"
            or not job.result
            or job.result.get("review_only") is not False
            or not approved
        ):
            raise APIError(
                422,
                "MANUFACTURER_INTAKE_REQUIRES_PRODUCTION",
                "실제 제조사 결과는 완료된 승인 제작 출력에 연결해 주세요. 데모·검토 기록은 테스트로 보관할 수 있습니다.",
            )
    return {
        "metrics_policy": METRICS_POLICY,
        "pricing_policy_version": pricing()["version"],
        "record_source": body.record_source,
        "rejection_kind": body.rejection_kind,
        "project_id": str(body.project_id),
        "job_id": job.id if job else None,
        "revision_id": job.revision_id if job else None,
        "environment": settings.environment,
        "verification": "self_reported" if body.record_source == "manufacturer" else "test_record",
    }


def intake_metrics(db):
    # Join the metadata atomically saved with each record, including its tenant.
    # A missing or ambiguous metadata event is not trusted as a real reply.
    events = defaultdict(list)
    for event in db.scalars(select(AuditEvent).where(AuditEvent.action == "printer_intake_recorded")):
        events[(event.tenant_id, event.entity_id)].append(event.details)
    groups = {"manufacturer": defaultdict(list), "test": defaultdict(list)}
    excluded_legacy = 0
    excluded_unlinked = 0
    for row in db.scalars(select(IntakeRecord)):
        matches = events.get((row.tenant_id, row.id), [])
        metadata = matches[0] if len(matches) == 1 else {}
        source = metadata.get("record_source")
        kind = metadata.get("rejection_kind")
        if (
            metadata.get("metrics_policy") != METRICS_POLICY
            or source not in groups
            or row.status not in {"submitted", "accepted", "rejected"}
            or (row.status == "rejected" and kind not in {"technical", "aesthetic"})
        ):
            excluded_legacy += 1
            continue
        if not row.job_id or metadata.get("job_id") != row.job_id:
            excluded_unlinked += 1
            continue
        groups[source][(row.tenant_id, row.job_id)].append((row.status, kind))

    result = {
        "policy_version": METRICS_POLICY,
        "unit": "unique_export_job",
        "denominator": "jobs_with_manufacturer_reply",
        "verification": "self_reported_not_independently_verified",
        "excluded_legacy_records": excluded_legacy,
        "excluded_unlinked_records": excluded_unlinked,
    }
    for source, jobs in groups.items():
        responded = rejected = aesthetic = 0
        for replies in jobs.values():
            responded += any(status in {"accepted", "rejected"} for status, _ in replies)
            rejected += any(status == "rejected" and kind == "technical" for status, kind in replies)
            aesthetic += any(status == "rejected" and kind == "aesthetic" for status, kind in replies)
        passed = responded - rejected
        result[source] = {
            "total_jobs": len(jobs),
            "responded_jobs": responded,
            "pending_jobs": len(jobs) - responded,
            "technical_pass_jobs": passed,
            "technical_rejected_jobs": rejected,
            "aesthetic_change_jobs": aesthetic,
            "technical_acceptance_rate": passed / responded if responded else None,
        }
    return result
