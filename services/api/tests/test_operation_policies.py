from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from services.api.tests.test_api import app,client,register
from services.api.tests.test_ai import ai,quote,job,run,image_result,state,credits
from services.api.billing.models import LedgerEntry,CreditBucket,Wallet
from services.api.billing.service import reserve
from services.api.operations.models import PolicyVersion,CreditCorrection
from services.api.feature_models import ProviderAttempt,AuditEvent
from services.api.models import User


def admin(app,auth):app.state.settings.admin_emails=(auth['user']['email'],)


def create_policy(client,kind='pricing',mutate=None):
    overview=client.get('/v1/admin/policies').json()['data'];payload=deepcopy(overview[kind])
    if mutate:mutate(payload)
    response=client.post('/v1/admin/policies',json={'kind':kind,'reason':'자동 검증용 정책 초안','payload':payload})
    assert response.status_code==201,response.text
    return response.json()['data'],overview['active_'+kind+'_id']


def publish(client,row,previous=None):
    return client.post('/v1/admin/policies/'+row['id']+'/publish',json={'expected_active_id':previous,'reason':'검증한 새 정책 게시','understands_existing_subscriptions_unchanged':True})


def test_drafts_do_not_change_prices_publish_cas_and_immutable_versions(client,app):
    auth=register(client);admin(app,auth)
    before=client.get('/v1/pricing').json()['data']
    row,previous=create_policy(client,mutate=lambda p:p['actions'].update({'image.generate.standard':12}))
    assert client.get('/v1/pricing').json()['data']==before
    other,_=create_policy(client)
    assert publish(client,row,previous).status_code==200
    current=client.get('/v1/pricing').json()['data']
    assert current['policy']['actions']['image.generate.standard']==12 and current['version']==row['version']
    assert publish(client,other,previous).json()['code']=='POLICY_CHANGED'
    assert publish(client,row,row['id']).json()['code']=='POLICY_ALREADY_PUBLISHED'
    with app.state.session_factory() as db:
        saved=db.get(PolicyVersion,row['id']);saved.reason='rewrite'
        with pytest.raises(ValueError,match='immutable'):db.commit()


def test_operator_auth_csrf_strict_integer_and_server_cost_limits(client,app):
    auth=register(client)
    assert client.get('/v1/admin/policies').status_code==403
    admin(app,auth);data=client.get('/v1/admin/policies').json()['data']
    for invalid in [True,'10',10.5]:
        payload=deepcopy(data['pricing']);payload['actions']['image.generate.standard']=invalid
        assert client.post('/v1/admin/policies',json={'kind':'pricing','reason':'잘못된 요금 입력 확인','payload':payload}).status_code==422
    payload=deepcopy(data['pricing']);payload['live_billing_enabled']=True
    assert client.post('/v1/admin/policies',json={'kind':'pricing','reason':'실결제 게이트 우회 시도','payload':payload}).status_code==422
    p=deepcopy(data['image']);p['daily_limit_usd']*=2
    assert client.post('/v1/admin/policies',json={'kind':'image','reason':'서버 예산 우회 시도','payload':p}).json()['code']=='BUDGET_POLICY_LIMIT'
    del client.headers['X-CSRF-Token']
    assert client.post('/v1/admin/policies',json={'kind':'pricing','reason':'정책 생성 보안 검사','payload':data['pricing']}).status_code==403


def test_new_quote_uses_published_actions_old_unreserved_quote_is_rejected(ai):
    app,client,auth,item=ai;admin(app,auth);old=quote(client,item)
    row,previous=create_policy(client,mutate=lambda p:p['actions'].update({'image.generate.standard':12}))
    assert publish(client,row,previous).status_code==200
    new=quote(client,item);assert new['unit_cost']==12 and new['pricing_version']==row['version']
    denied=client.post('/v1/jobs',headers={'Idempotency-Key':'stale-policy'},json={'quote_id':old['id']})
    assert denied.status_code==409 and denied.json()['code']=='QUOTE_CHANGED'
    assert credits(client)['reserved']==0


def test_queued_model_revocation_prevents_provider_and_releases(ai):
    app,client,auth,item=ai;admin(app,auth);created,_=job(client,item)
    row,previous=create_policy(client,'image',lambda p:p.update(default_model='gpt-image-2.5-flare',allowed_models=['gpt-image-2.5-flare']))
    assert publish(client,row,previous).status_code==200
    calls=[];run(app,lambda *_:calls.append(1))
    assert calls==[] and credits(client)['balance']==30 and credits(client)['reserved']==0
    assert state(client,created['id'])['status']=='failed'
    caps=client.get('/v1/ai/capabilities').json()['data']
    assert caps['defaults']['model']=='gpt-image-2.5-flare'
    assert not next(m for m in caps['models'] if m['id'].endswith('sunburst'))['enabled']


def test_quality_revocation_during_provider_preserves_cost_and_returns_credit(ai):
    app,client,auth,item=ai;admin(app,auth)
    estimate=quote(client,item,quality='low');r=client.post('/v1/jobs',headers={'Idempotency-Key':'low-before-revoke'},json={'quote_id':estimate['id']});assert r.status_code==202
    def provider(*_):
        row,previous=create_policy(client,'image',lambda p:p.update(allowed_qualities=['high']))
        assert publish(client,row,previous).status_code==200
        return image_result()
    run(app,provider)
    assert credits(client)['reserved']==0 and credits(client)['consumed']==0 and credits(client)['balance']==30
    assert state(client,r.json()['data']['id'])['status']=='failed'
    with app.state.session_factory() as db:
        attempt=db.scalar(select(ProviderAttempt));assert attempt.cost_usd==0.0065


def correction(client,tenant,amount,*,key=None,bucket=None,scope='standard_only'):
    return client.post('/v1/admin/credit-corrections',headers={'Idempotency-Key':key or str(uuid4())},json={'tenant_id':tenant,'amount':amount,'bucket_id':bucket,'scope':scope,'expires_days':7,'reason':'테스트 중 지급 오류 정정'})


def test_corrections_append_and_replay_without_mutating_prior_ledger(client,app):
    auth=register(client);admin(app,auth);tenant=auth['tenant']['id']
    before=credits(client)['ledger'];key='same-adjustment'
    first=correction(client,tenant,20,key=key);assert first.status_code==201,first.text
    assert correction(client,tenant,20,key=key).json()['data']['id']==first.json()['data']['id']
    assert correction(client,tenant,21,key=key).json()['code']=='IDEMPOTENCY_CONFLICT'
    bucket=first.json()['data']['bucket_id'];assert correction(client,tenant,-5,bucket=bucket).status_code==201
    summary=credits(client);assert (summary['balance'],summary['adjusted'],summary['expired'])==(45,5,0)
    for prior in before:assert prior in summary['ledger']
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(CreditCorrection))==2
        assert db.get(Wallet,tenant).ever_paid is False
        item=db.scalar(select(CreditCorrection));item.reason='rewrite'
        with pytest.raises(ValueError,match='append-only'):db.commit()


def test_corrections_cannot_remove_reserved_or_change_scope(ai):
    app,client,auth,item=ai;admin(app,auth);tenant=auth['tenant']['id'];job(client,item)
    trial=credits(client)['buckets'][0]
    assert correction(client,tenant,-21,bucket=trial['id']).json()['code']=='ADJUSTMENT_UNAVAILABLE'
    assert correction(client,tenant,-1,bucket=trial['id'],scope='paid').json()['code']=='CREDIT_SCOPE_MISMATCH'
    assert correction(client,tenant,-20,bucket=trial['id']).status_code==201
    assert (credits(client)['balance'],credits(client)['reserved'])==(0,10)
    assert run(app,lambda *_:image_result())==1
    result=credits(client);assert(result['consumed'],result['reserved'],result['adjusted'])==(10,0,20)


def test_concurrent_admin_correction_uses_one_ledger_record(client,app):
    from services.api.operations.service import correct_credits
    from services.api.operations.schemas import CorrectionBody
    auth=register(client);admin(app,auth);tenant=auth['tenant']['id'];user_id=auth['user']['id']
    body=CorrectionBody(tenant_id=tenant,amount=17,scope='standard_only',reason='동시 요청 중복 검증')
    def write(_):
        with app.state.session_factory() as db:
            result=correct_credits(db,db.get(User,user_id),body,'concurrent-key');db.commit();return result.id
    with ThreadPoolExecutor(2) as pool:ids=list(pool.map(write,range(2)))
    assert ids[0]==ids[1] and credits(client)['balance']==47
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event=='ADJUSTMENT'))==1
