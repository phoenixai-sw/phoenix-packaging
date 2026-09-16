"""Vector review PDF; deliberately cannot produce manufacturing-ready files.

The page is the finished face size in mm (not an A4 screenshot). Structural
guides and review labels are deliberately visible on this non-production file.
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

from PIL import Image
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from ..geometry import DEMO_TEMPLATE_ID, GeometryValidationError, validate_scene, geometry_for_scene, holes_for_face, barcode_geometry

ROOT = Path(__file__).resolve().parents[3]
FONT_PATH = ROOT / "fixtures" / "fonts" / "NotoSansKR-Regular.ttf"
FONT_ID = "PhoenixNotoSansKR"
FONT_LOCK = threading.Lock()
AssetResolver = Callable[[str], Path | bytes]


class ExportValidationError(GeometryValidationError):
    pass


@lru_cache(maxsize=1)
def _font() -> TTFont:
    if not FONT_PATH.is_file():
        raise ExportValidationError("FONT_UNAVAILABLE", "검증된 한글 글꼴 파일이 없습니다.", "font_id")
    with FONT_LOCK:
        if FONT_ID not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT_ID, str(FONT_PATH)))
    return pdfmetrics.getFont(FONT_ID)


def _scene_from_project(project: dict) -> dict:
    if project.get("schema_version") == "1.0":
        return project
    for key in ("scene", "draft_scene", "draft", "scene_json"):
        if isinstance(project.get(key), dict):
            value = project[key]
            return value.get("scene", value)
    raise ExportValidationError("SCENE_REQUIRED", "출력할 저장된 장면이 없습니다.")


def _width(text: str, size: float, spacing: float) -> float:
    return pdfmetrics.stringWidth(text, FONT_ID, size) + max(0, len(text) - 1) * spacing


def _layout_text(obj: dict) -> list[str]:
    """Character wrapping matching Konva wrap='char'; retain original in manifest."""
    font = _font()
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
            if _width(char, size, spacing) > maximum + 0.001:
                raise ExportValidationError("TEXT_OVERFLOW", "한 글자가 텍스트 상자보다 큽니다. 글자 크기나 상자를 조정해 주세요.", f"objects.{obj['id']}")
            candidate = line + char
            if line and _width(candidate, size, spacing) > maximum + 0.001:
                lines.append(line)
                line = char
            else:
                line = candidate
        lines.append(line)
    ascent, descent = pdfmetrics.getAscentDescent(FONT_ID, size)
    required = ascent - descent + max(0, len(lines) - 1) * size * obj["line_height"]
    if required > obj["height_mm"] * mm + 0.001:
        raise ExportValidationError("TEXT_OVERFLOW", "문구가 텍스트 상자 높이를 넘습니다. 글자 크기나 상자를 조정해 주세요.", f"objects.{obj['id']}")
    return lines


def validate_export(project: dict, *, production: bool = False) -> dict:
    if production or project.get("kind") == "production" or project.get("export_kind") == "production":
        raise ExportValidationError("PRODUCTION_EXPORT_DISABLED", "데모 구조는 검토용 출력만 가능합니다. 제작용 출력은 제조사 승인 후 지원합니다.", "kind")
    scene = validate_scene(_scene_from_project(project))
    _font()
    warnings = [
        {"code": "DEMO_UNAPPROVED", "message": "데모 구조 · 제조사 미승인"},
        {"code": "REVIEW_ONLY", "message": "검토용 · 제작 사용 불가"},
        {"code": "RGB_REVIEW", "message": "RGB 검토 PDF입니다. PDF/X·CMYK·별색·화이트 잉크 출력이 아닙니다."},
        {"code": "FINISHED_SIZE", "message": "페이지는 완성 치수와 같습니다. 바깥 블리드는 검토 페이지에서 잘립니다."},
    ]
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
            image = source.convert("RGBA")
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
        ascent, _ = pdfmetrics.getAscentDescent(FONT_ID, size)
        canvas.setFillColor(HexColor(obj["color"]))
        canvas.setFillAlpha(obj["opacity"])
        for index, line in enumerate(_layout_text(obj)):
            line_width = _width(line, size, obj["letter_spacing"])
            x = (width - line_width) / 2 if obj["align"] == "center" else width - line_width if obj["align"] == "right" else 0
            text = canvas.beginText(x, -ascent - index * size * obj["line_height"])
            text.setFont(FONT_ID, size)
            text.setCharSpace(obj["letter_spacing"])
            text.textOut(line)
            canvas.drawText(text)
    elif kind == "image":
        source, pixels = _resolve_image(obj["asset_id"], resolver)
        canvas.drawImage(source, 0, -height, width=width, height=height, mask="auto")
        effective_ppi = min(pixels[0] / (obj["width_mm"] / 25.4), pixels[1] / (obj["height_mm"] / 25.4))
        if effective_ppi < 300:
            warnings.append({"code": "LOW_PPI", "object_id": obj["id"], "effective_ppi": round(effective_ppi, 1),
                             "message": "원본 해상도가 300ppi 미만입니다. 제조사 기준 확인이 필요합니다."})
    elif kind == "barcode":
        barcode = barcode_geometry(obj["barcode_value"], obj["module_mm"], obj["bar_height_mm"])
        canvas.setFillColor(HexColor("#ffffff"))
        canvas.setFillAlpha(1)
        canvas.rect(0, -height, width, height, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#000000"))
        for bar in barcode["bars"]:
            canvas.rect(bar["x_mm"] * mm, -barcode["bar_height_mm"] * mm, bar["width_mm"] * mm, barcode["bar_height_mm"] * mm, fill=1, stroke=0)
        canvas.setFont(FONT_ID, 9 * obj["module_mm"] / 0.33)
        canvas.drawCentredString(width/2, -height + 1.2 * mm, barcode["value"])
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


def _draw_guides(canvas: Canvas, face: dict, page: int, total: int, structural_face: dict) -> None:
    width, height = face["width_mm"], face["height_mm"]
    regions = structural_face["regions"]
    canvas.saveState()
    for region in regions["no_print"]:
        canvas.setFillColor(Color(0.96, 0.68, 0.18, alpha=0.12))
        canvas.rect(region["x_mm"] * mm, (height - region["y_mm"] - region["height_mm"]) * mm, region["width_mm"] * mm, region["height_mm"] * mm, fill=1, stroke=0)
    canvas.setStrokeColor(HexColor("#db9138"))
    canvas.setLineWidth(0.2 * mm)
    for region in regions["no_print"]:
        canvas.rect(region["x_mm"]*mm,(height-region["y_mm"]-region["height_mm"])*mm,region["width_mm"]*mm,region["height_mm"]*mm,fill=0,stroke=1)
    for fold in regions.get("fold",[]):
        canvas.line(fold["x1_mm"]*mm,(height-fold["y1_mm"])*mm,fold["x2_mm"]*mm,(height-fold["y2_mm"])*mm)
    safe = regions["safe"]
    canvas.setStrokeColor(HexColor("#288f82"))
    canvas.setDash(2 * mm, 1.5 * mm)
    canvas.rect(safe["x_mm"] * mm, (height - safe["y_mm"] - safe["height_mm"]) * mm, safe["width_mm"] * mm, safe["height_mm"] * mm, stroke=1, fill=0)
    canvas.setDash()
    canvas.setStrokeColor(HexColor("#674a8f"))
    canvas.setLineWidth(0.25 * mm)
    canvas.rect(0.15 * mm, 0.15 * mm, (width - 0.3) * mm, (height - 0.3) * mm, stroke=1, fill=0)
    # Labels stay inside excluded closure/seal bands to avoid artwork overlap.
    canvas.setFillColor(Color(1, 1, 1, alpha=0.94))
    canvas.rect(10.5 * mm, (height - 9.5) * mm, (width - 21) * mm, 8.5 * mm, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#6c432d"))
    label = "검토용 · 제작 사용 불가"
    size = min(9, (width - 24) * mm / pdfmetrics.stringWidth(label, FONT_ID, 1))
    canvas.setFont(FONT_ID, size)
    canvas.drawCentredString(width * mm / 2, (height - 4) * mm, label)
    second = "데모 구조 · 제조사 미승인"
    canvas.setFont(FONT_ID, size * 0.76)
    canvas.drawCentredString(width * mm / 2, (height - 7.4) * mm, second)
    canvas.setFillColor(HexColor("#453b38"))
    footer = f"{face['name']}  |  {width:g} × {height:g} mm  |  1:1  |  {page}/{total}"
    size = min(8, (width - 24) * mm / pdfmetrics.stringWidth(footer, FONT_ID, 1))
    canvas.setFont(FONT_ID, size)
    canvas.drawCentredString(width * mm / 2, 5.4 * mm, footer)
    legend = "실선: 실링·후속 열접착  /  점선: 안전영역"
    size = min(6, (width - 24) * mm / pdfmetrics.stringWidth(legend, FONT_ID, 1))
    canvas.setFont(FONT_ID, size)
    canvas.drawCentredString(width * mm / 2, 2.3 * mm, legend)
    canvas.restoreState()


def _draw_holes(canvas: Canvas, scene: dict, face: dict, *, guides: bool) -> None:
    for hole in holes_for_face(scene,face["id"]):
        x,y=hole["center_x_mm"]*mm,(face["height_mm"]-hole["center_y_mm"])*mm
        canvas.saveState()
        canvas.setFillColor(HexColor("#ffffff"))
        canvas.circle(x,y,hole["diameter_mm"]*mm/2,fill=1,stroke=0)
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
        canvas.setFillColor(HexColor(face["background"]))
        canvas.rect(0,0,face["width_mm"]*mm,face["height_mm"]*mm,fill=1,stroke=0)
        for obj in sorted(face["objects"],key=lambda o:o["z_index"]):
            _draw_object(canvas,obj,face["height_mm"],resolver,warnings)
        _draw_holes(canvas,scene,face,guides=True)
        canvas.setStrokeColor(HexColor("#674a8f"));canvas.setLineWidth(.2*mm)
        canvas.rect(0,0,face["width_mm"]*mm,face["height_mm"]*mm,fill=0,stroke=1)
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
    canvas.drawString(3*mm,3*mm,"검토용 전개도 · 데모 구조 · 제조사 미승인 · 제작 사용 불가")
    canvas.showPage()
    return {"face_id":"net","width_mm":w,"height_mm":h}


def _render(project: dict, resolver: AssetResolver | None, production: bool) -> tuple[bytes, dict]:
    validation = validate_export(project, production=production)
    scene, warnings = validation["scene"], validation["warnings"]
    geometry=geometry_for_scene(scene)
    lookup={f["id"]:f for f in scene["faces"]}
    ordered_faces=[lookup[f["id"]] for f in geometry["faces"]]
    pages=[]
    output = BytesIO()
    canvas = Canvas(output, pageCompression=1, invariant=1)
    canvas.setTitle("Phoenix Packaging - 검토용 · 제작 사용 불가")
    canvas.setAuthor("Phoenix Packaging")
    canvas.setSubject("데모 구조 · 제조사 미승인 / Finished-size vector review PDF")
    for page, face in enumerate(ordered_faces, start=1):
        width, height = face["width_mm"] * mm, face["height_mm"] * mm
        canvas.setPageSize((width, height))
        canvas.setTrimBox((0, 0, width, height))
        canvas.setBleedBox((0, 0, width, height))
        canvas.setFillColor(HexColor(face["background"]))
        canvas.rect(0, 0, width, height, fill=1, stroke=0)
        for obj in sorted(face["objects"], key=lambda obj: obj["z_index"]):
            _draw_object(canvas, obj, face["height_mm"], resolver, warnings)
        _draw_holes(canvas,scene,face,guides=True)
        _draw_guides(canvas, face, page, len(ordered_faces), geometry["faces"][page-1])
        canvas.showPage()
        pages.append({"face_id":face["id"],"width_mm":face["width_mm"],"height_mm":face["height_mm"]})
    if geometry["template_id"] != "three-side-seal":
        pages.append(_draw_net(canvas,scene,geometry,resolver,warnings))
    canvas.save()
    data = output.getvalue()
    manifest = {"schema_version": "1.0", "kind": "review", "review_only": True, "production_enabled": False,
                "approval_status": "demo_unapproved", "template_version_id": scene.get("template_version_id") or DEMO_TEMPLATE_ID,
                "project_id": str(project.get("id", project.get("project_id", ""))),
                "revision": project.get("revision", project.get("revision_number", project.get("base_revision"))),
                "revision_id": project.get("revision_id"),
                "geometry_hash": geometry["geometry_hash"],
                "generated_at": datetime.now(timezone.utc).isoformat(), "sha256": hashlib.sha256(data).hexdigest(),
                "font": {"id": "NotoSansKR", "sha256": hashlib.sha256(FONT_PATH.read_bytes()).hexdigest(), "embedded": True, "license": "OFL-1.1"},
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
