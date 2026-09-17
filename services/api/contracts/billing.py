"""Billing amounts are KRW integers; credits are separate from provider cost."""
from typing import Annotated, Literal
from pydantic import Field
from .base import ContractModel


class CreditBucketData(ContractModel):
    id: str
    kind: str
    scope: str
    available: int
    reserved: int
    consumed: int
    expired: int
    adjusted: int
    expires_at: str


class CreditLedgerEntry(ContractModel):
    id: str
    event: str
    amount: int
    reason: str
    created_at: str
    job_id: str | None
    operation_key: str | None


class WalletSummary(ContractModel):
    balance: int
    available: int
    reserved: int
    consumed: int
    expired: int
    adjusted: int
    trial_expires_at: str | None
    buckets: list[CreditBucketData]
    ledger: list[CreditLedgerEntry]


class CreditsData(WalletSummary):
    mode: str
    payments_enabled: bool
    review_export_cost: int


class SubscriptionData(ContractModel):
    id: str
    plan_id: str
    next_plan_id: str | None
    status: str
    anchor_day: int
    timezone: str
    current_period_start: str
    current_period_end: str
    cancel_at_period_end: bool
    paid_until: str | None


class PaymentCancellation(ContractModel):
    amount: int
    reason: str
    at: str | None
    status: str


class PaymentSnapshot(ContractModel):
    provider_status: str
    method: str | None
    total_amount: int
    balance_amount: int
    cancelled_amount: int
    approved_at: str | None
    receipt_url: str | None
    cancels: list[PaymentCancellation]
    synced_at: str


class PaymentOrderData(ContractModel):
    id: str
    order_id: str
    kind: str
    plan_id: str | None
    amount: int
    currency: str
    credits: int
    status: str
    error: str | None
    pricing_version: str
    created_at: str
    checkout_kind: Literal['billing_auth', 'payment']
    customer_key: str | None
    expires_at: str
    payment: PaymentSnapshot | None = None
    service_order_id: str | None = None
    service_quote_id: str | None = None
    service_code: str | None = None
    service_name: str | None = None


class Entitlements(ContractModel):
    seats: int
    active_subscription: bool
    production_export: bool
    retention_until: str | None
    team_access: bool


class PaymentCapabilities(ContractModel):
    provider: str
    test_mode: bool
    checkout_available: bool
    live_enabled: bool
    mock_available: bool
    billing_auth_available: bool
    client_key: str | None
    message: str | None


class PlanPrice(ContractModel):
    id: str
    name: str
    monthly_ex_vat: int
    monthly_inc_vat: int
    credits: int
    seats: int


class TopupPrice(ContractModel):
    credits: int
    ex_vat: int
    inc_vat: int
    expires_months: int


class TrialPolicy(ContractModel):
    credits: int
    expires_days: int
    production_export: bool
    auto_conversion: bool


class PricingPolicy(ContractModel):
    version: str
    currency: str
    live_billing_enabled: bool
    plans: list[PlanPrice]
    topups: list[TopupPrice]
    trial: TrialPolicy
    actions: dict[str, int]


class BillingData(ContractModel):
    policy: PricingPolicy
    current_pricing_version: str
    agreed_plan_pricing_version: str | None
    summary: WalletSummary
    subscription: SubscriptionData | None
    orders: list[PaymentOrderData]
    entitlements: Entitlements
    payment_capabilities: PaymentCapabilities


class UnchangedPlan(ContractModel):
    change: Literal['unchanged']
    subscription: SubscriptionData


class ScheduledPlan(ContractModel):
    change: Literal['scheduled']
    subscription: SubscriptionData
    effective_at: str


class UpgradePlan(ContractModel):
    change: Literal['payment_required']
    order: PaymentOrderData


PlanChange = Annotated[UnchangedPlan | ScheduledPlan | UpgradePlan, Field(discriminator='change')]


class WebhookResult(ContractModel):
    received: bool
    ignored: bool
    status: str | None
