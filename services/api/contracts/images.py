"""Image operation settings, results and capability contracts."""
from typing import Literal
from .base import ContractModel
from .core import AssetData
from .geometry import Rect, Margins
from ..schemas import ImageCrop

ImageModel = Literal['gpt-image-2.5-sunburst', 'gpt-image-2.5-flare']
Quality = Literal['low', 'medium', 'high', 'xhigh', 'max', 'auto']


class LayoutContext(ContractModel):
    package_kind: str
    face_id: str
    brand_colors: list[str]
    reserved_object_count: int
    quiet_region_source: str


class ImageSettings(ContractModel):
    model: str | None = None
    quality: str | None = None
    requested_quality: str | None = None
    output_size: str | None = None
    output_width_px: int | None = None
    output_height_px: int | None = None
    output_effective_ppi: float | None = None
    output_experimental: bool | None = None
    ai_selection_version: str | None = None
    layout_context: LayoutContext | None = None


class ModelChoice(ContractModel):
    id: ImageModel
    label: str
    description: str
    enabled: bool


class QualityChoice(ContractModel):
    id: Quality
    label: str
    action_tier: Literal['standard', 'high']
    credit_cost: int
    enabled: bool


class ImagePreset(ContractModel):
    size: str
    quality: Quality
    experimental: bool
    enabled: bool | None = None


class ImageDefaults(ContractModel):
    model: ImageModel
    quality: Quality


class AutoQuality(ContractModel):
    selects: Literal['quality']
    changes_model: bool
    credit_cost: int
    actual_quality_may_be_unknown: bool


class ImageSizePolicy(ContractModel):
    version: str
    selection: str
    multiple: int
    min_pixels: int
    max_pixels: int
    max_edge: int
    min_aspect_ratio: float
    max_aspect_ratio: float
    experimental_above_pixels: int
    experimental: bool
    client_size_override: bool
    aspect_ratio_limit_behavior: str
    print_ppi_guaranteed: bool


class RemoveTextCapability(ContractModel):
    enabled: bool
    region_coordinates: str
    max_regions: int
    source_size_preserved: bool
    preservation_scope: str
    inside_region_quality_guaranteed: bool
    ocr_provider_call: bool
    mask_guidance: bool
    implementation: str


class RegionEditCapability(ContractModel):
    enabled: bool
    shapes: list[Literal['rect', 'polygon', 'brush']]
    max_points: int
    coordinates: Literal['source_normalized']
    preservation_scope: Literal['outside_edit_region']
    provider_mask: Literal[True]


class CutoutCapability(ContractModel):
    enabled: bool
    output: Literal['transparent_png_layer']
    print_requires_flatten: Literal[True]


class ImageCapabilities(ContractModel):
    provider: str
    model: str
    generate: bool
    edit: bool
    mask: bool
    standard: ImagePreset
    high: ImagePreset
    models: list[ModelChoice]
    qualities: list[QualityChoice]
    defaults: ImageDefaults
    selection_version: str
    auto_quality: AutoQuality
    size_policy: ImageSizePolicy
    high_edit: bool
    max_units: int
    preservation_guaranteed: bool
    edit_modes: list[Literal['full', 'remove_text', 'region', 'cutout']]
    remove_text: RemoveTextCapability
    region_edit: RegionEditCapability | None = None
    cutout: CutoutCapability | None = None


class PublicConfig(ContractModel):
    demo_mode: bool
    ai_provider: str
    ai_capabilities: ImageCapabilities
    billing_provider: str
    production_export_enabled: bool
    review_export_credits: int
    direct_upload: bool
    upload_max_bytes: int
    supported_upload_types: list[str]
    auth_provider: Literal['google']
    google_login_enabled: bool
    google_client_id: str | None


class QuoteData(ContractModel):
    id: str
    quote_id: str
    action: str
    requested_units: int
    credit_total: int
    unit_cost: int
    balance_before: int
    balance_after: int
    pricing_version: str
    project_id: str | None
    base_revision: int | None
    fingerprint: str | None
    expires_at: str
    input_hash: str
    image_settings: ImageSettings | None = None
    provider_mode: str | None = None


class EditShape(ContractModel):
    type: Literal['rect', 'polygon', 'brush']
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    points: list[list[float]] | None = None
    radius: float | None = None


class GeneratedAsset(ContractModel):
    id: str
    name: str
    source: str
    width_px: int
    height_px: int
    url: str
    model: str | None = None
    requested_quality: str | None = None
    actual_quality: str | None = None
    output_size: str | None = None
    actual_size: str | None = None
    edit_mode: str | None = None
    edit_region: ImageCrop | None = None
    edit_shapes: list[EditShape] | None = None
    has_alpha: bool | None = None
    edit_pixel_box: tuple[int, int, int, int] | None = None
    reference_asset_id: str | None = None
    preservation_scope: str | None = None
    source_size_px: tuple[int, int] | None = None
    outside_pixels_preserved: bool | None = None


class AIUnitResult(ContractModel):
    index: int
    status: str
    asset_id: str | None
    error_code: str | None


class AIGenerationResult(ContractModel):
    assets: list[GeneratedAsset]
    units: list[AIUnitResult]
    credit_reserved: int
    credit_charged: int
    credit_returned: int
    demo: bool


class ImageSource(ContractModel):
    id: str
    sha256: str
    width_px: int
    height_px: int


class PixelDimensions(ContractModel):
    width: int
    height: int


class ImageWarning(ContractModel):
    code: str
    message: str


class ImageQuality(ContractModel):
    source: ImageSource
    placed_mm: Rect
    effective_ppi: float
    original_effective_ppi: float
    crop: ImageCrop | None
    visible_pixels: tuple[float, float]
    resampled: bool
    extended: bool
    bleed_missing_mm: Margins | None
    target_ppi: float
    required_pixels: PixelDimensions
    warnings: list[ImageWarning]
    detail_recovery_claimed: bool


class ImageInspection(ImageQuality):
    base_revision: int
    face_id: str
    object_id: str


class ImageQualityPatch(Rect):
    asset_id: str
    crop: ImageCrop | None = None


class ImageProvenance(ContractModel):
    version: int
    algorithm: str
    detail_recovery_claimed: bool
    resampled: bool
    extended: bool
    native_equivalent_pixels: tuple[float, float]
    parent_pixels: tuple[int, int]
    output_pixels: tuple[int, int]
    source_crop: ImageCrop | None
    source_visible_pixels: tuple[float, float]
    source_effective_ppi: float
    source_original_effective_ppi: float
    target_ppi: float
    bleed_mode: str
    extended_mm: Margins
    extended_pixels: Margins
    source_content_rect_px: tuple[float, float, float, float]
    original_content_rect_px: tuple[float, float, float, float]
    root_source_asset_id: str
    root_source_sha256: str
    original_source_pixels: tuple[int, int]
    parent_asset_id: str
    parent_sha256: str
    parent_placed_mm: Rect
    output_placed_mm: Rect


class ImageMergePatch(ContractModel):
    asset_id: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: float
    z_index: int
    opacity: float


class ImageMergeResult(ContractModel):
    base_revision: int
    face_id: str
    object_ids: list[str]
    keep_object_id: str
    remove_object_ids: list[str]
    patch: ImageMergePatch
    asset: AssetData
    output_pixels: tuple[int, int]
    effective_ppi: float
    background_filled: bool
    credits_charged: int


class ImageQualityPreview(ContractModel):
    base_revision: int
    face_id: str
    object_id: str
    source: ImageSource
    asset: AssetData
    patch: ImageQualityPatch
    provenance: ImageProvenance
    quality: ImageQuality
    credits_charged: int
