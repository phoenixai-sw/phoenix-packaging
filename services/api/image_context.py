"""Freeze layout guidance at quote time without sending product copy or private IDs."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import re

CONTEXT_VERSION = "packaging-layout-v1"
MAX_REGIONS = 24
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _rect(x, y, width, height, fw, fh):
    left, top = max(0, x), max(0, y)
    right, bottom = min(fw, x + width), min(fh, y + height)
    if right <= left or bottom <= top:
        return None
    return {"x": round(left / fw, 6), "y": round(top / fh, 6),
            "width": round((right - left) / fw, 6), "height": round((bottom - top) / fh, 6)}


def _object_rect(obj, fw, fh):
    x, y, w, h = (float(obj.get(key, 0)) for key in ("x_mm", "y_mm", "width_mm", "height_mm"))
    angle = math.radians(float(obj.get("rotation_deg", 0)))
    c, s = math.cos(angle), math.sin(angle)
    # The shared scene convention rotates about the object's centre.
    cx, cy = x + w / 2, y + h / 2
    corners = [(cx + dx * c - dy * s, cy + dx * s + dy * c)
               for dx, dy in ((-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2))]
    left, top = min(p[0] for p in corners), min(p[1] for p in corners)
    right, bottom = max(p[0] for p in corners), max(p[1] for p in corners)
    # Calm space also covers a small legibility margin around the actual box.
    return _rect(left - 2, top - 2, right - left + 4, bottom - top + 4, fw, fh)


def build_image_context(project, face, colors, *, geometry=None):
    if geometry is None:
        from .geometry.snapshots import project_geometry
        geometry = project_geometry(project)
    resolved = next(item for item in geometry["faces"] if item["id"] == face["id"])
    fw, fh = float(face["width_mm"]), float(face["height_mm"])
    regions = []
    for obj in face.get("objects", []):
        if obj.get("type") not in {"text", "barcode"} or not obj.get("visible", True) or not obj.get("print_enabled", True) or obj.get("opacity", 1) == 0:
            continue
        region = _object_rect(obj, fw, fh)
        if region:
            regions.append(region)
    origin = "placed_editable_objects"
    count = len(regions)
    if len(regions) > MAX_REGIONS:
        # Preserve ALL reserved regions with a conservative union, not truncation.
        left, top = min(r["x"] for r in regions), min(r["y"] for r in regions)
        right, bottom = max(r["x"] + r["width"] for r in regions), max(r["y"] + r["height"] for r in regions)
        regions = [{"x": left, "y": top, "width": round(right-left, 6), "height": round(bottom-top, 6)}]
        origin = "combined_editable_objects"
    if not regions:
        safe = resolved["regions"]["safe"]
        regions = [_rect(safe["x_mm"] + safe["width_mm"] * .2, safe["y_mm"] + safe["height_mm"] * .2,
                         safe["width_mm"] * .6, safe["height_mm"] * .6, fw, fh)]
        origin = "empty_layout_default"
    palette = list(dict.fromkeys(color.upper() for color in colors
                               if isinstance(color, str) and HEX_COLOR.fullmatch(color)))[:12]
    context = {"version": CONTEXT_VERSION, "package_kind": project.template_id,
               "face_id": face["id"], "width_mm": fw, "height_mm": fh,
               "aspect_ratio": round(fw/fh, 6), "brand_colors": palette,
               "quiet_regions": regions, "quiet_region_source": origin,
               "reserved_object_count": count, "geometry_hash": geometry["geometry_hash"]}
    context["hash"] = sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return deepcopy(context)


def context_prompt(context):
    """Only a server-created, minimal visual context is sent to the provider."""
    visual = {key: context[key] for key in ("package_kind", "face_id", "width_mm", "height_mm",
                                          "aspect_ratio", "brand_colors", "quiet_regions")}
    return ("Layout context (coordinates are normalized 0..1 from the top-left): "
            + json.dumps(visual, ensure_ascii=False, separators=(",", ":"))
            + ". Use the provided brand palette when present. Keep each quiet region low-detail and calm for editable text/barcodes; "
            "do not render those text/barcode layers into the image. The package kind describes the flat artwork destination, "
            "not a request to draw a bag or box. Product photos and logos are separate customer-supplied layers; "
            "do not recreate or change the product shape unless the customer explicitly requests a visual change. ")
