"""Current approval notices never rewrite completed files or expose audit notes."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event

from services.api.database import utcnow
from services.api.export_approvals import REVOCATION_NOTICE, current_export_approvals
from services.api.feature_models import AuditEvent, RegistryVersion
from services.api.main import create_app
from services.api.models import Job, Project, Tenant, User
from services.api.production_jobs import process_production_jobs
from services.api.tests.auth_helpers import register
from tests.geometry_pdf.test_production_worker import setup, enqueue


def test_completed_output_uses_frozen_versions_current_public_revocation_and_preserves_file(setup):
    job_id, _, _ = enqueue(setup)
    assert process_production_jobs(setup.factory, setup.storage, setup.settings) == 1
    with setup.factory() as db:
        job = db.get(Job, job_id)
        assert job.status == 'succeeded', job.error
        frozen_snapshot, frozen_result = deepcopy(job.snapshot), deepcopy(job.result)
        original = setup.storage.get(job.result['storage_key'])
        admin_tenant = Tenant(name='Registry administrator tenant')
        db.add(admin_tenant)
        db.flush()
        version = db.get(RegistryVersion, setup.template_id)
        version.status = 'revoked'
        db.add_all([
            AuditEvent(tenant_id=admin_tenant.id, action='registry_revoked', entity_id=version.id,
                       created_at=utcnow()-timedelta(days=1), details={'public_reason':'이전 공개 사유'}),
            AuditEvent(tenant_id=admin_tenant.id, action='registry_revoked', entity_id=version.id,
                       details={'reason':'INTERNAL_CUSTOMER_EMAIL_AND_CASE', 'public_reason':'실링 조건 변경으로 새 도면을 확인해 주세요.',
                                'private_contact':'INTERNAL_CONTACT'}),
        ])
        # The project can move on; history must resolve the IDs frozen in this job.
        db.get(Project, setup.project_id).template_version_id = 'different-current-project-version'
        db.get(User, setup.user_id).email = 'approval-owner@example.com'
        db.commit()

    app = create_app(setup.settings)
    with TestClient(app) as client:
        register(client, 'approval-owner@example.com')
        detail = client.get('/v1/jobs/' + job_id)
        assert detail.status_code == 200, detail.text
        value = detail.json()['data']
        assert value['status'] == 'succeeded' and value['download_url']
        approval = value['current_approval']
        assert approval['status'] == 'revoked'
        assert approval['versions'][0]['id'] == setup.template_id
        assert approval['versions'][0]['public_reason'] == '실링 조건 변경으로 새 도면을 확인해 주세요.'
        assert approval['versions'][0]['revoked_at'].endswith('+00:00') and approval['checked_at']
        assert approval['versions'][1]['status'] == 'approved'
        listing = client.get(f'/v1/projects/{setup.project_id}/exports')
        assert listing.status_code == 200, listing.text
        listed = listing.json()['data']['items'][0]
        assert listed['current_approval']['versions'] == approval['versions']
        for response in (detail, listing):
            assert 'INTERNAL_' not in response.text and '이전 공개 사유' not in response.text
        downloaded = client.get('/v1/exports/' + job_id + '/download')
        assert downloaded.status_code == 200
        assert sha256(downloaded.content).hexdigest() == sha256(original).hexdigest()
        # Tenant authorization precedes registry/audit lookup on both endpoints.
        with TestClient(app) as other:
            register(other, 'unrelated@example.com')
            assert other.get('/v1/jobs/' + job_id).status_code == 404
            assert other.get(f'/v1/projects/{setup.project_id}/exports').status_code == 404
    with setup.factory() as db:
        job = db.get(Job, job_id)
        assert job.status == 'succeeded' and job.snapshot == frozen_snapshot
        assert {k:v for k,v in job.result.items() if k!='_integrity'} == frozen_result
        assert job.result['_integrity']['state']=='healthy'


def test_batch_projection_has_two_queries_and_never_exposes_legacy_internal_reason(setup):
    with setup.factory() as db:
        project = db.get(Project, setup.project_id)
        version = db.get(RegistryVersion, setup.template_id)
        version.status = 'revoked'
        db.add(AuditEvent(action='registry_revoked', entity_id=version.id,
                          details={'reason':'INTERNAL_LEGACY_REASON'}))
        db.commit()
        frozen = {'template_version_id':setup.template_id, 'print_profile_version_id':project.print_profile_version_id}
        jobs = [SimpleNamespace(id=str(uuid4()),kind='production_export',snapshot=frozen) for _ in range(100)]
        statements = []
        def record(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement)
        event.listen(db.get_bind(), 'before_cursor_execute', record)
        try:
            states = current_export_approvals(db, jobs)
        finally:
            event.remove(db.get_bind(), 'before_cursor_execute', record)
        assert len(statements) == 2
        assert len(states) == 100
        assert all(value['versions'][0]['public_reason'] == REVOCATION_NOTICE for value in states.values())
        assert 'INTERNAL_LEGACY_REASON' not in str(states)
        assert current_export_approvals(db, [SimpleNamespace(kind='review_export')]) == {}


def test_missing_wrong_kind_or_unapproved_versions_fail_closed_without_false_approval(setup):
    with setup.factory() as db:
        project = db.get(Project, setup.project_id)
        frozen = {'template_version_id':setup.template_id, 'print_profile_version_id':project.print_profile_version_id}
        job = SimpleNamespace(id='history',kind='production_export',snapshot=frozen)
        assert current_export_approvals(db,[job])['history']['status'] == 'approved'
        # Existing private notes and an old revocation do not turn review into approved.
        template = db.get(RegistryVersion, setup.template_id)
        template.status = 'review'
        db.flush()
        assert current_export_approvals(db,[job])['history']['status'] == 'unavailable'
        job.snapshot = {'template_version_id':project.print_profile_version_id}
        states = current_export_approvals(db,[job])['history']
        assert states['status'] == 'unavailable'
        assert all(version['status'] == 'unavailable' for version in states['versions'])
