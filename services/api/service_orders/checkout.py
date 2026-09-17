"""Accepted-quote checkout. Caller owns the transaction and tenant wallet lock."""
from copy import deepcopy
from datetime import timedelta
from sqlalchemy import select
from ..billing.models import BillingAccount, PaymentOrder, Subscription
from ..billing.policy import aware, plan, pricing
from ..billing.service import canonical_hash, lock_wallet
from ..database import new_id, utcnow
from ..errors import APIError
from ..feature_models import AuditEvent
from .models import ServiceCheckout, ServiceOrder, ServiceQuote

TERMS_POLICY='service-checkout-2026-09-18-v1'


def make_quote_terms(db,code,amount,scope,exclusions):
    agreed=deepcopy(pricing(db))
    pilot=code=='pilot_pro_first_month'
    if pilot and amount!=99000:
        raise APIError(422,'PILOT_PRICE_INVALID','첫 달 Pro 제안 견적은 VAT 포함 99,000원입니다.')
    selected=plan('pro',snapshot=agreed)
    public={'terms_version':'','service_code':code,'amount_inc_vat':amount,'currency':'KRW','vat_included':True,
        'checkout_kind':'billing_auth' if pilot else 'payment','automatic_renewal':pilot,
        'credits':selected['credits'] if pilot else 0,'seats':selected['seats'] if pilot else None,
        'plan_id':'pro' if pilot else None,'renewal_amount_inc_vat':selected['monthly_inc_vat'] if pilot else None,
        'renewal_interval':'month' if pilot else None,'pricing_version':agreed['version'],
        'policy_version':TERMS_POLICY,'proposal_only':True,
        'first_period_discount_inc_vat':max(0,selected['monthly_inc_vat']-amount) if pilot else 0,
        'upgrade_proration_basis':'paid_first_period_amount' if pilot else None}
    public['terms_version']=canonical_hash({'terms':public,'pricing':agreed,'scope':scope,'exclusions':exclusions})
    return {'public':public,'pricing':agreed}


def checkout_link(db,order_id):
    return db.get(ServiceCheckout,order_id)


def linked_order(db,service_id):
    link=db.scalar(select(ServiceCheckout).where(ServiceCheckout.service_order_id==service_id))
    return db.get(PaymentOrder,link.payment_order_id) if link else None


def validate_service_charge(db,order):
    link=checkout_link(db,order.id)
    if link is None:return
    row=db.get(ServiceOrder,link.service_order_id)
    if not row or row.tenant_id!=order.tenant_id or row.accepted_quote_id!=link.service_quote_id or row.status!='accepted':
        raise APIError(409,'SERVICE_CHECKOUT_CLOSED','업무가 시작·취소되었거나 수락한 견적이 달라 새 결제를 진행할 수 없습니다.')
    expected=link.snapshot['terms']['amount_inc_vat']
    if order.amount!=expected or order.currency!='KRW':
        raise APIError(409,'SERVICE_PRICE_CHANGED','수락한 서비스 견적과 결제 금액이 다릅니다.')


def create_service_order(db,tenant_id,operation_key,*,service_order_id,service_quote_id,service_base_revision,
                         recurring_consent,actor_id,settings,now=None):
    from ..billing.payments import billing_account,create_order
    now=now or utcnow();settings.validate();lock_wallet(db,tenant_id,now)
    if not operation_key or len(operation_key)>160:raise APIError(422,'IDEMPOTENCY_KEY_INVALID','주문 요청 식별자를 확인해 주세요.')
    row=db.get(ServiceOrder,str(service_order_id))
    if row is None or row.tenant_id!=tenant_id:raise APIError(404,'SERVICE_ORDER_NOT_FOUND','신청을 찾을 수 없습니다.')
    request={'kind':'service','service_order_id':str(service_order_id),'service_quote_id':str(service_quote_id),
             'service_base_revision':service_base_revision,'recurring_consent':recurring_consent}
    digest=canonical_hash(request)
    old=db.scalar(select(PaymentOrder).where(PaymentOrder.tenant_id==tenant_id,PaymentOrder.operation_key==operation_key))
    if old:
        if old.request_hash!=digest:raise APIError(409,'IDEMPOTENCY_CONFLICT','같은 식별자로 다른 주문을 만들 수 없습니다.')
        return old
    existing=linked_order(db,row.id)
    if existing:
        # Never make another PG order for the same quote, even after a timeout,
        # failure or refund. Explicit retry uses its original order/key.
        raise APIError(409,'SERVICE_PAYMENT_EXISTS','연결된 결제 주문을 조회·재시도해 주세요. 새 주문으로 중복 결제하지 않습니다.')
    if row.revision!=service_base_revision:raise APIError(409,'SERVICE_ORDER_CONFLICT','최신 신청 내역을 확인해 주세요.')
    q=db.get(ServiceQuote,str(service_quote_id))
    if row.status!='accepted' or not q or q.tenant_id!=tenant_id or q.order_id!=row.id or q.id!=row.accepted_quote_id:
        raise APIError(409,'SERVICE_QUOTE_CHANGED','현재 수락한 견적을 확인해 주세요.')
    if not q.checkout_terms:raise APIError(409,'SERVICE_TERMS_REQUIRED','결제·갱신 조건을 포함한 새 견적과 수락이 필요합니다.')
    if aware(q.expires_at)<=aware(now):raise APIError(409,'SERVICE_QUOTE_EXPIRED','결제 전 견적 기한이 지났습니다. 새 신청에서 견적을 확인해 주세요.')
    terms=deepcopy(q.checkout_terms['public']);agreed=deepcopy(q.checkout_terms['pricing'])
    if q.amount_inc_vat<=0:raise APIError(409,'SERVICE_NO_PAYMENT_DUE','수납 금액이 없는 견적입니다.')
    pilot=row.service_code=='pilot_pro_first_month'
    if pilot:
        expected={key:terms[key] for key in ('terms_version','renewal_amount_inc_vat','currency','automatic_renewal','plan_id')}
        expected['first_amount_inc_vat']=terms['amount_inc_vat']
        if recurring_consent!=expected:raise APIError(422,'RECURRING_CONSENT_REQUIRED','첫 달 금액과 이후 월 요금·자동 갱신에 명시적으로 동의해 주세요.')
        if db.scalar(select(Subscription.id).where(Subscription.tenant_id==tenant_id)):
            raise APIError(409,'PILOT_NEW_SUBSCRIBER_REQUIRED','첫 달 Pro 제안은 기존 구독 가입 이력이 없는 조직만 신청할 수 있습니다.')
        order=create_order(db,tenant_id,'subscription',operation_key,plan_id='pro',settings=settings,now=now,agreed_pricing=agreed)
        from ..billing.models import Invoice
        order.amount=q.amount_inc_vat
        db.get(Invoice,order.invoice_id).amount=q.amount_inc_vat
    else:
        if recurring_consent is not None:raise APIError(422,'UNEXPECTED_RECURRING_CONSENT','일회성 서비스에는 자동 갱신이 없습니다.')
        billing_account(db,tenant_id,settings)
        order=PaymentOrder(tenant_id=tenant_id,order_id='pp_'+new_id().replace('-',''),operation_key=operation_key,
            request_hash=digest,kind='service',amount=q.amount_inc_vat,currency='KRW',credits=0,
            pricing_version=q.policy_version,pricing_snapshot=agreed,credits_expires_at=now,created_at=now)
        db.add(order);db.flush()
    order.request_hash=digest
    link=ServiceCheckout(payment_order_id=order.id,tenant_id=tenant_id,service_order_id=row.id,service_quote_id=q.id,
        actor_id=actor_id,accepted_revision=row.revision,consent=deepcopy(recurring_consent),created_at=now,
        snapshot={'service_code':row.service_code,'scope':q.scope,'exclusions':q.exclusions,'quote_number':q.number,
            'amount_inc_vat':q.amount_inc_vat,'currency':q.currency,'terms':terms,'accepted_quote_id':q.id})
    db.add(link)
    db.add(AuditEvent(tenant_id=tenant_id,actor_id=actor_id,action='service_checkout.created',entity_id=row.id,
        details={'payment_order_id':order.id,'quote_id':q.id,'accepted_revision':row.revision,'terms_version':terms['terms_version'],'explicit_recurring_consent':pilot}))
    db.flush();return order


def checkout_payload(db,row,settings):
    from ..billing.payments import order_payload,subscription_payload
    from ..billing.sync import payment_snapshot
    order=linked_order(db,row.id)
    q=db.get(ServiceQuote,row.accepted_quote_id or row.current_quote_id) if row.accepted_quote_id or row.current_quote_id else None
    terms=deepcopy(q.checkout_terms['public']) if q and q.checkout_terms else None
    caps=settings.capabilities()
    block=None
    if row.status!='accepted':block='현재 수락한 견적에서만 수납을 시작할 수 있습니다.'
    elif not terms:block='결제 조건을 포함한 새 견적과 수락이 필요합니다.'
    elif order:block='연결된 결제 주문을 조회·재시도해 주세요.'
    elif aware(q.expires_at)<=aware(utcnow()):block='견적 기한이 지났습니다.'
    elif q.amount_inc_vat<=0:block='수납할 금액이 없습니다.'
    elif row.service_code=='pilot_pro_first_month' and db.scalar(select(Subscription.id).where(Subscription.tenant_id==row.tenant_id)):block='기존 구독 가입 이력이 있어 첫 달 Pro 제안을 사용할 수 없습니다.'
    elif not caps['checkout_available']:block=caps['message']
    account=db.get(BillingAccount,row.tenant_id) if caps['checkout_available'] else None
    refund_allowed=bool(order and order.status=='paid' and (order.kind!='service' or row.status=='accepted') and caps['checkout_available'])
    if refund_allowed and order.kind!='service':
        from ..billing.models import CreditBucket
        bucket=db.scalar(select(CreditBucket).where(CreditBucket.tenant_id==row.tenant_id,CreditBucket.grant_key=='order:'+order.id))
        refund_allowed=bool(bucket and not bucket.reserved and not bucket.consumed and not bucket.expired and bucket.available==bucket.granted)
    data=order_payload(order,account,settings) if order else None
    if data:data.update(payment=payment_snapshot(db,order),service_order_id=row.id,service_quote_id=q.id if q else None)
    sub=db.get(Subscription,order.subscription_id) if order and order.subscription_id else None
    retry_allowed=bool(order and order.status in {'pending','failed'} and row.status=='accepted' and
        aware(order.created_at)+timedelta(minutes=30)>aware(utcnow()) and caps['checkout_available'])
    return {'payment_status':order.status if order else 'not_collected','credits_granted':order.credits if order and order.status=='paid' else 0,
        'checkout_enabled':block is None,'checkout_blocked_reason':block,'checkout_terms':terms,'payment_order':data,
        'refund_allowed':refund_allowed,'refund_blocked_reason':None if refund_allowed else '미결제·업무 진행·크레딧 사용 내역은 환불 검토가 필요합니다.',
        'payment_retry_allowed':retry_allowed,'payment_retry_blocked_reason':None if retry_allowed else '만료·불명확·취소된 주문은 상태 조회 또는 담당자 확인이 필요합니다.',
        'subscription_active':bool(sub and sub.paid_until and aware(sub.paid_until)>aware(utcnow())),
        'subscription':subscription_payload(sub)}
