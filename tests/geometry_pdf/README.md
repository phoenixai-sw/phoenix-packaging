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

2026-09-16: 32 tests passed on bundled ReportLab 4.4.9/Python 3.12; the app's
locked environment is verified separately by the main acceptance run.

Regenerate the sample using `python tests/geometry_pdf/generate_sample.py`.
The artifact includes Korean, Latin, numbers, punctuation, vector shapes,
front/back labels and the review/manufacturer-unapproved warning on each page.
