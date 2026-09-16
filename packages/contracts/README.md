# Phoenix Packaging scene contract

The canonical structural scene model is `services/api/schemas.py:Scene`.
Run `python packages/contracts/generate.py` to regenerate `scene.schema.json`
and `scene.generated.ts`; `--check` detects drift without writing. Use the same
Python environment as the API. These files are intentionally generated from
the live model instead of maintaining another hand-written interface.

`schema_version` is `1.0`; every template face must exist exactly once: front/back,
stand-up front/back/bottom, or folding-box front/right/back/left/top/bottom. All
geometry is in millimetres and coordinates originate at the top-left. Rotation
is clockwise around the object's top-left. Font size and letter spacing are in
points. Text uses character wrapping and line-height defaults to 1.2. Font ID
`NotoSansKR` maps to the exact licensed font in `fixtures/fonts` on both servers
and browsers.

Structural schema validation is followed by `geometry.validate_scene` and
review export preflight. These also check matched dimensions, unique IDs,
rotated bounds, safe-region collisions, unavailable glyphs, text overflow and
asset IDs. Passing the TypeScript type is not a manufacturing approval.

## Python integration

```python
from pathlib import Path
from services.api.geometry import validate_dimensions, validate_scene
from services.api.exporters import export_review_pdf, render_review_pdf

geometry = validate_dimensions(23, 31, "cm")
checked = validate_scene(scene)
manifest = export_review_pdf(
    {"id": project_id, "revision": revision, "scene": checked},
    Path("output/review.pdf"),
    asset_resolver=authorized_local_asset,
)
pdf_bytes = render_review_pdf(checked, asset_resolver=authorized_local_asset)
```

The asset resolver receives an opaque asset ID and returns `Path` or `bytes`.
It must authorize tenant access before resolving the asset. Strings, URLs,
SVG and other unsupported raster formats are never handed to ReportLab. PNG,
JPEG and WebP images retain their original pixel dimensions; PNG alpha is
embedded as a transparency mask. Object opacity is supported.

`GeometryValidationError` and its subclass `ExportValidationError` expose
`code`, `message` and `field`, plus `as_dict()`. The exporter writes the PDF and
adjacent `.manifest.json`, and returns the manifest. It includes original
unchanged text, rendered lines, font/PDF hashes, page sizes, warnings and
explicitly unsupported production capabilities.

The review PDF page size is the finished face size. Guides/labels intentionally
remain visible. The page does not add an outer bleed strip; TrimBox and
BleedBox therefore equal MediaBox. The manifest warns about this review-only
behavior. Production export is blocked even if caller-supplied approval fields
claim that a demo has been approved.

`build_geometry(template_id, width, height, unit='mm', bottom_mm=..., depth_mm=..., holes=...)`
builds versioned demo nets, structural regions and assembly transforms. Stand-up
`bottom_mm` is expanded gusset width; its folded half is explicit. The back panel
is rotated 180 degrees in the flat connected net so assembled artwork remains
upright. `geometry_for_scene` hashes the registered version and engine geometry.

Production uses the separate `preflight_project` and `export_production_bundle`
adapter. It requires server-loaded evidence, exact approved dimensions, matching
manufacturer/material, current revision/all-face confirmation and actual output
capabilities. Only ordinary RGB PDF, embedded fonts, finished-size face pages and
zero outer bleed are supported. PDF/X, CMYK, spots, white ink, overprint, outlined
fonts and production hole cut contours fail closed. The six-file bundle includes
all-face preview, final-PDF barcode decode results and five payload hashes.
