"""Durable job results are discriminated by kind; private storage keys are absent."""
from typing import Annotated, Literal
from pydantic import Field
from .base import ContractModel
from .images import AIGenerationResult, ImageSettings
from .printing import ReviewManifest, ProductionManifest, PrintEngineManifest


class ExportAvailability(ContractModel):
    status: Literal['unchecked', 'available', 'suspect', 'temporarily_unverified', 'unavailable', 'compensated', 'deleted']
    reason: str | None
    checked_at: str | None
    next_check_at: str | None
    confirmed_at: str | None
    retry_allowed: bool
    credit_restored: int
    message: str


class IntakeSourceSummary(ContractModel):
    status: Literal['submitted', 'accepted', 'rejected'] | None
    last_recorded_at: str | None
    technical_rejected: bool
    record_count: int


class ExportCurrentIntake(ContractModel):
    manufacturer: IntakeSourceSummary
    test: IntakeSourceSummary
    unclassified_count: int
    total_count: int


class PrinterIntakeHistoryItem(ContractModel):
    id: str
    status: Literal['submitted', 'accepted', 'rejected']
    category: str
    manufacturer: str
    notes: str
    created_at: str
    record_source: Literal['manufacturer', 'test', 'legacy']
    rejection_kind: Literal['technical', 'aesthetic'] | None
    verification: Literal['self_reported', 'test_record', 'unclassified']
    evidence_attached: bool


class PrinterIntakeHistory(ContractModel):
    items: list[PrinterIntakeHistoryItem]
    next_cursor: str | None
    total: int


class JobBase(ContractModel):
    id: str
    project_id: str
    status: str
    created_at: str
    updated_at: str
    error: str | None
    download_url: str | None
    credit_reserved: int
    credit_charged: int
    credit_returned: int
    cancelable: bool
    availability: ExportAvailability | None = None


class AIGenerationJob(JobBase):
    kind: Literal['ai_generation']
    image_settings: ImageSettings
    result: AIGenerationResult | None


class ReviewExportResult(ContractModel):
    manifest: ReviewManifest
    review_only: Literal[True]
    credits_charged: int


class PrintEngineResult(ContractModel):
    format: Literal['print_engine_zip','print_request_zip']
    media_type: Literal['application/zip']
    filename: str
    sha256: str
    manifest: PrintEngineManifest
    review_only: Literal[True]
    credits_charged: int


class ReviewExportJob(JobBase):
    kind: Literal['review_export']
    result: ReviewExportResult | PrintEngineResult | None
    current_intake: ExportCurrentIntake | None = None


class ProductionExportResult(ContractModel):
    media_type: Literal['application/zip']
    filename: str
    sha256: str
    manifest: ProductionManifest | PrintEngineManifest
    review_only: Literal[False]
    credits_charged: int
    manufacturer_intake_status: str


class ExportApprovalVersion(ContractModel):
    kind: Literal['template', 'profile']
    id: str | None
    name: str
    status: str
    revoked_at: str | None
    public_reason: str | None


class ExportCurrentApproval(ContractModel):
    status: Literal['approved', 'revoked', 'unavailable']
    checked_at: str
    versions: list[ExportApprovalVersion]


class ProductionExportJob(JobBase):
    kind: Literal['production_export']
    result: ProductionExportResult | None
    current_approval: ExportCurrentApproval | None = None
    current_intake: ExportCurrentIntake | None = None


class EditableExportResult(ContractModel):
    format: Literal['phoenix-editable']
    format_version: str
    revision_number: int
    asset_count: int
    font_count: int
    rights_notice: str
    review_only: Literal[True]
    credits_charged: int
    media_type: Literal['application/zip']
    byte_size: int
    sha256: str


class EditableExportJob(JobBase):
    kind: Literal['editable_export']
    result: EditableExportResult | None


JobData = Annotated[AIGenerationJob | ReviewExportJob | ProductionExportJob | EditableExportJob, Field(discriminator='kind')]
