"""Noto glyph paths using exactly the existing PDF layout metrics."""
from functools import lru_cache
from io import BytesIO
import unicodedata
from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen
from reportlab.pdfbase import pdfmetrics
from .review_pdf import FONT_PATH, FONT_BOLD_PATH, _text_font_id, ExportValidationError


class CanvasPen(BasePen):
    def __init__(self, glyphs, path): super().__init__(glyphs); self.path = path
    def _moveTo(self, p): self.path.moveTo(*p)
    def _lineTo(self, p): self.path.lineTo(*p)
    def _curveToOne(self, p1, p2, p3): self.path.curveTo(*p1, *p2, *p3)
    def _closePath(self): self.path.close()
    def _endPath(self): pass


@lru_cache(maxsize=2)
def outline_font(weight):
    font = TTFont(FONT_BOLD_PATH if weight == 700 else FONT_PATH)
    return font, font.getGlyphSet(), font.getBestCmap()


@lru_cache(maxsize=8)
def custom_outline_font(sha256, data):
    import hashlib
    if hashlib.sha256(data).hexdigest()!=sha256:
        raise ExportValidationError("FONT_HASH_MISMATCH", "윤곽선 글꼴 해시가 일치하지 않습니다.")
    font=TTFont(BytesIO(data))
    if 'glyf' not in font or 'fvar' in font:
        raise ExportValidationError("FONT_OUTLINE_UNSUPPORTED", "검증된 정적 TrueType 윤곽선이 필요합니다.")
    return font,font.getGlyphSet(),font.getBestCmap()


def draw_outline_line(canvas, text, x, baseline, size, weight=400, spacing=0, *, font_source=None, font_id=None):
    if font_source is None:
        font, glyphs, cmap = outline_font(weight)
        font_id = _text_font_id({"font_weight":weight})
    else:
        font,glyphs,cmap=custom_outline_font(font_source.sha256,font_source.data)
        if not font_id:raise ExportValidationError("FONT_ASSET_MISMATCH", "윤곽선과 텍스트 배치 글꼴이 일치해야 합니다.")
    scale = size/font["head"].unitsPerEm
    for char in text:
        # This adapter deliberately matches the existing non-shaping renderer.
        if unicodedata.combining(char) or 0x1100 <= ord(char) <= 0x11FF or 0x0590 <= ord(char) <= 0x08FF:
            raise ExportValidationError("OUTLINE_SHAPING_UNSUPPORTED", "조합 자모·복합 조형 문자는 검증된 윤곽선 출력 범위를 벗어납니다.")
        glyph = cmap.get(ord(char))
        if glyph is None: raise ExportValidationError("MISSING_GLYPH", "윤곽선 글꼴에 없는 문자가 있습니다.")
        canvas.saveState(); canvas.translate(x, baseline); canvas.scale(scale, scale)
        path = canvas.beginPath(); glyphs[glyph].draw(CanvasPen(glyphs, path))
        canvas.drawPath(path, fill=1, stroke=0, fillMode=1); canvas.restoreState()
        x += pdfmetrics.stringWidth(char, font_id, size) + spacing
