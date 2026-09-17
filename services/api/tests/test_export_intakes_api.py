"""Customer history is an ACL-protected projection, not manufacturer verification."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from services.api.feature_models import AuditEvent
from services.api.jobs import process_pending_jobs
from services.api.models import Job, Project
from services.api.tests.test_api import project
from services.api.tests.test_business import business, paid, invitation, accept, workspace
from services.api.tests.test_printer_intakes import completed_job, record


def test_real_review_output_summary_and_history_agree_without_changing_frozen_result(business):
    app, owner, auth, _ = business
    item = project(owner)
    response = owner.post('/v1/exports', json={'project_id': item['id'], 'base_revision': 1})
    assert response.status_code == 202, response.text
    job_id = response.json()['data']['id']
    assert process_pending_jobs(app.state.session_factory, app.state.storage) == 1
    with app.state.session_factory() as db:
        job = db.get(Job, job_id)
        assert job.status == 'succeeded', job.error
        frozen_result, frozen_snapshot = deepcopy(job.result), deepcopy(job.snapshot)
        original_file = app.state.storage.get(job.result['storage_key'])
    assert record(owner, item, job_id, 'rejected', rejection_kind='technical').status_code == 201
    assert record(owner, item, job_id, 'accepted').status_code == 201
    detail = owner.get(f'/v1/jobs/{job_id}')
    listing = owner.get(f'/v1/projects/{item["id"]}/exports')
    history = owner.get(f'/v1/jobs/{job_id}/printer-intakes')
    assert detail.status_code == listing.status_code == history.status_code == 200
    summary = detail.json()['data']['current_intake']
    assert summary == listing.json()['data']['items'][0]['current_intake']
    assert summary['manufacturer']['status'] is None
    assert summary['test']['status'] == 'rejected' and summary['test']['technical_rejected']
    assert summary['total_count'] == history.json()['data']['total'] == 2
    assert all(row['record_source'] == 'test' and row['verification'] == 'test_record' for row in history.json()['data']['items'])
    assert detail.json()['data']['status'] == 'succeeded'
    with app.state.session_factory() as db:
        job = db.get(Job, job_id)
        assert job.result == frozen_result and job.snapshot == frozen_snapshot
        assert app.state.storage.get(job.result['storage_key']) == original_file


def test_self_recorded_manufacturer_history_is_paginated_and_survives_later_file_loss(business):
    app, owner, _, _ = business
    app.state.settings.demo_mode = False
    item = project(owner)
    job_id = completed_job(app, item)
    for status, extra in [('submitted', {}), ('rejected', {'rejection_kind': 'technical'}), ('accepted', {})]:
        assert record(owner, item, job_id, status, record_source='manufacturer', **extra).status_code == 201
    with app.state.session_factory() as db:
        job = db.get(Job, job_id)
        job.status = 'unavailable'
        # Private audit data is not customer history, even in the same tenant.
        event = db.scalar(select(AuditEvent).where(AuditEvent.entity_id.is_not(None), AuditEvent.action == 'printer_intake_recorded'))
        event.details = {**event.details, 'private_contact': 'PRIVATE_INTERNAL_NOTE'}
        db.commit()
    url = f'/v1/jobs/{job_id}/printer-intakes'
    seen = []
    before = None
    for _ in range(3):
        response = owner.get(url, params={'limit': 1, **({'before': before} if before else {})})
        assert response.status_code == 200, response.text
        payload = response.json()['data']
        assert payload['total'] == 3 and len(payload['items']) == 1
        assert 'PRIVATE_INTERNAL_NOTE' not in response.text
        row = payload['items'][0]
        assert row['record_source'] == 'manufacturer' and row['verification'] == 'self_reported'
        seen.append(row['id'])
        before = payload['next_cursor']
    assert len(set(seen)) == 3 and before is None
    for params in ({'limit': 0}, {'limit': 101}, {'before': 'wrong'}, {'before': str(uuid4())}):
        assert owner.get(url, params=params).status_code == 422


def test_history_allows_scoped_viewer_but_never_foreign_tenant_workspace_or_other_job_kind(business):
    app, owner, auth, member = business
    paid(app, auth['tenant']['id'])
    allowed, blocked = workspace(owner, 'Allowed'), workspace(owner, 'Blocked')
    item = project(owner)
    with app.state.session_factory() as db:
        db.get(Project, item['id']).workspace_id = allowed
        db.commit()
    job_id = completed_job(app, item)
    assert record(owner, item, job_id).status_code == 201
    url = f'/v1/jobs/{job_id}/printer-intakes'
    outsider, _ = member('intake-outsider@example.com')
    assert outsider.get(url).status_code == 404
    viewer, _ = member('intake-reader@example.com')
    accept(viewer, invitation(owner, 'intake-reader@example.com', [allowed], role='viewer'))
    assert viewer.get(url).status_code == 200
    assert record(viewer, item, job_id).status_code == 403
    with app.state.session_factory() as db:
        db.get(Project, item['id']).workspace_id = blocked
        db.commit()
    assert viewer.get(url).status_code == 404
    wrong_kind = completed_job(app, item, kind='editable_export')
    assert owner.get(f'/v1/jobs/{wrong_kind}/printer-intakes').status_code == 404
