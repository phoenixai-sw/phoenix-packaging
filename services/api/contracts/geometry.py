"""Public resolved geometry, distinct from editable scene and supplier inputs."""
from typing import Literal
from .base import ContractModel
from ..schemas import Hole, PouchFeatures, StructureRef
from ..geometry.definitions import StructureDefinitionV2, Semantics


class Rect(ContractModel):
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float


class Margins(ContractModel):
    left: float
    right: float
    top: float
    bottom: float


class Line(ContractModel):
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float


class NetPosition(ContractModel):
    x_mm: float
    y_mm: float
    rotation_deg: float = 0


class Assembly(ContractModel):
    position_mm: tuple[float, float, float]
    rotation_deg: tuple[float, float, float]
    uv_rotation_deg: float
    mirror_u: bool
    mirror_v: bool


class CircleHole(ContractModel):
    id: str | None
    kind: Literal['circle']
    center_x_mm: float
    center_y_mm: float
    radius_mm: float
    closed: bool


class NoPrintRegion(Rect):
    kind: str | None = None
    hole_id: str | None = None


class TearNotch(ContractModel):
    side: Literal['left', 'right']
    shape: Literal['round', 'v']
    points_mm: list[tuple[float, float]]


class CutContour(ContractModel):
    points_mm: list[tuple[float, float]]
    closed: bool


class Zipper(ContractModel):
    line: Line
    band: Rect


class Regions(ContractModel):
    cut: Rect
    safe: Rect
    bleed: Rect
    seal: list[Rect]
    fold: list[Line]
    hole: list[CircleHole]
    no_print: list[NoPrintRegion]
    top_closure: Rect | None = None
    hole_allowed: Rect | None = None
    header: Rect | None = None
    zipper: Zipper | None = None
    tear_line: Line | None = None
    tear_notches: list[TearNotch] | None = None
    cut_contour: CutContour | None = None
    structural_guards: list[NoPrintRegion] | None = None


class GeometryFace(ContractModel):
    id: str
    name: str
    width_mm: float
    height_mm: float
    regions: Regions
    net: NetPosition | None = None
    assembly: Assembly | None = None
    registered_structure: bool = False


class StructuralPart(Rect):
    id: str
    kind: str
    print_enabled: bool


class Dimensions(ContractModel):
    width_mm: float
    height_mm: float
    bottom_mm: float | None = None
    depth_mm: float | None = None


class Geometry(Dimensions):
    template_version_id: str
    geometry_hash: str
    approval_status: str
    production_enabled: bool
    label: str
    unit: Literal['mm']
    faces: list[GeometryFace]
    template_id: str | None = None
    geometry_template_id: str | None = None
    seals_mm: Margins | None = None
    safe_margin_mm: float | None = None
    bleed_mm: float | None = None
    bottom_half_mm: float | None = None
    structural_parts: list[StructuralPart] | None = None
    fold_lines: list[Line] | None = None
    assumptions: list[str] | None = None
    net_width_mm: float | None = None
    net_height_mm: float | None = None
    holes: list[Hole] | None = None
    pouch_features: PouchFeatures | None = None
    dimension_semantics: Semantics | None = None


class StructureSnapshot(ContractModel):
    snapshot_version: str
    template_version_id: str
    engine_version: str
    definition: StructureDefinitionV2
    definition_hash: str
    normalized_inputs: Dimensions
    input_hash: str
    geometry_hash: str
    physical_geometry_hash: str
    geometry: Geometry


class BarcodeBar(ContractModel):
    x_mm: float
    width_mm: float


class BarcodePlacement(ContractModel):
    x_mm: float
    y_mm: float
    mode: Literal['manual', 'automatic']


class BarcodeGeometry(ContractModel):
    value: str
    symbology: Literal['EAN13']
    bars: list[BarcodeBar]
    quiet_left_mm: float
    quiet_right_mm: float
    width_mm: float
    height_mm: float
    bar_height_mm: float
    module_mm: float
    barcode_usage: Literal['retail', 'sample']
    barcode_owned: bool | None = None
    sample_label: str | None = None
    foreground: str
    background: str
    notice: str
    placement: BarcodePlacement | None = None


class LayoutIssue(ContractModel):
    code: str
    message: str
    field: str | None = None
    face_id: str | None = None
    object_id: str | None = None


class StructurePreview(ContractModel):
    geometry: Geometry
    structure_ref: StructureRef
    review_only: bool
    production_enabled: bool
    layout_issues: list[LayoutIssue]
    layout_checked: bool
    can_apply: bool | None


class StructureValidation(ContractModel):
    normalized_definition: StructureDefinitionV2
    geometry: Geometry
    definition_hash: str
    review_only: bool
    production_enabled: bool


class RegisteredStructure(ContractModel):
    id: str
    name: str
    manufacturer: str
    status: str
    is_demo: bool
    family: str
    recipe_id: str
    definition: StructureDefinitionV2
    review_only: bool
    production_enabled: bool
