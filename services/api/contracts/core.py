"""Shared public entities. Optional fields preserve legacy omitted values."""
from typing import Generic, Literal, TypeVar
from .base import ContractModel
from .geometry import Geometry, StructureSnapshot
from .scene import StoredScene

T = TypeVar('T')


class Items(ContractModel, Generic[T]):
    items: list[T]


class ProjectData(ContractModel):
    id: str
    name: str
    product_name: str
    brand_name: str
    description: str
    width_mm: float
    height_mm: float
    bottom_mm: float | None
    depth_mm: float | None
    template_id: str
    template_version_id: str | None
    print_profile_version_id: str | None
    brand_id: str | None
    product_variant_id: str | None
    workspace_id: str | None
    material: str | None
    base_revision: int
    scene: StoredScene
    geometry: Geometry
    structure_snapshot: StructureSnapshot | None
    created_at: str
    updated_at: str
    approval_status: str
    review_only: bool


class RevisionData(ContractModel):
    id: str
    project_id: str
    number: int
    reason: str | None = None
    created_at: str
    scene: StoredScene | None = None


class RevisionsData(Items[RevisionData]):
    next_before_number: int | None
    has_more: bool
    total: int
    current_revision: int


class RevisionReference(ContractModel):
    id: str
    number: int


class RestoredProject(ProjectData):
    restored_from_revision: RevisionReference


class AssetData(ContractModel):
    id: str
    name: str
    content_type: str
    byte_size: int
    width_px: int
    height_px: int
    source: str
    url: str
    model: str | None = None
    requested_quality: str | None = None
    actual_quality: str | None = None
    output_size: str | None = None
    actual_size: str | None = None
    created_at: str | None = None
    demo: bool | None = None
    label: str | None = None
    credits_charged: int | None = None


class AssetsData(Items[AssetData]):
    next_offset: int | None


class SessionUser(ContractModel):
    id: str
    name: str
    email: str
    role: Literal['owner', 'editor', 'viewer']
    is_admin: bool
    email_verified: bool
    auth_provider: Literal['google']


class TenantData(ContractModel):
    id: str
    name: str


class MembershipData(TenantData):
    role: Literal['owner', 'editor', 'viewer']


class SessionData(ContractModel):
    user: SessionUser
    tenant: TenantData
    memberships: list[MembershipData]
    workspace_ids: list[str]
    csrf_token: str


class LeaseHolder(ContractModel):
    name: str
    is_self: bool


class EditorLease(ContractModel):
    status: Literal['active', 'available']
    editable: bool
    holder: LeaseHolder | None
    expires_at: str | None
    heartbeat_seconds: int
    lease_seconds: int
    lease_token: str | None = None


class GoogleChallenge(ContractModel):
    client_id: str
    nonce: str
    csrf_token: str
    expires_at: str


class LogoutData(ContractModel):
    logged_out: bool


class HealthData(ContractModel):
    status: Literal['ok']
    database: Literal['connected']
    environment: str


class UploadTicket(ContractModel):
    id: str
    upload_url: str
    method: Literal['PUT']
    headers: dict[str, str]
    expires_at: str
    max_bytes: int


class JobCount(ContractModel):
    kind: str
    status: str
    count: int


class TimelineEvent(ContractModel):
    id: str
    type: str
    project_id: str
    project_name: str
    revision: int
    status: str
    at: str
    reason: str | None = None


class WorkspaceOverview(ContractModel):
    project_count: int
    job_counts: list[JobCount]
    timeline: list[TimelineEvent]


class WorkerResult(ContractModel):
    processed: int
    breakdown: dict[str, int | Literal['retry_pending']]
