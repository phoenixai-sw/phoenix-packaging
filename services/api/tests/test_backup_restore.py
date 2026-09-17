"""Application-level recovery fixtures; no source/cloud credentials or remote DB."""
from copy import deepcopy
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

from services.api.config import Settings
from services.api.main import create_app
from services.api.jobs import process_pending_jobs
from services.api.models import Asset
from services.api.tests.test_api import register, project, image_file

SCRIPTS=Path(__file__).resolve().parents[3]/"scripts"
spec=spec_from_file_location("backup_platform_fixture",SCRIPTS/"backup-platform.py")
backup_script=module_from_spec(spec)
spec.loader.exec_module(backup_script)
sys.path.insert(0,str(SCRIPTS))
from verify_restored_app import isolated_runtime, verify_restored_app, _check_export


@pytest.fixture
def recovery(tmp_path,monkeypatch,request):
    source=tmp_path/"source"
    source.mkdir()
    settings=Settings(environment="test",database_url=f"sqlite:///{source/'source.db'}",storage_backend="local",storage_dir=source/"storage",ai_provider="disabled")
    with TestClient(create_app(settings)) as client:
        register(client)
        item=project(client)
        asset=client.post("/v1/assets",files={"file":image_file()}).json()["data"]
        scene=deepcopy(item["scene"])
        scene["faces"][0]["objects"].append({"id":"restored-image","type":"image","face_id":"front","asset_id":asset["id"],"x_mm":20,"y_mm":110,"width_mm":32,"height_mm":40})
        saved=client.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":1,"scene":scene})
        assert saved.status_code==200,saved.text
        queued=client.post("/v1/exports",json={"project_id":item["id"],"base_revision":2})
        assert queued.status_code==202,queued.text
        assert process_pending_jobs(client.app.state.session_factory,client.app.state.storage)==1
        if getattr(request,'param',None)=='editable':
            editable=client.post('/v1/exports',json={'project_id':item['id'],'base_revision':2,'kind':'editable'})
            assert editable.status_code==202,editable.text
            from services.api.editable_exports import process_editable_jobs
            assert process_editable_jobs(client.app.state.session_factory,client.app.state.storage)==1
        assert client.get("/v1/credits").status_code==200
        with client.app.state.session_factory() as db:
            row=db.get(Asset,asset["id"])
            row.metadata_json={"tag-shaped-json":{"__datetime__":"preserve this literal customer metadata"}}
            db.commit()
    monkeypatch.setenv("APP_ENV","test")
    monkeypatch.setenv("DATABASE_URL",settings.database_url)
    monkeypatch.setenv("STORAGE_BACKEND","local")
    monkeypatch.setenv("STORAGE_DIR",str(settings.storage_dir))
    output=tmp_path/"encrypted-backup"
    key=tmp_path/"separate.key"
    report=backup_script.backup(output,key)
    assert report["verified"] and report["objects_verified"]==(3 if getattr(request,'param',None)=='editable' else 2)
    credentials=tmp_path/"explicit-qa.json"
    credentials.write_text(json.dumps({"email":"owner@example.com"}),encoding="utf-8")
    return source,output,key,credentials


def test_encrypted_backup_reopens_authenticated_scene_ledger_assets_and_pdf(recovery,tmp_path):
    source,output,key,credentials=recovery
    unchanged=sha256((source/"source.db").read_bytes()).hexdigest()
    restored=tmp_path/"isolated-recovery"
    # Exercise a fresh process too: importing the API must not accidentally use
    # inherited cloud settings before the isolated app settings take effect.
    completed=subprocess.run([sys.executable,str(SCRIPTS/"backup-platform.py"),"--output",str(output),"--key-file",str(key),"--verify-only","--restore-dir",str(restored),"--qa-credentials",str(credentials)],
        env={**os.environ,"DATABASE_URL":"postgresql://must-not-use.invalid/source","STORAGE_BACKEND":"supabase","PAYMENT_PROVIDER":"toss_live","SMTP_HOST":"must-not-contact.invalid"},
        capture_output=True,text=True,timeout=60)
    assert completed.returncode==0,completed.stdout
    logical=json.loads(completed.stdout)
    assert logical["verified"] and logical["postgres_physical_restore_tested"] is False
    report=logical["application"]
    assert report["application_reopen_verified"] and report["ac31_scenario_covered"]
    assert report["offline_existing_owner_session"] and not report["google_authentication_tested"]
    assert report["projects_reopened"]==1 and report["revisions_reopened"]==2
    assert report["assets_reopened"]==report["scene_asset_links_verified"]==report["export_files_reopened"]==1
    assert report["ledger_entries_preserved"]>=1 and report["ledger_api_entries_reopened"]>=1
    assert report["external_network_enabled"] is report["source_database_used"] is report["postgres_physical_restore_tested"] is False
    assert sha256((source/"source.db").read_bytes()).hexdigest()==unchanged
    public_report=json.dumps(report)
    assert "owner@example.com" not in public_report and "safe-password-123" not in public_report
    with pytest.raises(FileExistsError):backup_script.verify(output,key,restored)


def test_recovery_requires_valid_explicit_credentials_and_blocks_external_http(recovery,tmp_path,monkeypatch):
    _,output,key,credentials=recovery
    restored=tmp_path/"isolated-recovery"
    backup_script.verify(output,key,restored)
    credentials.write_text(json.dumps({"email":"missing-owner@example.com"}),encoding="utf-8")
    with pytest.raises(ValueError,match="Recovered account selector"):
        verify_restored_app(restored,credentials)
    assert not (restored/"application-verification.json").exists()
    monkeypatch.setenv("DATABASE_URL","postgresql://must-not-use.invalid/source")
    monkeypatch.setenv("STORAGE_BACKEND","supabase")
    monkeypatch.setenv("PAYMENT_PROVIDER","toss_live")
    monkeypatch.setenv("SMTP_HOST","must-not-contact.invalid")
    with isolated_runtime(restored) as settings:
        assert settings.database_url.startswith("sqlite:///") and settings.storage_backend=="local"
        assert settings.ai_provider=="disabled" and not hasattr(settings,'smtp_host')
        with pytest.raises(RuntimeError,match="External HTTP is disabled"):
            httpx.get("https://must-not-contact.invalid")


@pytest.mark.parametrize("damage",["checksum","path-escape"])
def test_recovery_rejects_damaged_objects_and_path_escape(recovery,tmp_path,damage):
    _,output,key,_=recovery
    cipher=Fernet(key.read_bytes())
    encrypted=output/"application-backup.fernet"
    with ZipFile(BytesIO(cipher.decrypt(encrypted.read_bytes()))) as archive:
        contents={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(contents["manifest.json"])
    object_key=next(iter(manifest["objects"]))
    if damage=="checksum":contents[manifest["objects"][object_key]["path"]]=b"damaged"
    else:
        manifest["objects"]["../escaped-private-file"]=manifest["objects"].pop(object_key)
        contents["manifest.json"]=json.dumps(manifest).encode()
    stream=BytesIO()
    with ZipFile(stream,"w",ZIP_DEFLATED) as archive:
        for name,data in contents.items():archive.writestr(name,data)
    encrypted.write_bytes(cipher.encrypt(stream.getvalue()))
    restored=tmp_path/"failed-recovery"
    with pytest.raises(ValueError):backup_script.verify(output,key,restored)
    assert not (restored/"verification.json").exists()
    assert not (tmp_path/"escaped-private-file").exists()


def test_recovery_directory_must_not_contain_backup_or_key(recovery,tmp_path):
    _,output,key,_=recovery
    with pytest.raises(ValueError):backup_script.verify(output,key,tmp_path)
    with pytest.raises(ValueError):backup_script.verify(output,key,output/"plaintext")


def test_recovery_preserves_self_references_even_when_sql_rows_arrive_out_of_order(recovery,tmp_path):
    _,output,key,_=recovery
    cipher=Fernet(key.read_bytes())
    encrypted=output/"application-backup.fernet"
    with ZipFile(BytesIO(cipher.decrypt(encrypted.read_bytes()))) as archive:
        contents={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(contents["manifest.json"])
    rows=json.loads(contents["tables/credit_buckets.json"])
    child={**rows[0],"id":str(uuid4()),"kind":"compensation","grant_key":"test-child-before-parent","source_bucket_id":rows[0]["id"]}
    raw=json.dumps([child,*rows]).encode()
    contents["tables/credit_buckets.json"]=raw
    manifest["tables"]["credit_buckets"]={"rows":len(rows)+1,"sha256":sha256(raw).hexdigest()}
    contents["manifest.json"]=json.dumps(manifest).encode()
    stream=BytesIO()
    with ZipFile(stream,"w",ZIP_DEFLATED) as archive:
        for name,data in contents.items():archive.writestr(name,data)
    encrypted.write_bytes(cipher.encrypt(stream.getvalue()))
    assert backup_script.verify(output,key,tmp_path/"unordered-recovery")["verified"]


@pytest.mark.parametrize('recovery',['editable'],indirect=True)
def test_encrypted_backup_reopens_actual_editable_zip_with_scene_assets_fonts_and_license(recovery,tmp_path):
    source,output,key,credentials=recovery;unchanged=sha256((source/'source.db').read_bytes()).hexdigest()
    restored=tmp_path/'editable-recovery';backup_script.verify(output,key,restored)
    report=verify_restored_app(restored,credentials)
    assert report['application_reopen_verified'] and report['export_files_reopened']==2
    assert sha256((source/'source.db').read_bytes()).hexdigest()==unchanged


@pytest.fixture(scope='module')
def print_bundles(tmp_path_factory):
    from services.api.geometry import new_scene,validate_scene
    from services.api.exporters.print_profile import parse_print_profile
    from services.api.exporters.print_pdf import render_print_artifacts
    from services.api.exporters.production import export_production_bundle
    root=SCRIPTS.parent;directory=tmp_path_factory.mktemp('recovery-bundles')
    module_spec=spec_from_file_location('recovery_production_fixture',root/'tests/geometry_pdf/test_structures_production.py')
    fixture=module_from_spec(module_spec);module_spec.loader.exec_module(fixture)
    project,conditions=fixture.approved_project()
    legacy_dir=directory/'rgb';export_production_bundle(project,legacy_dir,conditions)
    icc=(root/'fixtures/icc/synthetic-cmyk-test.icc').read_bytes()
    profile=parse_print_profile({'icc_id':'test-fixture','icc_sha256':sha256(icc).hexdigest()})
    scene=validate_scene(new_scene('three-side-seal',160,230))
    test_dir=directory/'engine';render_print_artifacts({'id':'fixture','revision_id':'1','scene':scene},test_dir,profile,icc,test_mode=True)
    cmyk_dir=directory/'production';project['print_output']={'mode':'production','profile_id':conditions['profile']['id'],'requirements':profile}
    conditions['profile']['requirements']=profile
    export_production_bundle(project,cmyk_dir,conditions,icc_bytes=icc)
    result={}
    for name,path in [('legacy',legacy_dir),('engine',test_dir),('cmyk',cmyk_dir)]:
        content=BytesIO()
        with ZipFile(content,'w',ZIP_DEFLATED) as archive:
            for file in path.iterdir():archive.write(file,file.name)
        result[name]=content.getvalue()
    return result


@pytest.mark.parametrize('name',['legacy','engine','cmyk'])
def test_recovery_validates_actual_rgb_cmyk_and_test_zip_payloads(print_bundles,name):
    raw=print_bundles[name]
    _check_export(raw,'review_export' if name=='engine' else 'production_export',result={'format':'print_engine_zip'} if name=='engine' else {})


@pytest.mark.parametrize('damage',['hash','missing','duplicate','escape','size'])
def test_recovery_rejects_zip_manifest_damage_and_unsafe_members(print_bundles,damage):
    with ZipFile(BytesIO(print_bundles['engine'])) as archive:contents={name:archive.read(name) for name in archive.namelist()}
    if damage=='hash':contents['cut.pdf']+=b'changed'
    elif damage=='missing':contents.pop('fold.pdf')
    elif damage=='escape':contents['../escape.txt']=b'unsafe'
    elif damage=='size':
        manifest=json.loads(contents['manifest.json']);manifest['files'][0]['bytes']+=1;contents['manifest.json']=json.dumps(manifest).encode()
    raw=BytesIO()
    with ZipFile(raw,'w',ZIP_DEFLATED) as archive:
        for name,content in contents.items():archive.writestr(name,content)
        if damage=='duplicate':archive.writestr('cut.pdf',contents['cut.pdf'])
    with pytest.raises(ValueError):_check_export(raw.getvalue(),'review_export',result={'format':'print_engine_zip'})
