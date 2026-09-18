"""Print-request bundles: real-ICC CMYK files without the engine-test stamp, still without manufacturer approval."""
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from pypdf.generic import ContentStream
from services.api.main import create_app
from services.api.config import Settings
from services.api.tests.auth_helpers import register
from services.api.jobs import process_pending_jobs
from services.api.print_engine import BUILTIN_ID
from services.api.exporters.print_pdf import render_print_artifacts, COMBINED_NAME
from services.api.exporters.review_pdf import ExportValidationError
from tests.geometry_pdf.test_print_engine import ICC, profile, project

REAL_LIKE_ICC = ICC.read_bytes().replace(b'Phoenix SYNTHETIC', b'Fixture UNITTEST ')


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(environment='test', database_url=f"sqlite:///{tmp_path/'db.sqlite'}", storage_dir=tmp_path/'storage', admin_emails=('qa@example.com',)))


def _stamp_present(pdf_bytes):
    """The engine-test stamp is outlined text drawn as filled paths inside the 12mm footer band."""
    page = PdfReader(BytesIO(pdf_bytes)).pages[0]
    extra_mm = (float(page.mediabox.height) - float(page.trimbox.height)) * 25.4 / 72
    return extra_mm > 2*3 + .5  # profile bleed 3mm each side; the stamp band adds 12mm


def test_print_request_render_has_no_stamp_and_layered_dieline(tmp_path):
    item = project()
    result = render_print_artifacts(item, tmp_path/'request', profile(), ICC.read_bytes(), print_request=True)
    assert result['kind'] == 'print_request' and result['review_only'] is False and result['manufacturer_approval'] is False
    artwork = (tmp_path/'request'/'production.pdf').read_bytes()
    assert not _stamp_present(artwork)
    reader = PdfReader(BytesIO(artwork))
    assert float(reader.pages[0].mediabox.height)*25.4/72 == pytest.approx(230+6, abs=.01)
    assert 'PRINT REQUEST' in reader.metadata.title and 'ENGINE TEST' not in reader.metadata.title
    combined = PdfReader(BytesIO((tmp_path/'request'/COMBINED_NAME).read_bytes()))
    assert len(combined.pages) == 2
    names = sorted(str(g.get_object()['/Name']) for g in combined.trailer['/Root']['/OCProperties']['/OCGs'])
    assert names == ['Artwork', 'Dieline']
    spaces = combined.pages[0]['/Resources']['/ColorSpace']
    spots = {str(v.get_object()[1]) for v in spaces.values() if isinstance(v.get_object(), list) and v.get_object()[0] == '/Separation'}
    assert spots == {'/CutContour', '/Crease'}
    ops = [op for _, op in ContentStream(combined.pages[0].get_contents(), combined).operations]
    assert ops.count(b'BDC') == 2 and ops.count(b'EMC') == 2 and not any(op in (b'rg', b'RG', b'Tj') for op in ops)
    check = result['verification']['combined_file']
    assert check['layers'] == ['Artwork', 'Dieline'] and check['segments_matched'] > 0 and check['authoritative'] is False
    assert result['combined_file']['name'] == COMBINED_NAME
    from services.api.contracts.printing import PrintEngineManifest
    PrintEngineManifest.model_validate(result)


def test_engine_test_still_stamped_and_modes_exclusive(tmp_path):
    item = project()
    render_print_artifacts(item, tmp_path/'test', profile(), ICC.read_bytes(), test_mode=True)
    assert _stamp_present((tmp_path/'test'/'production.pdf').read_bytes())
    with pytest.raises(ExportValidationError) as exc:
        render_print_artifacts(item, tmp_path/'both', profile(), ICC.read_bytes(), test_mode=True, print_request=True)
    assert exc.value.code == 'PRINT_MODE_CONFLICT'


def _register_real_like_profile(client):
    upload = client.post('/v1/admin/print-engine/icc', files={'file': ('fixture.icc', REAL_LIKE_ICC, 'application/vnd.iccprofile')},
                         data={'source': 'Isolated unit-test ICC', 'license': 'CC0-1.0'})
    assert upload.status_code == 201, upload.text
    icc = upload.json()['data']
    created = client.post('/v1/admin/print-engine/profiles', json={'name': '인쇄 의뢰 시험', 'manufacturer': '내부 시험', 'material': '시험', 'source': '자체 시험', 'license': 'CC0-1.0',
                          'review_available': True, 'requirements': {'icc_id': icc['id'], 'icc_sha256': icc['sha256']}})
    assert created.status_code == 201, created.text
    return created.json()['data']['id']


def test_print_request_api_rejects_synthetic_icc_and_delivers_unstamped_zip(app):
    with TestClient(app) as client:
        register(client, email='qa@example.com')
        item = client.post('/v1/projects', json={'name': '의뢰본', 'product_name': '오리스틱', 'width_mm': 160, 'height_mm': 230}).json()['data']
        denied = client.post('/v1/print-engine/tests', json={'project_id': item['id'], 'base_revision': 1, 'profile_id': BUILTIN_ID, 'mode': 'print_request'})
        assert denied.status_code == 422 and denied.json()['code'] == 'TEST_ICC_PRODUCTION_FORBIDDEN'
        profile_id = _register_real_like_profile(client)
        listed = client.get('/v1/print-engine/profiles').json()['data']['items']
        assert {p['id']: p['print_request_available'] for p in listed} == {BUILTIN_ID: False, profile_id: True}
        response = client.post('/v1/print-engine/tests', json={'project_id': item['id'], 'base_revision': 1, 'profile_id': profile_id, 'mode': 'print_request'})
        assert response.status_code == 202, response.text
        job = response.json()['data']
        assert job['format'] == 'print_request_zip' and job['credits_charged'] == 0
        assert process_pending_jobs(app.state.session_factory, app.state.storage) == 1
        status = client.get('/v1/jobs/'+job['id']).json()['data']
        assert status['status'] == 'succeeded', status
        assert status['result']['format'] == 'print_request_zip' and status['result']['filename'].startswith('phoenix-print-request-')
        download = client.get('/v1/exports/'+job['id']+'/download')
        assert download.status_code == 200
        with ZipFile(BytesIO(download.content)) as archive:
            assert {'production.pdf', 'cut.pdf', 'fold.pdf', COMBINED_NAME, 'manifest.json', 'preflight.json', 'preview.png'} <= set(archive.namelist())
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['kind'] == 'print_request' and manifest['manufacturer_approval'] is False and manifest['pdf_x_conformance'] == 'not_claimed'
            assert not _stamp_present(archive.read('production.pdf'))
        # The same profile still produces the stamped engine test when asked for it.
        stamped = client.post('/v1/print-engine/tests', json={'project_id': item['id'], 'base_revision': 1, 'profile_id': profile_id})
        assert stamped.status_code == 202 and stamped.json()['data']['format'] == 'print_engine_zip'
        assert stamped.json()['data']['id'] != job['id']
        with app.state.session_factory() as db:
            from services.api.billing.models import Reservation
            from sqlalchemy import select
            assert not list(db.scalars(select(Reservation)))
