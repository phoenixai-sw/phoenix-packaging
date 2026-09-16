"""Synthetic intake fixtures validate calculations, never real manufacturer acceptance."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from services.api.feature_models import AuditEvent, IntakeRecord
from services.api.models import Job, Revision, User
from services.api.printer_intakes import intake_metrics
from services.api.tests.test_api import project
from services.api.tests.test_business import business, paid, invitation, accept


def completed_job(app, item, **changes):
    with app.state.session_factory() as db:
        revision = db.scalar(select(Revision).where(Revision.project_id == item["id"]))
        value = {
            "tenant_id": revision.tenant_id,
            "project_id": item["id"],
            "revision_id": revision.id,
            "kind": "production_export",
            "status": "succeeded",
            "operation_key": str(uuid4()),
            "request_hash": "a" * 64,
            "snapshot": {
                "approved_conditions": {
                    key: {"status": "approved", "is_demo": False}
                    for key in ("template", "profile")
                }
            },
            "result": {"review_only": False},
        }
        value.update(changes)
        row = Job(**value)
        db.add(row)
        db.commit()
        return row.id


def record(client, item, job, status="accepted", **extra):
    return client.post("/v1/printer-intakes", json={
        "project_id": item["id"], "job_id": job, "status": status,
        "category": "file", "manufacturer": "Synthetic test manufacturer",
        "notes": "Automated fixture; not an actual manufacturer reply.", **extra,
    })


def test_intake_unique_jobs_technical_rejections_and_aesthetic_changes(business):
    app, owner, auth, _ = business
    app.state.settings.demo_mode = False
    item = project(owner)
    jobs = [completed_job(app, item) for _ in range(4)]
    replies = [
        (0, "submitted", None), (0, "submitted", None),
        (0, "accepted", None), (0, "accepted", None),
        (1, "rejected", "aesthetic"), (1, "accepted", None),
        (2, "rejected", "technical"), (2, "accepted", None),
        (3, "submitted", None),
    ]
    for index, status, kind in replies:
        response = record(owner, item, jobs[index], status,
                          record_source="manufacturer", rejection_kind=kind)
        assert response.status_code == 201, response.text
        assert response.json()["data"]["verification"] == "self_reported"
    # A deliberately marked test rejection for the same job cannot alter live figures.
    assert record(owner, item, jobs[0], "rejected", rejection_kind="technical").status_code == 201
    with app.state.session_factory() as db:
        app.state.settings.admin_emails = (auth["user"]["email"],)
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "printer_intake_recorded"))
        job = db.get(Job, jobs[0])
        assert event.details["revision_id"] == job.revision_id
        assert event.details["pricing_policy_version"]
        db.commit()
    response = owner.get("/v1/admin/overview")
    assert response.status_code == 200, response.text
    metrics = response.json()["data"]["intake_metrics"]
    assert metrics["unit"] == "unique_export_job"
    assert metrics["verification"] == "self_reported_not_independently_verified"
    assert metrics["manufacturer"] == {
        "total_jobs": 4, "responded_jobs": 3, "pending_jobs": 1,
        "technical_pass_jobs": 2, "technical_rejected_jobs": 1,
        "aesthetic_change_jobs": 1, "technical_acceptance_rate": 2 / 3,
    }
    assert metrics["test"]["responded_jobs"] == 1
    assert metrics["test"]["technical_acceptance_rate"] == 0


def test_intake_defaults_test_and_excludes_legacy_unlinked_and_ambiguous_records(business):
    app, owner, auth, _ = business
    item = project(owner)
    job_id = completed_job(app, item)
    response = record(owner, item, job_id, "submitted")
    assert response.status_code == 201, response.text
    assert response.json()["data"]["record_source"] == "test"
    ambiguous = record(owner, item, job_id)
    assert ambiguous.status_code == 201
    assert record(owner, item, None).status_code == 201
    with app.state.session_factory() as db:
        db.add(IntakeRecord(tenant_id=auth["tenant"]["id"], project_id=item["id"],
                           job_id=job_id, status="accepted", category="file",
                           manufacturer="Legacy fixture", notes="No trusted source classification"))
        event = db.scalar(select(AuditEvent).where(AuditEvent.entity_id == ambiguous.json()["data"]["id"]))
        db.add(AuditEvent(tenant_id=event.tenant_id, action=event.action,
                          entity_id=event.entity_id, details=deepcopy(event.details)))
        db.commit()
        metrics = intake_metrics(db)
    assert metrics["manufacturer"]["technical_acceptance_rate"] is None
    assert metrics["manufacturer"]["responded_jobs"] == 0
    assert metrics["test"]["pending_jobs"] == 1
    assert metrics["test"]["technical_acceptance_rate"] is None
    assert metrics["excluded_legacy_records"] == 2
    assert metrics["excluded_unlinked_records"] == 1


@pytest.mark.parametrize("change", ["demo-mode", "missing-job", "review-job", "queued", "demo-template", "review-result"])
def test_manufacturer_source_requires_completed_non_demo_production(business, change):
    app, owner, _, _ = business
    app.state.settings.demo_mode = change == "demo-mode"
    item = project(owner)
    job_id = completed_job(app, item)
    with app.state.session_factory() as db:
        job = db.get(Job, job_id)
        if change == "review-job": job.kind = "review_export"
        if change == "queued": job.status = "queued"
        if change == "demo-template":
            snapshot = deepcopy(job.snapshot)
            snapshot["approved_conditions"]["template"]["is_demo"] = True
            job.snapshot = snapshot
        if change == "review-result": job.result = {"review_only": True}
        db.commit()
    response = record(owner, item, None if change == "missing-job" else job_id,
                      record_source="manufacturer")
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "MANUFACTURER_INTAKE_REQUIRES_PRODUCTION"
    # These same records are permitted when honestly labelled as tests.
    assert record(owner, item, job_id).status_code == 201


@pytest.mark.parametrize("status,extra", [
    ("rejected", {}),
    ("rejected", {"rejection_kind": "unknown"}),
    ("accepted", {"rejection_kind": "technical"}),
    ("submitted", {"rejection_kind": "aesthetic"}),
    ("accepted", {"record_source": "verified"}),
    ("accepted", {"verification": "manufacturer_verified"}),
])
def test_intake_rejects_ambiguous_or_client_verified_classification(business, status, extra):
    _, owner, _, _ = business
    item = project(owner)
    assert record(owner, item, None, status, **extra).status_code == 422


def test_intake_enforces_tenant_job_project_viewer_and_csrf_access(business):
    app, owner, auth, member = business
    item = project(owner)
    job_id = completed_job(app, item)
    other, _ = member("other-intake@example.com")
    assert record(other, item, job_id).status_code == 404
    second = project(owner)
    mismatch = record(owner, second, job_id)
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "JOB_PROJECT_MISMATCH"
    paid(app, auth["tenant"]["id"])
    viewer, _ = member("viewer-intake@example.com")
    accept(viewer, invitation(owner, "viewer-intake@example.com", role="viewer"))
    assert record(viewer, item, job_id).status_code == 403
    owner.headers["X-CSRF-Token"] = "invalid"
    assert record(owner, item, job_id).status_code == 403
