from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator, model_validator

Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,100}$")]
Coordinate = Annotated[float, Field(ge=-2000, le=2000, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class RegisterInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("이름을 입력해 주세요.")
        return value.strip()


class LoginInput(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class EmailInput(StrictModel):
    email: EmailStr


class AuthTokenInput(StrictModel):
    token: str = Field(min_length=32, max_length=200)


class ResetPasswordInput(AuthTokenInput):
    password: str = Field(min_length=10, max_length=128)


class SceneObject(StrictModel):
    id: Identifier
    type: Literal["text", "image", "shape"]
    face_id: Literal["front", "back"]
    x_mm: Coordinate
    y_mm: Coordinate
    width_mm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    height_mm: float = Field(gt=0, le=2000, allow_inf_nan=False)
    rotation_deg: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)
    z_index: int = Field(default=0, ge=-10000, le=10000)
    text: str | None = Field(default=None, max_length=12000)
    font_size_pt: float | None = Field(default=None, ge=4, le=400, allow_inf_nan=False)
    font_id: Literal["NotoSansKR"] | None = None
    color: Color | None = None
    align: Literal["left", "center", "right"] | None = None
    asset_id: UUID | None = None
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

    @field_validator("x_mm", "y_mm", "width_mm", "height_mm", "rotation_deg")
    @classmethod
    def normalize_mm(cls, value):
        return round(value, 4)

    @model_validator(mode="after")
    def validate_kind(self):
        if self.type == "text" and (self.text is None or self.font_size_pt is None or self.font_id is None):
            raise ValueError("텍스트 객체에는 문구, 글꼴과 글자 크기가 필요합니다.")
        if self.type == "image" and self.asset_id is None:
            raise ValueError("이미지 객체에는 업로드한 자산 ID가 필요합니다.")
        return self


class Face(StrictModel):
    id: Literal["front", "back"]
    name: str = Field(min_length=1, max_length=30)
    width_mm: float = Field(ge=60, le=600, allow_inf_nan=False)
    height_mm: float = Field(ge=80, le=800, allow_inf_nan=False)
    background: Color = "#F5F0E6"
    objects: list[SceneObject] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def objects_match_face(self):
        if any(obj.face_id != self.id for obj in self.objects):
            raise ValueError("객체의 면 정보가 일치하지 않습니다.")
        return self


class Scene(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    template_version_id: Literal["three-side-seal-demo-v1"] | None = None
    geometry_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    active_face_id: Literal["front", "back"] = "front"
    faces: list[Face] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def unique_faces_objects(self):
        if {face.id for face in self.faces} != {"front", "back"}:
            raise ValueError("3면 실링 봉투는 앞면과 뒷면이 각각 필요합니다.")
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
    template_id: Literal["three-side-seal"] = "three-side-seal"
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
    kind: Literal["review"] = "review"


class DemoBackgroundInput(StrictModel):
    palette: Literal["forest", "citrus", "berry"] = "forest"
