from copy import deepcopy
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import json
from fastapi.testclient import TestClient
from sqlalchemy import select,inspect,text
from alembic import command
from alembic.config import Config
from pypdf import PdfReader
from services.api.config import Settings
from services.api.main import create_app
from services.api.models import User,Project,Revision,Job
from services.api.feature_models import RegistryVersion
from services.api.tests.test_api import app,client,register,project
from services.api.tests.auth_helpers import google_login
from test_structure_v2 import separated


def admin(client,app):
    auth=register(client)
    app.state.settings.admin_emails=(auth["user"]["email"],)
    return auth


def registry(client,available=True):
    d=separated()
    response=client.post("/v1/admin/template-versions",json={"name":"자체 시험 도면","manufacturer":"가상 제조사 · 승인 아님","is_demo":True,
        "geometry_template_id":"three-side-seal","billing_family_key":"test-v2","source":"자체 작성 회귀시험","license":"시험용 자체 자료",
        "structure_definition":d,"review_available":available})
    assert response.status_code==201,response.text
    return response.json()["data"]


def clear_draft(client,item):
    scene=deepcopy(item["scene"])
    for f in scene["faces"]:f["objects"]=[]
    response=client.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":item["base_revision"],"scene":scene})
    assert response.status_code==200,response.text
    return response.json()["data"]


def apply(client,item,version):
    return client.patch(f"/v1/projects/{item['id']}/structure",json={"base_revision":item["base_revision"],"template_version_id":version["id"],"inputs":{"width_mm":160,"height_mm":230}})


def test_registry_draft_review_publication_is_not_manufacturing_approval(client,app):
    register(client)
    assert client.post("/v1/admin/template-versions",json={"name":"x","manufacturer":"x","source":"x","license":"x"}).status_code==403
    admin(client,app);hidden=registry(client,False)
    assert client.get("/v1/structures").json()["data"]["items"]==[]
    assert client.post("/v1/structures/preview",json={"template_version_id":hidden["id"],"inputs":{"width_mm":160,"height_mm":230}}).status_code==404
    visible=registry(client)
    item=client.get("/v1/structures").json()["data"]["items"][0]
    assert item["id"]==visible["id"] and item["status"]=="draft" and item["is_demo"] is True and item["production_enabled"] is False
    preview=client.post("/v1/structures/preview",json={"template_version_id":visible["id"],"inputs":{"width_mm":160,"height_mm":230}})
    assert preview.status_code==200 and preview.json()["data"]["geometry"]["faces"][0]["regions"]["safe"]["x_mm"]==13
    assert client.post("/v1/admin/template-versions/"+visible["id"]+"/approve",json={"evidence_asset_id":str(uuid4()),"notes":"No approval","approved_by_name":"test"}).status_code==422


def test_admin_validation_and_existing_layout_preview(client,app):
    register(client)
    assert client.post("/v1/admin/structures/validate",json={"structure_definition":separated()}).status_code==403
    admin(client,app)
    checked=client.post("/v1/admin/structures/validate",json={"structure_definition":separated()})
    assert checked.status_code==200,checked.text
    assert checked.json()["data"]["normalized_definition"]["recipe_id"]=="three-side-seal-separated-v1"
    version=registry(client);item=project(client)
    body={"template_version_id":version["id"],"inputs":{"width_mm":160,"height_mm":230},"project_id":item["id"],"base_revision":item["base_revision"]}
    blocked=client.post("/v1/structures/preview",json=body)
    assert blocked.status_code==200 and blocked.json()["data"]["can_apply"] is False
    assert blocked.json()["data"]["layout_issues"]
    assert apply(client,item,version).status_code==422
    item=clear_draft(client,item)
    body["base_revision"]=item["base_revision"]
    valid=client.post("/v1/structures/preview",json=body)
    assert valid.status_code==200 and valid.json()["data"]["can_apply"] is True
    body["base_revision"]-=1
    assert client.post("/v1/structures/preview",json=body).status_code==409


def test_registered_crop_quality_derivative_uses_same_snapshot(client,app):
    from PIL import Image
    import pytest
    admin(client,app);version=registry(client);item=clear_draft(client,project(client));item=apply(client,item,version).json()["data"]
    image=BytesIO();Image.new("RGB",(300,600),"#2a765c").save(image,format="PNG")
    asset=client.post("/v1/assets",files={"file":("source.png",image.getvalue(),"image/png")})
    assert asset.status_code==201,asset.text
    scene=deepcopy(item["scene"])
    scene["faces"][0]["objects"]=[{"id":"cropped","type":"image","face_id":"front","asset_id":asset.json()["data"]["id"],"x_mm":25,"y_mm":40,"width_mm":50,"height_mm":100,"crop":{"x":.5,"y":0,"width":.5,"height":1}}]
    saved=client.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":item["base_revision"],"scene":scene})
    assert saved.status_code==200,saved.text
    item=saved.json()["data"];body={"project_id":item["id"],"base_revision":item["base_revision"],"face_id":"front","object_id":"cropped"}
    inspected=client.post("/v1/image-quality/inspect",json=body)
    assert inspected.status_code==200,inspected.text
    quality=inspected.json()["data"];assert quality["effective_ppi"]==pytest.approx(76.2)
    preview=client.post("/v1/image-quality/preview",json={**body,"operation_key":"v2-crop-quality","source_sha256":quality["source"]["sha256"]})
    assert preview.status_code==201,preview.text
    assert preview.json()["data"]["patch"]["crop"] is None


def test_apply_save_clone_history_and_frozen_worker_export(client,app):
    admin(client,app);version=registry(client);original=clear_draft(client,project(client))
    old_revision=original["base_revision"]
    response=apply(client,original,version);assert response.status_code==200,response.text
    item=response.json()["data"];frozen=deepcopy(item["structure_snapshot"])
    assert item["review_only"] and item["print_profile_version_id"] is None
    assert apply(client,original,version).status_code==409
    scene=deepcopy(item["scene"])
    scene["faces"][0]["objects"]=[{"id":"registered-title","type":"text","face_id":"front","x_mm":20,"y_mm":35,"width_mm":115,"height_mm":20,"text":"동결 구조 저장 검증","font_size_pt":18,"font_weight":700,"font_id":"NotoSansKR"}]
    saved=client.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":item["base_revision"],"scene":scene})
    assert saved.status_code==200,saved.text
    item=saved.json()["data"]
    assert item["structure_snapshot"]==frozen
    reopened=client.get(f"/v1/projects/{item['id']}").json()["data"]
    assert reopened["scene"]["structure_ref"]==item["scene"]["structure_ref"]
    duplicate=client.post(f"/v1/projects/{item['id']}/duplicate",json={"name":"구조 복제"})
    assert duplicate.status_code==201 and duplicate.json()["data"]["structure_snapshot"]==frozen
    preflight=client.post("/v1/preflight",json={"project_id":item["id"],"base_revision":item["base_revision"],"kind":"production"})
    assert preflight.status_code==200,preflight.text
    assert "STRUCTURE_V2_PRODUCTION_UNSUPPORTED" in {x["code"] for x in preflight.json()["data"]["blockers"]}
    barcode=client.post("/v1/geometry/barcode",json={"project_id":item["id"],"base_revision":item["base_revision"],"scene":item["scene"],"face_id":"back","barcode_usage":"sample"})
    assert barcode.status_code==200,barcode.text
    queued=client.post("/v1/exports",json={"project_id":item["id"],"base_revision":item["base_revision"]})
    assert queued.status_code==202,queued.text
    jid=queued.json()["data"]["id"]
    editable=client.post("/v1/exports",json={"project_id":item["id"],"base_revision":item["base_revision"],"kind":"editable"})
    assert editable.status_code==202,editable.text
    editable_id=editable.json()["data"]["id"]
    with app.state.session_factory() as db:
        job=db.get(Job,jid);assert job.snapshot["structure_snapshot"]==frozen
        row=db.get(RegistryVersion,version["id"]);details=deepcopy(row.details);details["structure_definition"]["seals_mm"]["left"]=30;row.details=details;row.status="revoked";db.commit()
    processed=client.post("/v1/internal/jobs/process",headers={"Authorization":"Bearer test-worker-secret"})
    assert processed.status_code==200,processed.text
    job=client.get("/v1/jobs/"+jid).json()["data"]
    assert job["status"]=="succeeded",job
    assert job["result"]["manifest"]["geometry_hash"]==frozen["geometry_hash"]
    pdf=client.get(f"/v1/exports/{jid}/download")
    assert pdf.status_code==200 and "동결 구조 저장 검증" in PdfReader(BytesIO(pdf.content)).pages[0].extract_text()
    from zipfile import ZipFile
    archive=client.get(f"/v1/exports/{editable_id}/download")
    assert archive.status_code==200,client.get("/v1/jobs/"+editable_id).text
    with ZipFile(BytesIO(archive.content)) as zipped:
        assert json.loads(zipped.read("scene.json"))==item["scene"]
        assert json.loads(zipped.read("geometry.json"))==item["geometry"]
        assert json.loads(zipped.read("structure.json"))==frozen
    assert client.get("/v1/structures").json()["data"]["items"]==[]
    # Historical scene restore deliberately keeps CURRENT physical structure.
    with app.state.session_factory() as db:
        revision=db.scalar(select(Revision).where(Revision.project_id==item["id"],Revision.number==old_revision))
        rid=revision.id;assert revision.structure_snapshot is None
        latest=db.scalar(select(Revision).where(Revision.project_id==item["id"],Revision.number==item["base_revision"]))
        assert latest.structure_snapshot==frozen
    restored=client.post(f"/v1/projects/{item['id']}/revisions/{rid}/restore",json={"base_revision":item["base_revision"]})
    assert restored.status_code==200,restored.text
    assert restored.json()["data"]["structure_snapshot"]==frozen


def test_structure_apply_permissions_lease_features_and_client_snapshot_injection(client,app):
    admin(client,app);version=registry(client);item=clear_draft(client,project(client))
    with TestClient(app) as other:
        register(other,"other-structure@example.com")
        assert apply(other,item,version).status_code==404
    lease=client.post(f"/v1/projects/{item['id']}/edit-session",json={"editor_id":str(uuid4())}).json()["data"]
    assert apply(client,item,version).status_code==423
    client.headers["X-Editor-Lease"]=lease["lease_token"]
    response=apply(client,item,version);assert response.status_code==200,response.text
    item=response.json()["data"]
    scene=deepcopy(item["scene"]);scene["structure_snapshot"]={"approved":True}
    assert client.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":item["base_revision"],"scene":scene}).status_code==422
    scene=deepcopy(item["scene"]);scene["holes"]=[{"id":"hole","face_id":"front","center_x_mm":80,"center_y_mm":20,"diameter_mm":6}]
    response=client.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":item["base_revision"],"scene":scene})
    assert response.status_code==422 and response.json()["code"]=="REGISTERED_FEATURES_UNSUPPORTED"
    assert client.patch(f"/v1/projects/{item['id']}/settings",json={"base_revision":item["base_revision"],"template_version_id":None}).status_code==422


def test_snapshot_survives_application_restart(tmp_path):
    settings=Settings(environment="test",database_url=f"sqlite:///{tmp_path/'restart.db'}",storage_dir=tmp_path/"storage")
    with TestClient(create_app(settings)) as first:
        admin(first,first.app);version=registry(first);item=clear_draft(first,project(first));response=apply(first,item,version)
        assert response.status_code==200,response.text
        frozen=response.json()["data"]
    with TestClient(create_app(settings)) as second:
        google_login(second)
        assert second.get("/v1/projects/"+item["id"]).json()["data"]["structure_snapshot"]==frozen["structure_snapshot"]


def test_additive_0010_keeps_legacy_json_without_backfill(tmp_path,monkeypatch):
    from sqlalchemy import create_engine,MetaData,Table
    from sqlalchemy.orm import Session
    from services.api.models import Tenant
    from services.api.database import utcnow
    path=tmp_path/"migration.db";monkeypatch.setenv("DATABASE_URL",f"sqlite:///{path}");monkeypatch.setenv("APP_ENV","test")
    cfg=Config("services/api/alembic.ini");command.upgrade(cfg,"0009_editable_exports")
    engine=create_engine(f"sqlite:///{path}")
    metadata=MetaData();projects=Table("projects",metadata,autoload_with=engine);revisions=Table("project_revisions",metadata,autoload_with=engine)
    original_scene={"schema_version":"1.0","faces":[{"id":"front","text":"변경 없이 보존"}]}
    pid,rid=str(uuid4()),str(uuid4())
    with Session(engine) as db:
        tenant=Tenant(name="Legacy");db.add(tenant);db.flush()
        user=User(tenant_id=tenant.id,name="Owner",email="old-structure@example.com");db.add(user);db.flush()
        db.execute(projects.insert().values(id=pid,tenant_id=tenant.id,created_by=user.id,name="Legacy",product_name="원본",brand_name="",description="",template_id="three-side-seal",width_mm=160,height_mm=230,base_revision=1,scene=original_scene,material="",created_at=utcnow(),updated_at=utcnow()))
        db.execute(revisions.insert().values(id=rid,tenant_id=tenant.id,project_id=pid,number=1,scene=original_scene,reason="manual",created_at=utcnow()));db.commit()
    with engine.begin() as db:
        # Historical schema is inspected directly, not through the latest ORM.
        assert "structure_snapshot" not in {c["name"] for c in inspect(db).get_columns("projects")}
    command.upgrade(cfg,"0010_structure_snapshots")
    with engine.begin() as db:
        assert {"structure_snapshot"}<={c["name"] for c in inspect(db).get_columns("projects")}
        assert "structure_snapshot" in {c["name"] for c in inspect(db).get_columns("project_revisions")}
        assert db.scalar(select(projects.c.scene).where(projects.c.id==pid))==original_scene
        assert db.scalar(select(revisions.c.scene).where(revisions.c.id==rid))==original_scene
        assert db.execute(text("SELECT structure_snapshot FROM projects WHERE id=:id"),{"id":pid}).scalar_one() is None
    command.downgrade(cfg,"0009_editable_exports")
    with engine.begin() as db:
        assert "structure_snapshot" not in {c["name"] for c in inspect(db).get_columns("projects")}
        assert db.scalar(select(projects.c.scene).where(projects.c.id==pid))==original_scene
        assert db.scalar(select(revisions.c.scene).where(revisions.c.id==rid))==original_scene
    engine.dispose()
