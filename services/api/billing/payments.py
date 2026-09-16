"""Toss adapter and server-owned orders. No browser callback grants credits alone."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
import base64
import logging
import os
import secrets
from typing import Protocol

import httpx
from sqlalchemy import select, exists, or_

from ..database import new_id, utcnow
from ..errors import APIError
from .models import BillingAccount, BillingOutbox, CreditBucket, Invoice, Payment, PaymentEvent, PaymentOrder, Subscription, WebhookEvent
from .policy import SEOUL, add_months, aware, plan, pricing, prorate
from .service import canonical_hash, ensure_trial, grant_credits, lock_wallet
from .sync import reversible_upgrade, sync_verified_payment


class _NoPaymentURLLogs(logging.Filter):
    def filter(self, record):
        # HTTPX INFO logging would otherwise include billingKey in the API path.
        return "api.tosspayments.com" not in record.getMessage()


logging.getLogger("httpx").addFilter(_NoPaymentURLLogs())


@dataclass
class BillingSettings:
    provider: str = field(default_factory=lambda: os.getenv("PAYMENT_PROVIDER", "disabled"))
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    secret_key: str = field(default_factory=lambda: os.getenv("TOSS_SECRET_KEY", ""))
    client_key: str = field(default_factory=lambda: os.getenv("TOSS_CLIENT_KEY", ""))
    merchant_id: str = field(default_factory=lambda: os.getenv("TOSS_MERCHANT_ID", ""))
    encryption_key: str = field(default_factory=lambda: os.getenv("BILLING_ENCRYPTION_KEY", ""))
    policy_approved: bool = field(default_factory=lambda: os.getenv("BILLING_POLICY_APPROVED", "false").lower() == "true")
    live_enabled: bool = field(default_factory=lambda: os.getenv("LIVE_BILLING_ENABLED", "false").lower() == "true")

    def validate(self):
        if self.provider not in {"disabled", "mock", "toss_test", "toss_live"}:
            raise APIError(503, "PAYMENTS_NOT_CONFIGURED", "결제 제공자 설정을 확인해 주세요.")
        if self.provider == "disabled":
            raise APIError(503, "PAYMENTS_DISABLED", "결제 서비스 연결을 준비하고 있습니다.")
        if self.provider == "mock" and self.environment not in {"development", "test"}:
            raise APIError(503, "MOCK_PAYMENT_FORBIDDEN", "호스팅 환경에서는 모의 결제를 사용할 수 없습니다.")
        if self.provider == "toss_test" and self.environment == "production":
            raise APIError(503, "TEST_PAYMENT_FORBIDDEN", "운영 환경에서는 PG 테스트 결제를 사용할 수 없습니다.")
        if not self.encryption_key:
            raise APIError(503, "BILLING_ENCRYPTION_REQUIRED", "결제 정보 암호화 설정이 필요합니다.")
        if self.provider == "toss_test" and not self.secret_key.startswith(("test_sk_", "test_gsk_")):
            raise APIError(503, "TEST_PAYMENT_KEY_REQUIRED", "Toss 테스트 키를 연결해 주세요.")
        if self.provider == "toss_live" and not (self.environment == "production" and self.secret_key.startswith(("live_sk_", "live_gsk_")) and self.policy_approved and self.live_enabled and pricing().get("live_billing_enabled") is True):
            raise APIError(503, "LIVE_BILLING_GATE_CLOSED", "운영 요금·환불 정책과 결제 검증을 완료해야 실제 결제를 시작할 수 있습니다.")
        if self.provider.startswith("toss") and (not self.client_key or not self.merchant_id):
            raise APIError(503, "TOSS_ACCOUNT_REQUIRED", "상점 클라이언트 키와 MID를 확인해 주세요.")
        self.cipher()

    def cipher(self):
        from cryptography.fernet import Fernet
        try:
            return Fernet(self.encryption_key.encode("ascii"))
        except (ValueError, UnicodeError):
            raise APIError(503, "BILLING_ENCRYPTION_INVALID", "결제 암호화 키 형식이 올바르지 않습니다.") from None

    def encrypt(self, value):
        return self.cipher().encrypt(value.encode()).decode()

    def decrypt(self, value):
        try:
            return self.cipher().decrypt(value.encode()).decode()
        except Exception:
            raise APIError(503, "BILLING_KEY_UNAVAILABLE", "결제 수단 정보를 복호화할 수 없습니다.") from None

    def capabilities(self):
        try:
            self.validate()
            available, message = True, None
        except APIError as error:
            available, message = False, error.message
        return {"provider": self.provider, "test_mode": self.provider in {"mock", "toss_test"}, "checkout_available": available, "live_enabled": available and self.provider == "toss_live", "mock_available": available and self.provider == "mock" and self.environment in {"development", "test"}, "billing_auth_available": available and self.provider.startswith("toss"), "client_key": self.client_key if available and self.provider.startswith("toss") else None, "message": message}


class ProviderError(Exception):
    def __init__(self, code="PROVIDER_ERROR", retryable=False, uncertain=False):
        self.code, self.retryable, self.uncertain = code, retryable, uncertain
        super().__init__(code)


class PaymentProvider(Protocol):
    name: str
    def confirm(self, payment_key, order_id, amount): ...
    def query(self, order_id): ...
    def query_payment(self, payment_key): ...
    def issue_billing(self, auth_key, customer_key): ...
    def charge(self, billing_key, customer_key, order_id, amount, order_name): ...
    def cancel(self, payment_key, amount, reason, operation_key): ...


class TossProvider:
    def __init__(self, settings, transport=None):
        settings.validate()
        self.settings, self.name, self.transport = settings, settings.provider, transport

    def _request(self, method, path, body=None, key=None, allow_not_found=False):
        headers = {"Authorization": "Basic " + base64.b64encode((self.settings.secret_key + ":").encode()).decode(), "Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = sha256(key.encode()).hexdigest()
        try:
            with httpx.Client(base_url="https://api.tosspayments.com", timeout=20, transport=self.transport) as client:
                response = client.request(method, path, json=body, headers=headers)
        except httpx.TransportError:
            raise ProviderError("PAYMENT_RESPONSE_UNCERTAIN", retryable=True, uncertain=method != "GET") from None
        if allow_not_found and response.status_code == 404:
            return None
        if response.status_code >= 400:
            try:
                error = response.json()
            except ValueError:
                error = {}
            # Re-query an already processed operation; its error alone never
            # proves that the intended order was approved or canceled.
            already_processed = isinstance(error, dict) and error.get("code") in {"ALREADY_PROCESSED_PAYMENT", "ALREADY_CANCELED_PAYMENT"}
            raise ProviderError("PAYMENT_ALREADY_PROCESSED" if already_processed else "PAYMENT_PROVIDER_REJECTED", retryable=response.status_code >= 500 or response.status_code == 429, uncertain=method != "GET" and (response.status_code >= 500 or already_processed))
        try:
            value = response.json()
        except ValueError:
            raise ProviderError("PAYMENT_RESPONSE_UNCERTAIN", retryable=True, uncertain=True) from None
        if not isinstance(value, dict):
            raise ProviderError("PAYMENT_RESPONSE_UNCERTAIN", retryable=True, uncertain=method != "GET")
        return value

    def confirm(self, payment_key, order_id, amount):
        return self._request("POST", "/v1/payments/confirm", {"paymentKey": payment_key, "orderId": order_id, "amount": amount}, key="confirm:" + order_id)

    def query(self, order_id):
        from urllib.parse import quote
        return self._request("GET", "/v1/payments/orders/" + quote(order_id, safe=""), allow_not_found=True)

    def query_payment(self, payment_key):
        from urllib.parse import quote
        return self._request("GET", "/v1/payments/" + quote(payment_key, safe=""), allow_not_found=True)

    def issue_billing(self, auth_key, customer_key):
        return self._request("POST", "/v1/billing/authorizations/issue", {"authKey": auth_key, "customerKey": customer_key}, key="billing-key:" + customer_key + ":" + sha256(auth_key.encode()).hexdigest())

    def charge(self, billing_key, customer_key, order_id, amount, order_name):
        from urllib.parse import quote
        return self._request("POST", "/v1/billing/" + quote(billing_key, safe=""), {"customerKey": customer_key, "amount": amount, "orderId": order_id, "orderName": order_name}, key="billing-charge:" + order_id)

    def cancel(self, payment_key, amount, reason, operation_key):
        from urllib.parse import quote
        return self._request("POST", "/v1/payments/" + quote(payment_key, safe="") + "/cancel", {"cancelReason": reason[:200], "cancelAmount": amount, "refundableAmount": amount}, key="cancel:" + operation_key)


class MockProvider:
    """Injectable deterministic PG for local/test only; never an account grant API."""
    name = "mock"

    def __init__(self, environment="test"):
        if environment not in {"development", "test"}:
            raise APIError(503, "MOCK_PAYMENT_FORBIDDEN", "운영 환경에서 모의 결제를 사용할 수 없습니다.")
        self.payments, self.calls = {}, []

    def confirm(self, payment_key, order_id, amount):
        self.calls.append(("confirm", order_id))
        if order_id not in self.payments:
            self.payments[order_id] = {"orderId": order_id, "paymentKey": "mock_" + sha256(order_id.encode()).hexdigest(), "totalAmount": amount, "balanceAmount": amount, "currency": "KRW", "status": "DONE", "mId": "mock", "approvedAt": utcnow().isoformat()}
        return dict(self.payments[order_id])

    def query(self, order_id):
        self.calls.append(("query", order_id))
        return dict(self.payments[order_id]) if order_id in self.payments else None

    def query_payment(self, payment_key):
        return next((dict(value) for value in self.payments.values() if value["paymentKey"] == payment_key), None)

    def issue_billing(self, auth_key, customer_key):
        return {"billingKey": "mock_billing_" + sha256(customer_key.encode()).hexdigest(), "customerKey": customer_key, "mId": "mock"}

    def charge(self, billing_key, customer_key, order_id, amount, order_name):
        return self.confirm("mock", order_id, amount)

    def cancel(self, payment_key, amount, reason, operation_key):
        item = next((value for value in self.payments.values() if value["paymentKey"] == payment_key), None)
        if item is None:
            raise ProviderError("PAYMENT_NOT_FOUND")
        item["status"], item["balanceAmount"] = "CANCELED", 0
        item["cancels"] = [{"cancelAmount": amount, "transactionKey": "cancel_" + operation_key}]
        return dict(item)


def build_provider(settings):
    settings.validate()
    return MockProvider(settings.environment) if settings.provider == "mock" else TossProvider(settings)


def billing_account(db, tenant_id, settings):
    settings.validate()
    account = db.get(BillingAccount, tenant_id)
    if account is None:
        key = "phoenix_" + secrets.token_urlsafe(32)
        account = BillingAccount(tenant_id=tenant_id, customer_key_encrypted=settings.encrypt(key), customer_key_hash=sha256(key.encode()).hexdigest(), provider=settings.provider)
        db.add(account)
        db.flush()
    if account.provider != settings.provider:
        raise APIError(409, "PAYMENT_ENVIRONMENT_CHANGED", "테스트와 실제 결제 수단은 별도로 등록해 주세요.")
    return account


def subscription_payload(subscription):
    if subscription is None:
        return None
    return {"id": subscription.id, "plan_id": subscription.plan_id, "next_plan_id": subscription.next_plan_id, "status": subscription.status, "anchor_day": subscription.anchor_day, "timezone": subscription.timezone, "current_period_start": aware(subscription.current_period_start).isoformat(), "current_period_end": aware(subscription.current_period_end).isoformat(), "cancel_at_period_end": subscription.cancel_at_period_end, "paid_until": aware(subscription.paid_until).isoformat() if subscription.paid_until else None}


def order_payload(order, account=None, settings=None):
    return {"id": order.id, "order_id": order.order_id, "kind": order.kind, "plan_id": order.plan_id, "amount": order.amount, "currency": order.currency, "credits": order.credits, "status": order.status, "error": order.error, "pricing_version": order.pricing_version, "created_at": aware(order.created_at).isoformat(), "checkout_kind": "billing_auth" if order.kind == "subscription" else "payment", "customer_key": settings.decrypt(account.customer_key_encrypted) if account is not None and settings is not None else None, "expires_at": (aware(order.created_at) + timedelta(minutes=5 if order.kind == "upgrade" else 30)).isoformat()}


def entitlements(db, tenant_id, now=None):
    now = now or utcnow()
    wallet = ensure_trial(db, tenant_id, now=now)
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    active = bool(subscription and subscription.paid_until and aware(subscription.paid_until) > aware(now))
    seats = plan(subscription.plan_id)["seats"] if active else 1
    bought = db.scalars(select(CreditBucket).where(CreditBucket.tenant_id == tenant_id, CreditBucket.kind == "purchase")).all()
    keep_until = [aware(bucket.expires_at) for bucket in bought]
    if subscription and subscription.paid_until:
        keep_until.append(aware(subscription.paid_until) + timedelta(days=90))
    return {"seats": seats, "active_subscription": active, "production_export": wallet.ever_paid, "retention_until": max(keep_until).isoformat() if keep_until else None, "team_access": active and seats > 1}


def enforce_membership_entitlement(db, user, now=None):
    if user.role != "owner" and not entitlements(db, user.tenant_id, now)["team_access"]:
        raise APIError(403, "TEAM_ACCESS_EXPIRED", "구독 기간 종료 후에는 소유자만 작업 공간에 접근할 수 있습니다.")


def create_order(db, tenant_id, kind, operation_key, *, plan_id=None, credits=None, settings, now=None):
    now = now or utcnow()
    settings.validate()
    ensure_trial(db, tenant_id, now=now)
    if not operation_key or len(operation_key) > 160:
        raise APIError(422, "IDEMPOTENCY_KEY_INVALID", "주문 요청 식별자를 확인해 주세요.")
    digest = canonical_hash({"kind": kind, "plan_id": plan_id, "credits": credits})
    old = db.scalar(select(PaymentOrder).where(PaymentOrder.tenant_id == tenant_id, PaymentOrder.operation_key == operation_key))
    if old:
        if old.request_hash != digest:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "같은 식별자로 다른 주문을 만들 수 없습니다.")
        return old
    billing_account(db, tenant_id, settings)
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    invoice = None
    if kind == "subscription":
        selected = plan(plan_id)
        if subscription and subscription.paid_until and aware(subscription.paid_until) > aware(now):
            raise APIError(409, "SUBSCRIPTION_ALREADY_ACTIVE", "활성 구독은 요금제 변경으로 조정해 주세요.")
        if subscription and subscription.status == "pending":
            raise APIError(409, "SUBSCRIPTION_ORDER_PENDING", "기존 구독 주문을 완료하거나 다시 시도해 주세요.")
        if subscription is None:
            subscription = Subscription(tenant_id=tenant_id, plan_id=plan_id, anchor_day=aware(now).astimezone(SEOUL).day, billing_anchor=now, current_period_start=now, current_period_end=add_months(now), status="pending")
            db.add(subscription)
            db.flush()
        else:
            subscription.plan_id, subscription.status = plan_id, "pending"
            subscription.current_period_start, subscription.current_period_end = now, add_months(now)
            subscription.anchor_day, subscription.billing_anchor = aware(now).astimezone(SEOUL).day, now
            subscription.cancel_at_period_end, subscription.next_plan_id = False, None
        amount, grant = selected["monthly_inc_vat"], selected["credits"]
        expires = subscription.current_period_end
        invoice = Invoice(tenant_id=tenant_id, subscription_id=subscription.id, period_start=now, period_end=expires, plan_id=plan_id, amount=amount, credits=grant, created_at=now)
        db.add(invoice)
        db.flush()
    elif kind == "topup":
        if not subscription or not subscription.paid_until or aware(subscription.paid_until) <= aware(now):
            raise APIError(403, "ACTIVE_SUBSCRIPTION_REQUIRED", "추가 충전은 활성 구독 계정에서 구매할 수 있습니다.")
        selected = next((item for item in pricing()["topups"] if item["credits"] == credits), None)
        if selected is None:
            raise APIError(422, "UNKNOWN_TOPUP", "충전 상품을 확인해 주세요.")
        amount, grant = selected["inc_vat"], selected["credits"]
        expires = add_months(now, selected["expires_months"])
    else:
        raise APIError(422, "UNKNOWN_ORDER_KIND", "주문 종류를 확인해 주세요.")
    order = PaymentOrder(tenant_id=tenant_id, order_id="pp_" + new_id().replace("-", ""), operation_key=operation_key, request_hash=digest, kind=kind, plan_id=plan_id if kind == "subscription" else None, amount=amount, credits=grant, pricing_version=pricing()["version"], invoice_id=invoice.id if invoice else None, subscription_id=subscription.id if kind == "subscription" else None, period_start=invoice.period_start if invoice else None, period_end=invoice.period_end if invoice else None, credits_expires_at=expires, created_at=now)
    db.add(order)
    db.flush()
    return order


def _owned_order(db, tenant_id, order_id):
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_id == order_id, PaymentOrder.tenant_id == tenant_id))
    if order is None:
        raise APIError(404, "ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
    return order


def validate_provider_payment(order, result, settings, expected_payment_key=None):
    if not isinstance(result, dict) or result.get("orderId") != order.order_id or type(result.get("totalAmount")) is not int or result.get("totalAmount") != order.amount or result.get("currency") != order.currency:
        raise APIError(409, "PAYMENT_MISMATCH", "결제 조회 결과의 주문·금액·통화가 일치하지 않습니다.")
    if settings.provider != "mock" and result.get("mId") != settings.merchant_id:
        raise APIError(409, "PAYMENT_ACCOUNT_MISMATCH", "다른 상점의 결제는 처리할 수 없습니다.")
    key = result.get("paymentKey")
    if not isinstance(key, str) or not 1 <= len(key) <= 200 or (expected_payment_key and key != expected_payment_key):
        raise APIError(409, "PAYMENT_KEY_MISMATCH", "결제 식별자가 일치하지 않습니다.")


def _apply_paid(db, order, result, settings, now):
    validate_provider_payment(order, result, settings)
    if order.status in {"paid", "refunded"}:
        return order
    if result.get("status") != "DONE" or result.get("balanceAmount", order.amount) != order.amount:
        raise APIError(409, "PAYMENT_NOT_COMPLETED", "PG에서 결제 완료가 확인되지 않았습니다.")
    duplicate = db.scalar(select(Payment).where(Payment.provider_payment_key == result["paymentKey"]))
    if duplicate and duplicate.order_id != order.id:
        raise APIError(409, "PAYMENT_ALREADY_BOUND", "이미 다른 주문에 연결된 결제입니다.")
    if duplicate:
        return order
    wallet = lock_wallet(db, order.tenant_id, now)
    subscription = db.get(Subscription, order.subscription_id) if order.subscription_id else None
    approved_at = now
    if settings.provider != "mock":
        try:
            approved_at = aware(datetime.fromisoformat(result["approvedAt"].replace("Z", "+00:00")))
        except (KeyError, AttributeError, ValueError):
            raise APIError(409, "PAYMENT_TIMESTAMP_INVALID", "PG 승인 시각을 확인할 수 없습니다.") from None
    payment = Payment(tenant_id=order.tenant_id, order_id=order.id, provider_payment_key=result["paymentKey"], provider=settings.provider, amount=order.amount, currency=order.currency, status="DONE", approved_at=approved_at, created_at=now)
    db.add(payment)
    if order.kind == "subscription" and (not subscription or aware(subscription.current_period_start) != aware(order.period_start)):
        order.status, order.error = "reconciliation_required", "이전 구독 주문의 승인이 확인되어 운영 확인이 필요합니다."
        db.add(BillingOutbox(tenant_id=order.tenant_id, event_key=f"late-subscription:{order.id}", kind="payment.reconciliation_required", payload={"order_id": order.order_id}, created_at=now))
        db.flush()
        return order
    if order.kind == "upgrade" and (not subscription or subscription.plan_id != order.source_plan_id or aware(subscription.current_period_start) != aware(order.period_start) or aware(subscription.current_period_end) <= aware(now)):
        order.status, order.error = "reconciliation_required", "이전 구독 주기의 상향 결제가 확인되어 운영 확인이 필요합니다."
        db.add(BillingOutbox(tenant_id=order.tenant_id, event_key=f"late-upgrade:{order.id}", kind="payment.reconciliation_required", payload={"order_id": order.order_id}, created_at=now))
        db.flush()
        return order
    wallet.ever_paid = True
    order.status, order.error = "paid", None
    if order.kind == "subscription":
        subscription.anchor_day = aware(approved_at).astimezone(SEOUL).day
        subscription.billing_anchor = approved_at
        subscription.current_period_start = approved_at
        subscription.current_period_end = add_months(approved_at, anchor_day=subscription.anchor_day)
        order.period_start, order.period_end = approved_at, subscription.current_period_end
        order.credits_expires_at = subscription.current_period_end
        invoice = db.get(Invoice, order.invoice_id)
        invoice.period_start, invoice.period_end = approved_at, subscription.current_period_end
    if order.kind in {"subscription", "renewal"}:
        subscription.status, subscription.plan_id = "active", order.plan_id
        subscription.current_period_start, subscription.current_period_end = order.period_start, order.period_end
        subscription.paid_until = order.period_end
        subscription.next_plan_id = None
        invoice = db.get(Invoice, order.invoice_id)
        invoice.status, invoice.retry_at = "paid", None
        account = db.get(BillingAccount, order.tenant_id)
        if order.kind == "subscription" and not account.billing_key_encrypted:
            subscription.cancel_at_period_end = True
    elif order.kind == "upgrade":
        subscription.plan_id = order.plan_id
        subscription.next_plan_id = None
    kind = "purchase" if order.kind == "topup" else "monthly"
    grant_credits(db, order.tenant_id, order.credits, kind=kind, scope="paid", expires_at=order.credits_expires_at, grant_key="order:" + order.id, reason="추가 충전 결제" if kind == "purchase" else "구독 결제 크레딧", invoice_id=order.invoice_id, now=now)
    db.add(BillingOutbox(tenant_id=order.tenant_id, event_key=f"payment-paid:{order.id}", kind="payment.paid", payload={"order_id": order.order_id, "credits": order.credits}, created_at=now))
    db.flush()
    return order


def _provider_outcome(db, order, provider, settings, execute, now, expected_payment_key=None):
    try:
        # Query first: a lost response or duplicate callback never starts a new charge.
        result = provider.query(order.order_id)
        if result is None or result.get("status") in {"READY", "IN_PROGRESS"}:
            result = execute()
        validate_provider_payment(order, result, settings, expected_payment_key)
        return sync_verified_payment(db, order, result, settings=settings, source="approval", now=now)
    except ProviderError as error:
        if error.uncertain:
            try:
                found = provider.query(order.order_id)
            except ProviderError:
                found = None
            if found and found.get("status") not in {"READY", "IN_PROGRESS"}:
                validate_provider_payment(order, found, settings, expected_payment_key)
                return sync_verified_payment(db, order, found, settings=settings, source="approval_requery", now=now)
        order.status = "reconciliation_required" if error.uncertain else "failed"
        order.error = "결제 결과를 조회하고 있습니다. 중복 결제하지 마세요." if error.uncertain else "결제 승인을 완료하지 못했습니다. 결제 수단을 확인해 주세요."
        db.flush()
        return order


def confirm_order(db, tenant_id, order_id, payment_key, amount, *, provider, settings, now=None):
    now = now or utcnow()
    settings.validate()
    lock_wallet(db, tenant_id, now)
    order = _owned_order(db, tenant_id, order_id)
    if type(amount) is not int or amount != order.amount:
        raise APIError(409, "PAYMENT_AMOUNT_MISMATCH", "서버 주문 금액과 다릅니다.")
    if order.status in {"paid", "refunded"}:
        payment = db.scalar(select(Payment).where(Payment.order_id == order.id))
        if settings.provider != "mock" and payment and payment.provider_payment_key != payment_key:
            raise APIError(409, "PAYMENT_KEY_MISMATCH", "이미 연결된 결제 식별자와 다릅니다.")
        return order
    if order.kind == "upgrade" and (aware(order.period_end) <= aware(now) or aware(order.created_at) + timedelta(minutes=5) <= aware(now)):
        raise APIError(409, "UPGRADE_QUOTE_EXPIRED", "상향 변경 견적이 만료되었습니다.")
    if order.kind == "upgrade":
        subscription = db.get(Subscription, order.subscription_id)
        if subscription.plan_id != order.source_plan_id or aware(subscription.current_period_start) != aware(order.period_start):
            raise APIError(409, "UPGRADE_QUOTE_CHANGED", "다른 요금제 변경이 반영되었습니다. 새 견적을 확인해 주세요.")
    return _provider_outcome(db, order, provider, settings, lambda: provider.confirm(payment_key, order.order_id, order.amount), now, expected_payment_key=payment_key if settings.provider != "mock" else None)


def bind_and_charge(db, tenant_id, order_id, auth_key, customer_key, *, provider, settings, now=None):
    now = now or utcnow()
    settings.validate()
    lock_wallet(db, tenant_id, now)
    order = _owned_order(db, tenant_id, order_id)
    if order.kind != "subscription":
        raise APIError(422, "BILLING_ORDER_REQUIRED", "구독 주문에서 결제 수단을 등록해 주세요.")
    account = billing_account(db, tenant_id, settings)
    if not secrets.compare_digest(account.customer_key_hash, sha256(customer_key.encode()).hexdigest()):
        raise APIError(403, "CUSTOMER_KEY_MISMATCH", "이 계정의 결제 수단 인증이 아닙니다.")
    if order.status in {"paid", "refunded"}:
        return order
    # An earlier process may have died after charging but before committing the
    # newly issued billing key. Query the durable order before trying to reuse
    # a single-use authKey. A recovered payment without a saved billing key is
    # deliberately non-renewing until the owner registers a payment method.
    try:
        previous = provider.query(order.order_id)
    except ProviderError:
        order.status, order.error = "reconciliation_required", "기존 결제 결과를 조회하고 있습니다. 중복 결제하지 마세요."
        db.flush()
        return order
    if previous and previous.get("status") not in {"READY", "IN_PROGRESS"}:
        return sync_verified_payment(db, order, previous, settings=settings, source="billing_auth_requery", now=now)
    issued = {"billingKey": settings.decrypt(account.billing_key_encrypted), "customerKey": customer_key, "mId": settings.merchant_id if settings.provider != "mock" else "mock"} if account.billing_key_encrypted and order.status in {"failed", "reconciliation_required"} else provider.issue_billing(auth_key, customer_key)
    if issued.get("customerKey") != customer_key or (settings.provider != "mock" and issued.get("mId") != settings.merchant_id) or not isinstance(issued.get("billingKey"), str):
        raise APIError(409, "BILLING_KEY_MISMATCH", "결제 수단 등록 결과가 일치하지 않습니다.")
    account.billing_key_encrypted = settings.encrypt(issued["billingKey"])
    return _provider_outcome(db, order, provider, settings, lambda: provider.charge(issued["billingKey"], customer_key, order.order_id, order.amount, "Phoenix Packaging " + str(order.plan_id)), now)


def change_plan(db, tenant_id, new_plan_id, operation_key, *, settings, now=None):
    now = now or utcnow()
    settings.validate()
    lock_wallet(db, tenant_id, now)
    selected = plan(new_plan_id)
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if not subscription or not subscription.paid_until or aware(subscription.paid_until) <= aware(now):
        raise APIError(409, "ACTIVE_SUBSCRIPTION_REQUIRED", "활성 구독을 먼저 확인해 주세요.")
    old = plan(subscription.plan_id)
    if new_plan_id == subscription.plan_id:
        return {"subscription": subscription_payload(subscription), "change": "unchanged"}
    if selected["monthly_inc_vat"] < old["monthly_inc_vat"]:
        subscription.next_plan_id = new_plan_id
        db.flush()
        return {"subscription": subscription_payload(subscription), "change": "scheduled", "effective_at": aware(subscription.current_period_end).isoformat()}
    digest = canonical_hash({"kind": "upgrade", "plan_id": new_plan_id, "period_start": aware(subscription.current_period_start).isoformat()})
    previous = db.scalar(select(PaymentOrder).where(PaymentOrder.tenant_id == tenant_id, PaymentOrder.operation_key == operation_key))
    if previous:
        if previous.request_hash != digest:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "상향 변경 요청 식별자가 중복됩니다.")
        return {"change": "payment_required", "order": order_payload(previous, billing_account(db, tenant_id, settings), settings)}
    amount, credits = prorate(old, selected, subscription.current_period_start, subscription.current_period_end, now)
    if amount == 0:
        raise APIError(409, "PERIOD_RENEWING", "갱신 직전입니다. 새 결제 주기가 시작되면 요금제를 변경해 주세요.")
    order = PaymentOrder(tenant_id=tenant_id, order_id="pp_" + new_id().replace("-", ""), operation_key=operation_key, request_hash=digest, kind="upgrade", plan_id=new_plan_id, source_plan_id=subscription.plan_id, amount=amount, credits=credits, pricing_version=pricing()["version"], subscription_id=subscription.id, period_start=subscription.current_period_start, period_end=subscription.current_period_end, credits_expires_at=subscription.current_period_end, created_at=now)
    db.add(order)
    db.flush()
    return {"change": "payment_required", "order": order_payload(order, billing_account(db, tenant_id, settings), settings)}


def cancel_renewal(db, tenant_id, *, now=None):
    now = now or utcnow()
    lock_wallet(db, tenant_id, now)
    subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
    if subscription is None:
        raise APIError(404, "SUBSCRIPTION_NOT_FOUND", "해지할 구독이 없습니다.")
    subscription.cancel_at_period_end = True
    subscription.next_plan_id = None
    db.flush()
    return subscription


def _renewal_order(db, subscription, now):
    start = subscription.current_period_end
    invoice = db.scalar(select(Invoice).where(Invoice.subscription_id == subscription.id, Invoice.period_start == start))
    if invoice is None:
        selected = plan(subscription.next_plan_id or subscription.plan_id)
        invoice = Invoice(tenant_id=subscription.tenant_id, subscription_id=subscription.id, period_start=start, period_end=add_months(start, anchor_day=subscription.anchor_day), plan_id=selected["id"], amount=selected["monthly_inc_vat"], credits=selected["credits"], created_at=now)
        db.add(invoice)
        db.flush()
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.invoice_id == invoice.id))
    if order is None:
        order = PaymentOrder(tenant_id=subscription.tenant_id, order_id="pp_" + invoice.id.replace("-", ""), operation_key="invoice:" + invoice.id, request_hash=canonical_hash({"invoice_id": invoice.id}), kind="renewal", plan_id=invoice.plan_id, amount=invoice.amount, credits=invoice.credits, pricing_version=pricing()["version"], invoice_id=invoice.id, subscription_id=subscription.id, period_start=invoice.period_start, period_end=invoice.period_end, credits_expires_at=invoice.period_end, created_at=now)
        db.add(order)
        db.flush()
    return invoice, order


def process_due_invoices(session_factory, *, provider, settings, now=None, limit=20):
    """Worker-only entry point. Each invoice attempt owns one committed transaction."""
    now = now or utcnow()
    settings.validate()
    with session_factory() as db:
        # Exhausted or not-yet-due retries must not occupy the bounded scan and
        # indefinitely starve other tenants whose next invoice is due.
        blocked = exists(select(Invoice.id).where(Invoice.subscription_id == Subscription.id, Invoice.period_start == Subscription.current_period_end, or_(Invoice.attempts >= 3, Invoice.status.in_(["paid", "expired"]), Invoice.retry_at > now)))
        tenant_ids = list(db.scalars(select(Subscription.tenant_id).where(Subscription.current_period_end <= now, Subscription.status.in_(["active", "past_due"]), or_(Subscription.cancel_at_period_end.is_(True), ~blocked)).order_by(Subscription.current_period_end, Subscription.tenant_id).limit(max(1, min(limit, 100)))))
    processed = 0
    for tenant_id in tenant_ids:
        with session_factory() as db:
            lock_wallet(db, tenant_id, now)
            subscription = db.scalar(select(Subscription).where(Subscription.tenant_id == tenant_id))
            if aware(subscription.current_period_end) > aware(now):
                db.commit()
                continue
            if subscription.cancel_at_period_end:
                subscription.status = "canceled"
                db.commit()
                continue
            invoice, order = _renewal_order(db, subscription, now)
            # The provider's idempotency/order identity must survive process
            # death after approval, before local payment/grant commit.
            db.commit()
            lock_wallet(db, tenant_id, now)
            db.refresh(subscription); db.refresh(invoice); db.refresh(order)
            if aware(subscription.current_period_end) > aware(now) or invoice.status == "paid":
                db.commit()
                continue
            if subscription.cancel_at_period_end:
                subscription.status = "canceled"
                db.commit()
                continue
            if aware(invoice.period_end) <= aware(now):
                subscription.status, invoice.status, invoice.attempts = "past_due", "expired", 3
                order.status, order.error = "failed", "이미 끝난 구독 주기는 자동 청구하지 않습니다. 새 구독을 확인해 주세요."
                db.commit()
                continue
            if invoice.status == "paid" or invoice.attempts >= 3 or (invoice.retry_at and aware(invoice.retry_at) > aware(now)):
                db.commit()
                continue
            account = db.get(BillingAccount, tenant_id)
            if account is None or not account.billing_key_encrypted or account.provider != settings.provider:
                subscription.status, invoice.status = "past_due", "failed"
                order.status, order.error = "failed", "갱신 가능한 결제 수단이 없습니다."
                invoice.attempts = 3
                db.commit()
                continue
            invoice.attempts += 1
            billed = _provider_outcome(db, order, provider, settings, lambda: provider.charge(settings.decrypt(account.billing_key_encrypted), settings.decrypt(account.customer_key_encrypted), order.order_id, order.amount, "Phoenix Packaging " + str(order.plan_id)), now)
            if billed.status != "paid":
                subscription.status, invoice.status = "past_due", billed.status
                invoice.retry_at = aware(invoice.created_at) + timedelta(hours=24 if invoice.attempts == 1 else 72) if invoice.attempts < 3 else None
            db.commit()
            processed += 1
    return processed


def reconcile_pending_orders(session_factory, *, provider, settings, now=None, limit=20):
    """Worker-only re-query after uncertain approval; never initiates a charge."""
    now = now or utcnow()
    settings.validate()
    with session_factory() as db:
        # Confirmed partial/spent cancellations need an operator decision, not
        # endless polling that crowds uncertain approvals out of this batch.
        needs_review = exists(select(PaymentEvent.id).join(Payment, Payment.id == PaymentEvent.payment_id).where(Payment.order_id == PaymentOrder.id, PaymentEvent.kind == "REFUND"))
        ids = list(db.scalars(select(PaymentOrder.id).where(PaymentOrder.status == "reconciliation_required", ~needs_review).order_by(PaymentOrder.created_at).limit(max(1, min(limit, 100)))))
    processed = 0
    for order_id in ids:
        with session_factory() as db:
            order = db.get(PaymentOrder, order_id)
            lock_wallet(db, order.tenant_id, now)
            db.refresh(order)
            if order.status != "reconciliation_required":
                db.commit()
                continue
            try:
                verified = provider.query(order.order_id)
            except ProviderError:
                db.commit()
                continue
            if verified:
                sync_verified_payment(db, order, verified, settings=settings, source="worker_requery", now=now)
                processed += 1
            db.commit()
    return processed


def sync_order(db, tenant_id, order_id, *, provider, settings, now=None):
    """Owner-triggered provider lookup. This endpoint never approves or charges."""
    now = now or utcnow()
    settings.validate()
    lock_wallet(db, tenant_id, now)
    order = _owned_order(db, tenant_id, order_id)
    try:
        verified = provider.query(order.order_id)
    except ProviderError:
        raise APIError(503, "PAYMENT_QUERY_UNAVAILABLE", "결제 상태 재조회를 기다리고 있습니다.", retryable=True) from None
    if verified is None:
        raise APIError(409, "PAYMENT_UNVERIFIED", "PG에서 확인되지 않은 주문입니다.")
    return sync_verified_payment(db, order, verified, settings=settings, source="owner_requery", now=now)


def reconcile_webhook(db, payload, *, provider, settings, transmission_id=None, now=None):
    """Treat general Toss webhooks as lookup hints, never as payment proof."""
    now = now or utcnow()
    settings.validate()
    if not isinstance(payload, dict):
        raise APIError(422, "WEBHOOK_INVALID", "결제 알림 형식을 확인해 주세요.")
    if payload.get("eventType") not in {"PAYMENT_STATUS_CHANGED", "CANCEL_STATUS_CHANGED"}:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        raise APIError(422, "WEBHOOK_INVALID", "결제 알림 데이터 형식을 확인해 주세요.")
    order_id, payment_key = data.get("orderId"), data.get("paymentKey")
    if order_id is not None and (not isinstance(order_id, str) or not 1 <= len(order_id) <= 64):
        raise APIError(422, "WEBHOOK_ORDER_REQUIRED", "주문 식별자 형식을 확인해 주세요.")
    if payment_key is not None and (not isinstance(payment_key, str) or not 1 <= len(payment_key) <= 200):
        raise APIError(422, "PAYMENT_KEY_MISMATCH", "결제 식별자 형식을 확인해 주세요.")
    if not order_id and not payment_key:
        raise APIError(422, "WEBHOOK_ORDER_REQUIRED", "주문 식별자가 없는 결제 알림입니다.")
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.order_id == order_id)) if order_id else db.scalar(select(PaymentOrder).join(Payment, Payment.order_id == PaymentOrder.id).where(Payment.provider_payment_key == payment_key))
    # The same MID can serve another product. Unknown orders must never be
    # synthesized from caller data, or poison the provider's retry queue.
    if order is None:
        return None
    lock_wallet(db, order.tenant_id, now)
    db.refresh(order)
    transmission_key = None
    if transmission_id:
        if not isinstance(transmission_id, str) or len(transmission_id) > 200:
            raise APIError(422, "WEBHOOK_TRANSMISSION_INVALID", "결제 알림 식별자 형식을 확인해 주세요.")
        transmission_key = canonical_hash({"kind": "transmission", "provider": settings.provider, "order_id": order.order_id, "transmission_id": transmission_id})
        if db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_key == transmission_key)):
            return order
    try:
        verified = provider.query_payment(payment_key) if payment_key else provider.query(order.order_id)
    except ProviderError:
        raise APIError(503, "PAYMENT_QUERY_UNAVAILABLE", "결제 상태 재조회를 기다리고 있습니다.", retryable=True) from None
    if verified is None:
        raise APIError(409, "PAYMENT_UNVERIFIED", "PG에서 확인되지 않은 결제 알림입니다.")
    validate_provider_payment(order, verified, settings, payment_key)
    sync_verified_payment(db, order, verified, settings=settings, source="webhook", now=now)
    # Only acknowledge delivery in the SAME transaction as payment+ledger.
    # Failed requests remain retryable even with the same transmission ID.
    if transmission_key:
        db.add(WebhookEvent(tenant_id=order.tenant_id, event_key=transmission_key, order_id=order.id, verified_status=verified["status"], created_at=now))
    db.flush()
    return order


def refund_order(db, tenant_id, order_id, reason, *, provider, settings, now=None):
    now = now or utcnow()
    settings.validate()
    lock_wallet(db, tenant_id, now)
    order = _owned_order(db, tenant_id, order_id)
    if order.status == "refunded":
        return order
    if order.status != "paid":
        raise APIError(409, "PAID_ORDER_REQUIRED", "결제 완료 주문만 환불할 수 있습니다.")
    bucket = db.scalar(select(CreditBucket).where(CreditBucket.tenant_id == tenant_id, CreditBucket.grant_key == "order:" + order.id))
    if bucket is None or bucket.reserved or bucket.consumed or bucket.expired or bucket.available != bucket.granted:
        raise APIError(409, "REFUND_REVIEW_REQUIRED", "사용·예약·만료 내역이 있어 환불 검토가 필요합니다. 잔액을 임의 삭제하지 않습니다.")
    if order.kind == "upgrade" and reversible_upgrade(db, order, now) is None:
        raise APIError(409, "REFUND_REVIEW_REQUIRED", "이후 요금제 변경이 있거나 이전 요금제를 안전하게 복구할 수 없어 환불 검토가 필요합니다.")
    payment = db.scalar(select(Payment).where(Payment.order_id == order.id, Payment.tenant_id == tenant_id))
    if payment is None:
        raise APIError(409, "PAYMENT_UNVERIFIED", "원본 결제 내역을 확인할 수 없습니다.")
    # Query first even on retries: an earlier cancel may have succeeded before
    # a process died. Never issue another cancel against a changed balance.
    result = provider.query(order.order_id)
    validate_provider_payment(order, result, settings, payment.provider_payment_key)
    if result.get("status") in {"CANCELED", "PARTIAL_CANCELED"}:
        return sync_verified_payment(db, order, result, settings=settings, source="refund_requery", now=now)
    if result.get("status") != "DONE" or result.get("balanceAmount") != order.amount:
        raise APIError(409, "REFUND_UNCONFIRMED", "PG 결제 상태와 환불 가능 금액을 확인해 주세요.")
    if result.get("method") == "가상계좌":
        raise APIError(409, "REFUND_REVIEW_REQUIRED", "가상계좌 환불은 별도 계좌 확인이 필요하므로 운영 문의로 접수해 주세요.")
    try:
        result = provider.cancel(payment.provider_payment_key, order.amount, reason, "refund:" + order.id)
    except ProviderError:
        try:
            result = provider.query(order.order_id)
        except ProviderError:
            result = None
        if result is None:
            order.status, order.error = "reconciliation_required", "취소 결과를 조회하고 있습니다. 중복 취소하지 마세요."
            db.flush()
            return order
    validate_provider_payment(order, result, settings, payment.provider_payment_key)
    if result.get("status") not in {"CANCELED", "PARTIAL_CANCELED"}:
        raise APIError(409, "REFUND_UNCONFIRMED", "PG에서 취소 완료가 확인되지 않았습니다.")
    return sync_verified_payment(db, order, result, settings=settings, source="refund", now=now)
