from copy import deepcopy
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from services.api.tests.test_api import app,client,register,project
from services.api.tests.test_business import business,paid,invitation,accept
from services.api.billing.models import LedgerEntry,PaymentOrder,Subscription
from services.api.feature_models import AuditEvent
from services.api.service_orders.models import ServiceOrder,ServiceOrderEvent,ServiceQuote
from services.api.database import utcnow


def create(client,code='onboarding',project_id=None,key=None):
    r=client.post('/v1/service-orders',json={'service_code':code,'project_id':project_id,'request_note':'지원 범위와 일정 확인을 요청합니다.'},headers={'Idempotency-Key':key or str(uuid4())})
    assert r.status_code==201,r.text
    return r.json()['data']


def quote(client,item,amount=100000):
    r=client.post(f"/v1/admin/service-orders/{item['id']}/quotes",json={'base_revision':item['revision'],'amount_inc_vat':amount,'scope':'플랫폼 사용 안내 1회, 대상 사용자 1명','exclusions':'제조·인쇄·샘플 비용 제외','valid_days':7,'reason':'사용자 요청 범위 확인'})
    assert r.status_code==200,r.text
    return r.json()['data']


def accept_quote(client,item):
    return client.post(f"/v1/service-orders/{item['id']}/accept",json={'base_revision':item['revision'],'quote_id':item['current_quote_id'],'note':'제공 범위와 금액을 확인했습니다.','understands_no_payment':True})


def make_admin(app,auth):app.state.settings.admin_emails=(auth['user']['email'],)


def test_catalog_request_quote_and_accept_are_not_payment_or_subscription(client,app):
    auth=register(client);make_admin(app,auth)
    catalog=client.get('/v1/service-catalog').json()['data']
    assert {x['code']:x['suggested_amount_krw'] for x in catalog['items']}=={'file_review':55000,'onboarding':100000,'pilot_pro_first_month':99000}
    with app.state.session_factory() as db:before=db.scalar(select(func.count()).select_from(LedgerEntry))
    item=create(client);item=quote(client,item)
    accepted=accept_quote(client,item);assert accepted.status_code==200,accepted.text
    result=accepted.json()['data'];assert result['status']=='accepted' and result['accepted_quote_id']==item['current_quote_id']
    assert result['payment_status']=='not_collected' and result['credits_granted']==0 and result['checkout_enabled'] is False
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(PaymentOrder))==0
        assert db.scalar(select(func.count()).select_from(Subscription))==0
        assert db.scalar(select(func.count()).select_from(LedgerEntry))==before
        assert [e.kind for e in db.scalars(select(ServiceOrderEvent).order_by(ServiceOrderEvent.order_revision))]==['requested','quoted','accepted']


def test_idempotency_price_injection_and_tenant_isolation(business):
    app,owner,auth,member=business;other,_=member('other-service@example.com')
    key=str(uuid4());first=create(owner,key=key);assert create(owner,key=key)['id']==first['id']
    body={'service_code':'onboarding','request_note':'다른 요청 내용'}
    assert owner.post('/v1/service-orders',json=body,headers={'Idempotency-Key':key}).status_code==409
    assert owner.post('/v1/service-orders',json={**body,'amount':1},headers={'Idempotency-Key':'price-inject'}).status_code==422
    assert other.get(f"/v1/service-orders/{first['id']}").status_code==404
    assert other.post(f"/v1/service-orders/{first['id']}/cancel",json={'base_revision':1,'note':'다른 조직 취소'}).status_code==404
    p=project(owner)
    assert other.post('/v1/service-orders',json={'service_code':'file_review','project_id':p['id'],'request_note':'남의 파일 접근'},headers={'Idempotency-Key':'other-project'}).status_code==404
    assert owner.get('/v1/admin/service-orders').status_code==403


def test_viewer_and_editor_cannot_order_even_with_team_entitlement(business):
    app,owner,auth,member=business;paid(app,auth['tenant']['id'])
    for role in ('viewer','editor'):
        email=f'service-{role}@example.com';invite=invitation(owner,email,role=role);c,_=member(email);accept(c,invite)
        assert c.get('/v1/service-orders').status_code==403
        assert c.post('/v1/service-orders',json={'service_code':'onboarding','request_note':'팀원 직접 신청'},headers={'Idempotency-Key':str(uuid4())}).status_code==403


def test_revision_and_immutable_quote_history_prevent_silent_price_change(client,app):
    auth=register(client);make_admin(app,auth);row=create(client);q1=quote(client,row,100000);q2=quote(client,q1,110000)
    assert len(q2['quotes'])==2 and q2['quotes'][1]['amount_inc_vat']==100000
    assert accept_quote(client,q1).status_code==409
    mismatched=deepcopy(q2);mismatched['current_quote_id']=q1['current_quote_id']
    assert accept_quote(client,mismatched).status_code==409
    accepted=accept_quote(client,q2);assert accepted.status_code==200
    result=accepted.json()['data']
    changed=client.post(f"/v1/admin/service-orders/{row['id']}/quotes",json={'base_revision':result['revision'],'amount_inc_vat':1,'scope':'사용자가 이미 수락한 범위','exclusions':'추가 사항 없음','reason':'사후 가격 변경'})
    assert changed.status_code==409
    with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(ServiceQuote))==2


def test_expiry_cancel_and_pro_inquiry_cannot_activate_entitlements(client,app):
    auth=register(client);make_admin(app,auth);row=quote(client,create(client))
    with app.state.session_factory() as db:
        db.get(ServiceQuote,row['current_quote_id']).expires_at=utcnow()-timedelta(seconds=1);db.commit()
    assert accept_quote(client,row).json()['code']=='SERVICE_QUOTE_EXPIRED'
    canceled=client.post(f"/v1/service-orders/{row['id']}/cancel",json={'base_revision':row['revision'],'note':'견적 기간 만료로 취소'})
    assert canceled.status_code==200 and canceled.json()['data']['status']=='canceled'
    pilot=quote(client,create(client,'pilot_pro_first_month'),99000)
    pilot=accept_quote(client,pilot).json()['data']
    denied=client.post(f"/v1/admin/service-orders/{pilot['id']}/transition",json={'base_revision':pilot['revision'],'status':'in_progress','note':'결제 없이 구독 활성화 시도'})
    assert denied.status_code==409 and denied.json()['code']=='SUBSCRIPTION_FULFILLMENT_REQUIRED'


def test_work_state_transitions_keep_reasons_and_payment_separate(client,app):
    from cryptography.fernet import Fernet
    app.state.billing_settings.provider='mock';app.state.billing_settings.encryption_key=Fernet.generate_key().decode()
    auth=register(client);make_admin(app,auth);p=project(client)
    row=quote(client,create(client,'file_review',p['id']),55000)
    row=accept_quote(client,row).json()['data']
    assert client.post(f"/v1/admin/service-orders/{row['id']}/transition",json={'base_revision':row['revision'],'status':'completed','note':'단계 생략 시도'}).status_code==409
    assert client.post(f"/v1/admin/service-orders/{row['id']}/transition",json={'base_revision':row['revision'],'status':'in_progress','note':'수납 전 업무 시작 차단'}).json()['code']=='SERVICE_PAYMENT_REQUIRED'
    payment=client.post('/v1/billing/orders',json={'kind':'service','service_order_id':row['id'],'service_quote_id':row['accepted_quote_id'],'service_base_revision':row['revision']},headers={'Idempotency-Key':'work-payment'})
    assert payment.status_code==201,payment.text
    assert client.post('/v1/billing/mock-confirm',json={'order_id':payment.json()['data']['order_id']}).status_code==200
    for status in ('in_progress','delivered','completed'):
        changed=client.post(f"/v1/admin/service-orders/{row['id']}/transition",json={'base_revision':row['revision'],'status':status,'note':'내부 시험 업무 기록 — 실제 서비스 제공 아님'})
        assert changed.status_code==200,changed.text
        row=changed.json()['data'];assert row['status']==status and row['payment_status']=='paid'
    assert len(row['events'])==6
    assert client.post(f"/v1/service-orders/{row['id']}/cancel",json={'base_revision':row['revision'],'note':'완료 건 취소 시도'}).status_code==409


def test_service_order_pagination_and_csrf(client):
    register(client)
    for i in range(4):create(client)
    one=client.get('/v1/service-orders?limit=2').json()['data'];two=client.get('/v1/service-orders',params={'limit':2,'cursor':one['next_cursor']}).json()['data']
    assert len(one['items'])==len(two['items'])==2 and two['next_cursor'] is None
    assert {x['id'] for x in one['items']}.isdisjoint({x['id'] for x in two['items']})
    del client.headers['X-CSRF-Token']
    assert client.post('/v1/service-orders',json={'service_code':'onboarding','request_note':'CSRF 없는 신청'},headers={'Idempotency-Key':'no-csrf'}).status_code==403


def test_concurrent_replay_creates_one_order_and_acceptance_event(client,app):
    auth=register(client);make_admin(app,auth)
    key=str(uuid4())
    body={'service_code':'onboarding','request_note':'동시 신청은 한 건만 접수'}
    def submit(_):
        return client.post('/v1/service-orders',json=body,headers={'Idempotency-Key':key})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(submit,range(2)))
    assert [r.status_code for r in responses]==[201,201]
    assert len({r.json()['data']['id'] for r in responses})==1
    item=quote(client,responses[0].json()['data'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:accept_quote(client,item),range(2)))
    assert sorted(r.status_code for r in responses)==[200,409]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ServiceOrder))==1
        assert db.scalar(select(func.count()).select_from(ServiceOrderEvent).where(ServiceOrderEvent.kind=='accepted'))==1
        assert db.scalar(select(func.count()).select_from(PaymentOrder))==0
