"""Typed, versioned print capabilities. Registration is never manufacturing approval."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from ..geometry.validation import GeometryValidationError

ADAPTER_ID = "icc-cmyk-outline-v1"


class PrintProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    adapter_id: Literal["icc-cmyk-outline-v1"] = ADAPTER_ID
    pdf_standard: Literal["PDF"] = "PDF"
    color_space: Literal["CMYK"] = "CMYK"
    font_mode: Literal["outlined"] = "outlined"
    layout: Literal["face_pages", "net"] = "face_pages"
    bleed_mm: float = Field(default=3, ge=0, le=10, strict=True)
    icc_id: str = Field(min_length=1, max_length=100)
    icc_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rendering_intent: Literal["relative_colorimetric", "perceptual"] = "relative_colorimetric"
    black_point_compensation: StrictBool = True
    max_ink_percent: float = Field(default=400, ge=100, le=400, strict=True)
    min_ppi: float = Field(default=300, ge=72, le=2400, strict=True)
    untagged_rgb: Literal["sRGB"] = "sRGB"
    black_policy: Literal["vector_k100"] = "vector_k100"
    transparency: Literal["reject"] = "reject"
    structure_delivery: Literal["separate_pdf"] = "separate_pdf"
    cut_name: Literal["CUT"] = "CUT"
    fold_name: Literal["FOLD"] = "FOLD"
    spot_colors: Literal[False] = False
    white_ink: Literal[False] = False
    overprint: Literal[False] = False
    pdf_x: Literal[False] = False
    required_fields: list[str] = Field(default_factory=lambda: ["product_name", "net_weight", "ingredients", "allergens", "manufacturer", "storage"], min_length=1, max_length=30)


def parse_print_profile(value):
    try:
        return PrintProfile.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        raise GeometryValidationError("UNSUPPORTED_PRINT_PROFILE", "ICC·도련·윤곽선·분리 CUT/FOLD의 지원 조건을 확인해 주세요. PDF/X·별색·화이트·오버프린트는 지원하지 않습니다.", "print_profile") from exc


def capabilities(profile):
    p = parse_print_profile(profile)
    return {**p, "vector_barcode": True, "outlined_fonts": True, "cmyk": True,
            "icc_conversion": "LittleCMS2", "manufacturer_approval": False}
