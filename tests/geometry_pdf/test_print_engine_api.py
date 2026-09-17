from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from services.api.main import create_app
from services.api.config import Settings
from services.api.models import Job,User
from services.api.feature_models import RegistryVersion
from services.api.tests.auth_helpers import register
from services.api.jobs import process_pending_jobs
from services.api.print_engine import BUILTIN_ID

ICC=Path(__file__).resolve().parents[2]/'fixtures/icc/synthetic-cmyk-test.icc'


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(environment='test',database_url=f"sqlite:///{tmp_path/'db.sqlite'}",storage_dir=tmp_path/'storage',admin_emails=('qa@example.com',)))


def create(client):
    r=client.post('/v1/projects',json={'name':'ICC 시험','product_name':'오리스틱','width_mm':160,'height_mm':230})
    assert r.status_code==201,r.text
    return r.json()['data']


def test_durable_engine_zip_idempotency_download_and_no_credits(app):
    with TestClient(app) as client:
        register(client)
        item=create(client);body={'project_id':item['id'],'base_revision':1,'profile_id':BUILTIN_ID}
        response=client.post('/v1/print-engine/tests',json=body,headers={'idempotency-key':'actual-engine-test'})
        assert response.status_code==202,response.text
        job=response.json()['data'];assert job['format']=='print_engine_zip' and job['credits_charged']==0
        repeated=client.post('/v1/print-engine/tests',json=body,headers={'idempotency-key':'actual-engine-test'})
        assert repeated.json()['data']['id']==job['id']
        assert process_pending_jobs(app.state.session_factory,app.state.storage)==1
        status=client.get('/v1/jobs/'+job['id']).json()['data'];assert status['status']=='succeeded',status
        assert status['result']['format']=='print_engine_zip'
        download=client.get('/v1/exports/'+job['id']+'/download')
        assert download.status_code==200 and download.headers['content-type']=='application/zip'
        assert download.headers['content-disposition'].endswith('.zip"')
        with ZipFile(BytesIO(download.content)) as z:
            assert {'production.pdf','cut.pdf','fold.pdf','manifest.json','preflight.json','preview.png'}<=set(z.namelist())
            manifest=json.loads(z.read('manifest.json'));assert manifest['review_only'] is True and manifest['pdf_x_conformance']=='not_claimed'
            from services.api.contracts.printing import PrintEngineManifest
            PrintEngineManifest.model_validate(manifest)
            from hashlib import sha256
            assert len(manifest['files'])==5
            for file in manifest['files']:assert file['sha256']==sha256(z.read(file['name'])).hexdigest()
        with app.state.session_factory() as db:
            from services.api.billing.models import Reservation
            assert not list(db.scalars(select(Reservation)))


def test_access_and_cas_guards(app):
    with TestClient(app) as client:
        register(client);item=create(client)
        assert client.post('/v1/print-engine/tests',json={'project_id':item['id'],'base_revision':2,'profile_id':BUILTIN_ID}).status_code==409
        assert client.post('/v1/admin/print-engine/icc',files={'file':('p.icc',ICC.read_bytes(),'application/vnd.iccprofile')},data={'source':'test','license':'CC0-1.0'}).status_code==403
        with TestClient(app) as other:
            register(other,email='other@example.com')
            assert other.post('/v1/print-engine/tests',json={'project_id':item['id'],'base_revision':1,'profile_id':BUILTIN_ID}).status_code==404


def test_admin_upload_registration_and_revocation_fail_closed(app):
    with TestClient(app) as client:
        register(client,email='qa@example.com');item=create(client)
        upload=client.post('/v1/admin/print-engine/icc',files={'file':('test.icc',ICC.read_bytes(),'application/vnd.iccprofile')},data={'source':'Synthetic testing fixture only','license':'CC0-1.0'})
        assert upload.status_code==201,upload.text
        icc=upload.json()['data']
        profile=client.post('/v1/admin/print-engine/profiles',json={'name':'CI 시험','manufacturer':'내부 시험','material':'시험','source':'자체 시험','license':'CC0-1.0','review_available':True,
            'requirements':{'icc_id':icc['id'],'icc_sha256':icc['sha256']}})
        assert profile.status_code==201,profile.text
        identity=profile.json()['data']['id']
        r=client.post('/v1/print-engine/tests',json={'project_id':item['id'],'base_revision':1,'profile_id':identity});assert r.status_code==202,r.text
        with app.state.session_factory() as db:
            db.get(RegistryVersion,icc['id']).status='revoked';db.commit()
        process_pending_jobs(app.state.session_factory,app.state.storage)
        result=client.get('/v1/jobs/'+r.json()['data']['id']).json()['data']
        assert result['status']=='failed' and result['download_url'] is None


def test_worker_role_change_blocks_test_publication(app):
    with TestClient(app) as client:
        register(client);item=create(client)
        r=client.post('/v1/print-engine/tests',json={'project_id':item['id'],'base_revision':1,'profile_id':BUILTIN_ID});assert r.status_code==202,r.text
        with app.state.session_factory() as db:
            db.scalar(select(User)).role='viewer';db.commit()
        process_pending_jobs(app.state.session_factory,app.state.storage)
        with app.state.session_factory() as db:
            job=db.get(Job,r.json()['data']['id']);assert job.status=='failed' and not job.result


def test_failed_test_retry_download_acl_and_original_snapshot(app,monkeypatch):
    with TestClient(app) as client:
        register(client);item=create(client)
        response=client.post('/v1/print-engine/tests',json={'project_id':item['id'],'base_revision':1,'profile_id':BUILTIN_ID})
        identity=response.json()['data']['id']
        import services.api.exporters.print_pdf as renderer
        render=renderer.render_print_artifacts
        monkeypatch.setattr(renderer,'render_print_artifacts',lambda *a,**kw:(_ for _ in ()).throw(ValueError('fixture failure')))
        process_pending_jobs(app.state.session_factory,app.state.storage)
        assert client.get(f'/v1/exports/{identity}/download').status_code==409
        monkeypatch.setattr(renderer,'render_print_artifacts',render)
        with app.state.session_factory() as db:before=deepcopy(db.get(Job,identity).snapshot)
        retry=client.post(f'/v1/jobs/{identity}/retry');assert retry.status_code==202,retry.text
        process_pending_jobs(app.state.session_factory,app.state.storage)
        assert client.get(f'/v1/exports/{identity}/download').status_code==200
        with app.state.session_factory() as db:assert db.get(Job,identity).snapshot==before
        with TestClient(app) as other:
            register(other,email='other@example.com')
            assert other.post(f'/v1/jobs/{identity}/retry').status_code==404
            assert other.get(f'/v1/exports/{identity}/download').status_code==404


def test_builtin_icc_never_becomes_production_profile(app):
    from services.api.print_engine import freeze_print_output
    from services.api.errors import APIError
    with app.state.session_factory() as db:
        with pytest.raises(APIError) as exc:freeze_print_output(db,BUILTIN_ID,test_mode=False)
        assert exc.value.code=='TEST_ICC_PRODUCTION_FORBIDDEN'
