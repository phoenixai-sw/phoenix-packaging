from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator, model_validator

Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,100}$")]
FaceId = Literal["front", "back", "bottom", "left", "right", "top"]
TemplateKind = Literal["three-side-seal", "stand-up-pouch", "folding-box"]
Coordinate = Annotated[float, Field(ge=-2000, le=2000, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class ImageCrop(StrictModel):
    """Normalized EXIF-oriented source coordinates; placement bbox stays unchanged."""
    x: float = Field(ge=0, lt=1, allow_inf_nan=False, strict=True)
    y: float = Field(ge=0, lt=1, allow_inf_nan=False, strict=True)
    width: float = Field(ge=0.000001, le=1, allow_inf_nan=False, strict=True)
    height: float = Field(ge=0.000001, le=1, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def within_source(self):
        from .image_crop import normalized_crop
        result = normalized_crop(self.model_dump())
        self.width, self.height = result["width"], result["height"]
        return self


class SceneObject(StrictModel):
    id: Identifier
    type: Literal["text", "image", "shape", "barcode"]
    face_id: FaceId
    x_mm: Coordinate
    y_mm: Coordinate
    width_mm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    height_mm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    rotation_deg: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)
    z_index: int = Field(default=0, ge=-10000, le=10000)
    text: str | None = Field(default=None, max_length=12000)
    font_size_pt: float | None = Field(default=None, ge=4, le=400, allow_inf_nan=False)
    font_id: Literal["NotoSansKR"] | None = None
    font_weight: int = Field(default=400, ge=1, le=1000, strict=True)
    font_asset_id: UUID | None = None
    color: Color | None = None
    align: Literal["left", "center", "right"] | None = None
    asset_id: UUID | None = None
    crop: ImageCrop | None = None
    visible: bool = True
    print_enabled: bool = True
    locked: bool = False
    opacity: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    line_height: float | None = Field(default=None, ge=0.5, le=4, allow_inf_nan=False)
    letter_spacing: float | None = Field(default=None, ge=-5, le=30, allow_inf_nan=False)
    binding_key: str | None = Field(default=None, max_length=80)
    fill: Color | None = None
    stroke: Color | None = None
    stroke_width_mm: float | None = Field(default=None, ge=0, le=20, allow_inf_nan=False)
    shape: Literal["rect", "ellipse", "circle"] | None = None

    barcode_value: str | None = Field(default=None, pattern=r"^[0-9]{13}$")
    module_mm: float | None = Field(default=None, ge=0.264, le=0.66)
    bar_height_mm: float | None = Field(default=None, ge=18.28, le=100)
    barcode_owned: bool = False
    barcode_usage: Literal["retail", "sample"] = "retail"

    @field_validator("x_mm", "y_mm", "width_mm", "height_mm", "rotation_deg")
    @classmethod
    def normalize_mm(cls, value):
        return round(value, 4)

    @model_validator(mode="after")
    def validate_kind(self):
        if self.font_asset_id is not None and self.type != "text":
            raise ValueError("업로드 글꼴은 텍스트 객체에서만 사용할 수 있습니다.")
        if self.type == "text" and self.font_asset_id is None and self.font_weight not in (400, 700):
            raise ValueError("기본 글꼴은 400 또는 700 두께를 지원합니다.")
        if self.crop is not None and self.type != "image":
            raise ValueError("자르기는 이미지 객체에서만 사용할 수 있습니다.")
        if self.type == "text" and (self.text is None or self.font_size_pt is None or self.font_id is None):
            raise ValueError("텍스트 객체에는 문구, 글꼴과 글자 크기가 필요합니다.")
        if self.type == "image" and self.asset_id is None:
            raise ValueError("이미지 객체에는 업로드한 자산 ID가 필요합니다.")
        if self.type == "barcode":
            from .geometry.barcodes import validate_ean13
            validate_ean13(self.barcode_value)
            if self.barcode_usage == "sample":
                self.barcode_owned = False
        return self


class Face(StrictModel):
    id: FaceId
    name: str = Field(min_length=1, max_length=30)
    width_mm: float = Field(ge=20, le=800, allow_inf_nan=False)
    height_mm: float = Field(ge=20, le=800, allow_inf_nan=False)
    background: Color = "#F5F0E6"
    objects: list[SceneObject] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def objects_match_face(self):
        if any(obj.face_id != self.id for obj in self.objects):
            raise ValueError("객체의 면 정보가 일치하지 않습니다.")
        return self


class Hole(StrictModel):
    id: Identifier
    face_id: FaceId
    center_x_mm: Coordinate
    center_y_mm: Coordinate
    diameter_mm: float = Field(ge=4, le=10, allow_inf_nan=False)


class PouchFeatures(StrictModel):
    """Optional physical pouch finishing, measured down from the top cut edge."""
    model_config = ConfigDict(extra="forbid", validate_default=True)
    header_height_mm: float = Field(default=30, ge=20, le=100, allow_inf_nan=False)
    zipper_enabled: bool = Field(default=True, strict=True)
    zipper_y_mm: float = Field(default=35, ge=10, le=200, allow_inf_nan=False)
    zipper_band_mm: float = Field(default=6, ge=2, le=15, allow_inf_nan=False)
    tear_enabled: bool = Field(default=True, strict=True)
    tear_y_mm: float = Field(default=24, ge=12, le=100, allow_inf_nan=False)
    notch_depth_mm: float = Field(default=3, ge=1, le=5, allow_inf_nan=False)
    notch_height_mm: float = Field(default=4, ge=2, le=8, allow_inf_nan=False)
    notch_shape: Literal["round", "v"] = "round"

    @field_validator("header_height_mm", "zipper_y_mm", "zipper_band_mm", "tear_y_mm", "notch_depth_mm", "notch_height_mm", mode="before")
    @classmethod
    def normalize_features_mm(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("가공 치수는 숫자로 입력해 주세요.")
        return round(value, 4)


class StructureRef(StrictModel):
    template_version_id: str = Field(min_length=1,max_length=100)
    definition_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    engine_version: Literal["structure-v2.1"]
    geometry_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Scene(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    template_version_id: str | None = Field(default=None,max_length=100)
    template_kind: TemplateKind | None = None
    structure_ref: StructureRef | None = None
    bottom_mm: float | None = Field(default=None,ge=30,le=180)
    depth_mm: float | None = Field(default=None,ge=30,le=300)
    holes: list[Hole] = Field(default_factory=list,max_length=8)
    pouch_features: PouchFeatures | None = None
    confirmed_fields: list[str] = Field(default_factory=list,max_length=30)
    reviewed_face_ids: list[FaceId] = Field(default_factory=list,max_length=6)
    brand_id: UUID | None = None
    product_variant_id: UUID | None = None
    workspace_id: UUID | None = None
    geometry_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    active_face_id: FaceId = "front"
    faces: list[Face] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def unique_faces_objects(self):
        expected = {"three-side-seal":{"front","back"},"stand-up-pouch":{"front","back","bottom"},"folding-box":{"front","back","left","right","top","bottom"}}
        kind=self.template_kind or next((key for key in expected if self.template_version_id==key+"-demo-v1"),"three-side-seal")
        if self.pouch_features is not None and kind == "folding-box":
            raise ValueError("파우치 개봉부·지퍼 가공은 봉투와 스탠드 파우치에서만 사용할 수 있습니다.")
        if len(self.faces)!=len(expected[kind]) or {face.id for face in self.faces}!=expected[kind] or self.active_face_id not in expected[kind]:
            raise ValueError("포장 구조의 모든 면을 각각 한 번씩 포함해 주세요.")
        ids = [obj.id for face in self.faces for obj in face.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("객체 ID는 프로젝트 안에서 고유해야 합니다.")
        return self


class CreateProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    product_name: str = Field(min_length=1, max_length=160)
    brand_name: str = Field(default="", max_length=120)
    width_mm: float = Field(default=160, ge=60, le=600, allow_inf_nan=False)
    height_mm: float = Field(default=230, ge=80, le=800, allow_inf_nan=False)
    template_id: TemplateKind = "three-side-seal"
    bottom_mm: float | None = Field(default=None,ge=30,le=180)
    depth_mm: float | None = Field(default=None,ge=30,le=300)
    brand_id: UUID | None = None
    product_variant_id: UUID | None = None
    workspace_id: UUID | None = None
    description: str = Field(default="", max_length=4000)

    @field_validator("name", "product_name")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("내용을 입력해 주세요.")
        return value.strip()

    @field_validator("width_mm", "height_mm")
    @classmethod
    def normalize_mm(cls, value):
        return round(value, 4)

    @model_validator(mode="after")
    def validate_structure(self):
        from .geometry import build_geometry
        build_geometry(self.template_id,self.width_mm,self.height_mm,bottom_mm=self.bottom_mm,depth_mm=self.depth_mm)
        return self


class SaveDraftInput(StrictModel):
    base_revision: int = Field(ge=1)
    scene: Scene
    name: str | None = Field(default=None, min_length=1, max_length=160)


class RevisionInput(StrictModel):
    base_revision: int = Field(ge=1)
    reason: str = Field(default="manual", max_length=80)


class ExportInput(StrictModel):
    project_id: UUID
    base_revision: int = Field(ge=1)
    kind: Literal["review", "production", "editable"] = "review"
    reviewed_face_ids: list[FaceId] = Field(default_factory=list,max_length=6)
    quote_id: UUID | None = None


class DemoBackgroundInput(StrictModel):
    palette: Literal["forest", "citrus", "berry"] = "forest"
