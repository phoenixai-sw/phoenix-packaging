"""Synthetic intake readback; never claims independent manufacturer verification."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from services.api.database import utcnow
from services.api.errors import APIError
from services.api.export_intakes import _classified, batch_intake_summaries, list_export_intakes
from services.api.feature_models import AuditEvent, Evidence, IntakeRecord
from services.api.models import Job, Revision, Tenant
from services.api.printer_intakes import METRICS_POLICY, intake_metrics
from services.api.tests.test_api import app, client, project, register


@pytest.fixture
def intake_db(app, client):
    auth = register(client)
    item = project(client)
    with app.state.session_factory() as db:
        revision = db.scalar(select(Revision).where(Revision.project_id == item["id"]))
        job = Job(tenant_id=auth["tenant"]["id"], project_id=item["id"], revision_id=revision.id,
                  kind="production_export", status="succeeded", operation_key=str(uuid4()),
                  request_hash="a" * 64, snapshot={"private": "snapshot unchanged"},
                  result={"review_only": False, "private": "result unchanged"})
        db.add(job)
        db.commit()
        yield db, job, auth


def add(db, job, *, source="manufacturer", status="accepted", kind=None,
        at=None, metadata=None, duplicate=False, evidence_id=None):
    row = IntakeRecord(tenant_id=job.tenant_id, project_id=job.project_id, job_id=job.id,
                       status=status, category="file", manufacturer="Synthetic manufacturer",
                       notes="Fixture response, not independently verified.", evidence_id=evidence_id,
                       created_at=at or utcnow())
    db.add(row)
    db.flush()
    if source is not None:
        details = {"metrics_policy": METRICS_POLICY, "record_source": source,
                   "rejection_kind": kind, "job_id": job.id, "project_id": job.project_id,
                   "revision_id": job.revision_id, "private_reason": "do not expose",
                   **(metadata or {})}
        for _ in range(2 if duplicate else 1):
            db.add(AuditEvent(tenant_id=job.tenant_id, action="printer_intake_recorded",
                              entity_id=row.id, details=details))
    db.flush()
    return row


def summary(db, job):
    return batch_intake_summaries(db, tenant_id=job.tenant_id,
                                  project_id=job.project_id, job_ids=[job.id])[job.id]


def page(db, job, **kwargs):
    return list_export_intakes(db, tenant_id=job.tenant_id,
                               project_id=job.project_id, job_id=job.id, **kwargs)


def test_all_history_sticky_technical_reject_separates_test_and_is_read_only(intake_db):
    db, job, _ = intake_db
    original = deepcopy(job.result), deepcopy(job.snapshot)
    old = utcnow() - timedelta(days=10)
    add(db, job, status="rejected", kind="technical", at=old)
    for index in range(120):
        add(db, job, at=old + timedelta(hours=index + 1))
    add(db, job, source="test")
    db.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        result = summary(db, job)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert result["manufacturer"]["status"] == "rejected"
    assert result["manufacturer"]["technical_rejected"] is True
    assert result["manufacturer"]["record_count"] == 121
    assert result["test"]["status"] == "accepted" and result["test"]["technical_rejected"] is False
    assert result["total_count"] == 122
    assert len(statements) == 2 and all(sql.lstrip().startswith("SELECT") for sql in statements)
    assert intake_metrics(db)["manufacturer"]["technical_rejected_jobs"] == 1
    assert len(page(db, job)["items"]) == 30
    db.refresh(job)
    assert (job.result, job.snapshot) == original


@pytest.mark.parametrize("kwargs", [
    {"source": None}, {"duplicate": True}, {"metadata": {"metrics_policy": "unknown"}},
    {"metadata": {"job_id": str(uuid4())}}, {"metadata": {"project_id": str(uuid4())}},
    {"source": "external_verified"}, {"status": "rejected", "kind": None},
    {"status": "accepted", "kind": "technical"},
])
def test_missing_ambiguous_or_mismatched_audit_is_unclassified(intake_db, kwargs):
    db, job, _ = intake_db
    add(db, job, **kwargs)
    result = summary(db, job)
    assert result["unclassified_count"] == 1
    assert result["manufacturer"]["record_count"] == result["test"]["record_count"] == 0
    record = page(db, job)["items"][0]
    assert record["record_source"] == "legacy" and record["verification"] == "unclassified"
    assert record["rejection_kind"] is None
    assert "private_reason" not in str(record) and "actor_id" not in record


def test_tied_timestamp_keyset_paging_no_loss_and_foreign_cursor_rejected(intake_db):
    db, job, _ = intake_db
    now = utcnow()
    ids = [add(db, job, at=now).id for _ in range(7)]
    collected = []
    cursor = None
    while True:
        found = page(db, job, limit=2, before=cursor)
        assert found["total"] == 7
        collected.extend(row["id"] for row in found["items"])
        cursor = found["next_cursor"]
        if cursor is None:
            break
    assert collected == sorted(ids, reverse=True)
    for invalid in ("not-a-uuid", str(uuid4())):
        with pytest.raises(APIError) as error:
            page(db, job, before=invalid)
        assert error.value.code == "INVALID_INTAKE_CURSOR"


def test_tenant_project_and_job_kind_are_rechecked_before_projection(intake_db):
    db, job, _ = intake_db
    row = add(db, job)
    other = Tenant(name="other")
    db.add(other)
    db.flush()
    for args in ({"tenant_id": other.id, "project_id": job.project_id},
                 {"tenant_id": job.tenant_id, "project_id": str(uuid4())}):
        assert batch_intake_summaries(db, **args, job_ids=[job.id]) == {}
        with pytest.raises(APIError) as error:
            list_export_intakes(db, **args, job_id=job.id)
        assert error.value.status == 404
    # A foreign tenant audit with the same entity ID must not make ours ambiguous.
    db.add(AuditEvent(tenant_id=other.id, action="printer_intake_recorded", entity_id=row.id,
                      details={"record_source": "test"}))
    db.flush()
    assert summary(db, job)["manufacturer"]["record_count"] == 1
    for kind, status in (("ai_generate", "succeeded"), ("editable_export", "succeeded")):
        job.kind, job.status = kind, status
        db.flush()
        assert batch_intake_summaries(db, tenant_id=job.tenant_id, project_id=job.project_id,
                                      job_ids=[job.id]) == {}
    # File failure/loss later must never erase an already-recorded intake history.
    job.kind, job.status = "review_export", "failed"
    db.flush()
    assert job.id in batch_intake_summaries(db, tenant_id=job.tenant_id, project_id=None,
                                           job_ids=[job.id])


def test_evidence_indicator_does_not_grant_external_verification(intake_db):
    db, job, auth = intake_db
    evidence = Evidence(tenant_id=job.tenant_id, uploaded_by=auth["user"]["id"],
                        storage_key=f"{job.tenant_id}/fixture", name="private proof",
                        sha256="a" * 64, content_type="application/pdf", byte_size=10)
    db.add(evidence)
    db.flush()
    add(db, job, source="test", status="rejected", kind="aesthetic", evidence_id=evidence.id)
    record = page(db, job)["items"][0]
    assert record["evidence_attached"] is True
    assert record["verification"] == "test_record"
    assert "evidence_id" not in record and "storage_key" not in str(record)
    assert summary(db, job)["test"]["technical_rejected"] is False


def test_batch_has_no_per_job_queries_and_cursor_cannot_cross_jobs(intake_db):
    db, job, _ = intake_db
    identities = [job.id]
    foreign_cursor = None
    for index in range(24):
        other = Job(tenant_id=job.tenant_id, project_id=job.project_id, revision_id=job.revision_id,
                    kind="review_export", status="succeeded", operation_key=str(uuid4()),
                    request_hash="b" * 64, snapshot={}, result={"review_only": True})
        db.add(other)
        db.flush()
        identities.append(other.id)
        foreign_cursor = add(db, other, source="test").id
    db.commit()
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        found = batch_intake_summaries(db, tenant_id=job.tenant_id, project_id=None,
                                       job_ids=identities)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert len(statements) == 2 and len(found) == 25
    assert found[job.id]["total_count"] == 0
    assert all(found[identity]["test"]["record_count"] == 1 for identity in identities[1:])
    with pytest.raises(APIError) as error:
        page(db, job, before=foreign_cursor)
    assert error.value.code == "INVALID_INTAKE_CURSOR"


def test_query_bounds_empty_batch_and_postgres_sql(intake_db):
    db, job, _ = intake_db
    for limit in (0, 101, True, "30"):
        with pytest.raises(APIError):
            page(db, job, limit=limit)
    assert batch_intake_summaries(db, tenant_id=job.tenant_id, project_id=None, job_ids=[]) == {}
    with pytest.raises(APIError):
        batch_intake_summaries(db, tenant_id=job.tenant_id, project_id=None,
                               job_ids=[str(uuid4()) for _ in range(101)])
    compiled = str(select(_classified(job.tenant_id, job.project_id, [job.id])).compile(dialect=postgresql.dialect()))
    assert "->>" in compiled and "SELECT" in compiled
