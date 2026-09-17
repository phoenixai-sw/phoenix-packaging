"""Trusted asset lineage, never metadata supplied by a client scene."""
from math import isfinite
from .image_crop import crop_pixels


def image_quality_metrics(pixels, obj, metadata=None):
    visible = crop_pixels(pixels, obj)[2:]
    actual = min(visible[0] * 25.4 / obj["width_mm"], visible[1] * 25.4 / obj["height_mm"])
    provenance = (metadata or {}).get("image_quality") or {}
    native = provenance.get("native_equivalent_pixels", pixels)
    if not isinstance(native, (list, tuple)) or len(native) != 2 or any(
        isinstance(n, bool) or not isinstance(n, (int, float)) or not isfinite(n) or n <= 0 for n in native
    ):
        native = pixels
    visible_native = crop_pixels(native, obj)[2:]
    original = min(actual, visible_native[0] * 25.4 / obj["width_mm"], visible_native[1] * 25.4 / obj["height_mm"])
    return {"effective_ppi": actual, "original_effective_ppi": original,
            "visible_pixels": list(visible),
            "resampled": bool(provenance.get("resampled")),
            "extended": bool(provenance.get("extended")), "provenance": provenance}


def resolver_quality_metrics(resolver, asset_id, pixels, obj):
    accessor = getattr(resolver, "metadata", None)
    return image_quality_metrics(pixels, obj, accessor(asset_id) if callable(accessor) else None)
