"""Write the standard pouch structures that admins register as review structures.

Sizes are the Korean ready-made stand-up pouch series (10x17+3 ... 20x30+5 cm)
listed alike by several converters. Vendors quote the bottom as the folded
gusset depth, so the expanded gusset (bottom_mm) is twice that number.
These are market-common sizes, not a manufacturer's approved drawing.

Run: .venv/Scripts/python.exe scripts/build-standard-structures.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "structures"
SOURCE = "국내 기성 스탠드 파우치 공통 규격(팩포유·스타패키지 등 2곳 이상 동일 표기), 2026-09-25 조사"
SEAL_SOURCE = "파우치 업체 공개 가이드의 일반 실링 폭 5~10mm 중 넓은 쪽(10mm) 채택, 2026-09-25 조사"
LICENSE = "치수만 사용한 자체 작성 구조. 업체 도면을 복제하지 않음"
MANUFACTURER = "표준 규격 · 제조사 확인 전"

# (width, height, folded gusset depth listed by vendors)
STAND_UP = [(100, 170, 30), (130, 200, 40), (150, 220, 40), (180, 260, 50), (200, 300, 50)]
FACE_INSET = {"left": 12.0, "top": 25.0, "right": 12.0, "bottom": 15.0}  # side seals; top clears seal and zipper.
# Top stays at 25mm: the default layout puts the back title at 16% of the height (27mm on 100x170).
GUSSET_INSET = {"left": 10.0, "top": 10.0, "right": 10.0, "bottom": 10.0}


def stand_up(width, height, bottom):
    half = bottom / 2
    face = lambda face_id, y, rotation, z, turn: {
        "id": face_id, "width_mm": float(width), "height_mm": float(height),
        "net": {"x_mm": 0, "y_mm": float(y), "rotation_deg": rotation},
        "assembly": {"position_mm": [0, 0, z], "rotation_deg": [0, turn, 0], "uv_rotation_deg": 0, "mirror_u": False, "mirror_v": False},
        "safe_inset_mm": dict(FACE_INSET), "fold": []}
    line = lambda y: {"x1_mm": 0, "y1_mm": float(y), "x2_mm": float(width), "y2_mm": float(y)}
    return {
        "schema_version": "2.0", "recipe_id": "fixed-panel-net-v1", "family": "stand-up-pouch",
        "dimensions": {"width_mm": width, "height_mm": height, "bottom_mm": bottom},
        "dimension_semantics": {"basis": "finished_outer", "bottom_definition": "expanded_gusset"},
        "panels": [
            face("front", 0, 0, half, 0),
            face("back", height + bottom, 180, -half, 180),
            {"id": "bottom", "width_mm": float(width), "height_mm": float(bottom),
             "net": {"x_mm": 0, "y_mm": float(height), "rotation_deg": 0},
             "assembly": {"position_mm": [0, -height / 2, 0], "rotation_deg": [90, 0, 0], "uv_rotation_deg": 0, "mirror_u": False, "mirror_v": False},
             "safe_inset_mm": dict(GUSSET_INSET), "fold": [line(half)]}],
        "fold_lines": [line(height), line(height + half), line(height + bottom)],
        "feature_policy": "none", "finishing": None, "review_bleed_mm": 3}


THREE_SIDE_SEAL = {
    "schema_version": "2.0", "recipe_id": "three-side-seal-separated-v1", "family": "three-side-seal",
    "dimension_semantics": {"basis": "finished_outer", "compensation": "none"},
    "width_range_mm": {"minimum": 60, "maximum": 400}, "height_range_mm": {"minimum": 80, "maximum": 500},
    "seals_mm": {"left": 10, "right": 10, "top": 10, "bottom": 10}, "safe_margin_mm": 5,
    "feature_policy": "none", "review_bleed_mm": 3}


def registration(name, family, key, definition, source=SOURCE):
    return {"name": name, "manufacturer": MANUFACTURER, "is_demo": False, "geometry_template_id": family,
            "billing_family_key": key, "source": source, "license": LICENSE, "material": "",
            "structure_definition": definition, "review_available": True}


def main():
    items = []
    # The picker lists newest first; registering largest first shows the smallest size on top.
    for width, height, folded in reversed(STAND_UP):
        bottom = folded * 2
        definition = stand_up(width, height, bottom)
        (OUT / f"stand-up-pouch-{width}x{height}x{bottom}.json").write_text(json.dumps(definition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        items.append(registration(f"스탠드 파우치 {width}×{height} · 바닥 {folded}mm 접힘 (표준)", "stand-up-pouch", f"std-stand-up-{width}x{height}x{bottom}", definition))
    (OUT / "three-side-seal-seal10.json").write_text(json.dumps(THREE_SIDE_SEAL, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    items.append(registration("삼방 실링 · 실링 10mm (표준)", "three-side-seal", "std-three-side-seal-seal10", THREE_SIDE_SEAL, SEAL_SOURCE))
    (OUT / "standard-registrations.json").write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(items)} registrations written to {OUT}")


if __name__ == "__main__":
    main()
