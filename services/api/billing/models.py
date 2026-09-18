from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base, new_id, utcnow


class Wallet(Base):
    __tablename__ = "credit_wallets"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    ever_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditBucket(Base):
    __tablename__ = "credit_buckets"
    __table_args__ = (UniqueConstraint("tenant_id", "grant_key", name="uq_credit_grant"), CheckConstraint("granted >= 0 AND available >= 0 AND reserved >= 0 AND consumed >= 0 AND expired >= 0", name="ck_credit_bucket_nonnegative"), CheckConstraint("granted = available + reserved + consumed + expired", name="ck_credit_bucket_conservation"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("credit_wallets.tenant_id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    scope: Mapped[str] = mapped_column(String(30))
    grant_key: Mapped[str] = mapped_column(String(200))
    granted: Mapped[int] = mapped_column(Integer)
    available: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[int] = mapped_column(Integer, default=0)
    expired: Mapped[int] = mapped_column(Integer, default=0)
    source_bucket_id: Mapped[str | None] = mapped_column(ForeignKey("credit_buckets.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (UniqueConstraint("tenant_id", "event_key", name="uq_credit_ledger_event"), CheckConstraint("event IN ('GRANT','RESERVE','CAPTURE','RELEASE','EXPIRE','COMPENSATE','ADJUSTMENT')", name="ck_credit_ledger_event"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("credit_wallets.tenant_id"), index=True)
    bucket_id: Mapped[str | None] = mapped_column(ForeignKey("credit_buckets.id"), nullable=True)
    event_key: Mapped[str] = mapped_column(String(240))
    event: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    operation_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reason: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Quote(Base):
    __tablename__ = "credit_quotes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("credit_wallets.tenant_id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    units: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[int] = mapped_column(Integer)
    credit_total: Mapped[int] = mapped_column(Integer)
    pricing_version: Mapped[str] = mapped_column(String(80))
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    base_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    balance_before: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reservation(Base):
    __tablename__ = "credit_reservations"
    __table_args__ = (UniqueConstraint("tenant_id", "operation_key", name="uq_credit_reservation_operation"), CheckConstraint("units > 0 AND unit_cost >= 0 AND total = units * unit_cost", name="ck_credit_reservation_cost"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("credit_wallets.tenant_id"), index=True)
    operation_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))
    units: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="reserved")
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("credit_quotes.id"), nullable=True, unique=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    unit_status: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Allocation(Base):
    __tablename__ = "credit_allocations"
    __table_args__ = (UniqueConstraint("reservation_id", "unit_index", "bucket_id", name="uq_credit_allocation_unit_bucket"), CheckConstraint("amount > 0", name="ck_credit_allocation_amount"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("credit_wallets.tenant_id"), index=True)
    reservation_id: Mapped[str] = mapped_column(ForeignKey("credit_reservations.id"), index=True)
    unit_index: Mapped[int] = mapped_column(Integer)
    bucket_id: Mapped[str] = mapped_column(ForeignKey("credit_buckets.id"))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="held")


class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    customer_key_encrypted: Mapped[str] = mapped_column(Text)
    customer_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    billing_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True)
    plan_id: Mapped[str] = mapped_column(String(30))
    next_plan_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    anchor_day: Mapped[int] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Seoul")
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    billing_anchor: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pricing_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    next_pricing_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invoice(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (UniqueConstraint("subscription_id", "period_start", name="uq_subscription_invoice_period"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    subscription_id: Mapped[str] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    plan_id: Mapped[str] = mapped_column(String(30))
    amount: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentOrder(Base):
    __tablename__ = "payment_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "operation_key", name="uq_payment_order_operation"), CheckConstraint("amount >= 0 AND credits >= 0", name="ck_payment_order_nonnegative"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True)
    operation_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30))
    plan_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_plan_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="KRW")
    credits: Mapped[int] = mapped_column(Integer)
    pricing_version: Mapped[str] = mapped_column(String(80))
    pricing_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_pricing_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("billing_invoices.id"), nullable=True, unique=True)
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    credits_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id"), unique=True)
    provider_payment_key: Mapped[str] = mapped_column(String(200), unique=True)
    provider: Mapped[str] = mapped_column(String(30))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(30))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"))
    event_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BillingOutbox(Base):
    __tablename__ = "billing_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEvent(Base):
    __tablename__ = "billing_webhooks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(64), unique=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id"))
    verified_status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProductionEntitlement(Base):
    __tablename__ = "production_entitlements"
    __table_args__ = (UniqueConstraint("tenant_id", "fingerprint", name="uq_production_entitlement"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    identity_policy: Mapped[str] = mapped_column(String(80))
    reservation_id: Mapped[str | None] = mapped_column(ForeignKey("credit_reservations.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _immutable(*_):
    raise ValueError("Financial history is append-only; post a compensating entry")


for _model in (LedgerEntry, Payment, PaymentEvent):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)


class ReferralCode(Base):
    __tablename__ = "referral_codes"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (CheckConstraint("status IN ('pending','granted','void')", name="ck_referral_status"),
                      CheckConstraint("referrer_tenant_id <> referred_tenant_id", name="ck_referral_not_self"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    referrer_tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    referred_tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True)
    code: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
