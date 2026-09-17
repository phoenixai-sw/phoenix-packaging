"""Bounded registered structure data. A valid definition is never an approval.

Recipes are identifiers of audited Python functions, not executable expressions.
V2 deliberately supports rectangular panels/parts and orthogonal net placement.
Unsupported curves, arbitrary SVG and material compensation formulas fail closed.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, BeforeValidator, TypeAdapter, model_validator
from .validation import GeometryValidationError, normalize_mm

Family = Literal["three-side-seal", "stand-up-pouch", "folding-box"]
FaceId = Literal["front", "back", "left", "right", "top", "bottom"]
FACE_SETS = {"three-side-seal": {"front", "back"}, "stand-up-pouch": {"front", "back", "bottom"},
             "folding-box": {"front", "back", "left", "right", "top", "bottom"}}


def _finite_mm(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("치수는 유한한 숫자여야 합니다.")
    return normalize_mm(value, "mm")


MM = Annotated[float, BeforeValidator(_finite_mm), Field(ge=0, le=2000)]
PositiveMM = Annotated[float, BeforeValidator(_finite_mm), Field(gt=0, le=2000)]
SignedMM = Annotated[float, BeforeValidator(_finite_mm), Field(ge=-2000, le=2000)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Dimensions(Strict):
    width_mm: Annotated[PositiveMM, Field(ge=60, le=600)]
    height_mm: Annotated[PositiveMM, Field(ge=80, le=800)]
    bottom_mm: Annotated[PositiveMM, Field(ge=30, le=180)] | None = None
    depth_mm: Annotated[PositiveMM, Field(ge=30, le=300)] | None = None


class Semantics(Strict):
    basis: Literal["finished_outer", "panel", "inner", "outer"]
    bottom_definition: Literal["expanded_gusset"] | None = None
    material_thickness_mm: Annotated[MM, Field(le=20)] | None = None
    glue_allowance_mm: Annotated[MM, Field(le=100)] | None = None
    # Numeric fixed panels are authoritative; this metadata never triggers a formula.
    compensation: Literal["none", "included_in_fixed_panels"] = "none"


class Rect(Strict):
    x_mm: MM
    y_mm: MM
    width_mm: PositiveMM
    height_mm: PositiveMM


class Margins(Strict):
    left: Annotated[MM, Field(le=100)] = 5
    right: Annotated[MM, Field(le=100)] = 5
    top: Annotated[MM, Field(le=100)] = 5
    bottom: Annotated[MM, Field(le=100)] = 5


class NetPlacement(Strict):
    x_mm: MM
    y_mm: MM
    rotation_deg: Literal[0, 180] = 0


class Assembly(Strict):
    position_mm: tuple[SignedMM, SignedMM, SignedMM]
    rotation_deg: tuple[Literal[-180, -90, 0, 90, 180], Literal[-180, -90, 0, 90, 180], Literal[-180, -90, 0, 90, 180]]
    uv_rotation_deg: Literal[0, 90, 180, 270] = 0
    mirror_u: bool = False
    mirror_v: bool = False


class Line(Strict):
    x1_mm: MM
    y1_mm: MM
    x2_mm: MM
    y2_mm: MM

    @model_validator(mode="after")
    def nonzero(self):
        if self.x1_mm == self.x2_mm and self.y1_mm == self.y2_mm:
            raise ValueError("접힘선 길이는 0보다 커야 합니다.")
        return self


class Panel(Strict):
    id: FaceId
    width_mm: Annotated[PositiveMM, Field(ge=20, le=800)]
    height_mm: Annotated[PositiveMM, Field(ge=20, le=800)]
    net: NetPlacement
    assembly: Assembly
    safe_inset_mm: Margins = Field(default_factory=Margins)
    no_print: list[Rect] = Field(default_factory=list, max_length=24)
    fold: list[Line] = Field(default_factory=list, max_length=16)


class Part(Rect):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    kind: Literal["glue_tab", "flap"]
    attached_face_id: FaceId


class DefinitionBase(Strict):
    schema_version: Literal["2.0"] = "2.0"
    family: Family
    dimension_semantics: Semantics
    # First release has no registered processing capability. Existing demo features stay separate.
    feature_policy: Literal["none"] = "none"
    review_bleed_mm: Literal[3] = 3


class FixedDefinition(DefinitionBase):
    recipe_id: Literal["fixed-panel-net-v1"]
    dimensions: Dimensions
    panels: list[Panel] = Field(min_length=2, max_length=6)
    structural_parts: list[Part] = Field(default_factory=list, max_length=24)
    fold_lines: list[Line] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def valid_faces(self):
        if len(self.panels) != len(FACE_SETS[self.family]) or {f.id for f in self.panels} != FACE_SETS[self.family]:
            raise ValueError("구조의 모든 면이 각각 한 번 필요합니다.")
        ids = [part.id for part in self.structural_parts]
        if len(ids) != len(set(ids)) or any(part.attached_face_id not in FACE_SETS[self.family] for part in self.structural_parts):
            raise ValueError("구조 부품 식별자와 연결 면을 확인해 주세요.")
        if self.family == "folding-box":
            if self.dimensions.depth_mm is None or self.dimensions.bottom_mm is not None:
                raise ValueError("상자는 깊이가 필요하며 거싯 폭을 사용할 수 없습니다.")
            if self.dimension_semantics.material_thickness_mm is None or self.dimension_semantics.glue_allowance_mm is None:
                raise ValueError("상자 두께와 접착 여유를 명시해 주세요. 자동 두께 보정은 하지 않습니다.")
            if self.dimension_semantics.basis not in {"inner", "outer", "panel"}:
                raise ValueError("상자 치수는 내경·외경·패널 기준을 명시해 주세요.")
        else:
            if self.dimensions.depth_mm is not None or self.dimension_semantics.basis != "finished_outer":
                raise ValueError("파우치 치수는 실링을 포함한 완성 외곽 기준입니다.")
            if self.family == "stand-up-pouch" and (self.dimensions.bottom_mm is None or self.dimension_semantics.bottom_definition != "expanded_gusset"):
                raise ValueError("스탠드 바닥은 펼친 거싯 폭으로 명시해 주세요.")
            if self.family == "three-side-seal" and self.dimensions.bottom_mm is not None:
                raise ValueError("삼방 실링에는 바닥 거싯이 없습니다.")
        return self


class Range(Strict):
    minimum: PositiveMM
    maximum: PositiveMM

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum > self.maximum:
            raise ValueError("치수 최솟값은 최댓값 이하여야 합니다.")
        return self


class SeparatedDefinition(DefinitionBase):
    recipe_id: Literal["three-side-seal-separated-v1"]
    family: Literal["three-side-seal"] = "three-side-seal"
    width_range_mm: Range
    height_range_mm: Range
    seals_mm: Margins
    safe_margin_mm: Annotated[MM, Field(ge=1, le=20)] = 5
    panel_gap_mm: Annotated[MM, Field(le=100)] = 15
    preview_depth_mm: Annotated[PositiveMM, Field(le=100)] = 6

    @model_validator(mode="after")
    def range_and_semantics(self):
        if self.dimension_semantics.basis != "finished_outer" or self.dimension_semantics.bottom_definition is not None or self.dimension_semantics.compensation != "none":
            raise ValueError("분리 삼방 패널은 완성 외곽 치수만 사용합니다.")
        if not 60 <= self.width_range_mm.minimum <= self.width_range_mm.maximum <= 600 or not 80 <= self.height_range_mm.minimum <= self.height_range_mm.maximum <= 800:
            raise ValueError("등록 범위는 서버의 60~600 × 80~800mm 한도 안이어야 합니다.")
        if self.width_range_mm.minimum <= self.seals_mm.left + self.seals_mm.right + 2*self.safe_margin_mm or self.height_range_mm.minimum <= self.seals_mm.top + self.seals_mm.bottom + 2*self.safe_margin_mm:
            raise ValueError("허용 최솟값에도 실링과 여백을 제외한 안전영역이 있어야 합니다.")
        return self


StructureDefinitionV2 = Annotated[Union[FixedDefinition, SeparatedDefinition], Field(discriminator="recipe_id")]
DEFINITION_ADAPTER = TypeAdapter(StructureDefinitionV2)


def parse_definition(value):
    from pydantic import ValidationError
    try:
        return DEFINITION_ADAPTER.validate_python(value).model_dump(mode="json", exclude_none=True)
    except (ValidationError, GeometryValidationError) as exc:
        raise GeometryValidationError("INVALID_STRUCTURE_DEFINITION", "등록 구조의 레시피·치수·면 매핑을 확인해 주세요.", "structure_definition") from exc
