from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, JsonValue, StringConstraints
from typing import Annotated
from ..contracts.base import ContractModel
from ..contracts.scene import StoredScene

Kind = Literal["tenant", "project", "asset", "export"]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1000)]


class TargetBody(ContractModel):
    target_kind: Kind
    target_id: UUID
    reason: Reason


class AdminTargetBody(TargetBody):
    tenant_id: UUID


class HoldBody(AdminTargetBody):
    reason_code: Literal["dispute", "manual", "support"]


class VersionReason(ContractModel):
    revision: int = Field(ge=1)
    reason: Reason


class ReviewBody(VersionReason):
    decision: Literal["approved", "rejected"]


class HoldDTO(ContractModel):
    id: str
    tenant_id: str
    target_kind: Kind
    target_id: str
    reason_code: str
    reason: str
    revision: int
    created_at: datetime
    released_at: datetime | None
    release_reason: str | None


class NoticeDTO(ContractModel):
    id: str
    due_at: datetime
    stage_days: int
    status: Literal["unread", "read", "superseded"]
    created_at: datetime
    download_path: str = "/app/projects"
    channel: Literal["in_app"] = "in_app"


class RequestDTO(ContractModel):
    id: str
    tenant_id: str
    target_kind: Kind
    target_id: str
    reason: str
    status: Literal["requested", "approved", "rejected", "canceled", "executing", "executed", "attention_required"]
    review_reason: str | None
    revision: int
    created_at: datetime
    updated_at: datetime
    blockers: list[str]
    execution_supported: bool
    due_at: datetime | None
    executed_at: datetime | None
    cancelable: bool
    planned_bytes: int | None


class Capabilities(ContractModel):
    request: Literal[True] = True
    customer_data_execution: bool
    executable_scopes: list[Literal["asset", "export"]]
    grace_days: Literal[7] = 7
    known_orphan_execution: bool


class RetentionDTO(ContractModel):
    policy_version: str
    protected_until: datetime | None
    state: Literal["active", "protected", "expired_retained", "unspecified"]
    automatic_customer_deletion_enabled: Literal[False] = False
    holds: list[HoldDTO]
    notices: list[NoticeDTO]
    deletion_capabilities: Capabilities


class RequestsDTO(ContractModel):
    items: list[RequestDTO]
    next_offset: int | None


class NoticesDTO(ContractModel):
    items: list[NoticeDTO]
    next_offset: int | None


class SupportDTO(ContractModel):
    id: str
    tenant_id: str
    target_kind: Kind
    target_id: str
    reason: str
    actor_id: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    read_only: Literal[True] = True


class SupportProjectDTO(ContractModel):
    id: str
    name: str
    base_revision: int
    scene: StoredScene
    structure_snapshot: dict[str, JsonValue] | None


class BackupDTO(ContractModel):
    id: str
    state: str
    reason: str
    created_at: datetime
    finished_at: datetime | None
    verified: bool
    object_count: int
    manifest_hash: str | None


class BackupResolveBody(ContractModel):
    reason: Reason


class BackupAbandonBody(BackupResolveBody):
    process_stopped: bool = False


class GcDTO(ContractModel):
    id: str
    job_id: str
    tenant_id: str
    status: str
    blocker: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    byte_size: int
    attempts: int


class AuditDTO(ContractModel):
    id: str
    actor_id: str | None
    action: str
    entity_id: str | None
    created_at: datetime
    details: dict[str, JsonValue]


class AuditListDTO(ContractModel):
    items: list[AuditDTO]
    next_offset: int | None


class OperationsDTO(ContractModel):
    requests: list[RequestDTO]
    holds: list[HoldDTO]
    support_sessions: list[SupportDTO]
    backups: list[BackupDTO]
    gc_candidates: list[GcDTO]
    recent_audit: list[AuditDTO]
    known_orphan_execution: bool
    customer_data_execution: bool
