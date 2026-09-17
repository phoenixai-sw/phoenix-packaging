"""Authorized uploaded font bytes survive scene persistence and durable PDF jobs."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from services.api.font_assets.models import FontAsset
from services.api.font_assets.validation import inspect_font
from services.api.jobs import process_pending_jobs
from services.api.models import Job, Project
from services.api.print_engine import BUILTIN_ID
from services.api.tests.auth_helpers import register
from tests.geometry_pdf.test_custom_font_rendering import font_source
from tests.geometry_pdf.test_print_engine_api import app
from tests.geometry_pdf.test_print_production import cmyk_setup, setup
from tests.geometry_pdf.test_production_worker import enqueue as production_enqueue


def registered_project(client, source):
    declaration = {
        'license_name': 'OFL-1.1',
        'license_text': 'SIL OPEN FONT LICENSE Version 1.1; local test subset only.',
        'source_url': 'https://github.com/google/fonts/tree/main/ofl/notosanskr',
        'rights_holder': 'The Noto Project Authors',
        'web_use_confirmed': True, 'print_use_confirmed': True,
        'redistribution_allowed': True,
    }
    upload = client.post('/v1/fonts', files={'file': ('qa.ttf', source.data, 'font/ttf')},
                         data={'declaration': json.dumps(declaration)})
    assert upload.status_code == 201, upload.text
    font = upload.json()['data']
    assert font['sha256'] == source.sha256 and font['rights_verification'] == 'user_attested'
    brand = client.post('/v1/brands', json={'name': 'Font QA', 'font_asset_ids': [font['id']]})
    assert brand.status_code == 201, brand.text
    created = client.post('/v1/projects', json={'name': 'Font export QA', 'product_name': '오리스틱',
        'brand_id': brand.json()['data']['id'], 'width_mm': 160, 'height_mm': 230})
    assert created.status_code == 201, created.text
    project = created.json()['data']
    scene = deepcopy(project['scene'])
    scene['faces'][0]['objects'] = [{
        'id': 'actual-custom-font', 'type': 'text', 'face_id': 'front',
        'font_id': 'NotoSansKR', 'font_asset_id': font['id'], 'font_weight': font['weight'],
        'x_mm': 25, 'y_mm': 40, 'width_mm': 115, 'height_mm': 40,
        'font_size_pt': 24, 'text': '한글 오리스틱\n가나 ABC 37.5g × 4', 'color': '#000000',
    }]
    saved = client.patch(f"/v1/projects/{project['id']}/draft", json={'base_revision': 1, 'scene': scene})
    assert saved.status_code == 200, saved.text
    reopened = client.get(f"/v1/projects/{project['id']}").json()['data']
    assert reopened['scene'] == saved.json()['data']['scene']
    assert reopened['scene']['faces'][0]['objects'][0]['font_asset_id'] == font['id']
    return reopened, font


def enqueue(client, project, kind):
    body = {'project_id': project['id'], 'base_revision': project['base_revision']}
    route = '/v1/exports'
    if kind == 'outlined':
        route = '/v1/print-engine/tests'
        body['profile_id'] = BUILTIN_ID
    queued = client.post(route, json=body, headers={'Idempotency-Key': 'font-' + kind})
    assert queued.status_code == 202, queued.text
    return queued.json()['data']['id']


@pytest.mark.parametrize('kind', ['embedded', 'outlined'])
def test_upload_brand_save_reopen_and_worker_use_exact_font(app, font_source, kind):
    with TestClient(app) as client:
        register(client)
        project, font = registered_project(client, font_source)
        preflight = client.post('/v1/preflight', json={'project_id': project['id'],
            'base_revision': project['base_revision'], 'kind': 'review'})
        assert preflight.status_code == 200, preflight.text
        assert preflight.json()['data']['status'] == 'pass'
        job_id = enqueue(client, project, kind)
        with app.state.session_factory() as db:
            frozen = db.get(Job, job_id).snapshot['font_assets']
            assert len(frozen) == 1 and frozen[0]['sha256'] == font_source.sha256
        assert process_pending_jobs(app.state.session_factory, app.state.storage) == 1
        state = client.get('/v1/jobs/' + job_id).json()['data']
        assert state['status'] == 'succeeded', state
        download = client.get('/v1/exports/' + job_id + '/download')
        assert download.status_code == 200, download.text
        if kind == 'embedded':
            manifest = state['result']['manifest']
            fonts = manifest['font_weights']
            document = PdfReader(BytesIO(download.content))
            assert '한글 오리스틱' in document.pages[0].extract_text()
            assert '가나 ABC 37.5g × 4' in document.pages[0].extract_text()
            assert manifest['pdf_verification']['used_fonts_embedded']
        else:
            with ZipFile(BytesIO(download.content)) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                fonts = manifest['fonts']
                document = PdfReader(BytesIO(archive.read('production.pdf')))
                assert document.pages[0].extract_text() == ''
                assert manifest['review_only'] is True
        used = next(value for value in fonts if value.get('font_asset_id') == font['id'])
        assert used['sha256'] == font_source.sha256 and used['weight'] == font['weight']
        assert used['license_name'] == 'OFL-1.1'
        assert used['embedded'] is (kind == 'embedded')


@pytest.mark.parametrize('kind', ['embedded', 'outlined'])
def test_worker_rejects_changed_font_bytes_without_fallback_or_download(app, font_source, kind):
    with TestClient(app) as client:
        register(client)
        project, font = registered_project(client, font_source)
        job_id = enqueue(client, project, kind)
        with app.state.session_factory() as db:
            key = db.get(FontAsset, font['id']).storage_key
        # Storage corruption after queueing must not become a substituted font.
        changed = bytearray(font_source.data)
        changed[-1] ^= 1
        app.state.storage.put(key, bytes(changed), 'font/ttf')
        assert sha256(bytes(changed)).hexdigest() != font_source.sha256
        preflight = client.post('/v1/preflight', json={'project_id': project['id'],
            'base_revision': project['base_revision'], 'kind': 'review'})
        assert preflight.status_code == 200, preflight.text
        blocker = next(value for value in preflight.json()['data']['blockers'] if value['code'] == 'FONT_CHECKSUM_MISMATCH')
        assert blocker['object_id'] == 'actual-custom-font'
        assert process_pending_jobs(app.state.session_factory, app.state.storage) == 1
        state = client.get('/v1/jobs/' + job_id).json()['data']
        assert state['status'] == 'failed' and state['result'] is None and state['download_url'] is None
        assert client.get('/v1/exports/' + job_id + '/download').status_code == 409


def test_production_worker_keeps_authorized_font_snapshot_and_real_outlines(cmyk_setup, font_source):
    """Local approval fixtures only; this does not assert manufacturer approval."""
    from services.api.production_jobs import process_production_jobs
    s = cmyk_setup
    with s.factory() as db:
        font = FontAsset(id=font_source.asset_id, tenant_id=s.tenant_id, created_by=s.user_id,
            storage_key=f'{s.tenant_id}/fonts/{font_source.asset_id}.ttf', original_name='qa.ttf',
            **inspect_font(font_source.data), license_name='OFL-1.1',
            license_text='SIL OPEN FONT LICENSE Version 1.1; isolated test subset.',
            source_url='https://github.com/google/fonts/tree/main/ofl/notosanskr',
            rights_holder='The Noto Project Authors', redistribution_allowed=True)
        s.storage.put(font.storage_key, font_source.data, 'font/ttf')
        db.add(font)
        project = db.get(Project, s.project_id)
        scene = deepcopy(project.scene)
        obj = scene['faces'][0]['objects'][0]
        obj.update(text='한글 오리스틱', font_asset_id=font.id, font_weight=font.weight)
        project.scene = scene
        db.commit()
    identity, _, _ = production_enqueue(s)
    assert process_production_jobs(s.factory, s.storage, s.settings) == 1
    with s.factory() as db:
        job = db.get(Job, identity)
        assert job.status == 'succeeded', job.error
        assert job.snapshot['font_assets'][0]['sha256'] == font_source.sha256
        with ZipFile(BytesIO(s.storage.get(job.result['storage_key']))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            custom = next(row for row in manifest['fonts'] if row.get('font_asset_id'))
            assert custom['sha256'] == font_source.sha256 and custom['outlined'] is True
            assert not PdfReader(BytesIO(archive.read('production.pdf'))).pages[0].extract_text()
