from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from cryptography.fernet import Fernet
import httpx
import pytest
from sqlalchemy import func, select

from services.api.errors import APIError
from services.api.models import User
from services.api.billing.models import BillingAccount, CreditBucket, Invoice, LedgerEntry, Payment, PaymentEvent, PaymentOrder, Subscription, WebhookEvent
from services.api.billing.payments import BillingSettings, MockProvider, ProviderError, TossProvider, billing_account, bind_and_charge, cancel_renewal, change_plan, confirm_order, create_order, enforce_membership_entitlement, entitlements, process_due_invoices, reconcile_pending_orders, reconcile_webhook, refund_order
from services.api.billing.policy import add_months, aware, plan, prorate
from services.api.billing.service import capture_unit, reserve, wallet_summary


def subscribe(db, tenant, now, settings, provider, selected="starter"):
    order = create_order(db, tenant, "subscription", "subscribe", plan_id=selected, settings=settings, now=now)
    account = billing_account(db, tenant, settings)
    bind_and_charge(db, tenant, order.order_id, "mock-auth", settings.decrypt(account.customer_key_encrypted), provider=provider, settings=settings, now=now)
    return order


def test_first_payment_one_grant_and_encrypted_keys(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = subscribe(db, tenant, now, payment_settings, provider)
        confirm_order(db, tenant, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=now)
        assert order.status == "paid"
        assert wallet_summary(db, tenant, now=now)["balance"] == 530
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.scalar(select(func.count()).select_from(CreditBucket).where(CreditBucket.kind == "monthly")) == 1
        account = db.get(BillingAccount, tenant)
        assert account.customer_key_encrypted.startswith("gAAAA")
        assert account.billing_key_encrypted.startswith("gAAAA")
        assert "mock_billing" not in account.billing_key_encrypted
        assert payment_settings.decrypt(account.billing_key_encrypted).startswith("mock_billing_")


def test_amount_order_account_and_currency_must_match(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "order", plan_id="starter", settings=payment_settings, now=now)
        with pytest.raises(APIError) as wrong_amount:
            confirm_order(db, tenant, order.order_id, "mock", 1, provider=provider, settings=payment_settings, now=now)
        assert wrong_amount.value.code == "PAYMENT_AMOUNT_MISMATCH"
        assert not provider.calls
        malicious = {"orderId": order.order_id, "paymentKey": "forged", "totalAmount": order.amount, "currency": "USD", "status": "DONE", "mId": "mock"}
        provider.payments[order.order_id] = malicious
        with pytest.raises(APIError) as wrong_currency:
            confirm_order(db, tenant, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=now)
        assert wrong_currency.value.code == "PAYMENT_MISMATCH"
        assert db.scalar(select(func.count()).select_from(Payment)) == 0


def test_lost_payment_response_requeries_same_order_once(billing_db, payment_settings):
    class LostResponse(MockProvider):
        def confirm(self, *args):
            super().confirm(*args)
            raise ProviderError("TIMEOUT", retryable=True, uncertain=True)
    provider = LostResponse()
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "lost", plan_id="starter", settings=payment_settings, now=now)
        confirmed = confirm_order(db, tenant, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=now)
        assert confirmed.status == "paid"
        assert len([call for call in provider.calls if call[0] == "confirm"]) == 1
        assert wallet_summary(db, tenant, now=now)["balance"] == 530
        confirm_order(db, tenant, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=now)
        assert len([call for call in provider.calls if call[0] == "confirm"]) == 1


def test_forged_duplicate_and_reordered_webhooks_do_not_grant_twice(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "webhook", plan_id="starter", settings=payment_settings, now=now)
        fake = {"eventType": "PAYMENT_STATUS_CHANGED", "data": {"orderId": order.order_id, "status": "DONE", "totalAmount": order.amount}}
        with pytest.raises(APIError) as unverified:
            reconcile_webhook(db, fake, provider=provider, settings=payment_settings, now=now)
        assert unverified.value.code == "PAYMENT_UNVERIFIED"
        assert wallet_summary(db, tenant, now=now)["balance"] == 30
        provider.confirm("mock", order.order_id, order.amount)
        reconcile_webhook(db, fake, provider=provider, settings=payment_settings, now=now)
        # Forged/old event body never overrides the provider's current state.
        fake["data"]["status"] = "CANCELED"
        reconcile_webhook(db, fake, provider=provider, settings=payment_settings, now=now)
        assert order.status == "paid"
        assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert wallet_summary(db, tenant, now=now)["balance"] == 530


def test_korean_month_end_leap_anchor_and_unique_recurring_invoices(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    february = datetime(2024, 2, 29, tzinfo=timezone.utc)
    march = datetime(2024, 3, 31, tzinfo=timezone.utc)
    assert add_months(now, anchor_day=31) == february
    assert add_months(february, anchor_day=31) == march
    # UTC Jan30 23:30 is Korea Jan31, and this local anchor is preserved.
    korea_anchor = datetime(2023, 1, 30, 23, 30, tzinfo=timezone.utc)
    assert add_months(korea_anchor, anchor_day=31) == datetime(2023, 2, 27, 23, 30, tzinfo=timezone.utc)
    with factory.begin() as db:
        subscribe(db, tenant, now, payment_settings, provider)
    assert process_due_invoices(factory, provider=provider, settings=payment_settings, now=february) == 1
    assert process_due_invoices(factory, provider=provider, settings=payment_settings, now=february) == 0
    with factory.begin() as db:
        subscription = db.scalar(select(Subscription))
        assert subscription.anchor_day == 31
        assert aware(subscription.current_period_end) == march
        assert db.scalar(select(func.count()).select_from(Invoice)) == 2
        summary = wallet_summary(db, tenant, now=february)
        assert summary["balance"] == 500  # previous month and trial both expired
        assert summary["expired"] == 530


def test_monthly_expiry_preserves_purchase500(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        subscribe(db, tenant, now, payment_settings, provider)
        topup = create_order(db, tenant, "topup", "topup", credits=500, settings=payment_settings, now=now)
        confirm_order(db, tenant, topup.order_id, "mock", topup.amount, provider=provider, settings=payment_settings, now=now)
        assert wallet_summary(db, tenant, now=now)["balance"] == 1030
    due = add_months(now, anchor_day=31)
    process_due_invoices(factory, provider=provider, settings=payment_settings, now=due)
    with factory.begin() as db:
        summary = wallet_summary(db, tenant, now=due)
        assert summary["balance"] == 1000
        purchase = db.scalar(select(CreditBucket).where(CreditBucket.kind == "purchase"))
        assert purchase.available == 500 and aware(purchase.expires_at) == add_months(now, 12)


def test_prorated_upgrade_and_scheduled_downgrade(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    middle = now + timedelta(days=10, seconds=23)
    with factory.begin() as db:
        subscribe(db, tenant, now, payment_settings, provider)
        upgrade = change_plan(db, tenant, "pro", "up", settings=payment_settings, now=middle)
        expected = prorate(plan("starter"), plan("pro"), now, add_months(now), middle)
        assert (upgrade["order"]["amount"], upgrade["order"]["credits"]) == expected
        order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_id == upgrade["order"]["order_id"]))
        assert db.scalar(select(Subscription)).plan_id == "starter"
        confirm_order(db, tenant, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=middle)
        assert db.scalar(select(Subscription)).plan_id == "pro"
        assert entitlements(db, tenant, middle)["seats"] == 3
        downgrade = change_plan(db, tenant, "starter", "down", settings=payment_settings, now=middle)
        assert downgrade["change"] == "scheduled"
        assert db.scalar(select(Subscription)).plan_id == "pro"
    process_due_invoices(factory, provider=provider, settings=payment_settings, now=add_months(now))
    with factory.begin() as db:
        assert db.scalar(select(Subscription)).plan_id == "starter"
        assert entitlements(db, tenant, add_months(now))["seats"] == 1


def test_cancel_no_new_invoice_owner_retains_purchase_and_team_loses_access(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        subscribe(db, tenant, now, payment_settings, provider, "pro")
        topup = create_order(db, tenant, "topup", "topup", credits=500, settings=payment_settings, now=now)
        confirm_order(db, tenant, topup.order_id, "mock", topup.amount, provider=provider, settings=payment_settings, now=now)
        cancel_renewal(db, tenant, now=now + timedelta(days=1))
        assert entitlements(db, tenant, now + timedelta(days=1))["seats"] == 3
    due = add_months(now)
    assert process_due_invoices(factory, provider=provider, settings=payment_settings, now=due) == 0
    with factory.begin() as db:
        rights = entitlements(db, tenant, due)
        assert rights["seats"] == 1 and rights["production_export"] is True
        assert rights["retention_until"] == add_months(now, 12).isoformat()
        assert db.scalar(select(func.count()).select_from(Invoice)) == 1
        reservation = reserve(db, tenant, "after-cancel", "image.generate.standard", 1, now=due)
        capture_unit(db, tenant, reservation.id, 0, now=due)
        assert wallet_summary(db, tenant, now=due)["balance"] == 490
        owner = User(tenant_id=tenant, name="owner", email="owner@example.com", password_hash="unused", role="owner")
        editor = User(tenant_id=tenant, name="editor", email="editor@example.com", password_hash="unused", role="editor")
        enforce_membership_entitlement(db, owner, due)
        with pytest.raises(APIError) as denied:
            enforce_membership_entitlement(db, editor, due)
        assert denied.value.code == "TEAM_ACCESS_EXPIRED"
        with pytest.raises(APIError):
            create_order(db, tenant, "topup", "new-after-end", credits=500, settings=payment_settings, now=due)


def test_failed_renewal_no_grant_purchase_preserved_bounded_same_invoice_retries(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        subscribe(db, tenant, now, payment_settings, provider)
        topup = create_order(db, tenant, "topup", "topup", credits=500, settings=payment_settings, now=now)
        confirm_order(db, tenant, topup.order_id, "mock", topup.amount, provider=provider, settings=payment_settings, now=now)
    class FailingProvider(MockProvider):
        def charge(self, *args):
            self.calls.append(("charge", args[2]))
            raise ProviderError("CARD_DECLINED")
    failure = FailingProvider()
    due = add_months(now)
    assert process_due_invoices(factory, provider=failure, settings=payment_settings, now=due) == 1
    assert process_due_invoices(factory, provider=failure, settings=payment_settings, now=due + timedelta(hours=23)) == 0
    assert process_due_invoices(factory, provider=failure, settings=payment_settings, now=due + timedelta(hours=24)) == 1
    assert process_due_invoices(factory, provider=failure, settings=payment_settings, now=due + timedelta(hours=72)) == 1
    assert process_due_invoices(factory, provider=failure, settings=payment_settings, now=due + timedelta(hours=100)) == 0
    assert len({call[1] for call in failure.calls if call[0] == "charge"}) == 1
    with factory.begin() as db:
        assert wallet_summary(db, tenant, now=due)["balance"] == 500
        assert db.scalar(select(func.count()).select_from(Payment)) == 2
        assert db.scalar(select(func.count()).select_from(Invoice)) == 2


def test_full_unused_refund_records_reverse_entry_and_preserves_original_payment(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        subscribe(db, tenant, now, payment_settings, provider)
        topup = create_order(db, tenant, "topup", "refund", credits=500, settings=payment_settings, now=now)
        confirm_order(db, tenant, topup.order_id, "mock", topup.amount, provider=provider, settings=payment_settings, now=now)
        refunded = refund_order(db, tenant, topup.order_id, "미사용 추가 충전 환불", provider=provider, settings=payment_settings, now=now)
        assert refunded.status == "refunded"
        refund_order(db, tenant, topup.order_id, "반복 요청", provider=provider, settings=payment_settings, now=now)
        assert db.scalar(select(func.count()).select_from(PaymentEvent)) == 1
        assert db.scalar(select(Payment).where(Payment.order_id == topup.id)).status == "DONE"
        assert wallet_summary(db, tenant, now=now)["balance"] == 530
        assert db.scalar(select(LedgerEntry).where(LedgerEntry.event == "ADJUSTMENT")).amount == -500


def test_hosted_mock_and_unapproved_live_fail_closed(payment_settings):
    payment_settings.environment = "staging"
    assert payment_settings.capabilities()["mock_available"] is False
    assert payment_settings.capabilities()["checkout_available"] is False
    with pytest.raises(APIError):
        MockProvider("production")
    live = BillingSettings(provider="toss_live", environment="production", encryption_key=Fernet.generate_key().decode(), secret_key="live_sk_example", client_key="live_ck_example", merchant_id="merchant", policy_approved=True, live_enabled=True)
    assert live.capabilities()["live_enabled"] is False  # policy seed remains unapproved


def test_toss_adapter_uses_official_endpoints_basic_auth_and_idempotency():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"orderId": "order_123", "paymentKey": "payment-1", "status": "DONE", "totalAmount": 53900, "currency": "KRW", "mId": "merchant"})
    settings = BillingSettings(provider="toss_test", environment="test", secret_key="test_sk_example", client_key="test_ck_example", merchant_id="merchant", encryption_key=Fernet.generate_key().decode())
    provider = TossProvider(settings, transport=httpx.MockTransport(handler))
    provider.confirm("payment-1", "order_123", 53900)
    provider.query("order_123")
    provider.charge("billing-key", "customer_123", "order_123", 53900, "Pro")
    assert [request.url.path for request in calls] == ["/v1/payments/confirm", "/v1/payments/orders/order_123", "/v1/billing/billing-key"]
    assert all(request.headers["Authorization"].startswith("Basic ") for request in calls)
    assert len(calls[0].headers["Idempotency-Key"]) == 64
    assert b'"customerKey":"customer_123"' in calls[2].content


def test_parallel_upgrade_quotes_cannot_charge_after_plan_changes(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        subscribe(db, tenant, now, payment_settings, provider)
        first = change_plan(db, tenant, "pro", "to-pro", settings=payment_settings, now=now)["order"]
        second = change_plan(db, tenant, "partner", "to-partner", settings=payment_settings, now=now)["order"]
        confirm_order(db, tenant, first["order_id"], "mock", first["amount"], provider=provider, settings=payment_settings, now=now)
        before = len(provider.calls)
        with pytest.raises(APIError) as stale:
            confirm_order(db, tenant, second["order_id"], "mock", second["amount"], provider=provider, settings=payment_settings, now=now)
        assert stale.value.code == "UPGRADE_QUOTE_CHANGED"
        assert len(provider.calls) == before
        assert db.scalar(select(Subscription)).plan_id == "pro"


def test_recurring_scheduler_concurrency_pays_invoice_once(billing_db, payment_settings, provider):
    from concurrent.futures import ThreadPoolExecutor
    factory, tenant, now = billing_db
    with factory.begin() as db:
        subscribe(db, tenant, now, payment_settings, provider)
    due = add_months(now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(process_due_invoices, factory, provider=provider, settings=payment_settings, now=due) for _ in range(2)]
        assert sum(future.result(timeout=10) for future in futures) == 1
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(Invoice)) == 2
        assert db.scalar(select(func.count()).select_from(Payment)) == 2
        assert wallet_summary(db, tenant, now=due)["balance"] == 500


def test_delayed_payment_resolution_grants_once_without_new_charge(billing_db, payment_settings):
    class DeferredProvider(MockProvider):
        visible = False
        def query(self, order_id):
            return super().query(order_id) if self.visible else None
        def confirm(self, *args):
            super().confirm(*args)
            raise ProviderError("TIMEOUT", retryable=True, uncertain=True)
    provider = DeferredProvider()
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "unknown", plan_id="starter", settings=payment_settings, now=now)
        confirm_order(db, tenant, order.order_id, "mock", order.amount, provider=provider, settings=payment_settings, now=now)
        assert order.status == "reconciliation_required"
        assert wallet_summary(db, tenant, now=now)["balance"] == 30
    provider.visible = True
    assert reconcile_pending_orders(factory, provider=provider, settings=payment_settings, now=now + timedelta(seconds=30)) == 1
    assert reconcile_pending_orders(factory, provider=provider, settings=payment_settings, now=now + timedelta(seconds=31)) == 0
    assert len([call for call in provider.calls if call[0] == "confirm"]) == 1
    with factory.begin() as db:
        assert wallet_summary(db, tenant, now=now + timedelta(seconds=30))["balance"] == 530


def test_spent_subscription_cannot_be_silently_refunded(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = subscribe(db, tenant, now, payment_settings, provider)
        job = reserve(db, tenant, "spent", "image.generate.standard", 4, now=now)
        for index in range(4):
            capture_unit(db, tenant, job.id, index, now=now)
        with pytest.raises(APIError) as denied:
            refund_order(db, tenant, order.order_id, "사용 후 환불 신청", provider=provider, settings=payment_settings, now=now)
        assert denied.value.code == "REFUND_REVIEW_REQUIRED"
        assert order.status == "paid"
        assert wallet_summary(db, tenant, now=now)["balance"] == 490


def test_renewal_order_survives_worker_death_after_provider_approval(billing_db,payment_settings,provider):
    factory,tenant,now=billing_db
    with factory.begin() as db:subscribe(db,tenant,now,payment_settings,provider)
    class ProcessDied(BaseException):pass
    class InterruptedProvider(MockProvider):
        def charge(self,*args):
            super().charge(*args)
            raise ProcessDied()
    interrupted=InterruptedProvider();due=add_months(now)
    with pytest.raises(ProcessDied):process_due_invoices(factory,provider=interrupted,settings=payment_settings,now=due)
    with factory.begin() as db:
        order=db.scalar(select(PaymentOrder).where(PaymentOrder.kind=="renewal"))
        assert order is not None and order.order_id in interrupted.payments
        assert db.scalar(select(func.count()).select_from(Payment))==1
        durable_id=order.order_id
    assert process_due_invoices(factory,provider=interrupted,settings=payment_settings,now=due)==1
    with factory.begin() as db:
        assert db.scalar(select(PaymentOrder).where(PaymentOrder.kind=="renewal")).order_id==durable_id
        assert db.scalar(select(func.count()).select_from(Payment))==2
        assert wallet_summary(db,tenant,now=due)["balance"]==500
    assert len([call for call in interrupted.calls if call[0]=="confirm"])==1


def test_exhausted_renewal_does_not_starve_other_due_tenants(billing_db,payment_settings,provider):
    from services.api.models import Tenant
    factory,first,now=billing_db
    with factory.begin() as db:
        subscribe(db,first,now,payment_settings,provider)
        second=Tenant(name="Later due tenant",created_at=now);db.add(second);db.flush();second_id=second.id
        subscribe(db,second_id,now+timedelta(seconds=1),payment_settings,provider)
    class DeclinedProvider(MockProvider):
        def charge(self,*args):raise ProviderError("DECLINED")
    due=add_months(now)
    assert process_due_invoices(factory,provider=DeclinedProvider(),settings=payment_settings,now=due,limit=1)==1
    with factory.begin() as db:
        invoice=db.scalar(select(Invoice).where(Invoice.tenant_id==first,Invoice.period_start==due))
        invoice.attempts=3
    assert process_due_invoices(factory,provider=provider,settings=payment_settings,now=due+timedelta(seconds=2),limit=1)==1
    with factory.begin() as db:
        assert db.scalar(select(Subscription).where(Subscription.tenant_id==second_id)).status=="active"
        assert wallet_summary(db,second_id,now=due+timedelta(seconds=2))["balance"]==500


def test_live_and_test_providers_cannot_cross_environment_gates(monkeypatch):
    monkeypatch.setattr("services.api.billing.payments.pricing",lambda:{"live_billing_enabled":True})
    options=dict(provider="toss_live",encryption_key=Fernet.generate_key().decode(),secret_key="live_sk_example",client_key="live_ck_example",merchant_id="merchant",policy_approved=True,live_enabled=True)
    for environment in ("test","development","staging"):
        settings=BillingSettings(environment=environment,**options)
        assert settings.capabilities()["checkout_available"] is False
    assert BillingSettings(environment="production",**options).capabilities()["live_enabled"] is True
    test=BillingSettings(environment="production",**{**options,"provider":"toss_test","secret_key":"test_sk_example","client_key":"test_ck_example"})
    assert test.capabilities()["checkout_available"] is False


def test_first_subscription_crash_recovers_order_before_reusing_one_time_auth(billing_db,payment_settings):
    factory,tenant,now=billing_db
    class ProcessDied(BaseException):pass
    class InterruptedProvider(MockProvider):
        issued=False
        def issue_billing(self,*args):
            assert not self.issued,"one-time billing auth cannot be reused"
            self.issued=True;return super().issue_billing(*args)
        def charge(self,*args):
            super().charge(*args);raise ProcessDied()
    provider=InterruptedProvider()
    with factory.begin() as db:
        order=create_order(db,tenant,"subscription","durable-initial",plan_id="starter",settings=payment_settings,now=now)
        order_id,amount=order.order_id,order.amount
        account=billing_account(db,tenant,payment_settings)
        customer=payment_settings.decrypt(account.customer_key_encrypted)
    with pytest.raises(ProcessDied):
        with factory.begin() as db:bind_and_charge(db,tenant,order_id,"single-use-auth",customer,provider=provider,settings=payment_settings,now=now)
    with factory.begin() as db:
        recovered=bind_and_charge(db,tenant,order_id,"single-use-auth",customer,provider=provider,settings=payment_settings,now=now)
        assert recovered.status=="paid" and wallet_summary(db,tenant,now=now)["balance"]==530
        assert db.scalar(select(Subscription)).cancel_at_period_end is True
    assert len([call for call in provider.calls if call[0]=="confirm"])==1
