"""Port of aibridge payment-sync: one verified-state path, immutable finance.

Only server-owned orders enter this module. It never creates an order from an
untrusted webhook, grants mismatched prices, or stores card/account details.
"""
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import func, select

from ..database import utcnow
from ..errors import APIError
from ..feature_models import AuditEvent
from .models import BillingAccount, BillingOutbox, CreditBucket, Invoice, Payment, PaymentEvent, PaymentOrder, Subscription, WebhookEvent
from .policy import aware, plan
from .service import _event, canonical_hash, lock_wallet

STATUSES = {"READY", "IN_PROGRESS", "WAITING_FOR_DEPOSIT", "DONE", "CANCELED", "PARTIAL_CANCELED", "ABORTED", "EXPIRED"}


def summarize_provider_payment(result):
    status = result.get("status")
    total, balance = result.get("totalAmount"), result.get("balanceAmount")
    if status not in STATUSES or type(total) is not int or type(balance) is not int or not 0 <= balance <= total:
        raise APIError(409, "PAYMENT_STATE_INVALID", "PG 결제 상태와 금액을 확인할 수 없습니다.")
    cancels = result.get("cancels") or []
    if not isinstance(cancels, list) or len(cancels) > 100:
        raise APIError(409, "PAYMENT_CANCELLATION_INVALID", "PG 취소 내역 형식을 확인할 수 없습니다.")
    normalized = []
    for item in cancels:
        if not isinstance(item, dict) or type(item.get("cancelAmount")) is not int or item["cancelAmount"] <= 0 or not isinstance(item.get("transactionKey"), str) or not item["transactionKey"] or len(item["transactionKey"]) > 200:
            raise APIError(409, "PAYMENT_CANCELLATION_INVALID", "PG 취소 내역의 금액과 식별자를 확인할 수 없습니다.")
        normalized.append({"amount": item["cancelAmount"], "reason": str(item.get("cancelReason") or "PG 취소 확인")[:200], "at": item.get("canceledAt"), "transaction_key": item["transactionKey"], "status": item.get("cancelStatus", "DONE")})
    completed = [item for item in normalized if item["status"] == "DONE"]
    if len({item["transaction_key"] for item in completed}) != len(completed):
        raise APIError(409, "PAYMENT_CANCELLATION_INVALID", "PG 취소 식별자가 중복되었습니다.")
    cancelled = sum(item["amount"] for item in completed)
    if status in {"CANCELED", "PARTIAL_CANCELED"}:
        if cancelled != total - balance or cancelled <= 0 or (status == "CANCELED") != (balance == 0):
            raise APIError(409, "PAYMENT_CANCELLATION_MISMATCH", "PG 취소 금액과 남은 결제액이 일치하지 않습니다.")
    elif cancelled or (status == "DONE" and balance != total):
        raise APIError(409, "PAYMENT_CANCELLATION_MISMATCH", "PG 승인과 취소 상태가 일치하지 않습니다.")
    receipt = (result.get("receipt") or {}).get("url") if isinstance(result.get("receipt"), dict) else None
    parsed = urlparse(receipt) if isinstance(receipt, str) else None
    if parsed is None or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or not any(parsed.hostname == suffix or parsed.hostname.endswith("." + suffix) for suffix in ("tosspayments.com", "toss.im")):
        receipt = None
    return {"provider_status": status, "method": str(result.get("method"))[:80] if result.get("method") else None, "total_amount": total, "balance_amount": balance, "cancelled_amount": cancelled, "approved_at": result.get("approvedAt"), "receipt_url": receipt, "cancels": normalized}


def payment_snapshot(db, order):
    row = db.scalar(select(AuditEvent).where(AuditEvent.tenant_id == order.tenant_id, AuditEvent.entity_id == order.id, AuditEvent.action == "payment_state_synced").order_by(AuditEvent.details["sequence"].as_integer().desc(), AuditEvent.created_at.desc(), AuditEvent.id.desc()))
    if row is None:
        return None
    summary = dict(row.details["payment"])
    summary["cancels"] = [{key: value for key, value in item.items() if key != "transaction_key"} for item in summary["cancels"]]
    return {**summary, "synced_at": aware(row.created_at).isoformat()}


def _record_cancellation(db, order, result, summary, settings, now):
    payment = db.scalar(select(Payment).where(Payment.order_id == order.id, Payment.tenant_id == order.tenant_id))
    if payment is None:
        if db.scalar(select(Payment.id).where(Payment.provider_payment_key == result["paymentKey"])):
            raise APIError(409, "PAYMENT_ALREADY_BOUND", "이미 다른 주문에 연결된 결제입니다.")
        try:
            approved = aware(datetime.fromisoformat(result["approvedAt"].replace("Z", "+00:00")))
        except (KeyError, AttributeError, ValueError):
            raise APIError(409, "PAYMENT_TIMESTAMP_INVALID", "PG 승인 시각을 확인할 수 없습니다.") from None
        payment = Payment(tenant_id=order.tenant_id, order_id=order.id, provider_payment_key=result["paymentKey"], provider=settings.provider, amount=order.amount, currency=order.currency, status=summary["provider_status"], approved_at=approved, created_at=now)
        db.add(payment); db.flush()
        from ..metrics.service import record_recovered_payment
        record_recovered_payment(db,order,payment)
    if payment.provider_payment_key != result["paymentKey"] or payment.provider != settings.provider:
        raise APIError(409, "PAYMENT_KEY_MISMATCH", "기존 결제와 취소 식별자가 다릅니다.")
    recorded = db.scalar(select(func.coalesce(func.sum(PaymentEvent.amount), 0)).where(PaymentEvent.payment_id == payment.id, PaymentEvent.kind == "REFUND"))
    if summary["cancelled_amount"] < recorded:
        raise APIError(409, "PAYMENT_STATE_REGRESSION", "이미 확인된 취소보다 이전 결제 상태는 반영하지 않습니다.")
    for item in summary["cancels"]:
        if item["status"] != "DONE":
            continue
        key = "pg-cancel:" + canonical_hash({"provider": settings.provider, "payment": payment.provider_payment_key, "transaction": item["transaction_key"]})
        if db.scalar(select(PaymentEvent.id).where(PaymentEvent.event_key == key)):
            continue
        # Legacy full refunds already have an immutable refund:<order> event.
        if recorded >= summary["cancelled_amount"]:
            continue
        db.add(PaymentEvent(tenant_id=order.tenant_id, payment_id=payment.id, event_key=key, kind="REFUND", amount=item["amount"], reason=item["reason"], created_at=now))
        recorded += item["amount"]
    if recorded != summary["cancelled_amount"]:
        raise APIError(409, "PAYMENT_CANCELLATION_MISMATCH", "기록된 취소 합계와 PG 취소 합계가 다릅니다.")


def reversible_upgrade(db, order, now):
    """Return the subscription only for the last, intact current upgrade chain.

    Called under the wallet lock both before requesting a refund and before
    applying a verified external cancellation. No timestamp ordering guesses.
    """
    subscription = db.get(Subscription, order.subscription_id)
    if (order.kind != "upgrade" or order.status == "refunded" or not subscription
            or not subscription.paid_until or aware(subscription.paid_until) <= aware(now)
            or subscription.status != "active" or subscription.plan_id != order.plan_id
            or aware(subscription.current_period_start) != aware(order.period_start)
            or aware(subscription.current_period_end) != aware(order.period_end)):
        return None
    applied = list(db.scalars(select(PaymentOrder).join(CreditBucket, CreditBucket.grant_key == ("order:" + PaymentOrder.id)).where(
        PaymentOrder.tenant_id == order.tenant_id, CreditBucket.tenant_id == order.tenant_id,
        PaymentOrder.subscription_id == subscription.id, PaymentOrder.period_start == order.period_start,
        PaymentOrder.status.in_(["paid", "reconciliation_required"]))))
    base = [item for item in applied if item.kind in {"subscription", "renewal"}]
    upgrades = [item for item in applied if item.kind == "upgrade"]
    if len(base) != 1 or base[0].status != "paid" or not upgrades:
        return None
    try:
        upgrades.sort(key=lambda item: plan(item.plan_id,snapshot=item.pricing_snapshot)["monthly_inc_vat"])
        expected = base[0].plan_id
        for item in upgrades:
            if item.source_plan_id != expected or plan(item.plan_id,snapshot=item.pricing_snapshot)["monthly_inc_vat"] <= plan(expected,snapshot=item.source_pricing_snapshot or item.pricing_snapshot)["monthly_inc_vat"] or (item.id != order.id and item.status != "paid"):
                return None
            expected = item.plan_id
        if upgrades[-1].id != order.id or expected != subscription.plan_id:
            return None
    except APIError:
        return None
    return subscription


def _apply_cancellation(db, order, result, summary, settings, now):
    _record_cancellation(db, order, result, summary, settings, now)
    if order.invoice_id:
        db.get(Invoice, order.invoice_id).status = "refunded" if summary["provider_status"] == "CANCELED" else "partially_refunded"
    subscription = db.get(Subscription, order.subscription_id) if order.kind in {"subscription", "renewal"} else None
    current_period = subscription is not None and aware(subscription.current_period_start) == aware(order.period_start)
    # A confirmed full cancel must not trigger another automatic bill while
    # spent credits are under review. Existing usage remains intact.
    if summary["provider_status"] == "CANCELED" and current_period:
        subscription.cancel_at_period_end = True
    bucket = db.scalar(select(CreditBucket).where(CreditBucket.tenant_id == order.tenant_id, CreditBucket.grant_key == "order:" + order.id))
    fully_unused = bucket is not None and not bucket.reserved and not bucket.consumed and not bucket.expired and bucket.available == bucket.granted
    upgrade = reversible_upgrade(db, order, now) if order.kind == "upgrade" and summary["provider_status"] == "CANCELED" else None
    if upgrade:
        upgrade.plan_id = order.source_plan_id
        upgrade.pricing_snapshot = order.source_pricing_snapshot or upgrade.pricing_snapshot
        if upgrade.next_plan_id and plan(upgrade.next_plan_id,snapshot=upgrade.pricing_snapshot)["monthly_inc_vat"] >= plan(order.source_plan_id,snapshot=upgrade.pricing_snapshot)["monthly_inc_vat"]:
            upgrade.next_plan_id = None
            upgrade.next_pricing_snapshot = None
    upgrade_safe = order.kind != "upgrade" or upgrade is not None or order.status == "refunded" or bucket is None
    if summary["provider_status"] == "CANCELED" and upgrade_safe and (bucket is None or fully_unused or order.status == "refunded"):
        if fully_unused:
            bucket.expired += bucket.available; bucket.available = 0
            _event(db, order.tenant_id, bucket.id, "ADJUSTMENT", -bucket.granted, "refund:" + order.id, "PG 전액 취소 확인 후 미사용 지급분 정정", now, invoice_id=order.invoice_id)
        order.status, order.error = "refunded", None
        if current_period:
            subscription.status, subscription.paid_until = "canceled", now
    else:
        order.status, order.error = "reconciliation_required", "PG 취소를 확인했습니다. 부분 취소 또는 사용·예약 내역을 운영 검토하고 있습니다."


def sync_verified_payment(db, order, result, *, settings, source, now=None):
    """Apply a provider response inside caller's transaction; never commits."""
    from .payments import _apply_paid, validate_provider_payment
    now = now or utcnow()
    lock_wallet(db, order.tenant_id, now)
    db.refresh(order)
    account = db.get(BillingAccount, order.tenant_id)
    if account is None or account.provider != settings.provider:
        raise APIError(409, "PAYMENT_ENVIRONMENT_CHANGED", "주문을 만든 결제 환경과 현재 상점 설정이 다릅니다.")
    validate_provider_payment(order, result, settings)
    existing = db.scalar(select(Payment).where(Payment.order_id == order.id))
    if existing and (existing.provider_payment_key != result["paymentKey"] or existing.provider != settings.provider):
        raise APIError(409, "PAYMENT_KEY_MISMATCH", "기존 주문에 연결된 결제 식별자가 아닙니다.")
    summary = summarize_provider_payment(result)
    if existing and summary["provider_status"] not in {"DONE", "CANCELED", "PARTIAL_CANCELED"}:
        raise APIError(409, "PAYMENT_STATE_REGRESSION", "확인된 승인을 이전 결제 상태로 되돌리지 않습니다.")
    key = canonical_hash({"provider": settings.provider, "order_id": order.order_id, "payment_key": result["paymentKey"], "state": summary})
    if db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_key == key)):
        return order
    prior = payment_snapshot(db, order)
    if (prior and summary["cancelled_amount"] < prior["cancelled_amount"]) or (order.status == "refunded" and summary["provider_status"] != "CANCELED"):
        raise APIError(409, "PAYMENT_STATE_REGRESSION", "이미 확인된 취소를 이전 상태로 되돌리지 않습니다.")
    if summary["provider_status"] == "DONE":
        _apply_paid(db, order, result, settings, now)
    elif summary["provider_status"] in {"CANCELED", "PARTIAL_CANCELED"}:
        _apply_cancellation(db, order, result, summary, settings, now)
        db.add(BillingOutbox(tenant_id=order.tenant_id, event_key="external-cancel:" + key, kind="payment.external_cancellation", payload={"order_id": order.order_id}, created_at=now))
    elif summary["provider_status"] in {"ABORTED", "EXPIRED"} and order.status in {"pending", "failed", "reconciliation_required"}:
        if db.scalar(select(Payment.id).where(Payment.order_id == order.id)) is None:
            order.status, order.error = "failed", "PG에서 결제 실패 또는 만료를 확인했습니다."
            if order.invoice_id:
                db.get(Invoice, order.invoice_id).status = "failed"
            if order.kind == "subscription":
                subscription = db.get(Subscription, order.subscription_id)
                if subscription and subscription.status == "pending": subscription.status = "incomplete"
    sequence = db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == order.tenant_id, AuditEvent.entity_id == order.id, AuditEvent.action == "payment_state_synced")) + 1
    db.add(AuditEvent(tenant_id=order.tenant_id, action="payment_state_synced", entity_id=order.id, details={"source": source, "sequence": sequence, "payment": summary}, created_at=now))
    db.add(WebhookEvent(tenant_id=order.tenant_id, event_key=key, order_id=order.id, verified_status=summary["provider_status"], created_at=now))
    db.flush()
    return order
