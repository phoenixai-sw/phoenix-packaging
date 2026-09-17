"""Authoritative demo geometry. No values here are manufacturer specifications."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import re
from typing import Any

DEMO_TEMPLATE_ID = "three-side-seal-demo-v1"
EPSILON_MM = 0.001
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")


class GeometryValidationError(ValueError):
    def __init__(self, code: str, message: str, field: str = "scene") -> None:
        super().__init__(message)
        self.code, self.message, self.field = code, message, field

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "field": self.field}


def _number(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise GeometryValidationError("INVALID_NUMBER", "유한한 숫자를 입력해 주세요.", field)
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise GeometryValidationError("INVALID_NUMBER", "숫자와 단위를 따로 입력해 주세요.", field) from None
    if not number.is_finite():
        raise GeometryValidationError("INVALID_NUMBER", "유한한 숫자를 입력해 주세요.", field)
    return number


def normalize_mm(value: Any, unit: str, field: str = "dimension") -> float:
    """Explicit units only. Decimal conversion avoids binary unit-conversion drift."""
    if unit not in ("mm", "cm"):
        raise GeometryValidationError("INVALID_UNIT", "단위를 mm 또는 cm로 선택해 주세요.", "unit")
    factor = Decimal(10) if unit == "cm" else Decimal(1)
    try:
        return float((_number(value, field) * factor).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise GeometryValidationError("DIMENSION_OUT_OF_RANGE", "치수가 계산 가능한 범위를 벗어났습니다.", field) from None


def _r(x: float, y: float, w: float, h: float) -> dict:
    return {"x_mm": x, "y_mm": y, "width_mm": w, "height_mm": h}


def validate_dimensions(
    width: Any,
    height: Any,
    unit: str,
    seal_mm: Any = 10,
    safe_mm: Any = 5,
    bleed_mm: Any = 3,
    *,
    left_seal_mm: Any | None = None,
    right_seal_mm: Any | None = None,
    bottom_seal_mm: Any | None = None,
    top_seal_mm: Any | None = None,
    template_version_id: str = DEMO_TEMPLATE_ID,
) -> dict:
    if template_version_id != DEMO_TEMPLATE_ID:
        raise GeometryValidationError("UNSUPPORTED_TEMPLATE", "지원하지 않는 템플릿 버전입니다.", "template_version_id")
    w, h = normalize_mm(width, unit, "width"), normalize_mm(height, unit, "height")
    for field, value, lower, upper in (("width", w, 60, 600), ("height", h, 80, 800)):
        if not lower <= value <= upper:
            raise GeometryValidationError("DIMENSION_OUT_OF_RANGE", f"데모 {field} 허용 범위는 {lower}~{upper}mm입니다.", field)
    seals = {}
    for field, value in (("left", left_seal_mm), ("right", right_seal_mm), ("bottom", bottom_seal_mm), ("top", top_seal_mm)):
        seals[field] = normalize_mm(seal_mm if value is None else value, "mm", f"{field}_seal_mm")
        if not 1 <= seals[field] <= 40:
            raise GeometryValidationError("INVALID_SEAL", "데모 실링·후속 열접착 구간은 1~40mm로 입력해 주세요.", f"{field}_seal_mm")
    safe, bleed = normalize_mm(safe_mm, "mm", "safe_mm"), normalize_mm(bleed_mm, "mm", "bleed_mm")
    if not 1 <= safe <= 20:
        raise GeometryValidationError("INVALID_SAFE_MARGIN", "안전 여백은 1~20mm로 입력해 주세요.", "safe_mm")
    if not 0 <= bleed <= 10:
        raise GeometryValidationError("INVALID_BLEED", "데모 블리드는 0~10mm로 입력해 주세요.", "bleed_mm")
    left, right, top, bottom = (seals[k] for k in ("left", "right", "top", "bottom"))
    if w - left - right - safe * 2 <= 0 or h - top - bottom - safe * 2 <= 0:
        raise GeometryValidationError("EMPTY_SAFE_AREA", "실링과 안전 여백을 제외한 인쇄 영역이 없습니다.", "seal_mm")
    normalized = {"width_mm": w, "height_mm": h, "seals_mm": seals, "safe_margin_mm": safe, "bleed_mm": bleed}
    digest = hashlib.sha256(json.dumps({"template_version_id": template_version_id, **normalized}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    regions = {
        "cut": _r(0, 0, w, h), "fold": [], "hole": [],
        "seal": [_r(0, 0, left, h), _r(w - right, 0, right, h), _r(left, h - bottom, w - left - right, bottom)],
        "top_closure": _r(left, 0, w - left - right, top),
        "safe": _r(left + safe, top + safe, w - left - right - 2 * safe, h - top - bottom - 2 * safe),
        "bleed": _r(-bleed, -bleed, w + 2 * bleed, h + 2 * bleed),
    }
    regions["no_print"] = regions["seal"] + [regions["top_closure"]]
    return {"template_version_id": template_version_id, "approval_status": "demo", "production_enabled": False,
            "label": "데모 구조 · 제조사 미승인", "unit": "mm", **normalized, "geometry_hash": digest,
            "faces": [{"id": face, "name": name, "width_mm": w, "height_mm": h, "regions": deepcopy(regions)}
                      for face, name in (("front", "앞면"), ("back", "뒷면"))]}


def _bounded_number(obj: dict, key: str, lower: float, upper: float, prefix: str, default: Any = None) -> float:
    value = _number(obj.get(key) if obj.get(key) is not None else default, f"{prefix}.{key}")
    if not Decimal(str(lower)) <= value <= Decimal(str(upper)):
        raise GeometryValidationError("OBJECT_VALUE_OUT_OF_RANGE", f"{key} 값이 허용 범위를 벗어났습니다.", f"{prefix}.{key}")
    return float(value)


def _check_color(value: Any, field: str) -> None:
    if not isinstance(value, str) or not COLOR_RE.fullmatch(value):
        raise GeometryValidationError("INVALID_COLOR", "색상을 #RRGGBB 형식으로 지정해 주세요.", field)


def _corners(obj: dict) -> list[tuple[float, float]]:
    angle = math.radians(obj["rotation_deg"])
    cosine, sine = math.cos(angle), math.sin(angle)
    return [(obj["x_mm"] + x * cosine - y * sine, obj["y_mm"] + x * sine + y * cosine)
            for x, y in ((0, 0), (obj["width_mm"], 0), (obj["width_mm"], obj["height_mm"]), (0, obj["height_mm"]))]


def validate_scene(scene: dict, *, check_safe_area: bool = True, structure_snapshot=None) -> dict:
    """Return a deep copy; never silently resize or mutate customer input."""
    if not isinstance(scene, dict) or scene.get("schema_version") != "1.0":
        raise GeometryValidationError("INVALID_SCHEMA", "지원하는 장면 형식은 1.0입니다.", "schema_version")
    from .structures import geometry_for_scene
    from .barcodes import validate_barcode_object
    from .collisions import collision_report
    normalized = deepcopy(scene)
    faces = normalized.get("faces")
    if not isinstance(faces,list) or not faces or any(not isinstance(f,dict) for f in faces):
        raise GeometryValidationError("INVALID_FACES", "구조의 모든 면을 포함해 주세요.", "faces")
    geometry = geometry_for_scene(normalized, structure_snapshot=structure_snapshot)
    if geometry.get("pouch_features") is not None:
        normalized["pouch_features"] = deepcopy(geometry["pouch_features"])
    expected = {f["id"]: f for f in geometry["faces"]}
    if len(faces)!=len(expected) or {f.get("id") for f in faces}!=set(expected):
        raise GeometryValidationError("INVALID_FACES", "구조의 모든 면은 각각 한 번씩 필요합니다.", "faces")
    if normalized.get("active_face_id") not in expected:
        raise GeometryValidationError("INVALID_FACE", "선택한 면을 확인해 주세요.", "active_face_id")
    seen_ids: set[str] = set()
    for face in faces:
        prefix = f"faces.{face['id']}"
        expected_face = expected[face["id"]]
        current_dims = (normalize_mm(face.get("width_mm"),"mm"), normalize_mm(face.get("height_mm"),"mm"))
        if current_dims != (expected_face["width_mm"],expected_face["height_mm"]):
            raise GeometryValidationError("FACE_SIZE_MISMATCH", "구조와 편집 면의 치수가 일치하지 않습니다.", prefix)
        face["width_mm"], face["height_mm"] = current_dims
        if not isinstance(face.get("name", ""), str) or len(face.get("name", "")) > 100:
            raise GeometryValidationError("INVALID_FACE_NAME", "면 이름은 100자 이하의 문자열이어야 합니다.", prefix)
        _check_color(face.get("background", "#ffffff"), f"{prefix}.background")
        face.setdefault("background", "#ffffff")
        objects = face.get("objects")
        if not isinstance(objects, list) or len(objects) > 200:
            raise GeometryValidationError("INVALID_OBJECTS", "면별 객체는 최대 200개입니다.", f"{prefix}.objects")
        for index, obj in enumerate(objects):
            field = f"{prefix}.objects.{index}"
            if not isinstance(obj, dict):
                raise GeometryValidationError("INVALID_OBJECT", "객체 형식을 확인해 주세요.", field)
            oid = obj.get("id")
            if not isinstance(oid, str) or not IDENTIFIER_RE.fullmatch(oid) or oid in seen_ids:
                raise GeometryValidationError("INVALID_OBJECT_ID", "객체 ID는 중복되지 않는 식별자여야 합니다.", f"{field}.id")
            seen_ids.add(oid)
            kind = obj.get("type")
            if kind not in ("text", "image", "shape", "barcode") or obj.get("face_id") != face["id"]:
                raise GeometryValidationError("INVALID_OBJECT_TYPE", "객체 유형과 면을 확인해 주세요.", field)
            if obj.get("crop") is not None:
                if kind != "image":
                    raise GeometryValidationError("IMAGE_CROP_ONLY", "자르기는 이미지 객체에서만 사용할 수 있습니다.", f"{field}.crop")
                from ..image_crop import normalized_crop
                try:
                    obj["crop"] = normalized_crop(obj["crop"])
                except ValueError as exc:
                    raise GeometryValidationError("INVALID_IMAGE_CROP", str(exc), f"{field}.crop") from None
            if any(key in obj for key in ("url", "src", "asset_url", "image_url")):
                raise GeometryValidationError("EXTERNAL_ASSET_FORBIDDEN", "외부 이미지 주소 대신 업로드된 자산을 사용해 주세요.", field)
            for key in ("x_mm", "y_mm"):
                obj[key] = _bounded_number(obj, key, -10, 1000, field)
            for key in ("width_mm", "height_mm"):
                obj[key] = _bounded_number(obj, key, 0.1, 1000, field)
            obj["rotation_deg"] = _bounded_number(obj, "rotation_deg", -360, 360, field, 0)
            obj["z_index"] = _bounded_number(obj, "z_index", -10000, 10000, field, 0)
            obj["opacity"] = _bounded_number(obj, "opacity", 0, 1, field, 1)
            for key in ("visible", "print_enabled"):
                obj.setdefault(key, True)
                if not isinstance(obj[key], bool):
                    raise GeometryValidationError("INVALID_BOOLEAN", "표시·출력 상태를 확인해 주세요.", f"{field}.{key}")
            if kind in ("text", "shape"):
                obj["color"] = obj.get("color") or "#172c28"
                _check_color(obj["color"], f"{field}.color")
            if kind == "text":
                text = obj.get("text")
                if not isinstance(text, str) or len(text) > 12000 or any(ord(char) < 32 and char not in "\n\r\t" for char in text):
                    raise GeometryValidationError("INVALID_TEXT", "문구는 제어 문자 없이 12,000자 이하로 입력해 주세요.", f"{field}.text")
                obj["font_size_pt"] = _bounded_number(obj, "font_size_pt", 4, 400, field, 18)
                obj["font_id"] = obj.get("font_id") or "NotoSansKR"
                if obj["font_id"] != "NotoSansKR":
                    raise GeometryValidationError("UNSUPPORTED_FONT", "검증된 NotoSansKR 글꼴을 선택해 주세요.", f"{field}.font_id")
                obj.setdefault("font_weight",400)
                if obj["font_weight"] not in (400,700):
                    raise GeometryValidationError("UNSUPPORTED_FONT_WEIGHT", "글꼴 두께는 일반 400 또는 굵게 700을 선택해 주세요.", f"{field}.font_weight")
                obj["align"] = obj.get("align") or "left"
                if obj["align"] not in ("left", "center", "right"):
                    raise GeometryValidationError("INVALID_ALIGNMENT", "텍스트 정렬을 확인해 주세요.", f"{field}.align")
                obj["line_height"] = _bounded_number(obj, "line_height", 0.5, 4, field, 1.2)
                obj["letter_spacing"] = _bounded_number(obj, "letter_spacing", -5, 30, field, 0)
            if kind == "shape":
                obj["shape"] = obj.get("shape") or "rect"
                if obj["shape"] not in ("rect", "ellipse", "circle"):
                    raise GeometryValidationError("UNSUPPORTED_SHAPE", "지원하지 않는 도형입니다.", field)
                for key in ("fill", "stroke"):
                    if obj.get(key) is not None:
                        _check_color(obj[key], f"{field}.{key}")
                obj["stroke_width_mm"] = _bounded_number(obj, "stroke_width_mm", 0, 20, field, 0)
            if kind == "image":
                asset_id = obj.get("asset_id")
                if not isinstance(asset_id, str) or not IDENTIFIER_RE.fullmatch(asset_id):
                    raise GeometryValidationError("EXTERNAL_ASSET_FORBIDDEN", "업로드된 자산 ID만 사용할 수 있습니다.", f"{field}.asset_id")
            if kind == "barcode":
                validate_barcode_object(obj)
            if not obj["visible"] or not obj["print_enabled"]:
                continue
            region = expected_face["regions"]["safe" if kind in ("text","barcode") and check_safe_area else "bleed"]
            left, top = region["x_mm"], region["y_mm"]
            right, bottom = left + region["width_mm"], top + region["height_mm"]
            if any(x < left - EPSILON_MM or x > right + EPSILON_MM or y < top - EPSILON_MM or y > bottom + EPSILON_MM for x, y in _corners(obj)):
                raise GeometryValidationError("TEXT_OUTSIDE_SAFE_AREA" if kind in ("text","barcode") and check_safe_area else "OBJECT_OUTSIDE_BLEED", "객체가 안전 영역 또는 허용 블리드를 벗어났습니다.", field)
    if check_safe_area:
        issues = collision_report(normalized,geometry)
        if issues:
            problem=issues[0]
            raise GeometryValidationError(problem["code"],problem["message"],f"faces.{problem.get("face_id")}.objects.{problem.get("object_id") or problem.get("hole_id")}")
    return normalized


def default_scene(width_mm: float = 230, height_mm: float = 310, product_name: str = "높은 단백질 함량") -> dict:
    geometry = validate_dimensions(width_mm, height_mm, "mm")
    faces = []
    for face in geometry["faces"]:
        fid = face["id"]
        faces.append({"id": fid, "name": face["name"], "width_mm": geometry["width_mm"], "height_mm": geometry["height_mm"],
                      "background": "#f4f1e8", "objects": [{"id": f"{fid}-title", "type": "text", "face_id": fid,
                      "x_mm": 20, "y_mm": 40, "width_mm": geometry["width_mm"] - 40, "height_mm": 45,
                      "rotation_deg": 0, "z_index": 1, "text": product_name if fid == "front" else "제품 표시사항\n원재료·보관 방법을 확인해 주세요.",
                      "font_size_pt": 24, "font_id": "NotoSansKR", "color": "#173e34", "align": "left", "visible": True, "print_enabled": True}]})
    return {"schema_version": "1.0", "active_face_id": "front", "faces": faces}
