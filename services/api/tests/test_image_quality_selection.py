"""Model/quality routing and credit tiers; all providers are local doubles."""
import base64
from datetime import timedelta
from email.parser import BytesParser
from email.policy import default
import json

import httpx
import pytest
from sqlalchemy import select

from services.api.billing.models import Quote
from services.api.billing.policy import pricing
from services.api.billing.service import grant_credits
from services.api.config import Settings
from services.api.database import utcnow
from services.api.feature_models import AiUnit, ProviderAttempt
from services.api.image_provider import (
    AI_SELECTION_VERSION, IMAGE_MODELS, IMAGE_QUALITIES, ProviderError,
    generate_image, get_capabilities, quality_tier, resolve_image_selection,
)
from services.api.image_sizing import select_image_output
from services.api.models import Asset, Job
from services.api.tests.test_ai import ai, credits, image_result, run, state
from services.api.tests.test_image_text_removal import (
    create, patch_result, quote_body, upload,
)


@pytest.fixture
def selection(ai, monkeypatch):
    app, client, auth, item = ai
    monkeypatch.setenv("AI_HIGH_ENABLED", "true")
    app.state.settings.ai_high_enabled = True
    with app.state.session_factory() as db:
        grant_credits(db, auth["tenant"]["id"], 1000, kind="purchase", scope="paid",
                      expires_at=utcnow()+timedelta(days=30), grant_key="quality-test",
                      reason="Local test-only credits")
        db.commit()
    return app, client, auth, item


def body(item, quality="high", model=IMAGE_MODELS[0], **extra):
    return {"project_id":item["id"], "base_revision":item["base_revision"],
            "action":"image.generate."+quality_tier(quality), "requested_units":1,
            "model":model, "quality":quality, "prompt":"차분한 곡물 일러스트를 그려주세요.",
            "face_id":"front", **extra}


def test_capabilities_match_server_policy_and_disabled_premium():
    settings=Settings(ai_high_enabled=True)
    data=get_capabilities(settings)
    assert [x["id"] for x in data["models"]]==list(IMAGE_MODELS)
    assert data["defaults"]=={"model":IMAGE_MODELS[0],"quality":"high"}
    assert data["standard"]["quality"]=="high" and data["high"]["quality"]=="xhigh"
    assert data["high_edit"] is True
    for item in data["qualities"]:
        assert item["credit_cost"]==pricing()["actions"]["image.generate."+quality_tier(item["id"])]
        assert item["credit_cost"]==pricing()["actions"]["image.edit."+quality_tier(item["id"])]
    assert data["auto_quality"]=={"selects":"quality","changes_model":False,"credit_cost":20,"actual_quality_may_be_unknown":True}
    settings.ai_high_enabled=False
    assert [q["id"] for q in get_capabilities(settings)["qualities"] if q["enabled"]]==["low","medium","high"]
    settings.ai_provider="disabled"
    assert not any(q["enabled"] for q in get_capabilities(settings)["qualities"])
    assert not any(m["enabled"] for m in get_capabilities(settings)["models"])


def test_every_quote_freezes_model_quality_size_hash_and_tier(selection):
    app,client,_,item=selection
    hashes=set()
    for model in IMAGE_MODELS:
        for quality in IMAGE_QUALITIES:
            response=client.post("/v1/quotes",json=body(item,quality,model))
            assert response.status_code==201,response.text
            estimate=response.json()["data"];chosen=estimate["image_settings"]
            assert chosen["model"]==model and chosen["quality"]==chosen["requested_quality"]==quality
            assert estimate["unit_cost"]==(20 if quality_tier(quality)=="high" else 10)
            assert chosen["output_size"]==select_image_output(model,160,230)["output_size"]
            assert chosen["ai_selection_version"]==AI_SELECTION_VERSION
            assert chosen["output_effective_ppi"]>0 and chosen["output_experimental"] is True
            hashes.add(estimate["input_hash"])
    assert len(hashes)==12
    assert credits(client)["reserved"]==0


def test_omitted_quality_retains_standard_high_and_high_action_xhigh(selection):
    _,client,_,item=selection
    for tier,quality in (("standard","high"),("high","xhigh")):
        request=body(item);request.pop("quality");request.pop("model");request["action"]="image.generate."+tier
        response=client.post("/v1/quotes",json=request)
        assert response.status_code==201,response.text
        assert response.json()["data"]["image_settings"]["quality"]==quality


def test_bad_models_qualities_nested_sources_and_cheap_actions_never_create_quote(selection):
    app,client,_,item=selection
    bads=[{"model":v} for v in ["gpt-image-1","gpt-image-2.5",True,[],"auto"]]
    bads += [{"quality":v} for v in ["hd","HIGH",True,[],0]]
    bads += [{"quality":q,"action":"image.generate.standard"} for q in ["xhigh","max","auto"]]
    bads += [{"quality":"high","action":"image.generate.high"}]
    bads += [{"input_data":{k:v}} for k,v in [("model",IMAGE_MODELS[0]),("quality","high"),("requested_quality","max")]]
    for patch in bads:
        response=client.post("/v1/quotes",json={**body(item),**patch})
        assert response.status_code==422,response.text
    with app.state.session_factory() as db:
        assert db.scalar(select(Quote)) is None
    assert credits(client)["reserved"]==0


def test_server_overwrites_forged_version_size_and_price_in_nested_input(selection):
    app,client,_,item=selection
    request=body(item,"max",input_data={"ai_selection_version":"legacy", "unit_cost":0,"output_size":"16x16","output_effective_ppi":300,"model_source":"admin"})
    response=client.post("/v1/quotes",json=request)
    assert response.status_code==201,response.text
    estimate=response.json()["data"]
    assert estimate["unit_cost"]==20 and estimate["image_settings"]["output_size"]!="16x16"
    with app.state.session_factory() as db:
        frozen=db.get(Quote,estimate["id"]).input_data
        assert frozen["ai_selection_version"]==AI_SELECTION_VERSION
        assert "model_source" not in frozen and "unit_cost" not in frozen


@pytest.mark.parametrize("action_kind",["generate","edit"])
@pytest.mark.parametrize("quality",IMAGE_QUALITIES)
@pytest.mark.parametrize("model",IMAGE_MODELS)
def test_real_transport_sends_both_models_all_six_qualities_for_generate_and_edit(model,quality,action_kind):
    settings=Settings(environment="test",ai_provider="openai",openai_api_key="mock-only",ai_high_enabled=True)
    data={"action":f"image.{action_kind}.{quality_tier(quality)}","model":model,"quality":quality,"requested_quality":quality,
          "ai_selection_version":AI_SELECTION_VERSION,"width_mm":240,"height_mm":330,
          "prompt":"Detailed packaging background",**select_image_output(model,240,330)}
    expected=image_result();calls=[]
    def transport(request):
        calls.append(request)
        if action_kind=="edit":
            assert request.url.path=="/v1/images/edits"
            message=BytesParser(policy=default).parsebytes(("Content-Type: "+request.headers["content-type"]+"\r\n\r\n").encode()+request.content)
            sent={part.get_param("name",header="content-disposition"):part.get_payload(decode=True) for part in message.iter_parts()}
            assert sent["image"]==expected.content
            assert sent["model"].decode()==model and sent["quality"].decode()==quality
            assert sent["size"].decode()=="2432x3344"
        else:
            assert request.url.path=="/v1/images/generations"
            sent=json.loads(request.content)
            assert sent["model"]==model and sent["quality"]==quality and sent["size"]=="2432x3344"
        return httpx.Response(200,json={"quality":"max" if quality=="auto" else quality,
            "data":[{"b64_json":base64.b64encode(expected.content).decode()}],
            "usage":{"input_tokens":100,"input_tokens_details":{"image_tokens":20,"text_tokens":80},"output_tokens":200}})
    result=generate_image(settings,data,expected.content if action_kind=="edit" else None,transport=httpx.MockTransport(transport))
    assert len(calls)==1 and result.metadata["model"]==model
    assert result.metadata["requested_quality"]==quality
    assert result.metadata["actual_quality"]==("max" if quality=="auto" else quality)
    assert result.metadata["cost_usd"]==pytest.approx(.00656)


@pytest.mark.parametrize("reported",[None,"auto","unexpected",{"quality":"max"}])
def test_auto_actual_quality_is_unknown_when_provider_does_not_report_concrete_quality(reported):
    settings=Settings(ai_provider="openai",openai_api_key="mock-only",ai_high_enabled=True)
    data={"action":"image.generate.high","model":IMAGE_MODELS[1],"quality":"auto","requested_quality":"auto",
          "ai_selection_version":AI_SELECTION_VERSION,"width_mm":240,"height_mm":330,"prompt":"Quiet background",
          **select_image_output(IMAGE_MODELS[1],240,330)}
    result=generate_image(settings,data,transport=httpx.MockTransport(lambda _:httpx.Response(200,json={
        "quality":reported,"data":[{"b64_json":base64.b64encode(image_result().content).decode()}]})))
    assert result.metadata["requested_quality"]=="auto" and result.metadata["actual_quality"] is None


def test_twenty_credit_partial_results_capture_forty_and_return_twenty(selection):
    app,client,_,item=selection
    created,_=create(client,body(item,"max",IMAGE_MODELS[1],requested_units=3))
    assert credits(client)["reserved"]==60
    calls=0
    def provider(settings,data,reference):
        nonlocal calls
        calls+=1
        assert data["model"]==IMAGE_MODELS[1] and data["quality"]=="max"
        if calls==3: raise ProviderError("AI_REQUEST_REJECTED","Rejected test unit")
        image=image_result();image.metadata.update({"model":IMAGE_MODELS[1],"requested_quality":"max","actual_quality":"max"})
        return image
    assert run(app,provider)==3
    done=state(client,created["id"])
    assert done["status"]=="partially_succeeded"
    assert (done["credit_charged"],done["credit_returned"],done["credit_reserved"])==(40,20,0)
    assert done["result"]["assets"][0]["requested_quality"]=="max"
    with app.state.session_factory() as db:
        attempts=list(db.scalars(select(ProviderAttempt)))
        assert len(attempts)==3 and all(a.model==IMAGE_MODELS[1] for a in attempts)
        assert all(a.usage["image_settings"]["quality"]=="max" for a in attempts)


def test_auto_actual_quality_and_cost_survive_failed_storage(selection):
    app,client,_,item=selection
    created,_=create(client,body(item,"auto"))
    result=image_result();result.metadata.update({"model":IMAGE_MODELS[0],"requested_quality":"auto","actual_quality":"xhigh"})
    class Broken:
        def put(self,*_):raise OSError("test storage unavailable")
    run(app,lambda *_:result,storage=Broken())
    assert state(client,created["id"])["credit_returned"]==20
    with app.state.session_factory() as db:
        attempt=db.scalar(select(ProviderAttempt));unit=db.scalar(select(AiUnit))
        assert attempt.usage["image_settings"]["actual_quality"]=="xhigh" and attempt.cost_usd==.0065
        assert unit.result_metadata["actual_quality"]=="xhigh"
        assert db.scalar(select(Asset)) is None


def test_premium_edit_requires_one_reference_and_keeps_remove_text_exact_source_scope(selection):
    app,client,_,item=selection;asset,raw=upload(client,item)
    request={**quote_body(item,asset),"action":"image.edit.high","model":IMAGE_MODELS[1],"quality":"max"}
    for patch in ({"reference_asset_id":None},{"requested_units":2}):
        assert client.post("/v1/quotes",json={**request,**patch}).status_code==422
    created,estimate=create(client,request)
    assert estimate["credit_total"]==20 and estimate["image_settings"]["output_effective_ppi"] is None
    run(app,lambda *_:patch_result())
    done=state(client,created["id"])
    assert done["credit_charged"]==20
    result=done["result"]["assets"][0]
    assert result["outside_pixels_preserved"] is True and result["reference_asset_id"]==asset["id"]
    assert client.get(asset["url"]).content==raw


def test_premium_selection_cannot_spend_trial_credits(ai,monkeypatch):
    app,client,_,item=ai;app.state.settings.ai_high_enabled=True
    monkeypatch.setenv("AI_HIGH_ENABLED","true")
    response=client.post("/v1/quotes",json=body(item,"max"))
    assert response.status_code==402 and credits(client)["available"]==30


def test_model_allowlist_configuration_rejects_unknown_or_missing_default():
    for allowed in ((),("gpt-image-1",),(IMAGE_MODELS[1],)):
        with pytest.raises(ValueError,match="AI_IMAGE_MODELS"):
            Settings(environment="test",ai_image_models=allowed).validate()
    Settings(environment="test",image_model=IMAGE_MODELS[1],ai_image_models=(IMAGE_MODELS[1],)).validate()
