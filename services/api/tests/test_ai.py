"""Paid image calls are injectable; these tests never spend provider money."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import json
from threading import Event, Lock

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest
from sqlalchemy import delete, event, func, select

from services.api.ai_jobs import process_ai_jobs
from services.api.billing.models import Quote, Reservation, Subscription
from services.api.config import Settings
from services.api.database import utcnow
from services.api.feature_models import AiUnit, Membership, ProviderAttempt, WorkspaceMember
from services.api.image_provider import ImageResult, ProviderError, generate_image
from services.api.main import create_app
from services.api.models import Asset, Job, User
from services.api.tests.test_api import register, project, image_file
from services.api.tests.test_business import paid, workspace, invitation, accept


@pytest.fixture
def ai(tmp_path):
    app = create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path/'ai.db'}", storage_dir=tmp_path/"storage", mail_outbox_dir=tmp_path/"mail", ai_provider="fixture"))
    with TestClient(app) as client:
        auth = register(client)
        yield app, client, auth, project(client)


def quote(client, item, units=1, **extra):
    response = client.post("/v1/quotes", json={"project_id":item["id"], "base_revision":item["base_revision"], "action":"image.generate.standard", "requested_units":units, "prompt":"차분한 녹색 곡물 삽화 배경", "face_id":"front", **extra})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def job(client, item, units=1, key="generation-1"):
    estimate = quote(client, item, units)
    response = client.post("/v1/jobs", headers={"Idempotency-Key":key}, json={"quote_id":estimate["id"]})
    assert response.status_code == 202, response.text
    return response.json()["data"], estimate


def image_result():
    out = BytesIO(); Image.new("RGB", (24,32), "green").save(out, format="PNG")
    return ImageResult(out.getvalue(),24,32,{"provider_request_id":"req-test", "provider":"openai", "model":"gpt-image-2.5-sunburst", "usage":{"input_tokens":100,"output_tokens":200}, "cost_usd":0.0065,"cost_is_estimate":True})


def run(app, provider, limit=3, storage=None):
    return process_ai_jobs(app.state.session_factory,storage or app.state.storage,app.state.settings,limit=limit,provider=provider)


def state(client, identity):
    response=client.get(f"/v1/jobs/{identity}");assert response.status_code==200,response.text
    return response.json()["data"]


def credits(client):
    return client.get("/v1/credits").json()["data"]


def test_three_images_capture_twenty_release_ten_and_replay(ai):
    app, client, auth, item = ai
    created, estimate = job(client,item,3)
    assert credits(client)["reserved"]==30
    count=0
    def provider(*_):
        nonlocal count
        count+=1
        if count==3:raise ProviderError("AI_REQUEST_REJECTED","rejected")
        return image_result()
    assert run(app,provider)==3
    result=state(client,created["id"])
    assert result["status"]=="partially_succeeded"
    assert (result["credit_charged"],result["credit_returned"],result["credit_reserved"])==(20,10,0)
    assert len(result["result"]["assets"])==2
    assert (credits(client)["available"],credits(client)["consumed"])==(10,20)
    for asset in result["result"]["assets"]:
        assert client.get(asset["url"]).content==image_result().content
    replay=client.post("/v1/jobs",headers={"Idempotency-Key":"generation-1"},json={"quote_id":estimate["id"]})
    assert replay.status_code==202 and replay.json()["data"]["id"]==created["id"]
    assert run(app,provider)==0 and count==3


def test_cancel_only_queued_units_while_provider_is_running(ai):
    app, client, _, item = ai
    created,_=job(client,item,3)
    def provider(*_):
        canceled=client.post(f"/v1/jobs/{created['id']}/cancel")
        assert canceled.status_code==200,canceled.text
        assert canceled.json()["data"]["credit_returned"]==20
        return image_result()
    assert run(app,provider)==1
    result=state(client,created["id"])
    assert [unit["status"] for unit in result["result"]["units"]]==["succeeded","canceled","canceled"]
    assert result["credit_charged"]==10 and credits(client)["available"]==20
    assert client.post(f"/v1/jobs/{created['id']}/cancel").json()["data"]["credit_returned"]==20


def test_provider_usage_survives_storage_failure_without_customer_charge(ai):
    app,client,_,item=ai;created,_=job(client,item)
    class BrokenStorage:
        def put(self,*_):raise OSError("private upload unavailable")
    assert run(app,lambda *_:image_result(),storage=BrokenStorage())==1
    result=state(client,created["id"])
    assert result["credit_charged"]==0 and result["credit_returned"]==10
    with app.state.session_factory() as db:
        attempt=db.scalar(select(ProviderAttempt))
        assert attempt.request_id=="req-test" and attempt.usage["output_tokens"]==200 and attempt.cost_usd==0.0065
        assert db.scalar(select(func.count()).select_from(Asset))==0
    assert credits(client)["available"]==30
    assert run(app,lambda *_:pytest.fail("uncertain calls must not retry"))==0


def test_asset_insert_and_capture_roll_back_as_one_transaction(ai):
    app,client,_,item=ai;created,_=job(client,item)
    def reject_insert(*_):raise RuntimeError("simulated SQL publication failure")
    event.listen(Asset,"before_insert",reject_insert)
    try:run(app,lambda *_:image_result())
    finally:event.remove(Asset,"before_insert",reject_insert)
    assert state(client,created["id"])["credit_charged"]==0
    assert credits(client)["consumed"]==0 and credits(client)["available"]==30
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset))==0
        assert db.scalar(select(ProviderAttempt)).cost_usd==0.0065


def test_uncertain_provider_result_is_never_retried_even_if_retryable(ai):
    app,client,_,item=ai;created,_=job(client,item)
    def provider(*_):raise ProviderError("AI_TIMEOUT_UNCERTAIN","uncertain",retryable=True,uncertain=True)
    assert run(app,provider)==1
    assert state(client,created["id"])["status"]=="reconciliation_required"
    assert credits(client)["available"]==30
    assert run(app,lambda *_:pytest.fail("paid duplicate call"))==0


def test_expired_lease_late_success_preserves_cost_without_publishing(ai):
    app,client,_,item=ai;created,_=job(client,item)
    def provider(*_):
        with app.state.session_factory() as db:
            db.scalar(select(AiUnit)).lease_until=utcnow()-timedelta(seconds=1);db.commit()
        assert run(app,lambda *_:pytest.fail("expired running call must not retry"))==0
        return image_result()
    assert run(app,provider)==1
    assert state(client,created["id"])["credit_charged"]==0 and credits(client)["available"]==30
    with app.state.session_factory() as db:
        attempt=db.scalar(select(ProviderAttempt))
        assert attempt.status=="late_result_not_charged" and attempt.cost_usd==0.0065
        assert db.scalar(select(func.count()).select_from(Asset))==0


@pytest.mark.parametrize("during_call",[False,True])
@pytest.mark.parametrize("revocation",["membership","workspace","role","plan","account"])
def test_worker_rechecks_actor_team_and_workspace_before_call_and_publish(ai,during_call,revocation):
    app,owner,auth,_=ai;tenant=auth["tenant"]["id"];paid(app,tenant)
    space=workspace(owner,"Private client")
    scoped=owner.post("/v1/projects",json={"name":"Private design","product_name":"Tea","workspace_id":space}).json()["data"]
    with TestClient(app) as editor:
        identity=register(editor,"editor@example.com")["user"]["id"]
        accept(editor,invitation(owner,"editor@example.com",[space]))
        created,_=job(editor,scoped)
    def revoke():
        with app.state.session_factory() as db:
            if revocation=="membership":db.scalar(select(Membership).where(Membership.user_id==identity)).is_active=False
            elif revocation=="workspace":db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id==identity))
            elif revocation=="role":db.scalar(select(Membership).where(Membership.user_id==identity)).role="viewer"
            elif revocation=="plan":db.scalar(select(Subscription).where(Subscription.tenant_id==tenant)).paid_until=utcnow()-timedelta(seconds=1)
            else:db.get(User,identity).is_active=False
            db.commit()
    calls=0
    def provider(*_):
        nonlocal calls
        calls+=1;revoke();return image_result()
    if not during_call:revoke()
    run(app,provider)
    assert calls==int(during_call)
    assert state(owner,created["id"])["credit_charged"]==0
    assert credits(owner)["available"]==30
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset))==0
        attempts=list(db.scalars(select(ProviderAttempt)))
        assert len(attempts)==int(during_call)
        if attempts:assert attempts[0].cost_usd==0.0065


def test_configuration_change_does_not_upgrade_fixture_job_to_paid_call(ai):
    app,client,_,item=ai;created,_=job(client,item)
    app.state.settings.ai_provider="openai";app.state.settings.openai_api_key="test-key"
    app.state.settings.ai_require_verified_email=False
    run(app,lambda *_:pytest.fail("new provider must require a new quote"))
    assert state(client,created["id"])["credit_returned"]==10
    assert credits(client)["available"]==30


def test_quote_expiry_revision_and_tenant_cannot_be_bypassed(ai):
    app,client,_,item=ai
    expired=quote(client,item)
    with app.state.session_factory() as db:
        db.get(Quote,expired["id"]).expires_at=utcnow()-timedelta(seconds=1);db.commit()
    response=client.post("/v1/jobs",headers={"Idempotency-Key":"expired"},json={"quote_id":expired["id"]})
    assert response.status_code==409
    fresh=quote(client,item)
    scene=deepcopy(item["scene"]);scene["faces"][0]["objects"][1]["text"]="Saved after quote"
    assert client.patch(f"/v1/projects/{item['id']}/draft",json={"base_revision":1,"scene":scene}).status_code==200
    stale=client.post("/v1/jobs",headers={"Idempotency-Key":"stale"},json={"quote_id":fresh["id"]})
    assert stale.status_code==409 and stale.json()["code"]=="REVISION_CONFLICT"
    with TestClient(app) as other:
        register(other,"other@example.com")
        stolen=other.post("/v1/jobs",headers={"Idempotency-Key":"stolen"},json={"quote_id":fresh["id"]})
        assert stolen.status_code==404
    assert credits(client)["available"]==30 and credits(client)["reserved"]==0


def test_reference_assets_obey_workspace_access(ai):
    app,owner,auth,_=ai;paid(app,auth["tenant"]["id"])
    a,b=workspace(owner,"A"),workspace(owner,"B")
    first=owner.post("/v1/projects",json={"name":"A","product_name":"Tea","workspace_id":a}).json()["data"]
    second=owner.post("/v1/projects",json={"name":"B","product_name":"Tea","workspace_id":b}).json()["data"]
    asset=owner.post("/v1/assets",data={"project_id":second["id"]},files={"file":image_file()}).json()["data"]
    with TestClient(app) as editor:
        register(editor,"editor@example.com");accept(editor,invitation(owner,"editor@example.com",[a]))
        body={"project_id":first["id"],"base_revision":1,"action":"image.edit.standard","prompt":"녹색 질감으로 변경하세요","reference_asset_id":asset["id"],"requested_units":1}
        denied=editor.post("/v1/quotes",json=body)
        assert denied.status_code==404


def test_concurrent_duplicate_jobs_reserve_and_execute_once(ai):
    app,client,_,item=ai;estimate=quote(client,item)
    def submit():
        return client.post("/v1/jobs",headers={"Idempotency-Key":"same-user-operation"},json={"quote_id":estimate["id"]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:submit(),range(2)))
    assert all(response.status_code==202 for response in responses),[response.text for response in responses]
    assert len({response.json()["data"]["id"] for response in responses})==1
    assert credits(client)["reserved"]==10
    assert run(app,lambda *_:image_result())==1
    assert credits(client)["consumed"]==10


def test_at_most_two_concurrent_provider_calls_per_tenant(ai):
    app,client,_,item=ai
    for index in range(3):job(client,item,key=f"parallel-{index}")
    both_running=Event();release=Event();counter_lock=Lock();calls=0
    def provider(*_):
        nonlocal calls
        with counter_lock:
            calls+=1
            if calls==2:both_running.set()
        assert release.wait(10)
        return image_result()
    with ThreadPoolExecutor(max_workers=3) as pool:
        first=pool.submit(run,app,provider,1);second=pool.submit(run,app,provider,1)
        try:
            assert both_running.wait(10)
            assert pool.submit(run,app,provider,1).result(timeout=5)==0
            assert calls==2
        finally:release.set()
        assert first.result(timeout=10)==1 and second.result(timeout=10)==1
    assert run(app,lambda *_:image_result(),1)==1
    assert credits(client)["consumed"]==30


@pytest.mark.parametrize("model",["gpt-image-2.5-sunburst","gpt-image-2.5-flare"])
@pytest.mark.parametrize("action",["image.generate.standard","image.generate.high","image.edit.standard"])
def test_real_adapter_transport_uses_exact_model_quality_and_edit_endpoint(model,action):
    settings=Settings(environment="test",ai_provider="openai",openai_api_key="test-only-key",image_model=model,ai_high_enabled=True)
    data={"action":action,"prompt":"Minimal grain illustration","face_id":"front","width_mm":160,"height_mm":230}
    expected=image_result()
    def transport(request):
        assert request.headers["authorization"]=="Bearer test-only-key"
        if action=="image.edit.standard":
            assert request.url.path=="/v1/images/edits"
            assert "multipart/form-data" in request.headers["content-type"]
            body=request.content.decode("utf-8",errors="replace")
            assert model in body and 'name="image"; filename="reference.png"' in body
            assert 'name="quality"\r\n\r\nhigh' in body
        else:
            assert request.url.path=="/v1/images/generations"
            body=json.loads(request.content)
            assert body["model"]==model and body["quality"]=="high" and body["n"]==1
            assert body["size"]==("1536x1024" if action.endswith("high") else "1024x1024")
            assert "160 by 230 mm" in body["prompt"]
        return httpx.Response(200,headers={"x-request-id":"req-image"},json={"data":[{"b64_json":base64.b64encode(expected.content).decode()}],"usage":{"input_tokens":100,"input_tokens_details":{"text_tokens":80,"image_tokens":20},"output_tokens":200}})
    result=generate_image(settings,data,expected.content if action=="image.edit.standard" else None,transport=httpx.MockTransport(transport))
    assert result.content==expected.content and result.metadata["provider_request_id"]=="req-image"
    assert result.metadata["cost_usd"]==pytest.approx(0.00656)


def test_adapter_timeout_is_uncertain_and_does_not_retry_transport():
    calls=0
    def timeout(request):
        nonlocal calls
        calls+=1;raise httpx.ReadTimeout("provider may still be running",request=request)
    settings=Settings(ai_provider="openai",openai_api_key="test-only-key")
    with pytest.raises(ProviderError) as caught:
        generate_image(settings,{"action":"image.generate.standard","prompt":"abstract background"},transport=httpx.MockTransport(timeout))
    assert caught.value.uncertain and not caught.value.retryable and calls==1
