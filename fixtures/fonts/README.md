# Shared Korean review font

`NotoSansKR-Regular.ttf` is shared by the browser canvas and server PDF renderer.
It contains the full Korean glyph coverage from the upstream font; it is not a
Latin-only web subset. Keep the exact same file in the web public font directory.

- Family: Noto Sans KR, regular weight 400.
- License: SIL Open Font License 1.1; see [OFL.txt](OFL.txt).
- Official upstream: https://github.com/google/fonts/tree/main/ofl/notosanskr
- Source URL: https://raw.githubusercontent.com/google/fonts/main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf
- Downloaded: 2026-09-16.
- Variable source SHA-256: `194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252`.
- Bundled static SHA-256: `8e4000a13809588d46c1b791e874cd4567b6283eeb9d2e6835a3136a871a6bd0`.
- Static file size: 6,224,884 bytes.
- Build: fontTools 4.60.1 `instantiateVariableFont(font, {"wght": 400}, inplace=False)`.

The static instance is necessary for matching embedded TrueType metrics in
ReportLab and the browser. fontTools is a build dependency only; it is not needed
to run the application. The full variable source is not duplicated in the repo.
The original font family has been retained; the reserved font name `Source` is
not used for this generated instance. Review output embeds glyph subsets and
blocks unsupported characters instead of silently substituting another font.

## Bold editor face

`NotoSansKR-Bold.ttf` is the static weight-700 instance of the same official
variable source above, generated on 2026-09-17 with fontTools. The matching file
is bundled in `apps/web/public/fonts/`. The browser and PDF renderer select the
actual 400/700 instance; synthetic bold is not used.

- Source variable SHA-256 remains `194018e6b2b293a7964f037b25c0249ce1418bc9ab3c971060a03aa57861e252`.
- Static bold SHA-256: `f83cb7d28cc6c5ab36629da7bbed2d925f4c740665d0ae0de7455dadd9630efc`.
- Static bold file size: 6,222,736 bytes.
- Build: `instantiateVariableFont(font, {"wght": 700}, inplace=False)`.
- Bold family/subfamily and PostScript names are set to `Noto Sans KR`, `Bold`,
  and `NotoSansKR-Bold`; OS/2 and head bold flags match the real 700 outlines.
  This prevents ReportLab from deduplicating 400 and 700 as the upstream font's
  shared `NotoSansKR-Thin` PostScript name. The old 400 file remains unchanged.
- License: the same [OFL.txt](OFL.txt). No reserved `Source` name is introduced.
