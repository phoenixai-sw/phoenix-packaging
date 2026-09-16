"""Reference-project port: all HTTP is MockTransport, never a real PG call."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json

from cryptography.fernet import Fernet
import httpx
import pytest
from sqlalchemy import func, select

from services.api.errors import APIError
from services.api.feature_models import AuditEvent
from services.api.models import Tenant
from services.api.billing.models import CreditBucket, Invoice, LedgerEntry, Payment, PaymentEvent, PaymentOrder, Subscription, WebhookEvent
from services.api.billing.payments import BillingSettings, MockProvider, ProviderError, TossProvider, confirm_order, create_order, reconcile_webhook, refund_order, sync_order
from services.api.billing.service import capture_unit, reserve, wallet_summary
from services.api.billing.sync import payment_snapshot, sync_verified_payment


def paid(db, tenant, now, settings, provider):
    order = create_order(db, tenant, "subscription", "first", plan_id="starter", settings=settings, now=now)
    confirm_order(db, tenant, order.order_id, "mock", order.amount, settings=settings, provider=provider, now=now)
    return order


def canceled(result, amounts, status=None):
    value = deepcopy(result)
    value["balanceAmount"] = value["totalAmount"] - sum(amounts)
    value["status"] = status or ("PARTIAL_CANCELED" if value["balanceAmount"] else "CANCELED")
    value["cancels"] = [{"cancelAmount": amount, "transactionKey": f"transaction-{index}", "cancelReason": "고객 요청", "cancelStatus": "DONE", "canceledAt": value["approvedAt"]} for index, amount in enumerate(amounts)]
    return value


def event(order, key=None):
    return {"eventType": "PAYMENT_STATUS_CHANGED", "data": {"orderId": order.order_id, **({"paymentKey": key} if key else {})}}


def test_partial_then_full_cancellation_records_each_transaction_once_and_orders_snapshot(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = paid(db, tenant, now, payment_settings, provider)
        original = provider.query(order.order_id)
        partial = canceled(original, [1000])
        for _ in range(2):
            sync_verified_payment(db, order, partial, settings=payment_settings, source="test", now=now)
        assert order.status == "reconciliation_required"
        assert wallet_summary(db, tenant, now=now)["balance"] == 530
        assert payment_snapshot(db, order)["cancelled_amount"] == 1000
        full = canceled(original, [1000, order.amount - 1000])
        for _ in range(2):
            sync_verified_payment(db, order, full, settings=payment_settings, source="test", now=now)
        assert order.status == "refunded"
        assert db.get(Invoice, order.invoice_id).status == "refunded"
        assert db.scalar(select(Subscription)).status == "canceled"
        assert wallet_summary(db, tenant, now=now)["balance"] == 30
        events = list(db.scalars(select(PaymentEvent)))
        assert len(events) == 2 and sum(row.amount for row in events) == order.amount
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event == "ADJUSTMENT")) == 1
        assert db.scalar(select(Payment)).status == "DONE"
        snapshot = payment_snapshot(db, order)
        assert snapshot["provider_status"] == "CANCELED" and snapshot["balance_amount"] == 0
        assert all("transaction_key" not in item for item in snapshot["cancels"])


def test_external_full_cancel_after_spend_preserves_ledger_and_enters_review(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = paid(db, tenant, now, payment_settings, provider)
        reservation = reserve(db, tenant, "spent", "image.generate.standard", 4, now=now)
        for index in range(4): capture_unit(db, tenant, reservation.id, index, now=now)
        value = canceled(provider.query(order.order_id), [order.amount])
        sync_verified_payment(db, order, value, settings=payment_settings, source="webhook", now=now)
        assert order.status == "reconciliation_required"
        assert db.get(Invoice, order.invoice_id).status == "refunded"
        assert db.scalar(select(Subscription)).cancel_at_period_end is True
        assert wallet_summary(db, tenant, now=now)["balance"] == 490
        assert db.scalar(select(func.count()).select_from(PaymentEvent)) == 1
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event == "ADJUSTMENT")) == 0


def test_cancel_before_callback_never_grants_then_stale_done_cannot_reactivate(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "unobserved", plan_id="starter", settings=payment_settings, now=now)
        original = provider.confirm("mock", order.order_id, order.amount)
        sync_verified_payment(db, order, canceled(original, [order.amount]), settings=payment_settings, source="webhook", now=now)
        assert order.status == "refunded" and wallet_summary(db, tenant, now=now)["balance"] == 30
        with pytest.raises(APIError, match="이전 상태"):
            sync_verified_payment(db, order, original, settings=payment_settings, source="stale", now=now)
        assert db.scalar(select(func.count()).select_from(CreditBucket).where(CreditBucket.kind == "monthly")) == 0


@pytest.mark.parametrize("mutation,code", [
    ({"totalAmount": 1}, "PAYMENT_MISMATCH"),
    ({"totalAmount": 31900.0}, "PAYMENT_MISMATCH"),
    ({"currency": "USD"}, "PAYMENT_MISMATCH"),
    ({"orderId": "another-order"}, "PAYMENT_MISMATCH"),
    ({"paymentKey": "another-payment"}, "PAYMENT_KEY_MISMATCH"),
    ({"balanceAmount": -1}, "PAYMENT_STATE_INVALID"),
    ({"status": "CANCELED", "balanceAmount": 0, "cancels": []}, "PAYMENT_CANCELLATION_MISMATCH"),
])
def test_provider_identity_and_cancel_schema_never_silently_diverge(billing_db, payment_settings, provider, mutation, code):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = paid(db, tenant, now, payment_settings, provider)
        value = {**provider.query(order.order_id), **mutation}
        with pytest.raises(APIError) as caught:
            sync_verified_payment(db, order, value, settings=payment_settings, source="test", now=now)
        assert caught.value.code == code
        assert order.status == "paid" and wallet_summary(db, tenant, now=now)["balance"] == 530


def test_verified_snapshot_excludes_card_bank_secret_and_untrusted_receipt_url(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "summary", plan_id="starter", settings=payment_settings, now=now)
        value = provider.confirm("mock", order.order_id, order.amount)
        value.update({"card": {"number": "SECRET_CARD_NUMBER"}, "virtualAccount": {"accountNumber": "SECRET_BANK_NUMBER"}, "secret": "PRIVATE_SECRET", "receipt": {"url": "javascript:alert(1)"}})
        sync_verified_payment(db, order, value, settings=payment_settings, source="test", now=now)
        snapshot = payment_snapshot(db, order)
        assert snapshot["receipt_url"] is None
        stored = json.dumps(list(db.scalars(select(AuditEvent.details))))
        assert "SECRET" not in stored and "paymentKey" not in stored and "mock_" not in stored
        value["receipt"] = {"url": "https://dashboard.tosspayments.com/receipt/example"}
        sync_verified_payment(db, order, value, settings=payment_settings, source="test", now=now)
        assert payment_snapshot(db, order)["receipt_url"] == value["receipt"]["url"]


def test_webhook_failed_delivery_can_retry_same_id_and_replay_does_not_requery(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "delivery", plan_id="starter", settings=payment_settings, now=now)
        order_id, payload, amount = order.id, event(order), order.amount
    with pytest.raises(APIError):
        with factory.begin() as db:
            reconcile_webhook(db, payload, provider=provider, settings=payment_settings, transmission_id="same-id", now=now)
    provider.confirm("mock", payload["data"]["orderId"], amount)
    # Simulate database rollback after PG verification: do not dedupe failure.
    with pytest.raises(RuntimeError):
        with factory.begin() as db:
            reconcile_webhook(db, payload, provider=provider, settings=payment_settings, transmission_id="same-id", now=now)
            raise RuntimeError("transaction interrupted")
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 0
        reconcile_webhook(db, payload, provider=provider, settings=payment_settings, transmission_id="same-id", now=now)
        calls = len(provider.calls)
        reconcile_webhook(db, payload, provider=provider, settings=payment_settings, transmission_id="same-id", now=now)
        assert len(provider.calls) == calls and db.get(PaymentOrder, order_id).status == "paid"
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert wallet_summary(db, tenant, now=now)["balance"] == 530


def test_unknown_and_unrelated_webhook_never_queries_or_creates_order(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        assert reconcile_webhook(db, {"eventType": "PAYOUT_STATUS_CHANGED", "data": {}}, provider=provider, settings=payment_settings, now=now) is None
        assert reconcile_webhook(db, {"eventType": "PAYMENT_STATUS_CHANGED", "data": {"orderId": "unknown", "paymentKey": "unknown"}}, provider=provider, settings=payment_settings, now=now) is None
        assert not provider.calls and db.scalar(select(func.count()).select_from(PaymentOrder)) == 0


def test_webhook_looks_up_payment_key_but_requires_its_server_owned_order(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = paid(db, tenant, now, payment_settings, provider)
        key = provider.query(order.order_id)["paymentKey"]
        provider.payments[order.order_id] = canceled(provider.payments[order.order_id], [order.amount])
        # CANCEL_STATUS_CHANGED can locate a known payment without orderId.
        payload = {"eventType": "CANCEL_STATUS_CHANGED", "data": {"paymentKey": key, "status": "DONE"}}
        assert reconcile_webhook(db, payload, provider=provider, settings=payment_settings, now=now).status == "refunded"
        # A valid key for a different order must not apply that state here.
        other = create_order(db, tenant, "subscription", "second", plan_id="starter", settings=payment_settings, now=now + timedelta(seconds=1))
        with pytest.raises(APIError) as wrong:
            reconcile_webhook(db, event(other, key), provider=provider, settings=payment_settings, now=now)
        assert wrong.value.code == "PAYMENT_MISMATCH"


def test_concurrent_webhook_and_manual_sync_grant_and_record_once(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "concurrent", plan_id="starter", settings=payment_settings, now=now)
        order_id, payload = order.order_id, event(order)
        provider.confirm("mock", order.order_id, order.amount)
    def run(webhook):
        with factory.begin() as db:
            if webhook: reconcile_webhook(db, payload, provider=provider, settings=payment_settings, transmission_id="same", now=now)
            else: sync_order(db, tenant, order_id, provider=provider, settings=payment_settings, now=now)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in [pool.submit(run, value) for value in (True, True, False)]: future.result(timeout=15)
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "payment_state_synced")) == 1
        assert wallet_summary(db, tenant, now=now)["balance"] == 530


def test_refund_after_provider_success_and_process_death_queries_without_duplicate_cancel(billing_db, payment_settings):
    class ProcessDeath(BaseException): pass
    class Interrupted(MockProvider):
        canceled = 0
        def cancel(self, *args):
            self.canceled += 1
            super().cancel(*args)
            raise ProcessDeath()
    provider = Interrupted()
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order_id = paid(db, tenant, now, payment_settings, provider).order_id
    with pytest.raises(ProcessDeath):
        with factory.begin() as db:
            refund_order(db, tenant, order_id, "미사용 환불", provider=provider, settings=payment_settings, now=now)
    with factory.begin() as db:
        result = refund_order(db, tenant, order_id, "변경된 재시도 사유", provider=provider, settings=payment_settings, now=now)
        assert result.status == "refunded" and provider.canceled == 1
        assert db.scalar(select(func.count()).select_from(PaymentEvent)) == 1
        assert wallet_summary(db, tenant, now=now)["balance"] == 30


def test_owner_sync_cannot_access_another_tenant_and_never_approves(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "owner", plan_id="starter", settings=payment_settings, now=now)
        second = Tenant(name="different"); db.add(second); db.flush()
        with pytest.raises(APIError) as denied:
            sync_order(db, second.id, order.order_id, provider=provider, settings=payment_settings, now=now)
        assert denied.value.status == 404 and provider.calls == []
        with pytest.raises(APIError) as absent:
            sync_order(db, tenant, order.order_id, provider=provider, settings=payment_settings, now=now)
        assert absent.value.code == "PAYMENT_UNVERIFIED"
        assert all(call[0] == "query" for call in provider.calls)


@pytest.mark.parametrize("visible", [True, False])
def test_toss_already_processed_error_requires_authoritative_requery(billing_db, visible):
    factory, tenant, now = billing_db
    settings = BillingSettings(provider="toss_test", environment="test", secret_key="test_sk_placeholder", client_key="test_ck_placeholder", merchant_id="merchant", encryption_key=Fernet.generate_key().decode())
    queries, confirms = 0, 0
    with factory.begin() as db:
        order = create_order(db, tenant, "subscription", "already", plan_id="starter", settings=settings, now=now)
        def handler(request):
            nonlocal queries, confirms
            if request.method == "POST":
                confirms += 1
                return httpx.Response(400, json={"code": "ALREADY_PROCESSED_PAYMENT", "message": "Untrusted provider message"})
            queries += 1
            if queries == 1 or not visible: return httpx.Response(404)
            return httpx.Response(200, json={"orderId": order.order_id, "paymentKey": "verified-key", "totalAmount": order.amount, "balanceAmount": order.amount, "currency": "KRW", "status": "DONE", "mId": "merchant", "approvedAt": now.isoformat()})
        provider = TossProvider(settings, transport=httpx.MockTransport(handler))
        result = confirm_order(db, tenant, order.order_id, "verified-key", order.amount, provider=provider, settings=settings, now=now)
        assert queries == 2 and confirms == 1
        assert result.status == ("paid" if visible else "reconciliation_required")
        assert wallet_summary(db, tenant, now=now)["balance"] == (530 if visible else 30)


def test_toss_lookup_cancel_encoding_secret_auth_stable_idempotency_and_balance_guard():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "CANCELED"})
    settings = BillingSettings(provider="toss_test", environment="test", secret_key="test_sk_placeholder", client_key="test_ck_placeholder", merchant_id="merchant", encryption_key=Fernet.generate_key().decode())
    provider = TossProvider(settings, transport=httpx.MockTransport(handler))
    provider.query_payment("key?not-a-query")
    provider.cancel("payment-key", 10000, "사유" * 150, "order-refund-1")
    provider.cancel("payment-key", 10000, "사유" * 150, "order-refund-1")
    assert not calls[0].url.query and b"%3F" in calls[0].url.raw_path
    body = json.loads(calls[1].content)
    assert body["cancelAmount"] == body["refundableAmount"] == 10000
    assert len(body["cancelReason"]) == 200
    assert calls[1].headers["Idempotency-Key"] == calls[2].headers["Idempotency-Key"]
    assert "Authorization" in calls[1].headers and b"test_sk_" not in calls[1].content


def test_new_subscription_is_not_overwritten_by_older_failed_order_approval(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        old = create_order(db, tenant, "subscription", "old", plan_id="starter", settings=payment_settings, now=now)
        value = provider.confirm("mock", old.order_id, old.amount)
        sync_verified_payment(db, old, {**value, "status": "ABORTED"}, settings=payment_settings, source="webhook", now=now)
        assert old.status == "failed"
        current = create_order(db, tenant, "subscription", "new", plan_id="pro", settings=payment_settings, now=now + timedelta(minutes=1))
        confirm_order(db, tenant, current.order_id, "mock", current.amount, settings=payment_settings, provider=provider, now=now + timedelta(minutes=1))
        before = wallet_summary(db, tenant, now=now)["balance"]
        sync_verified_payment(db, old, value, settings=payment_settings, source="late_webhook", now=now + timedelta(minutes=2))
        assert old.status == "reconciliation_required"
        assert db.scalar(select(Subscription)).plan_id == "pro"
        assert wallet_summary(db, tenant, now=now)["balance"] == before


def test_uncertain_cancel_is_durable_and_worker_requery_never_cancels_again(billing_db, payment_settings):
    from services.api.billing.payments import reconcile_pending_orders
    class Lost(MockProvider):
        hidden = False
        cancels = 0
        def cancel(self, *args):
            self.cancels += 1
            super().cancel(*args)
            self.hidden = True
            raise ProviderError("NETWORK", retryable=True, uncertain=True)
        def query(self, *args):
            if self.hidden: raise ProviderError("QUERY_UNAVAILABLE", retryable=True)
            return super().query(*args)
    provider = Lost()
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = paid(db, tenant, now, payment_settings, provider)
        result = refund_order(db, tenant, order.order_id, "미사용 환불", provider=provider, settings=payment_settings, now=now)
        assert result.status == "reconciliation_required"
        assert wallet_summary(db, tenant, now=now)["balance"] == 530
    provider.hidden = False
    assert reconcile_pending_orders(factory, provider=provider, settings=payment_settings, now=now) == 1
    with factory.begin() as db:
        assert db.scalar(select(PaymentOrder)).status == "refunded"
        assert wallet_summary(db, tenant, now=now)["balance"] == 30 and provider.cancels == 1


def test_confirmed_partial_review_cannot_starve_later_uncertain_approval(billing_db, payment_settings, provider):
    from services.api.billing.payments import reconcile_pending_orders
    factory, tenant, now = billing_db
    with factory.begin() as db:
        old = paid(db, tenant, now, payment_settings, provider)
        sync_verified_payment(db, old, canceled(provider.query(old.order_id), [1000]), settings=payment_settings, source="webhook", now=now)
        other = Tenant(name="Other"); db.add(other); db.flush()
        current = create_order(db, other.id, "subscription", "current", plan_id="starter", settings=payment_settings, now=now + timedelta(minutes=1))
        provider.confirm("mock", current.order_id, current.amount)
        current.status = "reconciliation_required"
        current_id = current.id
    assert reconcile_pending_orders(factory, provider=provider, settings=payment_settings, now=now + timedelta(minutes=2), limit=1) == 1
    with factory.begin() as db:
        assert db.get(PaymentOrder, current_id).status == "paid"


def test_provider_mid_and_payment_environment_must_both_match(billing_db, payment_settings, provider):
    from services.api.billing.models import BillingAccount
    factory, tenant, now = billing_db
    with factory.begin() as db:
        order = paid(db, tenant, now, payment_settings, provider)
        original = provider.query(order.order_id)
        toss = BillingSettings(provider="toss_test", environment="test", secret_key="test_sk_placeholder", client_key="test_ck_placeholder", merchant_id="merchant", encryption_key=payment_settings.encryption_key)
        with pytest.raises(APIError) as environment:
            sync_verified_payment(db, order, {**original, "mId": "merchant"}, settings=toss, source="test", now=now)
        assert environment.value.code == "PAYMENT_ENVIRONMENT_CHANGED"
        # MID is validated even when the payment environment is consistent.
        db.get(BillingAccount, tenant).provider = "toss_test"
        with pytest.raises(APIError) as merchant:
            sync_verified_payment(db, order, original, settings=toss, source="test", now=now)
        assert merchant.value.code == "PAYMENT_ACCOUNT_MISMATCH"


def upgrade(db, tenant, now, settings, provider, target, key):
    from services.api.billing.payments import change_plan
    values = change_plan(db, tenant, target, key, settings=settings, now=now)["order"]
    return confirm_order(db, tenant, values["order_id"], "mock", values["amount"], provider=provider, settings=settings, now=now)


def test_unused_upgrade_refund_restores_original_plan_seats_and_preserves_base_period(billing_db, payment_settings, provider):
    from services.api.billing.payments import entitlements
    factory, tenant, now = billing_db
    with factory.begin() as db:
        paid(db, tenant, now, payment_settings, provider)
        subscription = db.scalar(select(Subscription))
        original_period = (subscription.current_period_start, subscription.current_period_end, subscription.paid_until, subscription.cancel_at_period_end)
        order = upgrade(db, tenant, now, payment_settings, provider, "pro", "upgrade-pro")
        assert entitlements(db, tenant, now)["seats"] == 3
        assert refund_order(db, tenant, order.order_id, "상향 전액 환불", provider=provider, settings=payment_settings, now=now).status == "refunded"
        rights = entitlements(db, tenant, now)
        assert subscription.plan_id == "starter" and rights["seats"] == 1 and rights["team_access"] is False
        assert rights["active_subscription"] is True
        assert (subscription.current_period_start, subscription.current_period_end, subscription.paid_until, subscription.cancel_at_period_end) == original_period
        assert wallet_summary(db, tenant, now=now)["balance"] == 530
        assert db.scalar(select(Payment).where(Payment.order_id == order.id)).status == "DONE"


def test_older_upgrade_refund_is_blocked_before_provider_and_reverse_chain_restores_each_source(billing_db, payment_settings, provider):
    from services.api.billing.payments import entitlements
    factory, tenant, now = billing_db
    with factory.begin() as db:
        paid(db, tenant, now, payment_settings, provider)
        first = upgrade(db, tenant, now, payment_settings, provider, "pro", "first-upgrade")
        second = upgrade(db, tenant, now, payment_settings, provider, "partner", "second-upgrade")
        calls = len(provider.calls)
        with pytest.raises(APIError) as denied:
            refund_order(db, tenant, first.order_id, "이전 상향 환불", provider=provider, settings=payment_settings, now=now)
        assert denied.value.code == "REFUND_REVIEW_REQUIRED" and len(provider.calls) == calls
        assert db.scalar(select(Subscription)).plan_id == "partner"
        refund_order(db, tenant, second.order_id, "최근 상향 환불", provider=provider, settings=payment_settings, now=now)
        assert db.scalar(select(Subscription)).plan_id == "pro" and entitlements(db, tenant, now)["seats"] == 3
        refund_order(db, tenant, first.order_id, "이전 상향 환불", provider=provider, settings=payment_settings, now=now)
        assert db.scalar(select(Subscription)).plan_id == "starter" and entitlements(db, tenant, now)["seats"] == 1


def test_external_upgrade_cancel_restores_entitlements_but_old_replay_cannot_undo_new_upgrade(billing_db, payment_settings, provider):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        paid(db, tenant, now, payment_settings, provider)
        old = upgrade(db, tenant, now, payment_settings, provider, "pro", "first-upgrade")
        value = canceled(provider.query(old.order_id), [old.amount])
        sync_verified_payment(db, old, value, settings=payment_settings, source="webhook", now=now)
        assert old.status == "refunded" and db.scalar(select(Subscription)).plan_id == "starter"
        current = upgrade(db, tenant, now, payment_settings, provider, "pro", "new-upgrade")
        value["receipt"] = {"url": "https://dashboard.tosspayments.com/receipt/changed"}
        sync_verified_payment(db, old, value, settings=payment_settings, source="replay", now=now)
        assert current.status == "paid" and db.scalar(select(Subscription)).plan_id == "pro"
        assert db.scalar(select(func.count()).select_from(PaymentEvent)) == 1


@pytest.mark.parametrize("broken", ["source", "period", "later_chain"])
def test_ambiguous_upgrade_cancellation_is_reviewed_without_claiming_refunded(billing_db, payment_settings, provider, broken):
    factory, tenant, now = billing_db
    with factory.begin() as db:
        paid(db, tenant, now, payment_settings, provider)
        order = upgrade(db, tenant, now, payment_settings, provider, "pro", "first-upgrade")
        subscription = db.scalar(select(Subscription))
        if broken == "source": order.source_plan_id = "partner"
        elif broken == "period": subscription.current_period_end += timedelta(days=1)
        else: upgrade(db, tenant, now, payment_settings, provider, "partner", "later-upgrade")
        calls = len(provider.calls)
        with pytest.raises(APIError) as denied:
            refund_order(db, tenant, order.order_id, "모호한 상향 환불", provider=provider, settings=payment_settings, now=now)
        assert denied.value.code == "REFUND_REVIEW_REQUIRED" and len(provider.calls) == calls
        expected_plan = subscription.plan_id
        sync_verified_payment(db, order, canceled(provider.query(order.order_id), [order.amount]), settings=payment_settings, source="webhook", now=now)
        assert order.status == "reconciliation_required" and subscription.plan_id == expected_plan
        assert db.scalar(select(PaymentEvent).where(PaymentEvent.payment_id == db.scalar(select(Payment.id).where(Payment.order_id == order.id)))).amount == order.amount
