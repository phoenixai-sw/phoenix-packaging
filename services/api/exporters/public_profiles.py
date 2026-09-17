"""Dated public intake references, never registry approvals or production entitlements.

Null values mean not publicly established for that specific product. The basic
profile is our review policy; it is not the intersection of supplier approvals.
"""
from copy import deepcopy

from ..geometry import GeometryValidationError, geometry_for_scene, validate_scene
from ..geometry.collisions import bounds, overlap
from ..image_quality_metadata import resolver_quality_metrics

BASIC_REVIEW_PROFILE_ID = "phoenix-basic-review-v1"
RETRIEVED_AT = "2026-09-17"
PUBLIC_PROFILES = {
    BASIC_REVIEW_PROFILE_ID: {
        "name": "Phoenix 기본 검토 규격", "kind": "review_policy",
        "scope": "소비자용 EAN-13을 포함하는 데모 파우치·박스의 면별 검토 PDF",
        "bleed_mm": 3, "safe_mm": 5, "min_ppi": 300, "recommended_font_pt": 7,
        "recommended_line_pt": .5, "font_mode": "embedded", "color_space": "RGB",
        "pdf_min_version": "1.5", "sources": [],
    },
    "hansung-flexible-public-20260917": {
        "name": "한성피엔지 공개 연포장 입고 참고", "kind": "supplier_public_reference",
        "scope": "공식 홈페이지 파일 제작 가이드의 연포장·그라비어 작업",
        "bleed_mm": 3, "safe_mm": 5, "min_ppi": 300, "recommended_ppi_max": 400,
        "font_mode": "outlined", "color_space": "CMYK", "pdf_min_version": None,
        "sources": ["https://hansung-print.com/"],
    },
    "packative-db40-public-20260917": {
        "name": "패커티브 DB40 공개 칼선 참고", "kind": "supplier_public_reference",
        "scope": "DB40 DeliveryBox 340×250×210mm 다운로드 칼선에 첨부된 안내",
        "bleed_mm": 3, "safe_mm": 5, "fold_safe_mm": 5, "min_ppi": 300,
        "recommended_font_pt": 7, "recommended_line_pt": .5,
        "font_mode": "outlined", "color_space": "CMYK", "pdf_min_version": None,
        "requires_original_dieline": True,
        "sources": ["https://cdn.packative.com/models/media/model/04/d4/774860b8d5e613dffc0506c9443f.pdf",
                    "https://packative.com/products/cookiebox/prod_01HWW028J8HYCG4G9HNVKAMGN7"],
    },
    "redprinting-sheet-public-20260917": {
        "name": "레드프린팅 일반 디지털 인쇄 공개 가이드", "kind": "supplier_public_reference",
        "scope": "일반 디지털 낱장 인쇄; 책자·브로슈어와 개별 박스 칼선 수치를 대체하지 않음",
        "bleed_mm": 2, "safe_mm": 3, "min_ppi": 300, "recommended_font_pt": 6,
        "recommended_line_pt": .25, "font_mode": "embedded_or_outlined", "color_space": "CMYK",
        "pdf_min_version": "1.5", "requires_acrobat_layers": True,
        "sources": ["https://www.redprinting.co.kr/down/guide/PRT_DFT.pdf"],
    },
    "redprinting-cakebox-public-20260917": {
        "name": "레드프린팅 조각 케이크 상자 공개 안내", "kind": "supplier_public_reference",
        "scope": "OTPKCAK 조각 케이크 상자 상품별 다운로드 칼선 작업",
        "bleed_mm": None, "safe_mm": None, "min_ppi": None,
        "font_mode": "outlined", "color_space": None, "pdf_min_version": "1.5",
        "requires_original_dieline": True, "requires_acrobat_layers": True,
        "sources": ["https://www.redprinting.co.kr/ko/product/item/OT/OTPKCAK"],
    },
}


def public_profile(profile_id=BASIC_REVIEW_PROFILE_ID):
    if profile_id not in PUBLIC_PROFILES:
        raise GeometryValidationError("UNKNOWN_PUBLIC_PROFILE", "알 수 없는 공개 검토 규격입니다.", "review_profile_id")
    return {"id": profile_id, **deepcopy(PUBLIC_PROFILES[profile_id]), "retrieved_at": RETRIEVED_AT,
            "manufacturer_approved": False, "production_authorization": False}


def inspect_basic_review(project, asset_resolver=None):
    """Measure stored content; quality advisories do not prohibit a labelled review.

    Structural validity, missing glyphs, overflow and unreadable assets are hard
    errors. Render-time PDF boxes, font streams and EAN decoding are separate gates.
    """
    from .review_pdf import _scene_from_project, _layout_text, _resolve_image
    profile = public_profile(project.get("review_profile_id", BASIC_REVIEW_PROFILE_ID))
    if profile["kind"] != "review_policy":
        raise GeometryValidationError("SUPPLIER_REFERENCE_NOT_EXPORT_PROFILE", "공개 업체 안내는 승인된 출력 프로필이 아닙니다.", "review_profile_id")
    scene = validate_scene(_scene_from_project(project), structure_snapshot=project.get("structure_snapshot"))
    geometry = geometry_for_scene(scene, structure_snapshot=project.get("structure_snapshot"))
    issues, measurements = [], []
    for face in scene["faces"]:
        structural = next(f for f in geometry["faces"] if f["id"] == face["id"])
        for obj in face["objects"]:
            if not obj["visible"] or not obj["print_enabled"]:
                continue
            detail = {"face_id": face["id"], "object_id": obj["id"]}
            def warn(code, message, **values):
                issues.append({"code": code, "message": message, "severity": "warning", "scope": "review", **detail, **values})
            if obj["type"] == "image":
                _, pixels = _resolve_image(obj["asset_id"], asset_resolver)
                quality = resolver_quality_metrics(asset_resolver, obj["asset_id"], pixels, obj)
                ppi = quality["effective_ppi"]
                measurements.append({"kind": "effective_ppi", **detail, "value": ppi, "minimum": profile["min_ppi"], "passed": ppi+1e-9 >= profile["min_ppi"]})
                if ppi+1e-9 < profile["min_ppi"]:
                    warn("BASIC_LOW_PPI", f"배치 크기 기준 {ppi:.2f}ppi로 기본 검토 기준 300ppi 미만입니다.", effective_ppi=ppi, minimum_ppi=300)
                if quality["resampled"] or quality["extended"]:
                    native = quality["original_effective_ppi"]
                    measurements.append({"kind": "original_effective_ppi", **detail, "value": native, "minimum": profile["min_ppi"], "passed": native+1e-9 >= profile["min_ppi"]})
                    if native+1e-9 < profile["min_ppi"]:
                        warn("BASIC_ORIGINAL_LOW_PPI", f"이미지 보완 후에도 원본 디테일은 {native:.2f}ppi입니다. 고해상도 원본으로 교체하거나 인쇄 크기를 줄여 주세요.", original_effective_ppi=native, effective_ppi=ppi, minimum_ppi=300)
                if quality["extended"]:
                    warn("BASIC_SYNTHETIC_BLEED", "이미지 가장자리를 반복·반사해 도련을 보완했습니다. 원본 촬영 영역이 아니므로 경계·무늬·투명도를 확인해 주세요.")
            if obj["type"] in {"image", "shape"}:
                left, top, right, bottom = bounds(obj)
                edge_shortfall = ((-3 < left <= 0) or (-3 < top <= 0) or
                                  (face["width_mm"] <= right < face["width_mm"]+3) or
                                  (face["height_mm"] <= bottom < face["height_mm"]+3))
                if edge_shortfall:
                    warn("BASIC_ARTWORK_BLEED", "재단선에 닿는 이미지·도형이 3mm 도련 끝까지 이어지지 않습니다. 바탕색만 자동 연장됩니다.")
            if obj["type"] == "text":
                _layout_text(obj)
                if obj["font_size_pt"] < profile["recommended_font_pt"]:
                    warn("BASIC_SMALL_TEXT", "기본 검토 권장 글자 크기 7pt 미만입니다.", actual_pt=obj["font_size_pt"])
            if obj["type"] in {"text", "barcode"}:
                left, top, right, bottom = bounds(obj)
                safe = min(left, top, face["width_mm"]-right, face["height_mm"]-bottom)
                measurements.append({"kind": "trim_clearance", **detail, "value_mm": safe, "minimum_mm": profile["safe_mm"], "passed": safe+1e-9 >= profile["safe_mm"]})
                if safe+1e-9 < profile["safe_mm"]:
                    warn("BASIC_SAFE_MARGIN", "중요 문구·바코드는 재단선에서 5mm 이상 띄워 주세요.", clearance_mm=safe)
                for fold in structural["regions"].get("fold", []):
                    guard = (min(fold["x1_mm"], fold["x2_mm"])-5, min(fold["y1_mm"], fold["y2_mm"])-5,
                             max(fold["x1_mm"], fold["x2_mm"])+5, max(fold["y1_mm"], fold["y2_mm"])+5)
                    if overlap(bounds(obj), guard):
                        warn("BASIC_FOLD_CLEARANCE", "기본 검토에서 중요 문구·바코드가 접힘선 5mm 보호 영역에 닿습니다.")
                        break
            if obj["type"] == "shape" and obj.get("stroke") and 0 < obj["stroke_width_mm"]*72/25.4 < profile["recommended_line_pt"]:
                warn("BASIC_THIN_LINE", "기본 검토 권장 선 두께 0.5pt 미만입니다.", actual_pt=obj["stroke_width_mm"]*72/25.4)
    return {"profile": profile, "quality_status": "warning" if issues else "pass", "issues": issues, "measurements": measurements,
            "output": {"pdf_version": "1.5", "bleed_mm": 3, "trim_size_preserved": True, "color_space": "RGB", "font_mode": "embedded"},
            "manufacturing_approval": False, "physical_barcode_grade_verified": False}


def compare_supplier_capabilities(profile_id):
    """No public profile can become approval evidence through this comparison."""
    profile = public_profile(profile_id)
    missing = []
    if profile["kind"] != "supplier_public_reference":
        raise GeometryValidationError("SUPPLIER_PROFILE_REQUIRED", "업체별 공개 참고 프로필을 선택해 주세요.")
    if profile.get("color_space") == "CMYK": missing.append("CMYK_CONVERSION_NOT_IMPLEMENTED")
    if profile.get("font_mode") == "outlined": missing.append("FONT_OUTLINING_NOT_IMPLEMENTED")
    if profile.get("requires_acrobat_layers"): missing.append("ACROBAT_LAYERS_NOT_IMPLEMENTED")
    if profile.get("requires_original_dieline"): missing.append("ORIGINAL_SUPPLIER_DIELINE_NOT_LOADED")
    return {"profile": profile, "unsupported_requirements": missing, "supplier_compliance_claimed": False,
            "unpublished": [field for field in ("bleed_mm", "safe_mm", "min_ppi", "pdf_min_version", "color_space") if profile.get(field) is None]}
