"""Public print reports: measured facts are distinct from supplier authorization."""
from typing import Literal
from pydantic import Field
from .base import ContractModel
from .geometry import CutContour, CircleHole, TearNotch
from .registry import OutputCapabilities, RegistryApproval
from ..schemas import StructureRef, PouchFeatures
from ..exporters.print_profile import PrintProfile
from ..geometry.finishing import FinishingApproval


class PrintIssue(ContractModel):
    code: str
    message: str
    severity: Literal['warning','error'] | None = None
    scope: Literal['review','production'] | None = None
    face_id: str | None = None
    object_id: str | None = None
    field: str | None = None
    face_ids: list[str] | None = None
    effective_ppi: float | None = None
    original_effective_ppi: float | None = None
    minimum_ppi: float | None = None
    actual_pt: float | None = None
    clearance_mm: float | None = None
    edges: list[Literal['left','right','top','bottom']] | None = None


class PublicPrintProfile(ContractModel):
    id: str
    name: str
    kind: Literal['review_policy','supplier_public_reference']
    scope: str
    bleed_mm: float | None
    safe_mm: float | None
    min_ppi: float | None
    font_mode: str
    color_space: str | None
    pdf_min_version: str | None
    sources: list[str]
    retrieved_at: str
    manufacturer_approved: Literal[False]
    production_authorization: Literal[False]
    recommended_font_pt: float | None = None
    recommended_line_pt: float | None = None
    recommended_ppi_max: float | None = None
    fold_safe_mm: float | None = None
    requires_original_dieline: bool | None = None
    requires_acrobat_layers: bool | None = None


class QualityMeasurement(ContractModel):
    kind: Literal['effective_ppi','original_effective_ppi','trim_clearance']
    face_id: str
    object_id: str
    passed: bool
    value: float | None = None
    minimum: float | None = None
    value_mm: float | None = None
    minimum_mm: float | None = None


class BasicOutput(ContractModel):
    pdf_version: str
    bleed_mm: float
    trim_size_preserved: bool
    color_space: Literal['RGB']
    font_mode: Literal['embedded']


class BasicReview(ContractModel):
    profile: PublicPrintProfile
    quality_status: Literal['pass','warning']
    issues: list[PrintIssue]
    measurements: list[QualityMeasurement]
    output: BasicOutput
    manufacturing_approval: Literal[False]
    physical_barcode_grade_verified: Literal[False]


class PrintCapabilities(PrintProfile):
    vector_barcode: Literal[True]
    outlined_fonts: Literal[True]
    cmyk: Literal[True]
    icc_conversion: Literal['LittleCMS2']
    manufacturer_approval: Literal[False]


class PreflightReport(ContractModel):
    schema_version: Literal['1.0']
    status: Literal['pass','blocked']
    review_allowed: bool
    production_allowed: bool
    issues: list[PrintIssue]
    basic_review: BasicReview | None
    capabilities: OutputCapabilities | PrintCapabilities
    geometry_hash: str | None
    faces: list[str]
    revision_id: str | None
    id: str | None = None
    kind: Literal['review','production'] | None = None
    blockers: list[PrintIssue] | None = None
    warnings: list[PrintIssue] | None = None
    checks: list[PrintIssue] | None = None
    manufacturing_gates: list[PrintIssue] | None = None


class FontInfo(ContractModel):
    id: str
    sha256: str
    embedded: bool
    weight: int | None = Field(default=None,ge=1,le=1000)
    license: str | None = None
    outlined: bool | None = None
    font_asset_id: str | None = None
    family: str | None = None
    license_name: str | None = None


class OriginalText(ContractModel):
    face_id: str
    object_id: str
    text: str
    rendered_lines: list[str] | None = None
    visible: bool | None = None
    print_enabled: bool | None = None


class ReviewPage(ContractModel):
    face_id: str
    width_mm: float
    height_mm: float
    bleed_mm: float | None = None
    role: Literal['face_review','assembly_reference'] | None = None
    media_width_mm: float | None = None
    media_height_mm: float | None = None


class PageBoxCheck(ContractModel):
    face_id: str
    role: str
    boxes_mm: dict[str,list[float]]
    tolerance_mm: float
    passed: bool


class BarcodeCheck(ContractModel):
    face_id: str
    object_id: str
    value: str
    digital_decode: Literal['passed']
    raster_dpi: int
    tool: str | None = None
    barcode_usage: Literal['retail','sample'] | None = None
    physical_print_scan: str | None = None
    physical_scan: Literal['not_tested'] | None = None


class ReviewVerification(ContractModel):
    page_boxes: list[PageBoxCheck]
    used_fonts_embedded: list[str]
    barcode_checks: list[BarcodeCheck]
    pdf_version: str
    manufacturer_approval: Literal[False]


class ReviewStructureFace(ContractModel):
    face_id: str
    cut_contour: CutContour | None
    holes: list[CircleHole]
    tear_notches: list[TearNotch]


class ReviewStructure(ContractModel):
    manufacturer_approved: Literal[False]
    cut_out_clipping: bool
    pouch_features: PouchFeatures | None
    faces: list[ReviewStructureFace]


class ReviewCapabilities(ContractModel):
    vector_text: bool
    embedded_fonts: bool
    original_raster_assets: bool
    pdf_x: Literal[False]
    cmyk: Literal[False]
    spot_colors: Literal[False]
    production: Literal[False]


class ReviewManifest(ContractModel):
    schema_version: Literal['1.0']
    kind: Literal['review']
    review_only: Literal[True]
    production_enabled: Literal[False]
    approval_status: str
    template_version_id: str
    project_id: str
    revision: int | None
    revision_id: str | None
    review_profile_id: str | None = None
    basic_preflight: BasicReview | None = None
    pdf_verification: ReviewVerification | None = None
    geometry_hash: str
    structure_ref: StructureRef | None = None
    generated_at: str
    sha256: str
    font: FontInfo
    # Older completed PDFs predate multi-weight/finishing metadata. The HTTP
    # routes exclude_unset recursively, preserving their exact stored manifest.
    font_weights: list[FontInfo] = Field(default_factory=list)
    review_structure: ReviewStructure | None = None
    pages: list[ReviewPage]
    warnings: list[PrintIssue]
    original_texts: list[OriginalText]
    capabilities: ReviewCapabilities


class BundleFile(ContractModel):
    name: str
    bytes: int
    sha256: str


class ProductionManifest(ContractModel):
    schema_version: Literal['1.0']
    kind: Literal['production']
    adapter: Literal['rgb-face-pages-v1']
    generated_at: str
    project_id: str
    revision_id: str
    geometry_hash: str
    template_id: str
    profile_id: str
    font: FontInfo
    font_weights: list[FontInfo]
    capabilities: OutputCapabilities
    files: list[BundleFile]
    manifest_hash_policy: str


class ICCInfo(ContractModel):
    sha256: str
    byte_size: int
    description: str
    copyright: str
    color_space: Literal['CMYK']
    device_class: Literal['prtr']


class ImageTransform(ContractModel):
    asset_id: str
    source_sha256: str
    input_profile: str
    output_profile_sha256: str
    output_mode: Literal['CMYK']
    pixels: list[int]
    maximum_ink_percent: float


class PrintPageCheck(ContractModel):
    role: Literal['artwork','cut','fold','process','combined']
    page: int
    boxes_mm: dict[str,list[float]]
    tolerance_mm: float
    icc_cmyk: Literal[True]
    text_outlined: Literal[True]


class FinishingPathCheck(ContractModel):
    role: Literal['cut','fold','process']
    face_id: str
    line_count: int
    cubic_count: int
    tolerance_mm: float
    passed: Literal[True]


class FinishingVerification(ContractModel):
    path_checks: list[FinishingPathCheck]
    cut_duplicate_check: Literal['passed']
    physical_tooling_tested: Literal[False]
    cubic_tolerance_mm: float


class FinishingHole(ContractModel):
    face_id: str
    center_x_mm: float
    center_y_mm: float
    diameter_mm: float


class FinishingNotch(ContractModel):
    face_id: str
    side: Literal['left','right']
    shape: Literal['round','v']
    depth_mm: float
    height_mm: float
    edge_endpoints_mm: list[float]


class FinishingProcess(ContractModel):
    face_id: str
    kind: Literal['header_reference','zipper_band','tear_reference','zipper_center','seal_region']
    polygon_mm: list[float] | None = None
    line_mm: list[float] | None = None


class FinishingPage(ContractModel):
    face_id: str
    width_mm: float
    height_mm: float
    cut: list[list[float]]
    fold: list[list[float]]
    cut_curves: list[list[float]]
    holes: list[FinishingHole]
    notches: list[FinishingNotch]
    process: list[FinishingProcess]


class FinishingManifest(ContractModel):
    schema_version: Literal['1.0']
    delivery: Literal['separate_process_pdf_v1']
    physical_specification: FinishingApproval
    physical_specification_hash: str
    coordinate_system: Literal['top-left-mm']
    cut_file: Literal['cut.pdf']
    process_file: Literal['process.pdf']
    fold_file: Literal['fold.pdf']
    artwork_knockouts: Literal[True]
    outer_bleed_preserved: Literal[True]
    cubic_tolerance_mm: float
    tear_line_role: Literal['reference_only_not_perforation']
    manufacturer_approval_inferred: Literal[False]
    pages: list[FinishingPage]


class CombinedFileCheck(ContractModel):
    file: Literal['artwork-with-dieline.pdf']
    layers: list[str]
    spot_colors: list[str]
    segments_matched: int
    tolerance_mm: float
    authoritative: Literal[False]


class PrintVerification(ContractModel):
    page_checks: list[PrintPageCheck]
    barcodes: list[BarcodeCheck]
    pdf_x: Literal['not_claimed']
    manufacturer_approval: Literal[False]
    finishing: FinishingVerification | None = None
    combined_file: CombinedFileCheck | None = None


class CombinedFileInfo(ContractModel):
    name: Literal['artwork-with-dieline.pdf']
    layers: list[str]
    dieline_spot_colors: dict[str,str]
    authoritative: Literal[False]
    note: str


class PrintEngineVersion(ContractModel):
    littlecms: str


class PrintEngineManifest(ContractModel):
    schema_version: Literal['2.0']
    adapter: Literal['icc-cmyk-outline-v1']
    kind: Literal['print_engine_test','print_request','production']
    review_only: bool
    manufacturer_approval: Literal[False]
    pdf_x_conformance: Literal['not_claimed']
    profile: PrintProfile
    combined_file: CombinedFileInfo | None = None
    geometry_hash: str
    structure_ref: StructureRef | None
    icc: ICCInfo
    engine: PrintEngineVersion
    fonts: list[FontInfo]
    original_texts: list[OriginalText]
    images: list[ImageTransform]
    issues: list[PrintIssue]
    verification: PrintVerification
    files: list[BundleFile]
    project_id: str | None = None
    revision_id: str | None = None
    template_id: str | None = None
    profile_id: str | None = None
    generated_at: str | None = None
    approval_evidence: dict[str,RegistryApproval] | None = None
    capabilities: PrintCapabilities | None = None
    manifest_hash_policy: str | None = None
    finishing: FinishingManifest | None = None


class PrintEngineResult(ContractModel):
    format: Literal['print_engine_zip','print_request_zip']
    media_type: Literal['application/zip']
    filename: str
    sha256: str
    manifest: PrintEngineManifest
    review_only: Literal[True]
    credits_charged: Literal[0]
