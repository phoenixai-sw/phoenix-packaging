from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from uuid import uuid4
from zipfile import ZipFile
import pytest
from fastapi.testclient import TestClient
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from sqlalchemy import select
from services.api.errors import APIError
from services.api.font_assets.models import FontAsset
from services.api.font_assets.validation import inspect_font
from services.api.font_assets.service import attach_font_resolver,freeze_fonts
from services.api.models import Job,Project
from services.api.tests.test_api import app,client,register


def font_bytes(*, weight=500, fs_type=0, composite=False):
    builder=FontBuilder(1000,isTTF=True)
    names=['.notdef','space','A','V','f','i','han']
    if composite:names+=['stack1','stack2','stack3']
    builder.setupGlyphOrder(names);builder.setupCharacterMap({32:'space',65:'A',86:'V',102:'f',105:'i',0xD55C:'han'})
    glyphs={}
    for name in names:
        pen=TTGlyphPen(glyphs)
        if name.startswith('stack'):
            child='A' if name=='stack1' else 'stack'+str(int(name[-1])-1)
            for _ in range(18):pen.addComponent(child,(1,0,0,1,0,0))
        elif name!='space':
            pen.moveTo((50,0));pen.lineTo((500,0));pen.lineTo((500,700));pen.lineTo((50,700));pen.closePath()
        glyphs[name]=pen.glyph()
    builder.setupGlyf(glyphs);builder.setupHorizontalMetrics({name:(600,0) for name in names})
    builder.setupHorizontalHeader(ascent=800,descent=-200)
    builder.setupNameTable({'familyName':'Owned Test Font','styleName':'Medium','uniqueFontIdentifier':'Owned-Test-500','fullName':'Owned Test Font Medium','psName':'OwnedTestFont-Medium'})
    builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200,usWeightClass=weight,fsType=fs_type)
    builder.setupPost();builder.setupMaxp();output=BytesIO();builder.save(output);return output.getvalue()


def declaration(**extra):
    return {'license_name':'Fixture test license','license_text':'This test fixture permits web, print and archive distribution.',
            'source_url':'https://example.com/test-font-license','rights_holder':'Test author','web_use_confirmed':True,
            'print_use_confirmed':True,'redistribution_allowed':True,**extra}


def upload(client,raw=None,**extra):
    response=client.post('/v1/fonts',files={'file':('owned.ttf',raw or font_bytes(),'font/ttf')},data={'declaration':json.dumps(declaration(**extra))})
    assert response.status_code==201,response.text
    return response.json()['data']


def font_project(client,font):
    brand=client.post('/v1/brands',json={'name':'Font brand','font_asset_ids':[font['id']]}).json()['data']
    response=client.post('/v1/projects',json={'name':'Font project','product_name':'A','brand_id':brand['id']})
    assert response.status_code==201,response.text
    return response.json()['data'],brand


def apply_font(client,project,font):
    scene=deepcopy(project['scene']);obj=scene['faces'][0]['objects'][0]
    obj.update(text='A한',font_asset_id=font['id'],font_weight=font['weight'])
    response=client.patch(f"/v1/projects/{project['id']}/draft",json={'base_revision':project['base_revision'],'scene':scene})
    assert response.status_code==200,response.text
    return response.json()['data']


@pytest.mark.parametrize('fs_type',[2,4,0x100,0x200,0x1000])
def test_restricted_embedding_or_subsetting_is_rejected(fs_type):
    with pytest.raises(APIError,match='편집') as error:inspect_font(font_bytes(fs_type=fs_type))
    assert error.value.code=='FONT_EMBEDDING_RESTRICTED'


def test_static_font_metrics_and_expanded_composite_limit():
    info=inspect_font(font_bytes())
    assert info['weight']==500 and info['ascent_ratio']==.8 and info['descent_ratio']==-.2
    with pytest.raises(APIError) as error:inspect_font(font_bytes(composite=True))
    assert error.value.code=='FONT_INVALID'
    for raw in (b'OTTO'+b'\0'*50,b'wOFF'+b'\0'*50,b'ttcf'+b'\0'*50,b'bad'):
        with pytest.raises(APIError):inspect_font(raw)


def test_private_font_upload_acl_csrf_and_missing_glyphs(client,app):
    register(client);raw=font_bytes();font=upload(client,raw)
    assert font['sha256']==sha256(raw).hexdigest() and font['rights_verification']=='user_attested'
    assert 'storage_key' not in font and 'license_text' not in font
    assert client.get(f"/v1/fonts/{font['id']}/content").content==raw
    report=client.post(f"/v1/fonts/{font['id']}/glyphs",json={'text':'A한🙂'}).json()['data']
    assert report['missing_codepoints']==['U+1F642'] and not report['supported']
    with TestClient(app) as other:
        register(other,'font-other@example.com')
        assert other.get(f"/v1/fonts/{font['id']}").status_code==404
        assert other.get(f"/v1/fonts/{font['id']}/content").status_code==404
        assert other.post('/v1/brands',json={'name':'foreign','font_asset_ids':[font['id']]}).status_code==404
    denied=client.post('/v1/fonts',headers={'X-CSRF-Token':'wrong'},files={'file':('x.ttf',raw,'font/ttf')},data={'declaration':json.dumps(declaration())})
    assert denied.status_code==403
    unconfirmed=client.post('/v1/fonts',files={'file':('x.ttf',raw,'font/ttf')},data={'declaration':json.dumps(declaration(web_use_confirmed=False))})
    assert unconfirmed.status_code==422


def test_brand_removal_preserves_existing_scene_and_history_but_denies_new_selection(client):
    register(client);font=upload(client);project,brand=font_project(client,font);project=apply_font(client,project,font)
    saved=client.get(f"/v1/projects/{project['id']}/revisions").json()['data']['items'][0]
    assert client.patch(f"/v1/brands/{brand['id']}",json={'name':'Font brand','font_asset_ids':[]}).status_code==200
    project=apply_font(client,project,font)  # current existing object keeps its original
    changed=deepcopy(project['scene']);changed['faces'][0]['objects'][1].update(text='AV',font_asset_id=font['id'],font_weight=font['weight'])
    denied=client.patch(f"/v1/projects/{project['id']}/draft",json={'base_revision':project['base_revision'],'scene':changed})
    assert denied.json()['code']=='FONT_BRAND_REQUIRED'
    restored=client.post(f"/v1/projects/{project['id']}/revisions/{saved['id']}/restore",json={'base_revision':project['base_revision']})
    assert restored.status_code==200,restored.text
    assert restored.json()['data']['scene']['faces'][0]['objects'][0]['font_asset_id']==font['id']


def test_font_weight_mismatch_and_checksum_never_fall_back(client,app):
    auth=register(client);font=upload(client);project,_=font_project(client,font)
    scene=deepcopy(project['scene']);scene['faces'][0]['objects'][0].update(font_asset_id=font['id'],font_weight=700)
    denied=client.patch(f"/v1/projects/{project['id']}/draft",json={'base_revision':1,'scene':scene})
    assert denied.json()['code']=='FONT_WEIGHT_MISMATCH'
    with app.state.session_factory() as db:
        font_row=db.get(FontAsset,font['id']);raw=app.state.storage.get(font_row.storage_key)
        project=apply_font(client,project,font)
        frozen=freeze_fonts(db,auth['tenant']['id'],project['scene'])
        resolver=attach_font_resolver(lambda _:None,db,app.state.storage,auth['tenant']['id'],{'font_assets':frozen})
        assert resolver.font(font['id']).data==raw
        app.state.storage.put(font_row.storage_key,raw+b'corrupt','font/ttf')
    assert client.get(f"/v1/fonts/{font['id']}/content").json()['code']=='FONT_CHECKSUM_MISMATCH'


def test_editable_zip_contains_exact_permitted_font_and_license(client,app,tmp_path):
    from services.api.editable_exports import process_editable_jobs
    register(client);raw=font_bytes();font=upload(client,raw);project,_=font_project(client,font);project=apply_font(client,project,font)
    response=client.post('/v1/exports',json={'project_id':project['id'],'base_revision':project['base_revision'],'kind':'editable'})
    assert response.status_code==202,response.text
    identity=response.json()['data']['id'];assert process_editable_jobs(app.state.session_factory,app.state.storage)==1
    result=client.get('/v1/jobs/'+identity).json()['data'];assert result['status']=='succeeded',result
    with ZipFile(BytesIO(client.get('/v1/exports/'+identity+'/download').content)) as archive:
        assert archive.read(f"fonts/{font['id']}.ttf")==raw
        assert declaration()['license_text'].encode() in archive.read(f"fonts/{font['id']}.license.txt")
        assert 'storage_key' not in archive.read('project.json').decode()
        assert json.loads(archive.read('scene.json'))['faces'][0]['objects'][0]['font_asset_id']==font['id']


def test_zip_requires_separate_font_file_distribution_rights(client):
    register(client);font=upload(client,redistribution_allowed=False);project,_=font_project(client,font);project=apply_font(client,project,font)
    result=client.post('/v1/exports',json={'project_id':project['id'],'base_revision':project['base_revision'],'kind':'editable'})
    assert result.status_code==422 and result.json()['code']=='FONT_REDISTRIBUTION_NOT_ALLOWED'


def test_direct_upload_publishes_immutable_copy_and_rechecks_ownership(tmp_path,monkeypatch):
    from services.api.main import create_app
    from services.api.config import Settings
    from services.api.storage import SupabaseStorage
    class FakeStorage(SupabaseStorage):
        def __init__(self):self.objects={}
        def put(self,key,raw,_):self.objects[key]=raw
        def get_limited(self,key,maximum):
            raw=self.objects[key]
            if len(raw)>maximum:raise ValueError('limit')
            return raw
        def signed_upload_url(self,key):return 'https://storage.example/upload/'+key
        def signed_url(self,key,*args,**kwargs):return 'https://storage.example/private/'+key
    storage=FakeStorage();monkeypatch.setattr('services.api.main.build_storage',lambda _:storage)
    application=create_app(Settings(environment='test',database_url=f"sqlite:///{tmp_path/'direct.db'}",storage_dir=tmp_path/'storage'))
    with TestClient(application) as client:
        auth=register(client);raw=font_bytes()
        ticket=client.post('/v1/fonts/uploads',json={**declaration(),'name':'safe.ttf','byte_size':len(raw)}).json()['data']
        key=f"{auth['tenant']['id']}/quarantine/{ticket['id']}";storage.put(key,raw,'font/ttf')
        response=client.post(f"/v1/fonts/uploads/{ticket['id']}/complete")
        assert response.status_code==200,response.text
        font=response.json()['data'];storage.put(key,b'changed later','font/ttf')
        repeat=client.post(f"/v1/fonts/uploads/{ticket['id']}/complete")
        assert repeat.status_code==200 and repeat.json()['data']['id']==font['id']
        assert storage.objects[f"{auth['tenant']['id']}/fonts/{font['id']}.ttf"]==raw
        assert client.get(f"/v1/fonts/{font['id']}/content",follow_redirects=False).status_code==307


def test_font_migration_is_additive_and_record_is_database_immutable(tmp_path,monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine,inspect,text
    url=f"sqlite:///{tmp_path/'font-migrated.db'}"
    monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config('services/api/alembic.ini');command.upgrade(cfg,'0015_admin_policies')
    engine=create_engine(url)
    before=set(inspect(engine).get_table_names());command.upgrade(cfg,'0016_font_assets')
    assert set(inspect(engine).get_table_names())-before=={'font_assets','font_upload_sessions'}
    with engine.begin() as connection:
        triggers=connection.execute(text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='font_assets'")).scalars().all()
        assert set(triggers)=={'immutable_font_update','immutable_font_delete'}
        assert 'font_asset_ids' in {column['name'] for column in inspect(connection).get_columns('brands')}
    command.downgrade(cfg,'0015_admin_policies');assert set(inspect(engine).get_table_names())==before
    engine.dispose()


def test_published_font_record_cannot_be_edited(client,app):
    register(client);font=upload(client)
    with app.state.session_factory() as db:
        row=db.get(FontAsset,font['id']);row.license_text='replace the past rights statement'
        with pytest.raises(ValueError,match='immutable'):db.commit()
        db.rollback();assert db.get(FontAsset,font['id']).license_text==declaration()['license_text']


def test_encrypted_backup_restores_exact_font_original_and_scene(client,app,tmp_path,monkeypatch):
    from importlib.util import module_from_spec,spec_from_file_location
    from pathlib import Path
    from services.api.config import Settings
    from services.api.database import build_database
    from services.api.storage import LocalStorage
    from services.api.font_assets.service import read_font
    auth=register(client);raw=font_bytes();font=upload(client,raw);project,_=font_project(client,font);project=apply_font(client,project,font)
    monkeypatch.setenv('DATABASE_URL',app.state.settings.database_url)
    monkeypatch.setenv('STORAGE_BACKEND','local');monkeypatch.setenv('STORAGE_DIR',str(app.state.settings.storage_dir))
    monkeypatch.setenv('APP_ENV','test')
    spec=spec_from_file_location('font_backup_test',Path('scripts/backup-platform.py'));module=module_from_spec(spec);spec.loader.exec_module(module)
    output=tmp_path/'font-backup';key=tmp_path/'font.key';restored=tmp_path/'restored-font'
    module.backup(output,key);module.verify(output,key,restore_dir=restored)
    settings=Settings(environment='test',database_url=f"sqlite:///{(restored/'restored.db').as_posix()}",storage_dir=restored/'storage')
    engine,sessions=build_database(settings)
    with sessions() as db:
        source=read_font(db,LocalStorage(settings.storage_dir),auth['tenant']['id'],font['id'])
        assert source.data==raw and source.sha256==font['sha256']
        assert db.get(Project,project['id']).scene['faces'][0]['objects'][0]['font_asset_id']==font['id']
    engine.dispose()
