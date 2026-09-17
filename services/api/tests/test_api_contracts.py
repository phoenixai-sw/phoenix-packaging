"""OpenAPI is the runtime response contract, not an independently hand-written spec."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from pydantic import ValidationError
from services.api.contracts.base import APIErrorBody, Envelope
from services.api.contracts.core import GoogleChallenge
from services.api.contracts.scene import StoredScene
from services.api.tests.test_api import app, client, register, project

ROOT = Path(__file__).resolve().parents[3]
METHODS = {'get', 'post', 'put', 'patch', 'delete'}
BINARY = {'/v1/assets/{asset_id}/content', '/v1/exports/{job_id}/download', '/v1/admin/evidence/{identity}/content', '/v1/fonts/{identity}/content',
          '/v1/admin/support-sessions/{identity}/assets/{asset_id}/content',
          '/v1/admin/support-sessions/{identity}/exports/{job_id}/download'}


@pytest.fixture(scope='module')
def document():
    spec = spec_from_file_location('isolated_contract_export', ROOT/'packages/contracts/export_openapi.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.document()


def resolve(document, schema):
    while '$ref' in schema:
        target = document
        for part in schema['$ref'].removeprefix('#/').split('/'):
            target = target[part]
        schema = target
    return schema


def test_every_public_operation_has_real_success_and_error_contract(document):
    checked = []
    operation_ids = []
    for path, operations in document['paths'].items():
        for method, operation in operations.items():
            if method not in METHODS:
                continue
            operation_ids.append(operation['operationId'])
            success = [value for status,value in operation['responses'].items() if status in {'200','201','202'}]
            assert success, (method,path,'no success response')
            if path in BINARY:
                assert 'application/json' not in success[0]['content'], (method,path)
                assert all(schema['schema'].get('format') == 'binary' for schema in success[0]['content'].values())
            else:
                for response in success:
                    schema = resolve(document,response['content']['application/json']['schema'])
                    assert set(schema['required']) >= {'data','request_id'}, (method,path,'not an envelope')
                    data = resolve(document,schema['properties']['data'])
                    assert data and (data.get('properties') or data.get('anyOf') or data.get('oneOf')), (method,path,'empty/untyped data')
            for status in ('401','403','409','422','423','429','503'):
                error = resolve(document,operation['responses'][status]['content']['application/json']['schema'])
                assert set(error['properties']) == {'code','message','field_errors','retryable','request_id'}
            checked.append((method,path))
    assert len(checked) >= 90
    assert len(operation_ids) == len(set(operation_ids)), 'Every operation ID must be unique for generation'


def test_nested_error_metadata_is_lossless_and_not_flattened():
    error = {'code':'REVISION_CONFLICT','message':'changed','field_errors':{'base_revision':{'server_revision':8},'preflight':{'issues':[{'code':'FONT_MISSING','object_id':'title'}]}},'retryable':True,'request_id':'req-1'}
    assert APIErrorBody.model_validate(error).model_dump() == error


def test_response_models_reject_accidental_secret_fields():
    value = {'data':{'client_id':'public-client','nonce':'one-use','csrf_token':'one-use','expires_at':'later'},'request_id':'req-2'}
    assert Envelope[GoogleChallenge].model_validate(value).data.client_id == 'public-client'
    value['data']['private_key'] = 'must-not-leak'
    with pytest.raises(ValidationError):
        Envelope[GoogleChallenge].model_validate(value)


def test_retired_password_routes_are_not_advertised(document):
    for path in ('login','register','request-verification','verify-email','request-password-reset','reset-password'):
        assert '/v1/auth/'+path not in document['paths']


def test_historical_scene_read_does_not_round_coordinates_or_add_missing_fields():
    from services.api.geometry.structures import new_scene
    value = new_scene('three-side-seal', 160, 230)
    value['faces'][1]['objects'] = [{'id':'legacy','type':'text','face_id':'back','text':'보존',
        'font_id':'NotoSansKR','font_size_pt':12,'x_mm':16,'y_mm':36.800000000000004,
        'width_mm':80,'height_mm':20}]
    result = StoredScene.model_validate(value).model_dump(mode='json', exclude_unset=True)
    assert result == value
    assert 'locked' not in result['faces'][1]['objects'][0]


@pytest.mark.parametrize('before_basic_profile', [False, True])
def test_legacy_completed_review_manifest_omissions_survive_http_without_rewriting_file(client, app, before_basic_profile):
    """Pre-finishing production records omitted these fields, not explicit nulls."""
    from copy import deepcopy
    from services.api.models import Job
    register(client)
    item = project(client)
    queued = client.post('/v1/exports', json={'project_id':item['id'], 'base_revision':1})
    assert queued.status_code == 202, queued.text
    identity = queued.json()['data']['id']
    processed = client.post('/v1/internal/jobs/process', headers={'Authorization':'Bearer test-worker-secret'})
    assert processed.status_code == 200, processed.text
    with app.state.session_factory() as db:
        job = db.get(Job, identity)
        assert job.status == 'succeeded', job.error
        historical = deepcopy(job.result)
        absent = ['structure_ref','font_weights','review_structure']
        if before_basic_profile:
            absent += ['review_profile_id','basic_preflight','pdf_verification']
            for page in historical['manifest']['pages']:
                page.pop('bleed_mm', None)
                page.pop('role', None)
        for key in absent:
            historical['manifest'].pop(key, None)
        job.result = historical
        db.commit()
        raw = app.state.storage.get(historical['storage_key'])
    for response in (client.get('/v1/jobs/'+identity),client.get(f"/v1/projects/{item['id']}/exports")):
        assert response.status_code == 200, response.text
        value = response.json()['data']
        if 'items' in value:
            value = value['items'][0]
        assert value['result']['manifest'] == historical['manifest']
        assert all(key not in value['result']['manifest'] for key in absent)
    assert client.get('/v1/exports/'+identity+'/download').content == raw
    with app.state.session_factory() as db:
        assert db.get(Job, identity).result == historical
