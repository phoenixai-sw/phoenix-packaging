from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select,func
import pytest

from services.api.tests.test_api import app,client,register,project
from services.api.tests.test_service_orders import create,quote,accept_quote,make_admin
from services.api.billing.models import PaymentOrder,Payment,Subscription,Invoice,CreditBucket,LedgerEntry,Wallet
from services.api.billing.payments import MockProvider,ProviderError,confirm_order,refund_order,process_due_invoices,entitlements,change_plan
from services.api.billing.sync import sync_verified_payment
from services.api.billing.policy import aware
from services.api.database import utcnow
from services.api.errors import APIError
from services.api.service_orders.models import ServiceOrder,ServiceQuote,ServiceCheckout


@pytest.fixture
def service(client,app):
    settings=app.state.billing_settings
    settings.provider='mock';settings.encryption_key=Fernet.generate_key().decode()
    auth=register(client);make_admin(app,auth)
    return app,client,auth


def accepted(client,code='onboarding',amount=100000):
    p=project(client)['id'] if code=='file_review' else None
    row=quote(client,create(client,code,p),amount)
    response=accept_quote(client,row);assert response.status_code==200,response.text
    return response.json()['data']


def request_body(row,consent=True):
    body={'kind':'service','service_order_id':row['id'],'service_quote_id':row['accepted_quote_id'],'service_base_revision':row['revision']}
    terms=row.get('checkout_terms')
    if consent and terms and terms['automatic_renewal']:
        body['recurring_consent']={k:terms[k] for k in ['terms_version','renewal_amount_inc_vat','currency','automatic_renewal','plan_id']}
        body['recurring_consent']['first_amount_inc_vat']=terms['amount_inc_vat']
    return body


def payment(client,row,key='service-payment',body=None):
    response=client.post('/v1/billing/orders',json=body or request_body(row),headers={'Idempotency-Key':key})
    assert response.status_code==201,response.text
    return response.json()['data']


def pay(client,order):
    result=client.post('/v1/billing/mock-confirm',json={'order_id':order['order_id']})
    assert result.status_code==200,result.text
    assert result.json()['data']['status']=='paid'
    return result.json()['data']


@pytest.mark.parametrize('code,amount',[('onboarding',100000),('file_review',55000),('onboarding',121000)])
def test_one_time_server_price_idempotent_zero_credits_no_renewal_and_refund(service,code,amount):
    app,client,auth=service;row=accepted(client,code,amount)
    order=payment(client,row);assert payment(client,row)['id']==order['id']
    assert order['kind']=='service' and order['checkout_kind']=='payment' and order['amount']==amount and order['credits']==0
    assert order['service_order_id']==row['id'] and order['service_quote_id']==row['accepted_quote_id']
    duplicate=client.post('/v1/billing/orders',json=request_body(row),headers={'Idempotency-Key':'other-key'})
    assert duplicate.status_code==409 and duplicate.json()['code']=='SERVICE_PAYMENT_EXISTS'
    pay(client,order);pay(client,order)
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Subscription))==0
        assert db.scalar(select(func.count()).select_from(Invoice))==0
        assert db.scalar(select(func.count()).select_from(Payment))==1
        assert db.scalar(select(func.count()).select_from(CreditBucket))==1  # trial only
        assert db.scalar(select(func.count()).select_from(LedgerEntry))==1
        assert db.get(Wallet,auth['tenant']['id']).ever_paid is False
        assert entitlements(db,auth['tenant']['id'])['production_export'] is False
    assert client.post(f"/v1/service-orders/{row['id']}/cancel",json={'base_revision':row['revision'],'note':'환불 없이 취소 시도'}).json()['code']=='SERVICE_PAYMENT_PENDING'
    refund=client.post('/v1/billing/orders/'+order['order_id']+'/refund',json={'reason':'업무 시작 전 취소'})
    assert refund.status_code==200 and refund.json()['data']['status']=='refunded'
    assert client.post('/v1/billing/orders/'+order['order_id']+'/refund',json={'reason':'같은 환불 재조회'}).json()['data']['status']=='refunded'
    detail=client.get('/v1/service-orders/'+row['id']).json()['data']
    assert detail['status']=='accepted' and detail['payment_status']=='refunded' and detail['credits_granted']==0
    assert client.post(f"/v1/service-orders/{row['id']}/cancel",json={'base_revision':row['revision'],'note':'환불 확인 후 신청 취소'}).status_code==200


def test_owner_tenant_csrf_and_caller_price_injection(service):
    app,client,auth=service;row=accepted(client)
    for change in [{'amount':1},{'credits':500},{'plan_id':'starter'}]:
        assert client.post('/v1/billing/orders',json={**request_body(row),**change},headers={'Idempotency-Key':'tamper'}).status_code==422
    assert client.post('/v1/billing/orders',json=request_body(row),headers={'Idempotency-Key':'csrf','X-CSRF-Token':'wrong'}).status_code==403
    with TestClient(app) as other:
        register(other,'other-checkout@example.com')
        assert other.post('/v1/billing/orders',json=request_body(row),headers={'Idempotency-Key':'other'}).status_code==404
    bad=request_body(row);bad['service_base_revision']-=1
    assert client.post('/v1/billing/orders',json=bad,headers={'Idempotency-Key':'stale'}).json()['code']=='SERVICE_ORDER_CONFLICT'
    order=payment(client,row)
    assert client.post('/v1/billing/confirm',json={'order_id':order['order_id'],'payment_key':'mock','amount':1}).json()['code']=='PAYMENT_AMOUNT_MISMATCH'


def test_pilot_explicit_consent_frozen_price_renewal_and_existing_subscription_block(service):
    app,client,auth=service;row=accepted(client,'pilot_pro_first_month',99000)
    terms=row['checkout_terms'];assert (terms['amount_inc_vat'],terms['renewal_amount_inc_vat'],terms['credits'],terms['seats'])==(99000,108900,1500,3)
    assert client.post('/v1/billing/orders',json=request_body(row,False),headers={'Idempotency-Key':'no-consent'}).json()['code']=='RECURRING_CONSENT_REQUIRED'
    bad=request_body(row);bad['recurring_consent']['renewal_amount_inc_vat']=99000
    assert client.post('/v1/billing/orders',json=bad,headers={'Idempotency-Key':'wrong-consent'}).json()['code']=='RECURRING_CONSENT_REQUIRED'
    order=payment(client,row);assert order['kind']=='subscription' and order['checkout_kind']=='billing_auth'
    pay(client,order)
    with app.state.session_factory() as db:
        sub=db.scalar(select(Subscription));due=aware(sub.current_period_end)
        assert sub.plan_id=='pro' and sub.pricing_snapshot['plans'][1]['monthly_inc_vat']==108900
        assert entitlements(db,auth['tenant']['id'])['seats']==3
        assert db.scalar(select(CreditBucket).where(CreditBucket.kind=='monthly')).granted==1500
    provider=MockProvider()
    process_due_invoices(app.state.session_factory,provider=provider,settings=app.state.billing_settings,now=due+timedelta(seconds=1))
    process_due_invoices(app.state.session_factory,provider=provider,settings=app.state.billing_settings,now=due+timedelta(seconds=2))
    with app.state.session_factory() as db:
        renewal=db.scalar(select(PaymentOrder).where(PaymentOrder.kind=='renewal'))
        assert renewal.amount==108900 and renewal.credits==1500 and renewal.status=='paid'
        assert db.scalar(select(func.count()).select_from(Invoice))==2
    second=accepted(client,'pilot_pro_first_month',99000)
    assert client.post('/v1/billing/orders',json=request_body(second),headers={'Idempotency-Key':'second-pilot'}).json()['code']=='PILOT_NEW_SUBSCRIBER_REQUIRED'


def test_old_accepted_quote_cannot_retroactively_bill_and_requires_new_acceptance(service):
    app,client,auth=service;row=accepted(client,'pilot_pro_first_month',99000)
    old_quote_id=row['accepted_quote_id']
    with app.state.session_factory.begin() as db:db.get(ServiceQuote,old_quote_id).checkout_terms=None
    assert client.post('/v1/billing/orders',json=request_body(row),headers={'Idempotency-Key':'legacy'}).json()['code']=='SERVICE_TERMS_REQUIRED'
    fresh=quote(client,row,99000)
    assert fresh['accepted_quote_id'] is None and fresh['status']=='quoted'
    assert client.post('/v1/billing/orders',json=request_body(row),headers={'Idempotency-Key':'stale-acceptance'}).status_code==409
    accepted_again=accept_quote(client,fresh).json()['data'];payment(client,accepted_again)
    with app.state.session_factory() as db:assert db.get(ServiceQuote,old_quote_id).checkout_terms is None


def test_concurrent_checkout_and_work_vs_refund_are_serialized(service):
    app,client,auth=service;row=accepted(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:client.post('/v1/billing/orders',json=request_body(row),headers={'Idempotency-Key':'same'}),range(2)))
    assert [r.status_code for r in results]==[201,201]
    assert len({r.json()['data']['id'] for r in results})==1
    order=results[0].json()['data'];pay(client,order)
    def work():return client.post(f"/v1/admin/service-orders/{row['id']}/transition",json={'base_revision':row['revision'],'status':'in_progress','note':'결제 확인 후 실제 업무 시작'})
    def refund():return client.post('/v1/billing/orders/'+order['order_id']+'/refund',json={'reason':'업무 시작 전 환불 요청'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(work);b=pool.submit(refund);results=[a.result(),b.result()]
    assert sorted(r.status_code for r in results)==[200,409]
    detail=client.get('/v1/service-orders/'+row['id']).json()['data']
    assert (detail['status'],detail['payment_status']) in {('accepted','refunded'),('in_progress','paid')}


def test_concurrent_pilot_orders_cannot_create_two_subscriptions(service):
    app,client,auth=service
    rows=[accepted(client,'pilot_pro_first_month',99000) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda row:client.post('/v1/billing/orders',json=request_body(row),headers={'Idempotency-Key':str(uuid4())}),rows))
    assert sorted(r.status_code for r in results)==[201,409]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Subscription))==1
        assert db.scalar(select(func.count()).select_from(ServiceCheckout))==1


def test_uncertain_service_payment_is_query_only_and_expired_order_never_calls_provider(service):
    app,client,auth=service;row=accepted(client);order=payment(client,row)
    class Uncertain(MockProvider):
        def confirm(self,*args):
            self.calls.append(('confirm',args[1]));raise ProviderError('TIMEOUT',uncertain=True)
    provider=Uncertain()
    with app.state.session_factory.begin() as db:
        result=confirm_order(db,auth['tenant']['id'],order['order_id'],'mock',order['amount'],provider=provider,settings=app.state.billing_settings)
        assert result.status=='reconciliation_required'
    with app.state.session_factory.begin() as db:
        with pytest.raises(APIError) as error:confirm_order(db,auth['tenant']['id'],order['order_id'],'mock',order['amount'],provider=provider,settings=app.state.billing_settings)
        assert error.value.code=='PAYMENT_RECONCILIATION_REQUIRED'
    assert len([x for x in provider.calls if x[0]=='confirm'])==1
    row2=accepted(client);order2=payment(client,row2,'expired')
    with app.state.session_factory.begin() as db:
        item=db.get(PaymentOrder,order2['id']);item.created_at=utcnow()-timedelta(hours=1)
    with app.state.session_factory.begin() as db:
        with pytest.raises(APIError) as error:confirm_order(db,auth['tenant']['id'],order2['order_id'],'mock',order2['amount'],provider=provider,settings=app.state.billing_settings)
        assert error.value.code=='SERVICE_CHECKOUT_EXPIRED'
    assert len([x for x in provider.calls if x[0]=='confirm'])==1


def test_external_partial_then_full_cancel_preserves_work_status_and_financial_history(service):
    app,client,auth=service;row=accepted(client);order=payment(client,row);pay(client,order)
    work=client.post(f"/v1/admin/service-orders/{row['id']}/transition",json={'base_revision':row['revision'],'status':'in_progress','note':'서비스 제공을 시작합니다.'})
    assert work.status_code==200
    with app.state.session_factory.begin() as db:
        item=db.get(PaymentOrder,order['id']);paid=db.scalar(select(Payment).where(Payment.order_id==item.id))
        result={'orderId':item.order_id,'paymentKey':paid.provider_payment_key,'totalAmount':100000,'balanceAmount':50000,'currency':'KRW','status':'PARTIAL_CANCELED','approvedAt':paid.approved_at.isoformat(),'cancels':[{'cancelAmount':50000,'transactionKey':'a'}]}
        sync_verified_payment(db,item,result,settings=app.state.billing_settings,source='test')
        assert item.status=='reconciliation_required'
        result.update(status='CANCELED',balanceAmount=0);result['cancels'].append({'cancelAmount':50000,'transactionKey':'b'})
        sync_verified_payment(db,item,result,settings=app.state.billing_settings,source='test')
        assert item.status=='refunded' and db.get(ServiceOrder,row['id']).status=='in_progress'
        assert db.scalar(select(func.count()).select_from(LedgerEntry))==1


def test_pilot_unused_refund_after_administrative_completion_revokes_access(service):
    app,client,auth=service;row=accepted(client,'pilot_pro_first_month',99000);order=payment(client,row);pay(client,order)
    for status in ['in_progress','delivered','completed']:
        result=client.post(f"/v1/admin/service-orders/{row['id']}/transition",json={'base_revision':row['revision'],'status':status,'note':'구독 등록 지원 단계 확인'})
        assert result.status_code==200,result.text;row=result.json()['data']
    assert row['refund_allowed'] is True and row['subscription_active'] is True
    result=client.post('/v1/billing/orders/'+order['order_id']+'/refund',json={'reason':'사용 전 첫 달 구독 환불'})
    assert result.status_code==200 and result.json()['data']['status']=='refunded'
    with app.state.session_factory() as db:
        access=entitlements(db,auth['tenant']['id'])
        assert access['active_subscription'] is False and access['team_access'] is False
        sub=db.scalar(select(Subscription));assert sub.cancel_at_period_end is True
        assert db.get(ServiceOrder,row['id']).status=='completed'


def test_pilot_cancel_renewal_keeps_first_period_and_does_not_bill_again(service):
    app,client,auth=service;row=accepted(client,'pilot_pro_first_month',99000);order=payment(client,row);pay(client,order)
    canceled=client.post('/v1/billing/cancel-renewal')
    assert canceled.status_code==200
    with app.state.session_factory() as db:
        sub=db.scalar(select(Subscription));due=aware(sub.current_period_end)
        assert entitlements(db,auth['tenant']['id'])['seats']==3
    provider=MockProvider()
    process_due_invoices(app.state.session_factory,provider=provider,settings=app.state.billing_settings,now=due+timedelta(seconds=1))
    assert provider.calls==[]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Invoice))==1
        assert not entitlements(db,auth['tenant']['id'],now=due+timedelta(seconds=1))['active_subscription']


def test_revoked_request_after_external_charge_records_money_without_fulfillment(service):
    app,client,auth=service;row=accepted(client);order=payment(client,row)
    provider=MockProvider();provider.confirm('mock',order['order_id'],order['amount'])
    with app.state.session_factory.begin() as db:
        # Simulate an earlier process/provider callback racing an administrative
        # correction. Real customer cancel routes reject pending linked orders.
        db.get(ServiceOrder,row['id']).status='canceled'
        item=db.get(PaymentOrder,order['id'])
        sync_verified_payment(db,item,provider.query(order['order_id']),settings=app.state.billing_settings,source='late_callback')
        assert item.status=='reconciliation_required'
        assert db.scalar(select(func.count()).select_from(Payment))==1
        assert db.scalar(select(func.count()).select_from(LedgerEntry))==1
        assert db.get(Wallet,auth['tenant']['id']).ever_paid is False


def test_pilot_upgrade_prorates_actual_first_payment_without_discounting_future_renewal(service):
    app,client,auth=service;row=accepted(client,'pilot_pro_first_month',99000);order=payment(client,row);pay(client,order)
    assert row['checkout_terms']['first_period_discount_inc_vat']==9900
    assert row['checkout_terms']['upgrade_proration_basis']=='paid_first_period_amount'
    with app.state.session_factory.begin() as db:
        sub=db.scalar(select(Subscription))
        changed=change_plan(db,auth['tenant']['id'],'partner','first-month-upgrade',settings=app.state.billing_settings,now=aware(sub.current_period_start))
        assert changed['order']['amount']==273900-99000
        upgrade=db.get(PaymentOrder,changed['order']['id'])
        assert upgrade.source_pricing_snapshot['plans'][1]['monthly_inc_vat']==108900
        assert sub.pricing_snapshot['plans'][1]['monthly_inc_vat']==108900


@pytest.mark.parametrize('field,value',[('automatic_renewal',1),('automatic_renewal','true'),('automatic_renewal',False),('first_amount_inc_vat',True),('renewal_amount_inc_vat','108900'),('currency','USD')])
def test_pilot_consent_requires_explicit_typed_values(service,field,value):
    app,client,auth=service;row=accepted(client,'pilot_pro_first_month',99000)
    body=request_body(row);body['recurring_consent'][field]=value
    assert client.post('/v1/billing/orders',json=body,headers={'Idempotency-Key':'invalid-consent'}).status_code==422
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(PaymentOrder))==0
        assert db.scalar(select(func.count()).select_from(Subscription))==0


def test_first_month_quote_rejects_unagreed_price(service):
    app,client,auth=service;row=create(client,'pilot_pro_first_month')
    response=client.post(f"/v1/admin/service-orders/{row['id']}/quotes",json={'base_revision':row['revision'],'amount_inc_vat':100000,'scope':'Pro 이용 지원 조건 확인','exclusions':'인쇄비용 별도','valid_days':7,'reason':'모집 가격 변경 시도'})
    assert response.status_code==422 and response.json()['code']=='PILOT_PRICE_INVALID'
    with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(ServiceQuote))==0
