"""Vector review PDF; deliberately cannot produce manufacturing-ready files.

The TrimBox is the finished face size in mm. New basic-profile jobs include
3mm bleed; legacy snapshots keep their original finished-size MediaBox.
Structural guides and review labels remain visible on this non-production file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from io import BytesIO
import json
from pathlib import Path
import threading
from typing import Callable

from PIL import Image, ImageOps
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from ..geometry import DEMO_TEMPLATE_ID, GeometryValidationError, validate_scene, geometry_for_scene, holes_for_face, barcode_geometry
from ..image_crop import normalized_crop
from ..image_quality_metadata import resolver_quality_metrics

ROOT = Path(__file__).resolve().parents[3]
FONT_PATH = ROOT / "fixtures" / "fonts" / "NotoSansKR-Regular.ttf"
FONT_ID = "PhoenixNotoSansKR"
FONT_BOLD_PATH = ROOT / "fixtures" / "fonts" / "NotoSansKR-Bold.ttf"
FONT_BOLD_ID = "PhoenixNotoSansKRBold"
FONT_LOCK = threading.Lock()
AssetResolver = Callable[[str], Path | bytes]


class ExportValidationError(GeometryValidationError):
    pass


@lru_cache(maxsize=2)
def _font(weight: int = 400) -> TTFont:
    if weight not in (400, 700):
        raise ExportValidationError("UNSUPPORTED_FONT_WEIGHT", "검증된 글꼴 두께 400 또는 700을 선택해 주세요.", "font_weight")
    path, font_id = (FONT_BOLD_PATH, FONT_BOLD_ID) if weight == 700 else (FONT_PATH, FONT_ID)
    if not path.is_file():
        raise ExportValidationError("FONT_UNAVAILABLE", "검증된 한글 글꼴 파일이 없습니다.", "font_id")
    with FONT_LOCK:
        if font_id not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_id, str(path)))
    return pdfmetrics.getFont(font_id)


def _text_font_id(obj: dict) -> str:
    weight = obj.get("font_weight", 400)
    _font(weight)
    return FONT_BOLD_ID if weight == 700 else FONT_ID


def _font_manifest(scene: dict) -> list[dict]:
    weights = {400} | {obj.get("font_weight", 400) for face in scene["faces"] for obj in face["objects"]
                       if obj["type"] == "text" and obj["visible"] and obj["print_enabled"]}
    return [{"id": "NotoSansKR", "weight": weight, "embedded": True, "license": "OFL-1.1",
             "sha256": hashlib.sha256((FONT_BOLD_PATH if weight == 700 else FONT_PATH).read_bytes()).hexdigest()}
            for weight in sorted(weights)]


def _scene_from_project(project: dict) -> dict:
    if project.get("schema_version") == "1.0":
        return project
    for key in ("scene", "draft_scene", "draft", "scene_json"):
        if isinstance(project.get(key), dict):
            value = project[key]
            return value.get("scene", value)
    raise ExportValidationError("SCENE_REQUIRED", "출력할 저장된 장면이 없습니다.")


def _width(text: str, size: float, spacing: float, font_id: str = FONT_ID) -> float:
    return pdfmetrics.stringWidth(text, font_id, size) + max(0, len(text) - 1) * spacing


def _layout_text(obj: dict) -> list[str]:
    """Character wrapping matching Konva wrap='char'; retain original in manifest."""
    font = _font(obj.get("font_weight", 400))
    font_id = _text_font_id(obj)
    text, size = obj["text"], obj["font_size_pt"]
    missing = sorted({ord(char) for char in text if char not in "\n\r\t" and ord(char) not in font.face.charToGlyph})
    if missing:
        codes = ", ".join(f"U+{point:04X}" for point in missing[:10])
        raise ExportValidationError("MISSING_GLYPH", f"글꼴에 없는 문자가 있습니다: {codes}", f"objects.{obj['id']}.text")
    maximum = obj["width_mm"] * mm
    spacing = obj["letter_spacing"]
    lines: list[str] = []
    # Newline normalization is rendering-only. The customer string is untouched.
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n"):
        line = ""
        for char in paragraph:
            if _width(char, size, spacing, font_id) > maximum + 0.001:
                raise ExportValidationError("TEXT_OVERFLOW", "한 글자가 텍스트 상자보다 큽니다. 글자 크기나 상자를 조정해 주세요.", f"objects.{obj['id']}")
            candidate = line + char
            if line and _width(candidate, size, spacing, font_id) > maximum + 0.001:
                lines.append(line)
                line = char
            else:
                line = candidate
        lines.append(line)
    ascent, descent = pdfmetrics.getAscentDescent(font_id, size)
    required = ascent - descent + max(0, len(lines) - 1) * size * obj["line_height"]
    if required > obj["height_mm"] * mm + 0.001:
        raise ExportValidationError("TEXT_OVERFLOW", "문구가 텍스트 상자 높이를 넘습니다. 글자 크기나 상자를 조정해 주세요.", f"objects.{obj['id']}")
    return lines


def validate_export(project: dict, *, production: bool = False) -> dict:
    if production or project.get("kind") == "production" or project.get("export_kind") == "production":
        raise ExportValidationError("PRODUCTION_EXPORT_DISABLED", "데모 구조는 검토용 출력만 가능합니다. 제작용 출력은 제조사 승인 후 지원합니다.", "kind")
    scene = validate_scene(_scene_from_project(project), structure_snapshot=project.get("structure_snapshot"))
    _font()
    warnings = [
        {"code": "DEMO_UNAPPROVED", "message": "데모 구조 · 제조사 미승인"},
        {"code": "REVIEW_ONLY", "message": "검토용 · 제작 사용 불가"},
        {"code": "RGB_REVIEW", "message": "RGB 검토 PDF입니다. PDF/X·CMYK·별색·화이트 잉크 출력이 아닙니다."},
        {"code": "FINISHED_SIZE", "message": "페이지는 완성 치수와 같습니다. 바깥 블리드는 검토 페이지에서 잘립니다."},
    ]
    if project.get("structure_snapshot") is not None:
        warnings[0]={"code":"REGISTERED_STRUCTURE_REVIEW","message":"등록 구조 검토 · 제작 사용 불가"}
    texts = []
    for face in scene["faces"]:
        for obj in face["objects"]:
            if obj["type"] == "text":
                lines = _layout_text(obj) if obj["visible"] and obj["print_enabled"] else []
                texts.append({"object_id": obj["id"], "face_id": face["id"], "text": obj["text"], "rendered_lines": lines,
                              "visible": obj["visible"], "print_enabled": obj["print_enabled"]})
    return {"scene": scene, "warnings": warnings, "original_texts": texts}


def _resolve_image(asset_id: str, resolver: AssetResolver | None) -> tuple[ImageReader, tuple[int, int]]:
    if resolver is None:
        raise ExportValidationError("ASSET_UNAVAILABLE", "이미지 자산을 읽을 수 없습니다.", "asset_id")
    try:
        resolved = resolver(asset_id)
        # A URL cannot ever be passed to ImageReader. The resolver must perform
        # tenant authorization before supplying local bytes or a trusted Path.
        if isinstance(resolved, bytes):
            if len(resolved) > 20 * 1024 * 1024:
                raise ValueError("too large")
            stream = BytesIO(resolved)
        elif isinstance(resolved, Path):
            if resolved.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("too large")
            stream = BytesIO(resolved.read_bytes())
        else:
            raise ValueError("resolver must return bytes or Path, never a URL")
        with Image.open(stream) as source:
            if source.format not in ("PNG", "JPEG", "WEBP") or source.width * source.height > 40_000_000:
                raise ValueError("unsupported image")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            size = image.size
        return ImageReader(image), size
    except Exception as exc:
        raise ExportValidationError("ASSET_UNAVAILABLE", "이미지 자산을 읽을 수 없습니다. 업로드 상태와 파일 형식을 확인해 주세요.", "asset_id") from exc


def _draw_object(canvas: Canvas, obj: dict, face_height: float, resolver: AssetResolver | None, warnings: list) -> None:
    if not obj["visible"] or not obj["print_enabled"]:
        return
    canvas.saveState()
    canvas.translate(obj["x_mm"] * mm, (face_height - obj["y_mm"]) * mm)
    canvas.rotate(-obj["rotation_deg"])
    canvas.setFillAlpha(obj["opacity"])
    canvas.setStrokeAlpha(obj["opacity"])
    width, height = obj["width_mm"] * mm, obj["height_mm"] * mm
    kind = obj["type"]
    if kind == "text":
        size = obj["font_size_pt"]
        font_id = _text_font_id(obj)
        ascent, _ = pdfmetrics.getAscentDescent(font_id, size)
        canvas.setFillColor(HexColor(obj["color"]))
        canvas.setFillAlpha(obj["opacity"])
        for index, line in enumerate(_layout_text(obj)):
            line_width = _width(line, size, obj["letter_spacing"], font_id)
            x = (width - line_width) / 2 if obj["align"] == "center" else width - line_width if obj["align"] == "right" else 0
            text = canvas.beginText(x, -ascent - index * size * obj["line_height"])
            text.setFont(font_id, size)
            text.setCharSpace(obj["letter_spacing"])
            text.textOut(line)
            canvas.drawText(text)
    elif kind == "image":
        source, pixels = _resolve_image(obj["asset_id"], resolver)
        crop = normalized_crop(obj.get("crop"))
        clip = canvas.beginPath(); clip.rect(0, -height, width, height)
        canvas.clipPath(clip, stroke=0, fill=0)
        source_width, source_height = width / crop["width"], height / crop["height"]
        canvas.drawImage(source, -crop["x"] * source_width, -(1-crop["y"]) * source_height,
                         width=source_width, height=source_height, mask="auto")
        effective_ppi = resolver_quality_metrics(resolver, obj["asset_id"], pixels, obj)["effective_ppi"]
        if effective_ppi < 300:
            warnings.append({"code": "LOW_PPI", "object_id": obj["id"], "effective_ppi": round(effective_ppi, 1),
                             "message": "배치 크기 기준 해상도가 기본 검토 기준 300ppi 미만입니다."})
    elif kind == "barcode":
        usage = obj.get("barcode_usage", "retail")
        barcode = barcode_geometry(obj["barcode_value"], obj["module_mm"], obj["bar_height_mm"], barcode_usage=usage)
        canvas.setFillColor(HexColor("#ffffff"))
        canvas.setFillAlpha(1)
        canvas.rect(0, -height, width, height, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#000000"))
        for bar in barcode["bars"]:
            canvas.rect(bar["x_mm"] * mm, -barcode["bar_height_mm"] * mm, bar["width_mm"] * mm, barcode["bar_height_mm"] * mm, fill=1, stroke=0)
        canvas.setFont(FONT_ID, 9 * obj["module_mm"] / 0.33)
        canvas.drawCentredString(width/2, -(barcode["bar_height_mm"] + 3.8) * mm, barcode["value"])
        if usage == "sample":
            canvas.setFont(FONT_ID, 7)
            canvas.drawCentredString(width/2, -(barcode["bar_height_mm"] + 8) * mm, "SAMPLE / 검토용")
    else:
        canvas.setFillColor(HexColor(obj.get("fill") or obj["color"]))
        canvas.setStrokeColor(HexColor(obj.get("stroke") or obj["color"]))
        canvas.setFillAlpha(obj["opacity"])
        canvas.setStrokeAlpha(obj["opacity"])
        canvas.setLineWidth(obj["stroke_width_mm"] * mm)
        stroke = int(bool(obj.get("stroke")) and obj["stroke_width_mm"] > 0)
        if obj["shape"] in ("ellipse", "circle"):
            canvas.ellipse(0, -height, width, 0, fill=1, stroke=stroke)
        else:
            canvas.rect(0, -height, width, height, fill=1, stroke=stroke)
    canvas.restoreState()


def _clip_print_area(canvas: Canvas, face: dict, structural_face: dict, bleed_mm: float = 0) -> None:
    """Keep bleed at outer edges, but remove actual holes/notches using even-odd clipping.

    A notch exclusion extends through the outside bleed so ink cannot bridge the
    cut-out. The retained mm contour is a review geometry, not a cutting-machine file.
    """
    width, height = face["width_mm"], face["height_mm"]
    path = canvas.beginPath()
    path.rect(-bleed_mm * mm, -bleed_mm * mm, (width + 2 * bleed_mm) * mm, (height + 2 * bleed_mm) * mm)
    for hole in structural_face["regions"].get("hole", []):
        x, y, r = hole["center_x_mm"], height - hole["center_y_mm"], hole["radius_mm"]
        # PDFPathObject takes width/height, unlike Canvas.ellipse's x2/y2.
        path.ellipse((x-r)*mm, (y-r)*mm, 2*r*mm, 2*r*mm)
    for notch in structural_face["regions"].get("tear_notches", []):
        points = notch["points_mm"]
        path.moveTo(points[0][0]*mm, (height-points[0][1])*mm)
        for x, y in points[1:]:
            path.lineTo(x*mm, (height-y)*mm)
        outside = -bleed_mm if notch["side"] == "left" else width+bleed_mm
        path.lineTo(outside*mm, (height-points[-1][1])*mm)
        path.lineTo(outside*mm, (height-points[0][1])*mm)
        path.close()
    canvas.clipPath(path, stroke=0, fill=0, fillMode=0)


def _draw_face_art(canvas: Canvas, scene: dict, face: dict, structural_face: dict,
                   resolver, warnings: list, bleed_mm: float = 0) -> None:
    canvas.saveState()
    _clip_print_area(canvas, face, structural_face, bleed_mm)
    canvas.setFillColor(HexColor(face["background"]))
    canvas.rect(-bleed_mm*mm, -bleed_mm*mm, (face["width_mm"]+2*bleed_mm)*mm,
                (face["height_mm"]+2*bleed_mm)*mm, fill=1, stroke=0)
    for obj in sorted(face["objects"], key=lambda obj: obj["z_index"]):
        _draw_object(canvas, obj, face["height_mm"], resolver, warnings)
    canvas.restoreState()


def _draw_cut_contour(canvas: Canvas, face: dict, structural_face: dict) -> None:
    width, height = face["width_mm"], face["height_mm"]
    contour = structural_face["regions"].get("cut_contour")
    canvas.saveState()
    canvas.setStrokeColor(HexColor("#674a8f"))
    canvas.setLineWidth(.25*mm)
    if contour:
        path = canvas.beginPath()
        points = contour["points_mm"]
        path.moveTo(points[0][0]*mm, (height-points[0][1])*mm)
        for x, y in points[1:]:
            path.lineTo(x*mm, (height-y)*mm)
        path.close()
        canvas.drawPath(path, fill=0, stroke=1)
    else:
        canvas.rect(.15*mm, .15*mm, (width-.3)*mm, (height-.3)*mm, fill=0, stroke=1)
    canvas.restoreState()


def _draw_finishing_guides(canvas: Canvas, face: dict, structural_face: dict) -> None:
    regions = structural_face["regions"]
    if not regions.get("header"):
        return
    width, height = face["width_mm"], face["height_mm"]
    canvas.saveState()
    canvas.setFont(FONT_ID, 6)
    header = regions["header"]
    y = header["y_mm"] + header["height_mm"]
    canvas.setStrokeColor(HexColor("#795ca5")); canvas.setFillColor(HexColor("#674a8f"))
    canvas.setLineWidth(.18*mm); canvas.setDash(1.5*mm, mm)
    canvas.line(0, (height-y)*mm, width*mm, (height-y)*mm)
    canvas.drawRightString((width-11)*mm, (height-y+1.2)*mm, f"개봉부 {header['height_mm']:g}mm · 가공 검토 가이드")
    zipper = regions.get("zipper")
    if zipper:
        band = zipper["band"]
        canvas.setDash(); canvas.setStrokeColor(HexColor("#007f87")); canvas.setFillColor(HexColor("#007f87"))
        canvas.rect(band["x_mm"]*mm, (height-band["y_mm"]-band["height_mm"])*mm, band["width_mm"]*mm, band["height_mm"]*mm, fill=0, stroke=1)
        line = zipper["line"]
        canvas.setDash(2*mm, .7*mm)
        canvas.line(line["x1_mm"]*mm, (height-line["y1_mm"])*mm, line["x2_mm"]*mm, (height-line["y2_mm"])*mm)
        canvas.drawString((band["x_mm"]+1)*mm, (height-line["y1_mm"]+1)*mm, "지퍼 대역 · 가공 가이드")
    tear = regions.get("tear_line")
    if tear:
        canvas.setDash(1.2*mm, mm); canvas.setStrokeColor(HexColor("#b05a14"))
        canvas.line(tear["x1_mm"]*mm, (height-tear["y1_mm"])*mm, tear["x2_mm"]*mm, (height-tear["y2_mm"])*mm)
    canvas.restoreState()


def _draw_guides(canvas: Canvas, face: dict, page: int, total: int, structural_face: dict) -> None:
    width, height = face["width_mm"], face["height_mm"]
    regions = structural_face["regions"]
    canvas.saveState()
    canvas.saveState()
    _clip_print_area(canvas, face, structural_face)
    for region in regions["no_print"]:
        canvas.setFillColor(Color(0.96, 0.68, 0.18, alpha=0.12))
        canvas.rect(region["x_mm"] * mm, (height - region["y_mm"] - region["height_mm"]) * mm, region["width_mm"] * mm, region["height_mm"] * mm, fill=1, stroke=0)
    canvas.setStrokeColor(HexColor("#db9138"))
    canvas.setLineWidth(0.2 * mm)
    for region in regions["no_print"]:
        canvas.rect(region["x_mm"]*mm,(height-region["y_mm"]-region["height_mm"])*mm,region["width_mm"]*mm,region["height_mm"]*mm,fill=0,stroke=1)
    for fold in regions.get("fold",[]):
        canvas.line(fold["x1_mm"]*mm,(height-fold["y1_mm"])*mm,fold["x2_mm"]*mm,(height-fold["y2_mm"])*mm)
    canvas.restoreState()
    safe = regions["safe"]
    canvas.setStrokeColor(HexColor("#288f82"))
    canvas.setDash(2 * mm, 1.5 * mm)
    canvas.rect(safe["x_mm"] * mm, (height - safe["y_mm"] - safe["height_mm"]) * mm, safe["width_mm"] * mm, safe["height_mm"] * mm, stroke=1, fill=0)
    canvas.setDash()
    _draw_cut_contour(canvas, face, structural_face)
    _draw_finishing_guides(canvas, face, structural_face)
    # Labels stay inside excluded closure/seal bands to avoid artwork overlap.
    canvas.setFillColor(Color(1, 1, 1, alpha=0.94))
    canvas.rect(10.5 * mm, (height - 9.5) * mm, (width - 21) * mm, 8.5 * mm, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#6c432d"))
    label = "검토용 · 제작 사용 불가"
    size = min(9, (width - 24) * mm / pdfmetrics.stringWidth(label, FONT_ID, 1))
    canvas.setFont(FONT_ID, size)
    canvas.drawCentredString(width * mm / 2, (height - 4) * mm, label)
    second = "등록 구조 검토 · 제작 사용 불가" if structural_face and structural_face.get("registered_structure") else "데모 구조 · 제조사 미승인"
    canvas.setFont(FONT_ID, size * 0.76)
    canvas.drawCentredString(width * mm / 2, (height - 7.4) * mm, second)
    canvas.setFillColor(HexColor("#453b38"))
    footer = f"{face['name']}  |  {width:g} × {height:g} mm  |  1:1  |  {page}/{total}"
    size = min(8, (width - 24) * mm / pdfmetrics.stringWidth(footer, FONT_ID, 1))
    canvas.setFont(FONT_ID, size)
    canvas.drawCentredString(width * mm / 2, 5.4 * mm, footer)
    legend = ("보라: 재단 · 원: 구멍 · 주황 점선: 절취 · 청록: 지퍼 · 녹색: 안전영역"
              if regions.get("header") else "실선: 실링·후속 열접착  /  점선: 안전영역")
    size = min(6, (width - 24) * mm / pdfmetrics.stringWidth(legend, FONT_ID, 1))
    canvas.setFont(FONT_ID, size)
    canvas.drawCentredString(width * mm / 2, 2.3 * mm, legend)
    canvas.restoreState()


def _draw_holes(canvas: Canvas, scene: dict, face: dict, *, guides: bool) -> None:
    for hole in holes_for_face(scene,face["id"]):
        x,y=hole["center_x_mm"]*mm,(face["height_mm"]-hole["center_y_mm"])*mm
        canvas.saveState()
        if guides:
            canvas.setStrokeColor(HexColor("#dc4364"))
            canvas.setLineWidth(.2*mm)
            canvas.circle(x,y,hole["diameter_mm"]*mm/2,fill=0,stroke=1)
            canvas.setDash(mm,mm)
            canvas.circle(x,y,(hole["diameter_mm"]/2+2)*mm,fill=0,stroke=1)
        canvas.restoreState()


def _draw_net(canvas: Canvas, scene: dict, geometry: dict, resolver, warnings) -> dict:
    """Review-only assembly sheet includes every actual panel, flap and glue tab."""
    w,h=geometry["net_width_mm"],geometry["net_height_mm"]
    canvas.setPageSize((w*mm,h*mm))
    canvas.setTrimBox((0,0,w*mm,h*mm)); canvas.setBleedBox((0,0,w*mm,h*mm))
    lookup={face["id"]:face for face in scene["faces"]}
    for structural in geometry["faces"]:
        face=lookup[structural["id"]]; net=structural["net"]
        canvas.saveState(); canvas.translate(net["x_mm"]*mm,(h-net["y_mm"]-face["height_mm"])*mm)
        if net.get("rotation_deg")==180:
            canvas.translate(face["width_mm"]*mm,face["height_mm"]*mm);canvas.rotate(180)
        _draw_face_art(canvas, scene, face, structural, resolver, warnings)
        _draw_holes(canvas,scene,face,guides=True)
        _draw_cut_contour(canvas, face, structural)
        _draw_finishing_guides(canvas, face, structural)
        canvas.setFont(FONT_ID,7);canvas.setFillColor(HexColor("#674a8f"))
        canvas.drawString(2*mm,(face["height_mm"]-4)*mm,face["name"]+" ↑")
        canvas.restoreState()
    for part in geometry["structural_parts"]:
        canvas.setFillColor(HexColor("#e9dfc8"));canvas.setStrokeColor(HexColor("#674a8f"))
        canvas.rect(part["x_mm"]*mm,(h-part["y_mm"]-part["height_mm"])*mm,part["width_mm"]*mm,part["height_mm"]*mm,fill=1,stroke=1)
    canvas.setStrokeColor(HexColor("#db9138"));canvas.setDash(2*mm,mm)
    for line in geometry["fold_lines"]:
        canvas.line(line["x1_mm"]*mm,(h-line["y1_mm"])*mm,line["x2_mm"]*mm,(h-line["y2_mm"])*mm)
    canvas.setDash();canvas.setFont(FONT_ID,10);canvas.setFillColor(HexColor("#88452f"))
    canvas.drawString(3*mm,3*mm,"검토용 전개도 · 등록 구조 · 제작 사용 불가" if scene.get("structure_ref") else "검토용 전개도 · 데모 구조 · 제조사 미승인 · 제작 사용 불가")
    canvas.showPage()
    return {"face_id":"net","width_mm":w,"height_mm":h,"role":"assembly_reference","bleed_mm":0}


def _render(project: dict, resolver: AssetResolver | None, production: bool) -> tuple[bytes, dict]:
    from .public_profiles import BASIC_REVIEW_PROFILE_ID, inspect_basic_review
    # Freeze policy on each job. Historical snapshots retain their original geometry.
    profile_id = project.get("review_profile_id")
    basic = None
    if profile_id is not None:
        if profile_id != BASIC_REVIEW_PROFILE_ID:
            raise ExportValidationError("UNKNOWN_REVIEW_PROFILE", "지원하지 않는 검토 출력 프로필입니다.", "review_profile_id")
        # Bound compressed image caching; a scene can contain hundreds of assets.
        resolver = lru_cache(maxsize=2)(resolver) if resolver else None
        basic = inspect_basic_review(project, resolver)
    bleed_mm = 3 if basic else 0
    validation = validate_export(project, production=production)
    scene, warnings = validation["scene"], validation["warnings"]
    if basic:
        warnings = [warning for warning in warnings if warning["code"] != "FINISHED_SIZE"]
        warnings += basic["issues"]
        warnings.append({"code":"BASIC_REVIEW_BLEED","message":"면별 페이지는 재단 치수 바깥 3mm 도련을 포함합니다. 전개도는 조립 참고용입니다."})
    geometry=geometry_for_scene(scene, structure_snapshot=project.get("structure_snapshot"))
    lookup={f["id"]:f for f in scene["faces"]}
    ordered_faces=[lookup[f["id"]] for f in geometry["faces"]]
    pages=[]
    output = BytesIO()
    canvas = Canvas(output, pageCompression=1, invariant=1, pdfVersion=(1,5) if basic else (1,4))
    canvas.setTitle("Phoenix Packaging - 검토용 · 제작 사용 불가")
    canvas.setAuthor("Phoenix Packaging")
    canvas.setSubject("등록 구조 · 제작 사용 불가 / Registered structure review" if scene.get("structure_ref") else "데모 구조 · 제조사 미승인 / Finished-size vector review PDF")
    for page, face in enumerate(ordered_faces, start=1):
        width, height = face["width_mm"] * mm, face["height_mm"] * mm
        bleed = bleed_mm * mm
        canvas.setPageSize((width+2*bleed, height+2*bleed))
        canvas.setTrimBox((bleed, bleed, width+bleed, height+bleed))
        canvas.setBleedBox((0, 0, width+2*bleed, height+2*bleed))
        canvas.saveState()
        canvas.translate(bleed, bleed)
        _draw_face_art(canvas, scene, face, geometry["faces"][page-1], resolver, warnings, bleed_mm)
        _draw_guides(canvas, face, page, len(ordered_faces), geometry["faces"][page-1])
        _draw_holes(canvas,scene,face,guides=True)
        canvas.restoreState()
        canvas.showPage()
        pages.append({"face_id":face["id"],"width_mm":face["width_mm"],"height_mm":face["height_mm"],
                      "media_width_mm":face["width_mm"]+2*bleed_mm,"media_height_mm":face["height_mm"]+2*bleed_mm,
                      "role":"face_review","bleed_mm":bleed_mm})
    if geometry["template_id"] != "three-side-seal":
        pages.append(_draw_net(canvas,scene,geometry,resolver,warnings))
    canvas.save()
    data = output.getvalue()
    verification = None
    if basic:
        from .pdf_verification import verify_review_pdf
        verification = verify_review_pdf(data, scene, pages, bleed_mm, structure_snapshot=project.get("structure_snapshot"))
    manifest = {"schema_version": "1.0", "kind": "review", "review_only": True, "production_enabled": False,
                "approval_status": "registered_review_only" if scene.get("structure_ref") else "demo_unapproved", "template_version_id": scene.get("template_version_id") or DEMO_TEMPLATE_ID,
                "project_id": str(project.get("id", project.get("project_id", ""))),
                "revision": project.get("revision", project.get("revision_number", project.get("base_revision"))),
                "revision_id": project.get("revision_id"),
                "review_profile_id": profile_id, "basic_preflight": basic, "pdf_verification": verification,
                "geometry_hash": geometry["geometry_hash"],
                "structure_ref": scene.get("structure_ref"),
                "generated_at": datetime.now(timezone.utc).isoformat(), "sha256": hashlib.sha256(data).hexdigest(),
                "font": {"id": "NotoSansKR", "sha256": hashlib.sha256(FONT_PATH.read_bytes()).hexdigest(), "embedded": True, "license": "OFL-1.1"},
                "font_weights": _font_manifest(scene),
                "review_structure": {"manufacturer_approved": False, "cut_out_clipping": True,
                    "pouch_features": geometry.get("pouch_features"),
                    "faces": [{"face_id": f["id"], "cut_contour": f["regions"].get("cut_contour"),
                               "holes": f["regions"].get("hole", []), "tear_notches": f["regions"].get("tear_notches", [])}
                              for f in geometry["faces"]]},
                "pages": pages,
                "warnings": warnings, "original_texts": validation["original_texts"],
                "capabilities": {"vector_text": True, "embedded_fonts": True, "original_raster_assets": True,
                                 "pdf_x": False, "cmyk": False, "spot_colors": False, "production": False}}
    return data, manifest


def render_review_pdf(scene: dict, *, asset_resolver: AssetResolver | None = None, production: bool = False) -> bytes:
    return _render(scene, asset_resolver, production)[0]


def export_review_pdf(project: dict, output_path: Path, asset_resolver: AssetResolver | None = None) -> dict:
    """Write PDF plus adjacent .manifest.json. Validate fully before writing."""
    data, manifest = _render(project, asset_resolver, False)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    output_path.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
