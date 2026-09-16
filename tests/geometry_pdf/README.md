# Geometry and PDF verification

Run `python -m pytest tests/geometry_pdf -q` with API development dependencies
plus `pypdf`. The suite tests output files rather than mocking the PDF canvas.

Verified scenarios:

- AC05: 230 x 310mm equals 23 x 31cm, hashes match, MediaBox/TrimBox/BleedBox
  differ by less than 0.01mm from declared finished dimensions.
- AC06: absent units, invalid values, unsupported bounds, impossible seals and
  empty print areas are rejected.
- AC03: Korean/English/numeric/special-character original strings survive in
  the manifest and extractable PDF text; font glyphs are embedded; missing
  glyphs and text overflow stop output.
- AC11 review scope: two ordered face pages, 1:1 dimensions, vector text and
  guides, original raster pixels, PNG alpha, visible review-only labels.
- AC12 review scope: production blocked regardless of caller approval claims.
- AC29 export scope: external/path/data asset IDs are rejected, resolver URLs
  cannot reach ReportLab, HTML remains literal text.

`artifacts/review-sample.pdf` and the adjacent manifest were generated using
the bundled Python runtime and visually verified after Poppler rendering.
The sample has two face pages and embedded Korean text. This is development
evidence, not a manufacturer-approved print sample.

2026-09-16 extension: 66 tests pass in the API environment. Added AC07–12/P5
checks cover actual PDF EAN-13 decoding with ZXing-C++, leading zero preservation,
TypeScript encoder parity, protected quiet zones/folds/holes, exact stand-up and
box face/net dimensions, six outward face normals and non-mirrored orientation,
net flap/glue non-overlap, exact approved manufacturer dimensions and materials,
RGB six-file bundle hashes and atomic first/repeat credit capture. Failure tests
include revision changes, revoked approval after upload, disabled operations,
lost actor access and storage failure without credit capture or entitlement.

`generate_structural_samples.py` creates demo stand-up (4 pages) and folding box
(7 pages) samples, including flat nets. Poppler rendered these artifacts and the
face markers, gusset fold, holes, barcode and glue/flaps were visually inspected.
All are intentionally labeled manufacturer-unapproved. They do not establish
real manufacturer approval, physical barcode scanning or PDF/X conformance.

Regenerate the sample using `python tests/geometry_pdf/generate_sample.py`.
The artifact includes Korean, Latin, numbers, punctuation, vector shapes,
front/back labels and the review/manufacturer-unapproved warning on each page.
