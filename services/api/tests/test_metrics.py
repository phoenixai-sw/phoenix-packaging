"""Internal measurements only: synthetic payments/provider results, zero external calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from services.api.tests.test_api import app,client,register,project,google_login,image_file
from services.api.tests.test_ai import ai,job,run,image_result,credits
from services.api.image_provider import ProviderError
from services.api.tests.test_editor_sessions import claim
from services.api.database import utcnow
from services.api.models import User,Project,Revision,Job,ProjectEditLease
from services.api.feature_models import ProviderAttempt,AiUnit
from services.api.billing.models import Payment,PaymentOrder,PaymentEvent
from services.api.metrics.models import MetricEvent,Acquisition,ActivitySlice,CostEntry
from services.api.metrics.service import record_event,record_signup,record_payment_paid,record_recovered_payment
from services.api.metrics.schemas import AcquisitionInput
from services.api.metrics.reporting import overview


def admin(app,auth):app.state.settings.admin_emails=(auth['user']['email'],)


def report(client):
    response=client.get('/v1/admin/metrics');assert response.status_code==200,response.text
    return response.json()['data']


def test_signup_attribution_trial_dedup_privacy_and_existing_login(client,app,monkeypatch):
    original=client.post
    def post(url,**kwargs):
        if url=='/v1/auth/google':kwargs['json']['acquisition']={'channel':'paid_search','utm_source':'google','utm_medium':'cpc','utm_campaign':'campaign_42'}
        return original(url,**kwargs)
    monkeypatch.setattr(client,'post',post)
    auth=register(client);monkeypatch.setattr(client,'post',original)
    assert google_login(client).status_code==200
    with app.state.session_factory() as db:
        row=db.get(Acquisition,auth['tenant']['id'])
        assert row.channel=='paid_search' and row.utm_campaign==sha256(b'campaign_42').hexdigest()
        names=list(db.scalars(select(MetricEvent.name)))
        assert names.count('signup_completed')==names.count('trial_granted')==1
        user=db.get(User,auth['user']['id']);record_signup(db,user,AcquisitionInput(channel='other'));db.commit()
        assert db.get(Acquisition,user.tenant_id).channel=='paid_search'
        assert 'campaign_42' not in json.dumps([row.properties for row in db.scalars(select(MetricEvent))])
    for value in ['email@example.com','한글 원문','https://private.example/a','x'*81]:
        with pytest.raises(ValueError):AcquisitionInput(utm_campaign=value)


def test_events_atomic_idempotent_cross_tenant_and_no_public_injection(client,app):
    auth=register(client);item=project(client)
    with app.state.session_factory() as db:
        record_event(db,'brief_completed',key='rolled-back',tenant_id=auth['tenant']['id'])
        db.rollback()
    with app.state.session_factory() as db:
        assert db.scalar(select(MetricEvent).where(MetricEvent.event_key=='brief_completed:rolled-back')) is None
        a=record_event(db,'brief_completed',key='same',tenant_id=auth['tenant']['id']);b=record_event(db,'brief_completed',key='same',tenant_id=auth['tenant']['id']);assert a.id==b.id
        with pytest.raises(ValueError):record_event(db,'signup_completed',key='content',tenant_id=auth['tenant']['id'],properties={'prompt':'private'})
        db.commit()
        with pytest.raises(ValueError):record_event(db,'project_created',key='foreign',tenant_id=str(uuid4()),project_id=item['id'])
        a.properties={'amount':1}
        with pytest.raises(ValueError,match='append-only'):db.commit()
    assert client.post('/v1/metrics/events',json={'name':'subscription_paid'}).status_code==404


def test_text_change_and_preflight_record_only_counts(client,app):
    auth=register(client);item=project(client);scene=deepcopy(item['scene'])
    scene['faces'][0]['objects'][1]['text']='NEVER COPY PRIVATE PRODUCT WORDS'
    response=client.patch('/v1/projects/'+item['id']+'/draft',json={'base_revision':1,'scene':scene});assert response.status_code==200,response.text
    preflight=client.post('/v1/preflight',json={'project_id':item['id'],'base_revision':2,'kind':'production','reviewed_face_ids':['front','back']})
    assert preflight.status_code==200,preflight.text
    with app.state.session_factory() as db:
        rows=list(db.scalars(select(MetricEvent)))
        names={row.name for row in rows}
        assert {'project_created','brief_completed','text_edited','all_faces_reviewed','preflight_failed'}<=names
        assert 'NEVER COPY' not in json.dumps([row.properties for row in rows])
        text=next(row for row in rows if row.name=='text_edited')
        assert text.properties=={'changed_object_count':1} and text.revision_id


def test_activity_requires_lease_and_server_time_caps_idle_replay(client,app,monkeypatch):
    auth=register(client);item=project(client)
    assert client.post('/v1/metrics/activity',json={'project_id':item['id']}).status_code==423
    _,lease=claim(client,item);headers={'X-Editor-Lease':lease['lease_token']}
    base=utcnow().replace(microsecond=0);base=base-timedelta(seconds=base.second%30)
    monkeypatch.setattr('services.api.metrics.routes.utcnow',lambda:base)
    first=client.post('/v1/metrics/activity',headers=headers,json={'project_id':item['id']})
    assert first.status_code==200,first.text
    assert first.json()['data']['estimated_seconds_added']==0
    assert not client.post('/v1/metrics/activity',headers=headers,json={'project_id':item['id']}).json()['data']['recorded']
    monkeypatch.setattr('services.api.metrics.routes.utcnow',lambda:base+timedelta(seconds=31))
    assert client.post('/v1/metrics/activity',headers=headers,json={'project_id':item['id']}).json()['data']['estimated_seconds_added']==30
    monkeypatch.setattr('services.api.metrics.routes.utcnow',lambda:base+timedelta(seconds=100))
    assert client.post('/v1/metrics/activity',headers=headers,json={'project_id':item['id']}).json()['data']['estimated_seconds_added']==0
    assert client.post('/v1/metrics/activity',headers=headers,json={'project_id':item['id'],'seconds':999999}).status_code==422
    assert client.post('/v1/metrics/activity',headers={'X-Editor-Lease':str(uuid4())},json={'project_id':item['id']}).status_code==423
    with app.state.session_factory() as db:
        assert db.scalar(select(func.sum(ActivitySlice.seconds)))==30
        db.get(User,auth['user']['id']).role='viewer';db.commit()
    assert client.post('/v1/metrics/activity',headers=headers,json={'project_id':item['id']}).status_code==403


def test_activity_concurrent_same_bucket_one_record_and_cross_tenant(client,app):
    auth=register(client);item=project(client);_,lease=claim(client,item)
    cookie=client.cookies.get('phoenix_session');headers={'X-CSRF-Token':auth['csrf_token'],'X-Editor-Lease':lease['lease_token']}
    def call(_):
        with TestClient(app) as peer:
            peer.cookies.set('phoenix_session',cookie)
            r=peer.post('/v1/metrics/activity',headers=headers,json={'project_id':item['id']});assert r.status_code==200,r.text
            return r.json()['data']['recorded']
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(call,range(2)))==[False,True]
    with TestClient(app) as other:
        register(other,'outside@example.com');assert other.post('/v1/metrics/activity',headers={'X-Editor-Lease':lease['lease_token']},json={'project_id':item['id']}).status_code==404


def test_insufficient_credit_real_error_persists_after_rollback(ai):
    app,client,auth,item=ai;job(client,item,3)
    response=client.post('/v1/quotes',json={'project_id':item['id'],'base_revision':1,'action':'image.generate.standard','requested_units':1,'prompt':'원문 노출하면 안 됨','face_id':'front'})
    assert response.status_code==402,response.text
    with app.state.session_factory() as db:
        row=db.scalar(select(MetricEvent).where(MetricEvent.name=='credits_insufficient'))
        assert row and row.tenant_id==auth['tenant']['id'] and row.properties=={'required':10,'available':0}


def payment(db,tenant,kind,at,provider='toss_live',status='DONE'):
    key=str(uuid4());order=PaymentOrder(tenant_id=tenant,order_id=key,operation_key=key,request_hash='a'*64,kind=kind,amount=29000,credits=100,pricing_version='test-policy',status='paid',credits_expires_at=at+timedelta(days=30))
    db.add(order);db.flush();row=Payment(tenant_id=tenant,order_id=order.id,provider_payment_key=key,provider=provider,amount=29000,currency='KRW',status=status,approved_at=at)
    db.add(row);db.flush();record_recovered_payment(db,order,row) if status!='DONE' else record_payment_paid(db,order,row);return row


def test_first_live_subscription_cohort_excludes_renewals_topup_test_mock_and_refund_separate(client,app):
    auth=register(client);admin(app,auth);now=utcnow();tenant=auth['tenant']['id']
    with app.state.session_factory() as db:
        first=payment(db,tenant,'subscription',now-timedelta(seconds=20))
        for kind in ['subscription','renewal','topup','upgrade']:payment(db,tenant,kind,now-timedelta(seconds=10))
        payment(db,tenant,'subscription',now,'mock');payment(db,tenant,'subscription',now,'toss_test')
        db.add(PaymentEvent(tenant_id=tenant,payment_id=first.id,event_key='refund-fixture',kind='REFUND',amount=29000,reason='test only'));db.commit()
    value=report(client)
    assert value['payments']=={'subscription_payments':2,'renewals':1,'topups':1,'upgrades':1,'first_subscription_customers':1,'mock_payments_excluded':1,'test_payments_excluded':1,'unrecognized_providers_excluded':0,'refund_events':1}
    assert value['cohorts'][0]['first_subscription_customers']==1 and value['cohorts'][0]['trial_tenants']==1
    assert value['payment_fees_unknown']==5


def test_provider_cost_basis_preserves_unknown_and_separates_refunded_failure(ai):
    app,client,auth,item=ai;admin(app,auth);created,_=job(client,item);run(app,lambda *_:image_result())
    with app.state.session_factory() as db:
        unit=db.scalar(select(AiUnit));existing=db.scalar(select(ProviderAttempt));existing.provider='openai';existing.cost_usd=0.0065;existing.cost_is_estimate=True
        for status,value,estimate in [('failed',None,True),('failed',0.25,False),('canceled_before_provider',None,True)]:
            db.add(ProviderAttempt(tenant_id=auth['tenant']['id'],unit_id=unit.id,provider='openai',model='fixture-model',status=status,cost_usd=value,cost_is_estimate=estimate))
        db.commit()
    value=report(client);totals={(r['category'],r['basis']):r for r in value['totals']}
    assert totals['provider','unknown']['amount'] is None
    assert Decimal(totals['provider','actual']['amount'])==Decimal('0.25')
    assert Decimal(totals['provider','estimate']['amount'])==Decimal('0.0065')
    assert {'estimate','actual','unknown','not_called'}=={r['basis'] for r in value['provider_attempts']}
    assert value['missing_cost_categories']==['storage','output','support','payment_fee']
    with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(MetricEvent).where(MetricEvent.name=='generation_succeeded'))==1


def cost_body(**extra):return {'category':'storage','basis':'unknown','currency':'USD','amount':None,'reason':'청구서 아직 미수신','occurred_at':utcnow().isoformat(),**extra}
def cost(client,body,key=None):return client.post('/v1/admin/metrics/costs',headers={'Idempotency-Key':key or str(uuid4())},json=body)


def test_admin_costs_append_only_idempotency_correction_currency_and_audit(client,app):
    auth=register(client);assert client.get('/v1/admin/metrics').status_code==403
    admin(app,auth);body=cost_body();first=cost(client,body,'same');assert first.status_code==201,first.text
    assert cost(client,body,'same').json()['data']['id']==first.json()['data']['id']
    assert cost(client,cost_body(reason='다른 사유 덮어쓰기'),'same').status_code==409
    original=first.json()['data'];new=cost(client,{**body,'basis':'actual','amount':'0.125','supersedes_id':original['id'],'reason':'청구서 원가 정정'});assert new.status_code==201,new.text
    assert cost(client,{**body,'supersedes_id':original['id'],'reason':'동일 원문 중복 정정'}).status_code==409
    assert cost(client,cost_body(category='support',currency='KRW',basis='estimate',amount='500',support_minutes=12)).status_code==201
    value=report(client);assert value['reported_support_minutes']==12
    assert {(r['category'],r['basis'],r['currency']) for r in value['totals']}=={('storage','actual','USD'),('support','estimate','KRW')}
    history=client.get('/v1/admin/metrics/costs?limit=2').json()['data'];assert len(history['items'])==2 and history['next_offset']==2
    assert len(client.get('/v1/admin/metrics/costs?offset=2&limit=2').json()['data']['items'])==1
    with app.state.session_factory() as db:
        assert db.get(CostEntry,original['id']).amount is None
        row=db.get(CostEntry,original['id']);row.reason='overwrite'
        with pytest.raises(ValueError,match='append-only'):db.commit()
    client.headers.pop('X-CSRF-Token');assert cost(client,body).status_code==403


@pytest.mark.parametrize('change',[{'basis':'actual','amount':None},{'basis':'unknown','amount':0},{'amount':'NaN'},{'amount':'-1'},{'reason':'     '},{'support_minutes':2},{'project_id':str(uuid4())}])
def test_cost_schema_rejects_ambiguous_or_unscoped_measurements(client,app,change):
    auth=register(client);admin(app,auth)
    assert cost(client,cost_body(**change)).status_code==422


def test_cost_link_ownership_and_concurrent_correction(client,app):
    auth=register(client);item=project(client);admin(app,auth)
    assert cost(client,cost_body(tenant_id=str(uuid4()),project_id=item['id'])).status_code==404
    first=cost(client,cost_body()).json()['data'];cookie=client.cookies.get('phoenix_session')
    def attempt(_):
        with TestClient(app) as peer:
            peer.cookies.set('phoenix_session',cookie);peer.headers['X-CSRF-Token']=auth['csrf_token']
            return cost(peer,cost_body(supersedes_id=first['id'],basis='actual',amount='1')).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(attempt,range(2)))==[201,409]


def test_report_range_inventory_and_no_silent_truncation(client,app,monkeypatch):
    auth=register(client);admin(app,auth);item=project(client)
    asset=client.post('/v1/assets',files={'file':image_file()}).json()['data']
    value=report(client);assert value['storage_observed_objects']==1 and value['storage_observed_bytes']==asset['byte_size']
    assert client.get('/v1/admin/metrics?start=2020-01-01&end=2026-01-01').status_code==422
    monkeypatch.setattr('services.api.metrics.reporting.REPORT_ROWS',0)
    assert client.get('/v1/admin/metrics').json()['code']=='METRICS_REPORT_TOO_LARGE'


def test_elapsed_time_excludes_review_and_future_outputs_and_activity_is_distinct(client,app):
    auth=register(client);item=project(client);now=utcnow()
    with app.state.session_factory() as db:
        p=db.get(Project,item['id']);p.created_at=now-timedelta(hours=2);revision=db.scalar(select(Revision).where(Revision.project_id==p.id))
        for index,(kind,review,at) in enumerate([('review_export',True,now-timedelta(hours=1)),('production_export',False,now-timedelta(minutes=10)),('production_export',False,now+timedelta(hours=1))]):
            db.add(Job(tenant_id=p.tenant_id,project_id=p.id,revision_id=revision.id,kind=kind,status='succeeded',operation_key='time-'+str(index),request_hash='a'*64,snapshot={},result={'review_only':review},created_at=at,updated_at=at))
        db.add(ActivitySlice(tenant_id=p.tenant_id,project_id=p.id,actor_id=auth['user']['id'],session_id='fixture',lease_id='fixture',time_bucket=1,seconds=30,created_at=now-timedelta(minutes=20)))
        db.commit();data=overview(db,now-timedelta(days=1),now)
    row=data['timings'][0];assert row['elapsed_seconds']==6600 and row['estimated_active_edit_seconds']==30 and row['activity_observed']


def test_partial_generation_records_per_unit_success_failure_without_prompt(ai):
    app,client,auth,item=ai;job(client,item,3);calls=[]
    def provider(*_):
        calls.append(1)
        if len(calls)==3:raise ProviderError('AI_REQUEST_REJECTED','test rejected')
        return image_result()
    assert run(app,provider)==3
    assert credits(client)['consumed']==20 and credits(client)['balance']==10
    with app.state.session_factory() as db:
        rows=list(db.scalars(select(MetricEvent).where(MetricEvent.name.in_(['generation_succeeded','generation_failed']))))
        assert [r.name for r in rows].count('generation_succeeded')==2 and [r.name for r in rows].count('generation_failed')==1
        assert all(r.job_id and r.revision_id and r.project_id==item['id'] for r in rows)
        assert all('prompt' not in r.properties for r in rows)


def test_export_deletion_pending_still_counts_storage_until_confirmed_deleted(client,app):
    auth=register(client);item=project(client);admin(app,auth)
    with app.state.session_factory() as db:
        revision=db.scalar(select(Revision).where(Revision.project_id==item['id']))
        for state,size in [('deleting',321),('deleted',700)]:
            db.add(Job(tenant_id=auth['tenant']['id'],project_id=item['id'],revision_id=revision.id,kind='review_export',status='succeeded',operation_key='inventory-'+state,request_hash='a'*64,snapshot={},result={'storage_key':state,'byte_size':size,'_retention':{'state':state}}))
        db.commit()
    value=report(client);assert value['storage_observed_objects']==1 and value['storage_observed_bytes']==321


@pytest.mark.parametrize('status',['CANCELED','PARTIAL_CANCELED'])
def test_first_seen_cancellation_keeps_historical_conversion_and_event_sources(client,app,status):
    auth=register(client);admin(app,auth);tenant=auth['tenant']['id'];now=utcnow()
    with app.state.session_factory() as db:
        prior=payment(db,tenant,'subscription',now-timedelta(days=2),status=status)
        payment(db,tenant,'subscription',now-timedelta(seconds=1))
        db.add(PaymentEvent(tenant_id=tenant,payment_id=prior.id,event_key='first-refund',kind='REFUND',amount=29000 if status=='CANCELED' else 1000,reason='fixture only'))
        for provider in ['fixture','openai']:
            record_event(db,'generation_succeeded',key=provider,tenant_id=tenant,properties={'provider':provider})
        for source in ['test','manufacturer']:
            record_event(db,'printer_accepted',key=source,tenant_id=tenant,properties={'record_source':source})
        db.commit();value=overview(db,now-timedelta(days=1),now+timedelta(minutes=1))
    assert value['payments']['first_subscription_customers']==0
    assert value['payments']['subscription_payments']==1 and value['payments']['refund_events']==1
    assert {(r['provider'],r['count']) for r in value['event_counts'] if r['name']=='generation_succeeded'}=={('fixture',1),('openai',1)}
    assert {(r['record_source'],r['count']) for r in value['event_counts'] if r['name']=='printer_accepted'}=={('test',1),('manufacturer',1)}
    full=report(client);assert full['payments']['first_subscription_customers']==1
