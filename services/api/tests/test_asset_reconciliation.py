from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256

import httpx
import pytest
from sqlalchemy import func, select

from services.api.asset_reconciliation import PROBE_INTERVAL, probe_asset, reconcile_ai_assets
from services.api.billing.models import CreditBucket, LedgerEntry
from services.api.billing.policy import aware
from services.api.billing.service import wallet_summary
from services.api.database import utcnow
from services.api.feature_models import AiUnit, AuditEvent, ProviderAttempt
from services.api.models import Asset
from services.api.storage import SupabaseStorage
from services.api.config import Settings
from services.api.tests.test_ai import ai, job, run, image_result, state, credits


def completed(ai,units=1):
    app,client,auth,item=ai
    created,_=job(client,item,units)
    assert run(app,lambda *_:image_result())==units
    with app.state.session_factory() as db:
        assets=list(db.scalars(select(Asset).order_by(Asset.id)))
    return app,client,auth,item,created,assets


@pytest.mark.parametrize("failure",["missing","corrupt"])
def test_confirmed_loss_compensates_once_blocks_download_and_never_regenerates(ai,failure):
    app,client,auth,item,created,assets=completed(ai)
    asset=assets[0];path=app.state.storage.path(asset.storage_key)
    if failure=="missing":path.unlink()
    else:path.write_bytes(b"confirmed corrupted PNG")
    first=utcnow()+PROBE_INTERVAL
    assert reconcile_ai_assets(app.state.session_factory,app.state.storage,now=first)==1
    assert credits(client)["available"]==20 and state(client,created["id"])["status"]=="succeeded"
    assert reconcile_ai_assets(app.state.session_factory,app.state.storage,now=first+timedelta(seconds=1))==0
    assert reconcile_ai_assets(app.state.session_factory,app.state.storage,now=first+PROBE_INTERVAL)==1
    assert credits(client)["available"]==30
    result=state(client,created["id"])
    assert result["status"]=="reconciliation_required" and result["credit_returned"]==10 and result["credit_charged"]==0
    assert result["result"]["assets"]==[]
    response=client.get(f"/v1/assets/{asset.id}/content")
    assert response.status_code==410 and response.json()["code"]=="ASSET_UNAVAILABLE_COMPENSATED"
    edit=client.post("/v1/quotes",json={"project_id":item["id"],"base_revision":1,"action":"image.edit.standard","requested_units":1,"prompt":"change image color","reference_asset_id":asset.id})
    assert edit.status_code==410
    assert reconcile_ai_assets(app.state.session_factory,app.state.storage,now=first+2*PROBE_INTERVAL)==0
    assert run(app,lambda *_:pytest.fail("never regenerate a compensated image"))==0
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=="COMPENSATE"))==1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action=="asset_loss_compensated"))==1
        assert db.scalar(select(ProviderAttempt)).cost_usd==0.0065


def test_healthy_read_clears_loss_suspicion(ai):
    app,client,_,_,created,assets=completed(ai)
    path=app.state.storage.path(assets[0].storage_key);content=path.read_bytes();path.unlink()
    now=utcnow()+PROBE_INTERVAL
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now)
    path.write_bytes(content)
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now+PROBE_INTERVAL)
    assert credits(client)["available"]==20 and state(client,created["id"])["status"]=="succeeded"
    path.unlink()
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now+timedelta(days=1)+PROBE_INTERVAL)
    assert credits(client)["available"]==20  # Old missing observation was cleared.


def test_transient_read_error_clears_confirmation_and_never_compensates(ai):
    app,client,_,_,_,assets=completed(ai)
    path=app.state.storage.path(assets[0].storage_key);path.unlink()
    now=utcnow()+PROBE_INTERVAL
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now)
    class TimeoutStorage:
        def get(self,key):raise httpx.ReadTimeout("transient storage outage")
    reconcile_ai_assets(app.state.session_factory,TimeoutStorage(),now=now+PROBE_INTERVAL)
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now+2*PROBE_INTERVAL)
    assert credits(client)["available"]==20
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now+3*PROBE_INTERVAL)
    assert credits(client)["available"]==30


def test_two_workers_cannot_double_compensate_or_count_same_probe_twice(ai):
    app,client,_,_,_,assets=completed(ai)
    app.state.storage.path(assets[0].storage_key).unlink()
    now=utcnow()+PROBE_INTERVAL
    def parallel(at):
        with ThreadPoolExecutor(max_workers=2) as pool:
            return sum(pool.map(lambda _:reconcile_ai_assets(app.state.session_factory,app.state.storage,now=at),range(2)))
    assert parallel(now)==1 and credits(client)["available"]==20
    assert parallel(now+PROBE_INTERVAL)==1 and credits(client)["available"]==30


def test_expired_trial_loss_restores_same_scope_for_seven_days(ai):
    app,_,auth,_,_,assets=completed(ai)
    app.state.storage.path(assets[0].storage_key).unlink()
    now=utcnow()+timedelta(days=15)
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now)
    confirmed=now+PROBE_INTERVAL
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=confirmed)
    with app.state.session_factory() as db:
        compensation=db.scalar(select(CreditBucket).where(CreditBucket.kind=="compensation"))
        assert compensation.scope=="standard_only" and compensation.available==10
        assert aware(compensation.expires_at)==confirmed+timedelta(days=7)
        assert wallet_summary(db,auth["tenant"]["id"],now=confirmed)["available"]==10


def test_partial_job_loss_keeps_other_image_and_rotates_bounded_checks(ai):
    app,client,_,_,created,assets=completed(ai,2)
    now=utcnow()+PROBE_INTERVAL
    assert reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now,limit=1)==1
    assert reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now,limit=1)==1
    assert reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now+PROBE_INTERVAL)==0
    app.state.storage.path(assets[0].storage_key).unlink()
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now+timedelta(days=1))
    reconcile_ai_assets(app.state.session_factory,app.state.storage,now=now+timedelta(days=1)+PROBE_INTERVAL)
    result=state(client,created["id"])
    assert result["status"]=="partially_succeeded" and len(result["result"]["assets"])==1
    assert result["credit_charged"]==10 and result["credit_returned"]==10


@pytest.mark.parametrize("status,body",[(500,{"code":"InternalError"}),(401,{"code":"InvalidJWT"}),(403,{"code":"AccessDenied"}),(404,{"code":"NoSuchBucket"}),(404,{"code":"not_found"}),(404,{"code":"NoSuchKey"})])
def test_http_failure_is_uncertain_without_independent_absence_confirmation(status,body):
    class Ambiguous:
        def get(self,key):
            response=httpx.Response(status,json=body,request=httpx.Request("GET","https://storage.test/object"));response.raise_for_status()
        def confirm_missing(self,key):return False
    assert probe_asset(Ambiguous(),"tenant/object",sha256(b"original").hexdigest(),8)[0]=="uncertain"


def test_supabase_absence_requires_privileged_healthy_bucket_and_successful_list(monkeypatch):
    original=httpx.Client;responses={"bucket":200,"items":[]}
    def handler(request):
        if "/bucket/" in request.url.path:return httpx.Response(responses["bucket"],json={"id":"phoenix-private"})
        return httpx.Response(200,json=responses["items"])
    monkeypatch.setattr("services.api.storage.httpx.Client",lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    storage=SupabaseStorage(Settings(supabase_url="https://storage.test",supabase_service_role_key="sb_secret_test"))
    assert storage.confirm_missing("tenant/ai/image.png") is True
    responses["items"]=[{"name":"image.png"}]
    assert storage.confirm_missing("tenant/ai/image.png") is False
    responses["items"]=[{}]
    assert storage.confirm_missing("tenant/ai/image.png") is False
    responses["items"]=[];responses["bucket"]=403
    assert storage.confirm_missing("tenant/ai/image.png") is False
    responses["bucket"]=200;storage.headers["apikey"]="anon_or_invalid"
    assert storage.confirm_missing("tenant/ai/image.png") is False


def test_missing_http_object_requires_independent_confirmation():
    class Missing:
        def get(self,key):
            response=httpx.Response(404,json={"code":"NoSuchKey"},request=httpx.Request("GET","https://storage.test/object"));response.raise_for_status()
        def confirm_missing(self,key):return True
    assert probe_asset(Missing(),"tenant/object",sha256(b"original").hexdigest(),8)==("missing","object_absent")


def test_real_supabase_streamed_error_body_is_available_for_safe_missing_probe(monkeypatch):
    original=httpx.Client
    def handler(request):
        if "/bucket/" in request.url.path:return httpx.Response(200,json={"id":"phoenix-private"})
        if "/object/list/" in request.url.path:return httpx.Response(200,json=[])
        return httpx.Response(404,json={"code":"NoSuchKey","message":"Object not found"})
    monkeypatch.setattr("services.api.storage.httpx.Client",lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    storage=SupabaseStorage(Settings(supabase_url="https://storage.test",supabase_service_role_key="sb_secret_test"))
    assert probe_asset(storage,"tenant/ai/image.png",sha256(b"original").hexdigest(),8)==("missing","object_absent")
