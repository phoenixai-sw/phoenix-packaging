from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import struct
import zlib
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select,func
from services.api.config import Settings
from services.api.database import utcnow
from services.api.main import create_app
from services.api.models import Asset,LoginSession
from services.api.feature_models import UploadSession,Membership
from services.api.storage import SupabaseStorage
from services.api.uploads import MAX_BYTES,QUOTA_BYTES,cleanup_quarantine
from services.api.tests.test_api import register,image_file
from services.api.tests.test_business import paid

class MemorySupabase(SupabaseStorage):
    def __init__(self):self.objects={};self.put_count=0;self.deleted=[]
    def put(self,key,body,content_type):self.objects[key]=bytes(body);self.put_count+=1
    def get(self,key):return self.objects[key]
    def get_limited(self,key,max_bytes):
        result=self.get(key)
        if len(result)>max_bytes:raise ValueError("over limit")
        return result
    def signed_upload_url(self,key):return "https://storage.example.test/upload/"+key+"?token=fixture-only"
    def signed_url(self,key,ttl=60,download_name=None):return "https://storage.example.test/download/"+key
    def delete(self,key):self.objects.pop(key,None);self.deleted.append(key)

@pytest.fixture
def direct(tmp_path,monkeypatch):
    storage=MemorySupabase();monkeypatch.setattr("services.api.main.build_storage",lambda _:storage)
    app=create_app(Settings(environment="test",database_url=f"sqlite:///{tmp_path/'upload.db'}",storage_dir=tmp_path/"storage",upload_limit=4*1024*1024))
    with TestClient(app) as client:
        auth=register(client)
        yield app,client,auth,storage

def initiate(client,size,content_type="image/png",name="image.png"):
    response=client.post("/v1/assets/uploads",json={"name":name,"byte_size":size,"content_type":content_type})
    assert response.status_code==201,response.text
    return response.json()["data"]

def quarantine(app,storage,session_id,data):
    with app.state.session_factory() as db:key=db.get(UploadSession,session_id).storage_key
    storage.objects[key]=data;return key

def complete(client,upload):return client.post(f"/v1/assets/uploads/{upload['id']}/complete")

def test_exact_20mib_limit_and_immutable_completed_asset(direct):
    app,client,auth,storage=direct
    assert client.post("/v1/assets/uploads",json={"name":"too-large.png","byte_size":MAX_BYTES+1,"content_type":"image/png"}).status_code==422
    base=image_file()[1];raw=base+b"\0"*(MAX_BYTES-len(base))
    upload=initiate(client,len(raw));key=quarantine(app,storage,upload["id"],raw)
    assert upload["max_bytes"]==MAX_BYTES and upload["method"]=="PUT" and upload["headers"]["x-upsert"]=="false"
    response=complete(client,upload);assert response.status_code==200,response.text
    asset=response.json()["data"];assert asset["byte_size"]==MAX_BYTES
    with app.state.session_factory() as db:
        row=db.get(Asset,asset["id"]);final_key=row.storage_key;assert final_key!=key and "/assets/" in final_key
        assert row.metadata_json["sha256"]==sha256(raw).hexdigest()
    storage.objects[key]=b"changed after completion"
    assert storage.get(final_key)==raw
    assert complete(client,upload).json()["data"]["id"]==asset["id"]
    assert storage.put_count==1

@pytest.mark.parametrize("case",["length","format","invalid","pixels","undecodable-pixels","actual-over-limit"])
def test_declared_and_actual_payload_are_validated_before_asset_copy(direct,case):
    app,client,auth,storage=direct;raw=image_file()[1];size=len(raw);mime="image/png"
    if case=="length":size+=1
    elif case=="format":mime="image/jpeg"
    elif case=="invalid":raw=b"not an image";size=len(raw)
    elif case=="pixels":
        data=bytearray(raw);data[16:24]=struct.pack(">II",8000,8000);data[29:33]=struct.pack(">I",zlib.crc32(bytes(data[12:29]))&0xffffffff);raw=bytes(data)
    elif case=="undecodable-pixels":
        def chunk(kind,data):return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
        raw=b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",1,1,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(b""))+chunk(b"IEND",b"");size=len(raw)
    elif case=="actual-over-limit":raw=b"a"*(MAX_BYTES+1)
    upload=initiate(client,size,mime);quarantine(app,storage,upload["id"],raw)
    response=complete(client,upload);assert response.status_code==422,response.text
    assert response.json()["code"]=="ASSET_INVALID" and storage.put_count==0
    assert complete(client,upload).status_code==409
    with app.state.session_factory() as db:
        assert db.get(UploadSession,upload["id"]).status=="rejected"
        assert db.scalar(select(func.count()).select_from(Asset))==0

def test_completion_replay_survives_quarantine_cleanup(direct):
    app,client,auth,storage=direct;raw=image_file()[1]
    upload=initiate(client,len(raw));key=quarantine(app,storage,upload["id"],raw)
    asset=complete(client,upload).json()["data"]
    with app.state.session_factory() as db:db.get(UploadSession,upload["id"]).created_at=utcnow()-timedelta(hours=3,minutes=1);db.commit()
    assert cleanup_quarantine(app.state.session_factory,storage)==1 and key not in storage.objects
    replay=complete(client,upload);assert replay.status_code==200 and replay.json()["data"]["id"]==asset["id"]
    assert cleanup_quarantine(app.state.session_factory,storage)==0 and storage.put_count==1

def test_pending_limit_expiry_quota_and_csrf(direct):
    app,client,auth,storage=direct
    assert client.post("/v1/assets/uploads",headers={"X-CSRF-Token":"wrong"},json={"name":"x","content_type":"image/png","byte_size":1}).status_code==403
    pending=[initiate(client,100) for _ in range(5)]
    denied=client.post("/v1/assets/uploads",json={"name":"sixth","content_type":"image/png","byte_size":100})
    assert denied.status_code==429
    with app.state.session_factory() as db:
        for row in db.scalars(select(UploadSession)):row.expires_at=utcnow()-timedelta(seconds=1)
        db.commit()
    assert complete(client,pending[0]).status_code==409
    with app.state.session_factory() as db:
        db.add(Asset(tenant_id=auth["tenant"]["id"],storage_key="quota-fixture",original_name="quota.png",content_type="image/png",byte_size=QUOTA_BYTES-99,width_px=1,height_px=1));db.commit()
    denied=client.post("/v1/assets/uploads",json={"name":"overflow","content_type":"image/png","byte_size":100})
    assert denied.status_code==422 and denied.json()["code"]=="ASSET_QUOTA_EXCEEDED"

def test_final_quota_rechecked_when_other_uploads_use_capacity(direct):
    app,client,auth,storage=direct;raw=image_file()[1];upload=initiate(client,len(raw));quarantine(app,storage,upload["id"],raw)
    with app.state.session_factory() as db:
        db.add(Asset(tenant_id=auth["tenant"]["id"],storage_key="quota-fixture",original_name="quota.png",content_type="image/png",byte_size=QUOTA_BYTES-len(raw)+1,width_px=1,height_px=1));db.commit()
    response=complete(client,upload)
    assert response.status_code==422 and response.json()["code"]=="ASSET_QUOTA_EXCEEDED" and storage.put_count==0

def test_cross_tenant_and_same_team_non_owner_cannot_complete(direct):
    app,client,auth,storage=direct;raw=image_file()[1];upload=initiate(client,len(raw));quarantine(app,storage,upload["id"],raw)
    other=TestClient(app);other_auth=register(other,"other@example.com")
    assert complete(other,upload).status_code==404
    paid(app,auth["tenant"]["id"],"pro")
    with app.state.session_factory() as db:
        db.add(Membership(tenant_id=auth["tenant"]["id"],user_id=other_auth["user"]["id"],role="editor",is_active=True))
        session=db.scalar(select(LoginSession).where(LoginSession.user_id==other_auth["user"]["id"]));session.active_tenant_id=auth["tenant"]["id"];db.commit()
    assert other.get("/v1/me").status_code==200
    assert complete(other,upload).status_code==404
    assert complete(client,upload).status_code==200;other.close()

def test_concurrent_complete_copies_once(direct):
    app,client,auth,storage=direct;raw=image_file()[1];upload=initiate(client,len(raw));quarantine(app,storage,upload["id"],raw)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:complete(client,upload),range(2)))
    assert [response.status_code for response in responses]==[200,200]
    assert len({response.json()["data"]["id"] for response in responses})==1 and storage.put_count==1
    with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(Asset))==1

def test_storage_stream_limit_and_signed_upload_response_validation(monkeypatch):
    real=httpx.Client;calls=[]
    class Stream(httpx.SyncByteStream):
        def __iter__(self):yield b"a"*8;yield b"b"*8
    def handler(request):
        calls.append(request)
        if request.method=="POST":return httpx.Response(200,json={"url":"/object/upload/sign/private-uploads/tenant/quarantine/id?token=fixture"})
        if request.method=="DELETE":return httpx.Response(200,json=[])
        return httpx.Response(200,stream=Stream())
    monkeypatch.setattr("services.api.storage.httpx.Client",lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    storage=SupabaseStorage(Settings(supabase_url="https://storage.example.test",supabase_service_role_key="fixture-key",supabase_storage_bucket="private"))
    assert storage.signed_upload_url("tenant/quarantine/id").startswith("https://storage.example.test/storage/v1/object/upload/sign/private-uploads/")
    with pytest.raises(ValueError):storage.get_limited("tenant/quarantine/id",10)
    assert storage.get_limited("tenant/quarantine/id",16)==b"a"*8+b"b"*8
    assert "/object/private-uploads/tenant/quarantine/id" in str(calls[-1].url)
    storage.delete("tenant/quarantine/id");assert calls[-1].url.path.endswith("/object/private-uploads")
    storage.delete("tenant/assets/id");assert calls[-1].url.path.endswith("/object/private")
    with pytest.raises(ValueError):storage.signed_upload_url("../other-tenant")
    with pytest.raises(ValueError):storage.signed_upload_url("tenant/assets/id")
