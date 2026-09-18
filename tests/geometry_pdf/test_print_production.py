"""Isolated DB approval fixtures exercise publication; no real manufacturer is asserted."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile
import json
import pytest
from sqlalchemy import select,func
from tests.geometry_pdf.test_production_worker import setup,enqueue
from services.api.models import Project,Job
from services.api.feature_models import RegistryVersion,Evidence
from services.api.billing.models import Reservation,LedgerEntry
from services.api.production_jobs import process_production_jobs
from services.api.exporters.print_profile import parse_print_profile
from services.api.exporters.print_color import inspect_icc
from services.api.contracts.printing import PrintEngineManifest
from services.api.metrics.models import MetricEvent

ICC=Path(__file__).resolve().parents[2]/'fixtures/icc/synthetic-cmyk-test.icc'


@pytest.fixture
def cmyk_setup(setup):
    # A separately named local fixture tests administrator-supplied ICC wiring.
    # Its copyright still states no printer/material/ISO/manufacturer approval.
    # Built-in synthetic registration is separately tested as production-blocked.
    raw=ICC.read_bytes().replace(b'Phoenix SYNTHETIC',b'Fixture UNITTEST ')
    assert len(raw)==len(ICC.read_bytes())
    info=inspect_icc(raw)
    with setup.factory() as db:
        project=db.get(Project,setup.project_id);profile=db.get(RegistryVersion,project.print_profile_version_id)
        template=db.get(RegistryVersion,project.template_version_id)
        for row in (template,profile):row.approval={**row.approval,'approved_by_name':'Local fixture','notes':'Only an isolated test record'}
        evidence=Evidence(tenant_id=setup.tenant_id,uploaded_by=setup.user_id,storage_key=f'{setup.tenant_id}/evidence/{uuid4()}',name='Isolated test ICC',sha256=info['sha256'],content_type='application/vnd.iccprofile',byte_size=len(raw))
        setup.storage.put(evidence.storage_key,raw,evidence.content_type);db.add(evidence);db.flush()
        icc=RegistryVersion(kind='icc_profile',name='Local fixture only',manufacturer='Fixture manufacturer',status='draft',is_demo=False,created_by=setup.user_id,
                            details={**info,'evidence_id':evidence.id,'source':'isolated unit test','license':'CC0-1.0'})
        db.add(icc);db.flush()
        profile.details={**profile.details,'requirements':parse_print_profile({'icc_id':icc.id,'icc_sha256':info['sha256'],'bleed_mm':3.175})}
        scene=deepcopy(project.scene)
        scene['faces'][0]['objects']=[{'id':'text','type':'text','face_id':'front','text':'한글 제작 엔진 시험','font_size_pt':24,'font_weight':700,'x_mm':20,'y_mm':40,'width_mm':110,'height_mm':30}]
        project.scene=scene;db.commit();setup.icc_id=icc.id
    return setup


def test_actual_cmyk_production_worker_eight_file_bundle_and_atomic_charge(cmyk_setup):
    s=cmyk_setup;identity,_,_=enqueue(s)
    assert process_production_jobs(s.factory,s.storage,s.settings)==1
    with s.factory() as db:
        job=db.get(Job,identity);assert job.status=='succeeded',job.error
        assert job.result['credits_charged']==40 and job.result['review_only'] is False
        with ZipFile(BytesIO(s.storage.get(job.result['storage_key']))) as z:
            assert len(z.namelist())==9
            manifest=json.loads(z.read('manifest.json'));PrintEngineManifest.model_validate(manifest)
            assert manifest['adapter']=='icc-cmyk-outline-v1' and not manifest['review_only']
            assert manifest['pdf_x_conformance']=='not_claimed'
            assert len(manifest['files'])==8
            for file in manifest['files']:assert sha256(z.read(file['name'])).hexdigest()==file['sha256']
        assert db.scalar(select(func.sum(LedgerEntry.amount)).where(LedgerEntry.event=='CAPTURE'))==40
        event=db.scalar(select(MetricEvent).where(MetricEvent.name=='production_export_succeeded',MetricEvent.job_id==identity))
        assert event and event.tenant_id==s.tenant_id and event.revision_id==job.revision_id


def test_icc_revocation_after_uploaded_bundle_blocks_publication_and_refunds(cmyk_setup):
    s=cmyk_setup;identity,_,_=enqueue(s);original=s.storage.put
    def revoke(key,raw,mime):
        original(key,raw,mime)
        with s.factory() as db:db.get(RegistryVersion,s.icc_id).status='revoked';db.commit()
    s.storage.put=revoke
    process_production_jobs(s.factory,s.storage,s.settings)
    with s.factory() as db:
        job=db.get(Job,identity);assert job.status=='failed' and job.result is None
        assert db.get(Reservation,job.snapshot['reservation_id']).status=='released'
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='CAPTURE'))==0
        assert db.scalar(select(func.count()).select_from(MetricEvent).where(MetricEvent.name=='production_export_succeeded'))==0
