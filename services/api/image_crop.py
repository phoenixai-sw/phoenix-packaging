"""Non-destructive source crop shared by scene validation, PDF and PPI.

Coordinates use the EXIF-oriented source, not the object's rotated placement.
The source asset is immutable. None/omission means the complete image.
"""
from math import isfinite

FULL_CROP = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


def normalized_crop(crop=None):
    if crop is None:
        return dict(FULL_CROP)
    if not isinstance(crop, dict) or set(crop) != set(FULL_CROP):
        raise ValueError("자르기에는 원본 기준 x, y, width, height가 필요합니다.")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) for value in crop.values()):
        raise ValueError("자르기 좌표는 유한한 숫자로 입력해 주세요.")
    result = {key: float(value) for key, value in crop.items()}
    if not (0 <= result["x"] < 1 and 0 <= result["y"] < 1
            and 0.000001 <= result["width"] <= 1 and 0.000001 <= result["height"] <= 1
            and result["x"] + result["width"] <= 1 + 1e-12
            and result["y"] + result["height"] <= 1 + 1e-12):
        raise ValueError("자르기 영역은 원본 이미지 안에 있어야 하며 폭과 높이는 0보다 커야 합니다.")
    # Accept only floating-point addition noise, not a material out-of-bounds crop.
    result["width"] = min(result["width"], 1 - result["x"])
    result["height"] = min(result["height"], 1 - result["y"])
    return result


def crop_pixels(pixels, obj):
    crop = normalized_crop(obj.get("crop"))
    return (pixels[0] * crop["x"], pixels[1] * crop["y"],
            pixels[0] * crop["width"], pixels[1] * crop["height"])
