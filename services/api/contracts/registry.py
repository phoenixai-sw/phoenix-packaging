"""Registry snapshots retain supplier extension keys as JSON-valued data.

Unknown supplier requirements are intentionally returned and fail preflight;
they are not executed or silently discarded by response serialization.
"""
from typing import Literal
from pydantic import Field, JsonValue
from .base import ContractModel
from .business import BarcodeRegistration, VerificationLinks
from ..geometry.definitions import StructureDefinitionV2


class RegistryApproval(ContractModel):
    evidence_asset_id: str
    evidence_sha256: str
    approved_by: str
    approved_by_name: str
    approved_at: str
    source: str
    license: str
    notes: str
    structure_definition_hash: str | None = None


class UnapprovedEvidence(ContractModel):
    """A draft registry version has no approval fields yet."""
    pass


class RegistryVersionData(ContractModel):
    id: str
    kind: Literal['template', 'profile']
    name: str
    manufacturer: str
    status: str
    is_demo: bool
    geometry_template_id: str | None = None
    billing_family_key: str | None = None
    requirements: dict[str, JsonValue] = Field(default_factory=dict, description='Supplier-declared requirements; unsupported keys remain visible and fail preflight.')
    approved_dimensions: dict[str, float | None] = Field(default_factory=dict)
    source: str = ''
    license: str = ''
    material: str = ''
    structure_definition: StructureDefinitionV2 | None = None
    structure_definition_hash: str | None = None
    review_available: bool = False
    approval: RegistryApproval | UnapprovedEvidence | None


class MissingRegistryVersion(ContractModel):
    id: str | None
    status: Literal['unapproved']
    is_demo: Literal[True]


class DemoTemplate(ContractModel):
    id: str
    name: str
    version: str
    status: Literal['demo']
    approval_status: str
    review_only: bool
    description: str
    faces: list[str]
    default_width_mm: float
    default_height_mm: float
    min_width_mm: float
    max_width_mm: float
    min_height_mm: float
    max_height_mm: float
    seal_mm: float
    safe_mm: float
    bleed_mm: float


class PreparationCheck(ContractModel):
    key: str
    label: str
    status: Literal['pass', 'needs_input', 'blocked']
    detail: str


class BarcodePreparation(ContractModel):
    variant_id: str | None
    code: str
    registration: BarcodeRegistration | None
    scene_values: list[str]
    checks: list[PreparationCheck]
    verification_links: VerificationLinks


class ManufacturingPreparation(ContractModel):
    template: RegistryVersionData | MissingRegistryVersion
    profile: RegistryVersionData | MissingRegistryVersion
    material: str | None
    dimensions: dict[str, float]
    checks: list[PreparationCheck]
    admin_url: str


class OutputCapabilities(ContractModel):
    pdf_standard: str
    color_space: str
    font_mode: str
    layout: str
    bleed_mm: float
    vector_text: bool
    vector_barcode: bool
    pdf_x: bool
    cmyk: bool
    spot_colors: bool
    white_ink: bool
    overprint: bool
    outlined_fonts: bool


class PrintPreparation(ContractModel):
    base_revision: int
    barcode: BarcodePreparation
    manufacturing: ManufacturingPreparation
    capabilities: OutputCapabilities
    notice: str


class ReadinessItem(ContractModel):
    key: str
    label: str
    ready: bool
    detail: str


class AdminCounts(ContractModel):
    users: int
    projects: int
    jobs: int


class AuditEntry(ContractModel):
    id: str
    action: str
    entity_id: str | None
    details: dict[str, JsonValue] = Field(description='Action-specific immutable audit data; never ORM records or secrets.')
    created_at: str


class ProviderCost(ContractModel):
    provider: str
    model: str
    attempts: int
    cost_usd: float | None
    estimated: bool


class BudgetAlert(ContractModel):
    code: str
    severity: Literal['warning', 'error']
    message: str


class ProviderBudget(ContractModel):
    provider: str
    enforced: bool
    timezone: str
    date: str
    day_limit_usd: float
    per_request_allowance_usd: float
    day_guard_usd: float
    day_recorded_cost_usd: float
    day_unknown_attempts: int
    day_attempts: int
    month_guard_usd: float
    month_alert_usd: float
    estimate_only: bool
    alerts: list[BudgetAlert]


class IntakeStatistic(ContractModel):
    status: str
    category: str
    count: int


class IntakeMetricGroup(ContractModel):
    total_jobs: int
    responded_jobs: int
    pending_jobs: int
    technical_pass_jobs: int
    technical_rejected_jobs: int
    aesthetic_change_jobs: int
    technical_acceptance_rate: float | None


class IntakeMetrics(ContractModel):
    policy_version: str
    unit: str
    denominator: str
    verification: str
    excluded_legacy_records: int
    excluded_unlinked_records: int
    manufacturer: IntakeMetricGroup
    test: IntakeMetricGroup


class AdminOverview(ContractModel):
    readiness: list[ReadinessItem]
    counts: AdminCounts
    audit: list[AuditEntry]
    provider_costs: list[ProviderCost]
    provider_budget: ProviderBudget
    intake_stats: list[IntakeStatistic]
    intake_metrics: IntakeMetrics


class EvidenceData(ContractModel):
    id: str
    name: str
    sha256: str


class PrinterIntakeData(ContractModel):
    id: str
    status: Literal['submitted', 'accepted', 'rejected']
    record_source: Literal['manufacturer', 'test']
    rejection_kind: Literal['technical', 'aesthetic'] | None
    verification: Literal['self_reported', 'test_record']
