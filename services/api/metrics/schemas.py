from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from ..contracts.base import ContractModel

Category = Literal["storage", "output", "support", "payment_fee"]
Basis = Literal["actual", "estimate", "unknown"]


class AcquisitionInput(ContractModel):
    channel: Literal["direct", "organic", "paid_search", "paid_social", "referral", "email", "other"] = "direct"
    utm_source: Literal["google", "naver", "kakao", "instagram", "facebook", "youtube", "newsletter", "partner", "direct", "other"] | None = None
    utm_medium: Literal["cpc", "paid_social", "organic", "referral", "email", "direct", "other"] | None = None
    # Store only a SHA-256 grouping key, never raw campaign/query content.
    utm_campaign: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class ActivityInput(ContractModel):
    project_id: UUID


class ActivityDTO(ContractModel):
    recorded: bool
    estimated_seconds_added: int
    measurement: Literal["bounded_browser_activity_estimate"] = "bounded_browser_activity_estimate"


class CostInput(ContractModel):
    tenant_id: UUID | None = None
    project_id: UUID | None = None
    job_id: UUID | None = None
    payment_id: UUID | None = None
    category: Category
    basis: Basis
    amount: Decimal | None = Field(default=None, ge=0, le=1000000000, max_digits=16, decimal_places=6, allow_inf_nan=False)
    currency: Literal["KRW", "USD"]
    support_minutes: int | None = Field(default=None, ge=0, le=100000)
    reason: str = Field(min_length=5, max_length=1000)
    supersedes_id: UUID | None = None
    occurred_at: datetime

    @model_validator(mode="after")
    def consistent_measurement(self):
        if (self.basis=="unknown") != (self.amount is None): raise ValueError("unknown 비용은 amount=null, 실제/추정 비용은 금액이 필요합니다.")
        if self.support_minutes is not None and self.category!="support": raise ValueError("지원 시간은 support 항목만 기록합니다.")
        if self.payment_id and self.category!="payment_fee": raise ValueError("결제 연결은 payment_fee 항목만 사용합니다.")
        if (self.project_id or self.job_id or self.payment_id) and not self.tenant_id: raise ValueError("자료 연결에는 조직 ID가 필요합니다.")
        if self.occurred_at.tzinfo is None: raise ValueError("시각에 UTC 오프셋이 필요합니다.")
        self.reason=self.reason.strip()
        if len(self.reason)<5: raise ValueError("사유를 5자 이상 기록해 주세요.")
        return self


class CostDTO(ContractModel):
    id: str
    tenant_id: str | None
    project_id: str | None
    job_id: str | None
    payment_id: str | None
    category: Category
    basis: Basis
    amount: Decimal | None
    currency: Literal["KRW", "USD"]
    support_minutes: int | None
    reason: str
    supersedes_id: str | None
    occurred_at: datetime
    created_at: datetime


class CountDTO(ContractModel):
    name: str
    count: int
    provider: str | None = None
    record_source: str | None = None


class CostsPage(ContractModel):
    items: list[CostDTO]
    next_offset: int | None


class CohortDTO(ContractModel):
    channel: str
    utm_source: str | None
    utm_medium: str | None
    campaign_hash: str | None
    signups: int
    trial_tenants: int
    first_subscription_customers: int
    conversion_rate: float | None


class TimingDTO(ContractModel):
    project_id: str
    created_at: datetime
    first_production_at: datetime | None
    elapsed_seconds: int | None
    estimated_active_edit_seconds: int
    activity_observed: bool


class ProviderCostDTO(ContractModel):
    attempt_id: str
    tenant_id: str
    job_id: str | None
    model: str
    requested_quality: str | None
    actual_quality: str | None
    output_size: str | None
    provider: str
    status: str
    basis: Literal["actual", "estimate", "unknown", "not_called", "fixture"]
    cost_usd: Decimal | None
    error_code: str | None
    created_at: datetime


class CostTotalDTO(ContractModel):
    category: str
    currency: str
    basis: str
    amount: Decimal | None
    entries: int


class MetricsOverviewDTO(ContractModel):
    start: datetime
    end: datetime
    timezone: Literal["UTC"] = "UTC"
    event_counts: list[CountDTO]
    cohorts: list[CohortDTO]
    payments: dict[str, int]
    timings: list[TimingDTO]
    provider_attempts: list[ProviderCostDTO]
    provider_attempt_count: int
    costs: list[CostDTO]
    totals: list[CostTotalDTO]
    storage_observed_bytes: int
    storage_observed_objects: int
    output_jobs: int
    reported_support_minutes: int
    payment_fees_unknown: int
    missing_cost_categories: list[Category]
    limits: dict[str, int]
    limitations: list[str]
