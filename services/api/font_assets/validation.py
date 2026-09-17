"""Static glyf TrueType only. OS/2 flags never replace the uploader's license."""
from hashlib import sha256
from io import BytesIO
import unicodedata
from fontTools.ttLib import TTFont
from ..errors import APIError

MAX_BYTES = 20*1024*1024


def clean_name(value):
    return ''.join(c for c in value if not unicodedata.category(c).startswith('C')).strip()[:120]


def inspect_font(raw):
    if not 0 < len(raw) <= MAX_BYTES or raw[:4] != b'\x00\x01\x00\x00':
        raise APIError(422, 'FONT_FORMAT_UNSUPPORTED', '20MiB 이하의 정적 TrueType TTF 파일만 지원합니다.')
    try:
        with TTFont(BytesIO(raw), lazy=False, checkChecksums=2) as font:
            if not {'glyf','loca','head','hhea','hmtx','maxp','OS/2','cmap','name'}.issubset(font.keys()):
                raise ValueError('required tables')
            if any(key in font for key in ('fvar','CFF ','CFF2','COLR','SVG ','CBDT','sbix')):
                raise APIError(422, 'FONT_FORMAT_UNSUPPORTED', '가변·CFF·컬러 글꼴은 지원하지 않습니다. 정적 TTF를 선택하세요.')
            fs_type = int(font['OS/2'].fsType)
            # ReportLab subsets fonts. Preview-only, restricted, no-subsetting,
            # bitmap-only and unknown bits cannot safely enter this editor.
            if fs_type not in (0, 8):
                raise APIError(422, 'FONT_EMBEDDING_RESTRICTED', '이 글꼴의 파일 내 사용 제한은 편집·부분 포함 출력과 맞지 않습니다.')
            weight = int(font['OS/2'].usWeightClass)
            cmap = font.getBestCmap() or {}
            if not 1 <= weight <= 1000 or not cmap or not 16 <= font['head'].unitsPerEm <= 16384:
                raise ValueError('invalid metrics')
            order = font.getGlyphOrder()
            if not 1 <= len(order) <= 65535 or any(name not in font['hmtx'].metrics for name in order):
                raise ValueError('invalid glyph metrics')
            # Parse every glyph before publication and bound composite recursion.
            visited = set()
            expanded = {}
            total_points = 0
            def inspect(name, stack):
                nonlocal total_points
                if name in stack or len(stack) > 16: raise ValueError('recursive glyph')
                if name in visited: return expanded[name]
                glyph = font['glyf'][name]
                if glyph.isComposite():
                    if len(glyph.components) > 64: raise ValueError('complex glyph')
                    points=sum(inspect(component.glyphName, stack | {name}) for component in glyph.components)
                    if points>20000:raise ValueError('expanded composite coordinates')
                else:
                    if glyph.numberOfContours > 1000: raise ValueError('complex contours')
                    points = len(glyph.coordinates) if glyph.numberOfContours else 0
                    total_points += points
                    if points > 20000 or total_points > 4_000_000: raise ValueError('complex coordinates')
                visited.add(name)
                expanded[name]=points
                return points
            for name in order: inspect(name, set())
            from reportlab.pdfbase.ttfonts import TTFont as PDFont
            metrics = PDFont('validation-only',BytesIO(raw)).face
            family = clean_name(font['name'].getDebugName(16) or font['name'].getDebugName(1) or '')
            subfamily = clean_name(font['name'].getDebugName(17) or font['name'].getDebugName(2) or '')
            if not family: raise ValueError('missing name')
            return {'family':family,'subfamily':subfamily,'weight':weight,'glyph_count':len(cmap),
                    'fs_type':fs_type,'byte_size':len(raw),'sha256':sha256(raw).hexdigest(),
                    'ascent_ratio':metrics.ascent/1000,'descent_ratio':metrics.descent/1000}
    except APIError:
        raise
    except Exception:
        raise APIError(422, 'FONT_INVALID', '글꼴 파일의 표·문자·윤곽선 정보를 확인할 수 없습니다.') from None


def glyph_report(raw, text):
    with TTFont(BytesIO(raw), lazy=True) as font: cmap = font.getBestCmap() or {}
    missing = sorted({ord(c) for c in text if c not in '\r\n\t' and ord(c) not in cmap})
    shaping = sorted({ord(c) for c in text if unicodedata.combining(c) or 0x1100 <= ord(c) <= 0x11ff or 0x0590 <= ord(c) <= 0x08ff})
    return {'supported':not missing and not shaping,
            'missing_codepoints':[f'U+{n:04X}' for n in missing],
            'unsupported_shaping':[f'U+{n:04X}' for n in shaping]}
