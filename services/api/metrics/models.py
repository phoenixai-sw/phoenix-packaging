from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base, new_id, utcnow


class MetricEvent(Base):
    __tablename__ = "metric_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_key: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("project_revisions.id"), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(100))
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Acquisition(Base):
    __tablename__ = "metric_acquisitions"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(30))
    utm_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(80), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActivitySlice(Base):
    __tablename__ = "metric_activity_slices"
    __table_args__ = (UniqueConstraint("project_id", "time_bucket", name="uq_metric_activity_bucket"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(String(36))
    lease_id: Mapped[str] = mapped_column(String(36))
    time_bucket: Mapped[int] = mapped_column(Integer)
    seconds: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CostEntry(Base):
    __tablename__ = "metric_cost_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation_key: Mapped[str] = mapped_column(String(160), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(30))
    basis: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    support_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("metric_cost_entries.id"), nullable=True, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _immutable(*_):
    raise ValueError("Operational history is append-only; add a reasoned correction")


for model in (MetricEvent, Acquisition, ActivitySlice, CostEntry):
    event.listen(model, "before_update", _immutable)
    event.listen(model, "before_delete", _immutable)
