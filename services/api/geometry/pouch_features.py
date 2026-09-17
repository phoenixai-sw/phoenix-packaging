"""Optional pouch finishing. Positions are explicit design choices, not approval."""
from copy import deepcopy
import math

from .validation import GeometryValidationError, _r


def normalize_pouch_features(value, template_id, width_mm, height_mm):
    if value is None:
        return None
    if template_id not in {"three-side-seal", "stand-up-pouch"}:
        raise GeometryValidationError("POUCH_FEATURES_UNSUPPORTED", "파우치 가공은 봉투와 스탠드 파우치에서만 사용할 수 있습니다.", "pouch_features")
    from pydantic import ValidationError
    from ..schemas import PouchFeatures
    try:
        features = PouchFeatures.model_validate(value).model_dump()
    except ValidationError:
        raise GeometryValidationError("POUCH_FEATURES_INVALID", "개봉부·지퍼·뜯는 선의 유한한 mm 값과 허용 범위를 확인해 주세요.", "pouch_features") from None
    header = features["header_height_mm"]
    if header > height_mm - 35:
        raise GeometryValidationError("POUCH_HEADER_TOO_LARGE", "개봉부 아래에 내용물·표시사항을 위한 공간을 남겨 주세요.", "pouch_features.header_height_mm")
    if features["tear_enabled"]:
        half = features["notch_height_mm"] / 2
        if features["tear_y_mm"] - half < 14 or features["tear_y_mm"] + half > header:
            raise GeometryValidationError("POUCH_TEAR_POSITION", "뜯는 노치는 상단 실링에서 4mm 이상 떨어지고 개봉부 안에 있어야 합니다.", "pouch_features.tear_y_mm")
    if features["zipper_enabled"]:
        top = features["zipper_y_mm"] - features["zipper_band_mm"] / 2
        bottom = features["zipper_y_mm"] + features["zipper_band_mm"] / 2
        if top < header + 1 or bottom + 5 >= height_mm - 15:
            raise GeometryValidationError("POUCH_ZIPPER_POSITION", "지퍼 대역은 개봉부 아래 1mm 이상에 놓고 표시사항 안전 영역을 남겨 주세요.", "pouch_features.zipper_y_mm")
    return features


def physical_pouch_features(features):
    """Inactive slider values do not change the physical/billing identity."""
    if features is None:
        return None
    result = {"header_height_mm": features["header_height_mm"], "zipper_enabled": features["zipper_enabled"], "tear_enabled": features["tear_enabled"]}
    if features["zipper_enabled"]:
        result.update({key: features[key] for key in ("zipper_y_mm", "zipper_band_mm")})
    if features["tear_enabled"]:
        result.update({key: features[key] for key in ("tear_y_mm", "notch_depth_mm", "notch_height_mm", "notch_shape")})
    return result


def apply_pouch_features(geometry, features):
    if features is None:
        return
    w, h = geometry["width_mm"], geometry["height_mm"]
    geometry["pouch_features"] = deepcopy(features)
    geometry["assumptions"].append("개봉부·지퍼·뜯는 노치의 치수는 사용자 설계값이며 제조사 가공 승인과 별도입니다.")
    for face in geometry["faces"]:
        if face["id"] not in {"front", "back"}:
            continue
        regions = face["regions"]
        header = _r(0, 0, w, features["header_height_mm"])
        guards = [{"kind": "header_guard", **header}]
        regions.update(header=header, zipper=None, tear_line=None, tear_notches=[])
        safe_top = features["header_height_mm"] + 5
        if features["zipper_enabled"]:
            y, band = features["zipper_y_mm"], features["zipper_band_mm"]
            rect = _r(10, y - band / 2, w - 20, band)
            regions["zipper"] = {"line": {"x1_mm": 10, "y1_mm": y, "x2_mm": w - 10, "y2_mm": y}, "band": rect}
            guards.append({"kind": "zipper_guard", **_r(10, y - band / 2 - 2, w - 20, band + 4)})
            safe_top = y + band / 2 + 5
        points = [[0, 0], [w, 0], [w, h], [0, h]]
        hole_bottom = features["header_height_mm"] - 2
        if features["tear_enabled"]:
            y, half, depth = features["tear_y_mm"], features["notch_height_mm"] / 2, features["notch_depth_mm"]
            regions["tear_line"] = {"x1_mm": depth, "y1_mm": y, "x2_mm": w - depth, "y2_mm": y}
            left = [[0, y-half], [depth, y], [0, y+half]]
            if features["notch_shape"] == "round":
                # A bounded half-ellipse polyline gives identical U-shaped cut
                # coordinates to the editor, 3D and PDF consumers.
                left = [[round(depth*math.sin(math.pi*i/12),4),round(y-half*math.cos(math.pi*i/12),4)] for i in range(13)]
            right = [[round(w-x,4),py] for x,py in left]
            regions["tear_notches"] = [
                {"side": "left", "shape": features["notch_shape"], "points_mm": left},
                {"side": "right", "shape": features["notch_shape"], "points_mm": right},
            ]
            points = [[0, 0], [w, 0], *right, [w, h], [0, h], *reversed(left)]
            guards.append({"kind": "tear_guard", **_r(0, y - max(2,half), w, max(4,half*2))})
            for x in (0, w-depth-2):
                guards.append({"kind": "notch_guard", **_r(x, y-half-2, depth+2, half*2+4)})
            hole_bottom = min(hole_bottom, y - 2)
        regions["cut_contour"] = {"points_mm": points, "closed": True}
        regions["structural_guards"] = guards
        regions["no_print"].extend(deepcopy(guards))
        # A hanger belongs inside the opening header. The header text guard itself
        # must not reject it; seal, tear and zipper protection remain authoritative.
        regions["hole_allowed"] = _r(20, 12, w - 40, max(0, hole_bottom - 12))
        safe = regions["safe"]
        bottom = safe["y_mm"] + safe["height_mm"]
        safe.update(y_mm=safe_top, height_mm=round(bottom-safe_top, 4))
