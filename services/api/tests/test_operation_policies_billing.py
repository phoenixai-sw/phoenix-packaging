"""Published price changes affect new agreements, never frozen orders/subscriptions."""
from copy import deepcopy
from datetime import timedelta
from sqlalchemy import select
from services.api.billing.tests.conftest import billing_db,payment_settings,provider
from services.api.billing.tests.test_payments import subscribe
from services.api.models import Tenant,User
from services.api.config import Settings
from services.api.billing.models import PaymentOrder,Subscription,Invoice
from services.api.billing.payments import create_order,confirm_order,change_plan,refund_order,process_due_invoices
from services.api.billing.policy import pricing,plan,prorate,add_months
from services.api.operations.schemas import CreatePolicy,PublishPolicy
from services.api.operations.service import create_policy,publish_policy


def publish_new_prices(db,tenant_id):
    admin=User(tenant_id=tenant_id,email='policy-fixture@example.test',name='Policy fixture',password_hash='unused',role='owner',is_admin=True)
    db.add(admin);db.flush()
    payload=pricing();payload.pop('version')
    for value in payload['plans']:
        value['monthly_ex_vat']*=2;value['monthly_inc_vat']*=2;value['credits']*=2
    for value in payload['topups']:
        value['ex_vat']*=2;value['inc_vat']*=2
    settings=Settings(environment='test')
    draft=create_policy(db,admin,CreatePolicy(kind='pricing',payload=payload,reason='Isolated price compatibility test'),settings)
    publish_policy(db,admin,draft.id,PublishPolicy(expected_active_id=None,reason='Publish isolated price test',understands_existing_subscriptions_unchanged=True),settings)
    return pricing(db)


def test_pending_subscription_and_confirmation_keep_agreed_amount_and_snapshot(billing_db,payment_settings,provider):
    factory,tenant,now=billing_db
    with factory.begin() as db:
        order=create_order(db,tenant,'subscription','pending',plan_id='starter',settings=payment_settings,now=now)
        frozen=deepcopy(order.pricing_snapshot);amount,credits,version=order.amount,order.credits,order.pricing_version
        current=publish_new_prices(db,tenant)
        assert current['version']!=version and plan('starter',db=db)['monthly_inc_vat']==amount*2
        retry=create_order(db,tenant,'subscription','pending',plan_id='starter',settings=payment_settings,now=now)
        assert retry.id==order.id and (retry.amount,retry.credits,retry.pricing_version)==(amount,credits,version)
        confirm_order(db,tenant,order.order_id,'mock-key',amount,provider=provider,settings=payment_settings,now=now+timedelta(seconds=20))
        assert order.status=='paid' and order.pricing_snapshot==frozen
        subscription=db.get(Subscription,order.subscription_id)
        assert subscription.pricing_snapshot==frozen
        assert db.get(Invoice,order.invoice_id).amount==amount


def test_old_subscription_renews_old_table_while_new_customer_uses_new_table(billing_db,payment_settings,provider):
    factory,tenant,now=billing_db
    with factory.begin() as db:
        original=subscribe(db,tenant,now,payment_settings,provider)
        frozen=deepcopy(original.pricing_snapshot);old_amount,old_credits=original.amount,original.credits
        current=publish_new_prices(db,tenant)
        new_tenant=Tenant(name='New customer after publication');db.add(new_tenant);db.flush()
        fresh=create_order(db,new_tenant.id,'subscription','new',plan_id='starter',settings=payment_settings,now=now)
        assert (fresh.amount,fresh.credits)==(old_amount*2,old_credits*2)
        assert fresh.pricing_snapshot==current and fresh.pricing_version==current['version']
    due=add_months(now,anchor_day=31)
    assert process_due_invoices(factory,provider=provider,settings=payment_settings,now=due)==1
    with factory.begin() as db:
        renewal=db.scalar(select(PaymentOrder).where(PaymentOrder.tenant_id==tenant,PaymentOrder.kind=='renewal'))
        assert renewal.status=='paid'
        assert (renewal.amount,renewal.credits)==(old_amount,old_credits)
        assert renewal.pricing_snapshot==frozen and db.get(Subscription,renewal.subscription_id).pricing_snapshot==frozen


def test_pending_topup_frozen_but_new_topup_gets_published_price(billing_db,payment_settings,provider):
    factory,tenant,now=billing_db
    with factory.begin() as db:
        subscribe(db,tenant,now,payment_settings,provider)
        pending=create_order(db,tenant,'topup','old-topup',credits=500,settings=payment_settings,now=now)
        frozen=deepcopy(pending.pricing_snapshot);old_amount=pending.amount
        publish_new_prices(db,tenant)
        fresh=create_order(db,tenant,'topup','new-topup',credits=500,settings=payment_settings,now=now)
        assert fresh.amount==old_amount*2 and fresh.pricing_snapshot!=frozen
        confirm_order(db,tenant,pending.order_id,'mock',pending.amount,provider=provider,settings=payment_settings,now=now)
        assert pending.status=='paid' and pending.amount==old_amount and pending.pricing_snapshot==frozen


def test_existing_plan_change_and_refund_preserve_source_agreement(billing_db,payment_settings,provider):
    factory,tenant,now=billing_db;middle=now+timedelta(days=10)
    with factory.begin() as db:
        initial=subscribe(db,tenant,now,payment_settings,provider)
        frozen=deepcopy(initial.pricing_snapshot);subscription=db.get(Subscription,initial.subscription_id)
        current=publish_new_prices(db,tenant)
        changed=change_plan(db,tenant,'pro','upgrade',settings=payment_settings,now=middle)
        upgrade=db.scalar(select(PaymentOrder).where(PaymentOrder.order_id==changed['order']['order_id']))
        expected=prorate(plan('starter',snapshot=frozen),plan('pro',snapshot=frozen),now,add_months(now),middle)
        assert (upgrade.amount,upgrade.credits)==expected
        assert upgrade.pricing_snapshot==upgrade.source_pricing_snapshot==frozen
        confirm_order(db,tenant,upgrade.order_id,'mock',upgrade.amount,provider=provider,settings=payment_settings,now=middle)
        assert subscription.plan_id=='pro' and subscription.pricing_snapshot==frozen
        change_plan(db,tenant,'starter','downgrade',settings=payment_settings,now=middle)
        assert subscription.next_pricing_snapshot==frozen and subscription.next_pricing_snapshot!=current
        refund_order(db,tenant,upgrade.order_id,'Unused upgrade refund fixture',provider=provider,settings=payment_settings,now=middle+timedelta(seconds=10))
        assert upgrade.status=='refunded' and subscription.plan_id=='starter'
        assert subscription.pricing_snapshot==upgrade.source_pricing_snapshot==frozen
        assert subscription.next_plan_id is None and subscription.next_pricing_snapshot is None


def test_legacy_subscription_without_snapshot_uses_seed_not_new_active_policy(billing_db,payment_settings,provider):
    factory,tenant,now=billing_db
    with factory.begin() as db:
        original=subscribe(db,tenant,now,payment_settings,provider)
        db.get(Subscription,original.subscription_id).pricing_snapshot=None
        old_amount=original.amount;publish_new_prices(db,tenant)
    assert process_due_invoices(factory,provider=provider,settings=payment_settings,now=add_months(now,anchor_day=31))==1
    with factory.begin() as db:
        order=db.scalar(select(PaymentOrder).where(PaymentOrder.tenant_id==tenant,PaymentOrder.kind=='renewal'))
        assert order.amount==old_amount and order.pricing_snapshot==pricing()
