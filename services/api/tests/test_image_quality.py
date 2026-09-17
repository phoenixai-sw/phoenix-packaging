from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select

from services.api.config import Settings
from services.api.errors import APIError
from services.api.geometry import new_scene
from services.api.image_quality import install_image_quality_routes, make_derivative
from services.api.image_quality_metadata import image_quality_metrics
from services.api.main import create_app, asset_payload
from services.api.models import Asset, Project, User
from services.api.tests.test_api import register, project
from services.api.tests.test_business import paid


def png(size=(32, 40), alpha=False):
    image = Image.new("RGBA" if alpha else "RGB", size)
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel((x, y), (x % 256, y % 256, (x+y) % 256, 80+x % 175) if alpha else (x % 256, y % 256, (x+y) % 256))
    stream=BytesIO();image.save(stream,"PNG");return stream.getvalue()


def image_object(asset_id, **extra):
    return {"id":"image-one","type":"image","face_id":"front","asset_id":asset_id,
            "x_mm":0,"y_mm":0,"width_mm":160,"height_mm":230,"rotation_deg":0,
            "z_index":0,"visible":True,"print_enabled":True,**extra}


@pytest.fixture
def quality(tmp_path):
    app=create_app(Settings(environment="test",database_url=f"sqlite:///{tmp_path/'quality.db'}",storage_dir=tmp_path/"storage"))
    if not any(getattr(route,"path",None)=="/v1/image-quality/inspect" for route in app.routes):
        def session():
            with app.state.session_factory() as db:yield db
        install_image_quality_routes(app,session,asset_payload)
    with TestClient(app) as client:
        auth=register(client);created=project(client);raw=png();asset_id=str(uuid4())
        key=f"{auth['tenant']['id']}/assets/{asset_id}";app.state.storage.put(key,raw,"image/png")
        with app.state.session_factory() as db:
            db.add(Asset(id=asset_id,tenant_id=auth["tenant"]["id"],storage_key=key,original_name="source.png",content_type="image/png",byte_size=len(raw),width_px=32,height_px=40,metadata_json={"sha256":sha256(raw).hexdigest()}))
            row=db.get(Project,created["id"]);scene=new_scene("three-side-seal",160,230)
            scene["faces"][0]["objects"]=[image_object(asset_id)];row.scene=scene;db.commit()
        body={"project_id":created["id"],"base_revision":1,"face_id":"front","object_id":"image-one","target_ppi":72}
        yield app,client,auth,body,asset_id,raw


def inspect(client,body):
    response=client.post("/v1/image-quality/inspect",json=body)
    assert response.status_code==200,response.text
    return response.json()["data"]


def preview_body(client,body,**extra):
    return {**body,"operation_key":str(uuid4()),"source_sha256":inspect(client,body)["source"]["sha256"],"resample":True,"bleed_mode":"edge",**extra}


def test_inspect_actual_pixels_mm_and_missing_bleed(quality):
    app,client,auth,body,source,raw=quality
    data=inspect(client,body)
    assert data["source"]=={"id":source,"sha256":sha256(raw).hexdigest(),"width_px":32,"height_px":40}
    assert data["effective_ppi"]==pytest.approx(40*25.4/230)
    assert data["original_effective_ppi"]==data["effective_ppi"]
    assert data["bleed_missing_mm"]==dict.fromkeys(("left","right","top","bottom"),3)
    assert data["required_pixels"]=={"width":454,"height":652}


@pytest.mark.parametrize("mode",["edge","mirror"])
def test_real_padding_preserves_original_rgba_and_creates_expected_borders(mode):
    raw=png((4,4),True);obj=image_object(str(uuid4()),width_mm=4,height_mm=4)
    result,patch,lineage=make_derivative(raw,{"width_mm":4,"height_mm":4},obj,{},resample=False,bleed_mode=mode)
    with Image.open(BytesIO(result)) as image, Image.open(BytesIO(raw)) as original:
        assert image.size==(10,10)
        assert image.crop((3,3,7,7)).tobytes()==original.tobytes()
        assert image.getpixel((2,3))==original.getpixel((0,0))
        assert image.getpixel((1,3))==original.getpixel((0 if mode=="edge" else 1,0))
        assert image.getpixel((8,3))==original.getpixel((3 if mode=="edge" else 2,0))
    assert patch=={"x_mm":-3,"y_mm":-3,"width_mm":10,"height_mm":10}
    assert lineage["resampled"] is False and lineage["extended"] is True


def test_reflection_handles_padding_larger_than_source():
    raw=png((2,2));obj=image_object(str(uuid4()),width_mm=2,height_mm=2)
    result,_,_=make_derivative(raw,{"width_mm":2,"height_mm":2},obj,{},resample=False,bleed_mode="mirror")
    with Image.open(BytesIO(result)) as im:
        assert [im.getpixel((x,3))[0] for x in range(8)]==[1,1,0,0,1,1,0,0]


def test_preview_immutable_source_scene_and_idempotency(quality):
    app,client,auth,body,source,raw=quality;request=preview_body(client,body)
    before=client.get(f"/v1/projects/{body['project_id']}").json()["data"]
    response=client.post("/v1/image-quality/preview",json=request);assert response.status_code==201,response.text
    data=response.json()["data"];assert data["patch"]["asset_id"]!=source and data["credits_charged"]==0
    after=client.get(f"/v1/projects/{body['project_id']}").json()["data"]
    assert before["scene"]==after["scene"] and after["base_revision"]==1
    assert data["quality"]["effective_ppi"]>=72
    assert data["quality"]["original_effective_ppi"]==pytest.approx(40*25.4/230)
    assert data["quality"]["bleed_missing_mm"]==dict.fromkeys(("left","right","top","bottom"),0)
    with app.state.session_factory() as db:
        assert app.state.storage.get(db.get(Asset,source).storage_key)==raw
        assert db.scalar(select(func.count()).select_from(Asset))==2
        assert db.get(Asset,data["asset"]["id"]).metadata_json["image_quality"]["root_source_asset_id"]==source
    assert client.post("/v1/image-quality/preview",json=request).json()["data"]["asset"]["id"]==data["asset"]["id"]
    changed=client.post("/v1/image-quality/preview",json={**request,"target_ppi":100})
    assert changed.status_code==409 and changed.json()["code"]=="IDEMPOTENCY_CONFLICT"


def test_repeated_resampling_retains_root_detail_and_production_gate(quality):
    app,client,auth,body,source,raw=quality
    response=client.post("/v1/image-quality/preview",json=preview_body(client,body))
    data=response.json()["data"]
    scene=client.get(f"/v1/projects/{body['project_id']}").json()["data"]["scene"]
    scene["faces"][0]["objects"][0].update(data["patch"])
    saved=client.patch(f"/v1/projects/{body['project_id']}/draft",json={"base_revision":1,"scene":scene});assert saved.status_code==200,saved.text
    body={**body,"base_revision":2,"target_ppi":150}
    second=client.post("/v1/image-quality/preview",json=preview_body(client,body,bleed_mode="none"));assert second.status_code==201,second.text
    value=second.json()["data"]
    assert value["provenance"]["root_source_asset_id"]==source
    assert value["provenance"]["original_source_pixels"]==[32,40]
    assert value["quality"]["original_effective_ppi"]==pytest.approx(40*25.4/230)
    from services.api.exporters.preflight import preflight_project
    scene["faces"][0]["objects"][0].update(value["patch"])
    with app.state.session_factory() as db:
        def resolver(identity):return app.state.storage.get(db.get(Asset,identity).storage_key)
        resolver.metadata=lambda identity:db.get(Asset,identity).metadata_json
        report=preflight_project({"scene":scene},asset_resolver=resolver)
    codes={item["code"] for item in report["issues"]}
    assert "ORIGINAL_LOW_PPI" in codes and "BASIC_ORIGINAL_LOW_PPI" in codes and "BASIC_SYNTHETIC_BLEED" in codes
    assert "BASIC_ARTWORK_BLEED" not in codes


def test_revision_hash_and_untrusted_metadata_checks(quality):
    app,client,auth,body,source,raw=quality;request=preview_body(client,body)
    assert client.post("/v1/image-quality/preview",json={**request,"base_revision":2}).status_code==409
    assert client.post("/v1/image-quality/preview",json={**request,"source_sha256":"0"*64}).status_code==409
    assert client.post("/v1/image-quality/preview",json={**request,"native_equivalent_pixels":[9000,9000]}).status_code==422
    with app.state.session_factory() as db:
        asset=db.get(Asset,source);app.state.storage.put(asset.storage_key,png((32,39)),"image/png")
    assert client.post("/v1/image-quality/inspect",json=body).json()["code"]=="SOURCE_HASH_MISMATCH"


def test_cross_tenant_viewer_and_csrf(quality):
    app,client,auth,body,source,raw=quality;request=preview_body(client,body)
    with TestClient(app) as other:
        register(other,"other-quality@example.com")
        assert other.post("/v1/image-quality/inspect",json=body).status_code==404
        assert other.post("/v1/image-quality/preview",json=request).status_code==404
    assert client.post("/v1/image-quality/preview",headers={"X-CSRF-Token":"bad"},json=request).status_code==403
    paid(app,auth["tenant"]["id"])
    with app.state.session_factory() as db:db.get(User,auth["user"]["id"]).role="viewer";db.commit()
    assert client.post("/v1/image-quality/inspect",json=body).status_code==200
    assert client.post("/v1/image-quality/preview",json=request).status_code==403


def test_ppi_pixel_limit_and_no_change(quality):
    app,client,auth,body,source,raw=quality;request=preview_body(client,body)
    assert client.post("/v1/image-quality/preview",json={**request,"target_ppi":601}).status_code==422
    assert client.post("/v1/image-quality/preview",json={**request,"target_ppi":True}).status_code==422
    assert client.post("/v1/image-quality/preview",json={**request,"resample":False,"bleed_mode":"none"}).json()["code"]=="IMAGE_QUALITY_NO_CHANGE"
    with pytest.raises(APIError) as error:
        make_derivative(raw,{"width_mm":800,"height_mm":800},image_object(source,width_mm=800,height_mm=800),{},target_ppi=600)
    assert error.value.code=="IMAGE_PIXEL_LIMIT"


def test_rotated_image_resample_only():
    raw=png();obj=image_object(str(uuid4()),rotation_deg=10)
    with pytest.raises(APIError) as error:make_derivative(raw,{"width_mm":160,"height_mm":230},obj,{},bleed_mode="edge")
    assert error.value.code=="ROTATED_BLEED_UNSUPPORTED"
    result,patch,_=make_derivative(raw,{"width_mm":160,"height_mm":230},obj,{},target_ppi=72)
    assert patch["x_mm"]==0 and result!=raw


def test_original_density_unchanged_at_threshold_after_extension():
    raw=png((117,117));obj=image_object(str(uuid4()),width_mm=10,height_mm=10)
    _,patch,lineage=make_derivative(raw,{"width_mm":10,"height_mm":10},obj,{},target_ppi=600,bleed_mode="edge")
    metrics=image_quality_metrics(lineage["output_pixels"],{**obj,**patch},{"image_quality":lineage})
    assert metrics["original_effective_ppi"]==pytest.approx(117*25.4/10)
    assert metrics["original_effective_ppi"]<300 and metrics["effective_ppi"]>=600


def test_workspace_asset_isolation(quality):
    from services.api.feature_models import Workspace
    app,client,auth,body,source,raw=quality;request=preview_body(client,body)
    paid(app,auth["tenant"]["id"])
    with app.state.session_factory() as db:
        workspace=Workspace(tenant_id=auth["tenant"]["id"],name="private")
        db.add(workspace);db.flush();db.get(Asset,source).workspace_id=workspace.id
        db.get(User,auth["user"]["id"]).role="editor";db.commit()
    assert client.post("/v1/image-quality/inspect",json=body).status_code==404
    assert client.post("/v1/image-quality/preview",json=request).status_code==404


def test_concurrent_same_key_creates_one_derivative(quality):
    app,client,auth,body,source,raw=quality;request=preview_body(client,body)
    cookie=client.cookies.get("phoenix_session");csrf=client.headers["X-CSRF-Token"]
    def run(_):
        with TestClient(app) as each:
            each.cookies.set("phoenix_session",cookie);each.headers["X-CSRF-Token"]=csrf
            response=each.post("/v1/image-quality/preview",json=request)
            assert response.status_code==201,response.text
            return response.json()["data"]["asset"]["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert results[0]==results[1]
    with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(Asset).where(Asset.source=="image_quality"))==1


def test_byte_quota_and_rate_limits(quality,monkeypatch):
    app,client,auth,body,source,raw=quality;request=preview_body(client,body)
    monkeypatch.setattr("services.api.image_quality.QUOTA_BYTES",len(raw))
    response=client.post("/v1/image-quality/preview",json=request)
    assert response.status_code==422 and response.json()["code"]=="ASSET_QUOTA_EXCEEDED"
    with app.state.session_factory() as db:
        for n in range(20):
            db.add(Asset(tenant_id=auth["tenant"]["id"],storage_key=f"fixture-limit/{n}",original_name="limit",content_type="image/png",byte_size=1,width_px=1,height_px=1,source="image_quality"))
        db.commit()
    assert client.post("/v1/image-quality/preview",json=request).json()["code"]=="IMAGE_QUALITY_RATE_LIMIT"


def test_live_preflight_and_review_worker_keep_provenance(quality):
    from services.api.jobs import process_pending_jobs
    app,client,auth,body,source,raw=quality
    response=client.post("/v1/image-quality/preview",json=preview_body(client,body,target_ppi=300))
    assert response.status_code==201,response.text
    patch=response.json()["data"]["patch"]
    scene=client.get(f"/v1/projects/{body['project_id']}").json()["data"]["scene"]
    scene["faces"][0]["objects"][0].update(patch)
    assert client.patch(f"/v1/projects/{body['project_id']}/draft",json={"base_revision":1,"scene":scene}).status_code==200
    preflight=client.post("/v1/preflight",json={"project_id":body["project_id"],"base_revision":2,"kind":"review"})
    assert preflight.status_code==200,preflight.text
    assert "BASIC_ORIGINAL_LOW_PPI" in {i["code"] for i in preflight.json()["data"]["warnings"]}
    exported=client.post("/v1/exports",json={"project_id":body["project_id"],"base_revision":2,"kind":"review"})
    assert exported.status_code==202,exported.text
    assert process_pending_jobs(app.state.session_factory,app.state.storage)==1
    data=client.get(f"/v1/jobs/{exported.json()['data']['id']}").json()["data"]
    assert data["status"]=="succeeded",data
    assert "BASIC_ORIGINAL_LOW_PPI" in {i["code"] for i in data["result"]["manifest"]["basic_preflight"]["issues"]}
    assert client.get(data["download_url"]).content.startswith(b"%PDF-")


def test_pending_upload_bytes_are_reserved(quality,monkeypatch):
    from datetime import timedelta
    from services.api.database import utcnow
    from services.api.feature_models import UploadSession
    app,client,auth,body,source,raw=quality;request=preview_body(client,body)
    monkeypatch.setattr("services.api.image_quality.QUOTA_BYTES",100_000)
    with app.state.session_factory() as db:
        db.add(UploadSession(tenant_id=auth["tenant"]["id"],user_id=auth["user"]["id"],storage_key="pending/fixture",name="pending.png",content_type="image/png",byte_size=99_900,expires_at=utcnow()+timedelta(minutes=10)))
        db.commit()
    assert client.post("/v1/image-quality/preview",json=request).json()["code"]=="ASSET_QUOTA_EXCEEDED"
