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
