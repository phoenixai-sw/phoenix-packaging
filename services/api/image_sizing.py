"""Server-owned GPT Image 2.5 output policy, frozen into quotes and jobs.

Limits verified against https://developers.openai.com/api/docs/guides/image-generation
on 2026-09-17. Pixel count above 2560*1440 is experimental, not a print approval.
"""
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import lru_cache


SIZE_POLICY_VERSION = "packaging-face-max-v1"
SUPPORTED_MODELS = frozenset({"gpt-image-2.5-sunburst", "gpt-image-2.5-flare"})
SIZE_MULTIPLE = 16
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
MAX_EDGE = 3840
EXPERIMENTAL_ABOVE_PIXELS = 2560 * 1440


class ImageSizeError(ValueError):
    pass


def require_supported_model(model):
    if model not in SUPPORTED_MODELS:
        raise ImageSizeError("지원되지 않는 이미지 모델의 출력 크기는 선택할 수 없습니다.")


def _dimension(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ImageSizeError("이미지 면의 규격을 확인해 주세요.")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not Decimal("0.0001") <= number <= 1_000_000:
            raise ValueError()
        return Fraction(number)
    except (ValueError, InvalidOperation, OverflowError):
        raise ImageSizeError("이미지 면의 규격을 확인해 주세요.") from None


def valid_dimensions(width, height):
    return (isinstance(width, int) and not isinstance(width, bool)
            and isinstance(height, int) and not isinstance(height, bool)
            and 0 < width <= MAX_EDGE and 0 < height <= MAX_EDGE
            and width % SIZE_MULTIPLE == height % SIZE_MULTIPLE == 0
            and MIN_PIXELS <= width * height <= MAX_PIXELS
            and width <= 3 * height and height <= 3 * width)


@lru_cache(maxsize=1)
def _sizes():
    # Fixed 240*240 search bound, independent of user-controlled dimensions.
    return tuple((w, h, w*h) for w in range(16, MAX_EDGE+1, 16)
                 for h in range(16, MAX_EDGE+1, 16) if valid_dimensions(w, h))


def select_image_output(model, width_mm, height_mm):
    require_supported_model(model)
    width, height = _dimension(width_mm), _dimension(height_mm)
    face_ratio = width / height
    ratio = min(Fraction(3), max(Fraction(1, 3), face_ratio))
    # Favor an exact or near-exact aspect ratio without falling to a small image
    # merely because a low-resolution pair happens to match a fractional ratio.
    attainable = min(Fraction(MAX_PIXELS), MAX_EDGE**2 / ratio, MAX_EDGE**2 * ratio)
    minimum = max(Fraction(MIN_PIXELS), attainable * Fraction(9, 10))
    candidates = ((w, h, pixels) for w, h, pixels in _sizes() if pixels >= minimum)
    w, h, pixels = min(candidates, key=lambda item: (abs(Fraction(item[0], item[1]) / ratio - 1), -item[2]))
    return {
        "output_size": f"{w}x{h}",
        "size_policy_version": SIZE_POLICY_VERSION,
        "output_width_px": w,
        "output_height_px": h,
        "output_experimental": pixels > EXPERIMENTAL_ABOVE_PIXELS,
        "output_aspect_ratio_limited": ratio != face_ratio,
        "output_effective_ppi": round(float(min(Fraction(w*254, 10)/width, Fraction(h*254, 10)/height)), 2),
    }


def frozen_image_output(model, data):
    """Verify new server snapshots; preserve the size of jobs created before v1."""
    require_supported_model(model)
    if "size_policy_version" not in data and "output_size" not in data:
        w, h = (1536, 1024) if data.get("action") == "image.generate.high" else (1024, 1024)
        return {"output_size": f"{w}x{h}", "size_policy_version": "legacy-fixed-v0",
                "output_width_px": w, "output_height_px": h, "output_experimental": False,
                "output_aspect_ratio_limited": False}
    if data.get("size_policy_version") != SIZE_POLICY_VERSION:
        raise ImageSizeError("이미지 크기 정책이 일치하지 않습니다. 새 견적을 받아 주세요.")
    expected = select_image_output(model, data.get("width_mm"), data.get("height_mm"))
    if data.get("output_size") != expected["output_size"]:
        raise ImageSizeError("서버에서 확정한 이미지 크기와 작업 정보가 일치하지 않습니다.")
    return expected


def size_capabilities():
    return {"version": SIZE_POLICY_VERSION, "selection": "face_aspect_max",
            "multiple": SIZE_MULTIPLE, "min_pixels": MIN_PIXELS, "max_pixels": MAX_PIXELS,
            "max_edge": MAX_EDGE, "min_aspect_ratio": 1/3, "max_aspect_ratio": 3,
            "experimental_above_pixels": EXPERIMENTAL_ABOVE_PIXELS,
            "experimental": True, "client_size_override": False,
            "aspect_ratio_limit_behavior": "clamp", "print_ppi_guaranteed": False}
