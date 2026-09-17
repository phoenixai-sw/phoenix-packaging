from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ..database import Base, new_id, utcnow


class RetentionAccount(Base):
    __tablename__ = "retention_accounts"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    protected_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(40), default="retention-v1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetentionHold(Base):
    __tablename__ = "retention_holds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    target_kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(36))
    reason_code: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class DeletionRequest(Base):
    __tablename__ = "deletion_requests"
    __table_args__ = (UniqueConstraint("tenant_id", "operation_key", name="uq_deletion_operation"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    target_kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)
    operation_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="requested", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    blocker: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetentionNotice(Base):
    __tablename__ = "retention_notices"
    __table_args__ = (UniqueConstraint("tenant_id", "event_key", name="uq_retention_notice"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    event_key: Mapped[str] = mapped_column(String(160))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stage_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="unread")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportSession(Base):
    __tablename__ = "support_access_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    login_session_id: Mapped[str] = mapped_column(String(36))
    target_kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MaintenanceGate(Base):
    __tablename__ = "storage_maintenance_gate"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class BackupRun(Base):
    __tablename__ = "storage_backup_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    state: Mapped[str] = mapped_column(String(30), default="pinning", index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    object_count: Mapped[int] = mapped_column(Integer, default=0)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class BackupPin(Base):
    __tablename__ = "storage_backup_pins"
    __table_args__ = (UniqueConstraint("run_id", "storage_key", name="uq_backup_pin"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("storage_backup_runs.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(300), index=True)


class StorageIntent(Base):
    __tablename__ = "storage_write_intents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("ai_units.id"), nullable=True)
    lease_id: Mapped[str] = mapped_column(String(36))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="planned", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GcCandidate(Base):
    __tablename__ = "storage_gc_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    intent_id: Mapped[str] = mapped_column(ForeignKey("storage_write_intents.id"), unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="observed")
    blocker: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
