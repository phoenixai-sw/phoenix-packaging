"""Assert final PDF pixels, embedded fonts, and independent sample-barcode decode."""
from copy import deepcopy
from io import BytesIO
import hashlib

from pypdf import PdfReader
from pypdf.generic import ContentStream
import pypdfium2 as pdfium
import pytest
from reportlab.lib.units import mm

from services.api.geometry import new_scene, geometry_for_scene, validate_scene, barcode_geometry
from services.api.exporters.review_pdf import (
    export_review_pdf, ExportValidationError, _font, _width, _layout_text,
    FONT_ID, FONT_BOLD_ID, FONT_BOLD_PATH, ROOT,
)
from services.api.exporters.production import _production_pdf
from services.api.exporters.preflight import preflight_project
from services.api.exporters.public_profiles import BASIC_REVIEW_PROFILE_ID


def project(scene):
    return {"scene": scene, "review_profile_id": BASIC_REVIEW_PROFILE_ID}


def text_object(**changes):
    return {"id": "title", "type": "text", "face_id": "front", "x_mm": 20, "y_mm": 50,
            "width_mm": 110, "height_mm": 15, "font_size_pt": 24, "font_weight": 400,
            "text": "한글 King Kong Bites", "line_height": 1.2, "letter_spacing": 0, **changes}


def render_page(data, index=0):
    document = pdfium.PdfDocument(data)
    page = document[index]
    bitmap = page.render(scale=4)
    image = bitmap.to_pil().convert("RGB").copy()
    bitmap.close(); page.close(); document.close()
    return image


def pixel(image, x_mm, y_mm, bleed_mm=3):
    return image.getpixel((round((x_mm+bleed_mm)*mm*4), round((y_mm+bleed_mm)*mm*4)))


@pytest.mark.parametrize("shape", ["round", "v"])
def test_pdf_hole_and_notches_remove_art_with_real_clipping_and_mirror(tmp_path, shape):
    scene = new_scene("three-side-seal", 90, 180)
    scene["pouch_features"] = {"header_height_mm": 30, "zipper_enabled": True, "zipper_y_mm": 35,
                               "zipper_band_mm": 6, "tear_enabled": True, "tear_y_mm": 24,
                               "notch_depth_mm": 3, "notch_height_mm": 4, "notch_shape": shape}
    scene["holes"] = [{"id": "hanger", "face_id": "front", "center_x_mm": 35,
                       "center_y_mm": 17, "diameter_mm": 6}]
    scene["geometry_hash"] = geometry_for_scene(scene)["geometry_hash"]
    for face in scene["faces"]:
        face["background"] = "#3e2448"
        # A late full-face object must also be physically clipped, not cover the cutouts.
        face["objects"] = [{"id": f"art-{face['id']}", "type": "shape", "face_id": face["id"],
                            "x_mm": -3, "y_mm": -3, "width_mm": 96, "height_mm": 186,
                            "color": "#3e2448", "fill": "#3e2448", "z_index": 100}]
    output = tmp_path / f"{shape}.pdf"
    manifest = export_review_pdf(project(scene), output)
    reader = PdfReader(output)
    for index, center in [(0, 35), (1, 55)]:
        image = render_page(output.read_bytes(), index)
        assert min(pixel(image, center, 17)) > 245
        assert min(pixel(image, .7, 24)) > 245
        assert min(pixel(image, 89.3, 24)) > 245
        assert min(pixel(image, -1, 24)) > 245
        assert min(pixel(image, .7, 20)) < 200
        # Round U removes more near its shoulder than the triangular V.
        assert (min(pixel(image, 2.1, 23)) > 245) == (shape == "round")
        assert min(pixel(image, 90-center, 17)) < 200
        stream = ContentStream(reader.pages[index].get_contents(), reader)
        assert any(operator == b"W*" for _, operator in stream.operations)
        assert "지퍼 대역" in reader.pages[index].extract_text()
        assert "절취" in reader.pages[index].extract_text()
    cuts = manifest["review_structure"]["faces"]
    assert [f["holes"][0]["center_x_mm"] for f in cuts] == [35, 55]
    assert all(f["cut_contour"]["closed"] for f in cuts)
    assert not manifest["review_structure"]["manufacturer_approved"]
    assert not preflight_project(project(scene))["production_allowed"]


@pytest.mark.parametrize("module,height,rotation", [(.264, 18.28, 0), (.33, 22.85, 90), (.66, 45.7, 180)])
def test_sample_barcode_final_pdf_decodes_with_label_without_retail_authority(tmp_path, module, height, rotation):
    scene = new_scene("three-side-seal", 160, 230)
    spec = barcode_geometry("9521234567899", module, height, barcode_usage="sample")
    scene["faces"][0]["objects"] = [{"id": "sample", "type": "barcode", "face_id": "front",
        "x_mm": 100 if rotation else 30, "y_mm": 130 if rotation else 70,
        "width_mm": spec["width_mm"], "height_mm": spec["height_mm"], "rotation_deg": rotation,
        "barcode_value": spec["value"], "barcode_usage": "sample", "barcode_owned": False,
        "module_mm": module, "bar_height_mm": height}]
    output = tmp_path / "sample.pdf"
    manifest = export_review_pdf(project(scene), output)
    text = PdfReader(output).pages[0].extract_text()
    assert "SAMPLE / 검토용" in text and "9521234567899" in text
    checks = manifest["pdf_verification"]["barcode_checks"]
    assert checks[0]["digital_decode"] == "passed"
    assert checks[0]["barcode_usage"] == "sample"
    assert checks[0]["physical_print_scan"] == "not_for_real_world_use"
    assert spec["height_mm"] == pytest.approx(height+9)
    preflight = preflight_project(project(scene))
    assert not preflight["production_allowed"]
    assert any("SAMPLE" in issue["code"] for issue in preflight["issues"])


def test_bold_metrics_can_reject_a_box_that_fits_regular():
    _font(); _font(700)
    phrase = "KING KONG BITES"
    regular = _width(phrase, 20, 0, FONT_ID)
    bold = _width(phrase, 20, 0, FONT_BOLD_ID)
    assert bold > regular
    value = text_object(text=phrase, font_size_pt=20, width_mm=(regular+bold)/2/mm, height_mm=8)
    assert _layout_text(value) == [phrase]
    with pytest.raises(ExportValidationError) as error:
        _layout_text({**value, "font_weight": 700})
    assert error.value.code == "TEXT_OVERFLOW"


def test_actual_bold_and_regular_font_streams_are_distinct_and_match_browser(tmp_path):
    assert hashlib.sha256(FONT_BOLD_PATH.read_bytes()).digest() == hashlib.sha256((ROOT/"apps/web/public/fonts/NotoSansKR-Bold.ttf").read_bytes()).digest()
    scene = new_scene("three-side-seal", 160, 230)
    scene["faces"][0]["objects"] = [text_object(), text_object(id="bold", y_mm=80, font_weight=700)]
    output = tmp_path / "weights.pdf"
    manifest = export_review_pdf(project(scene), output)
    fonts = manifest["pdf_verification"]["used_fonts_embedded"]
    assert any("NotoSansKR-Bold" in name for name in fonts)
    assert any("NotoSansKR-Thin" in name for name in fonts)  # Legacy 400 instance keeps its upstream internal name.
    assert [entry["weight"] for entry in manifest["font_weights"]] == [400, 700]
    assert manifest["font_weights"][0]["sha256"] != manifest["font_weights"][1]["sha256"]
    production_reader = PdfReader(BytesIO(_production_pdf(validate_scene(scene), None)))
    assert "한글 King Kong Bites" in production_reader.pages[0].extract_text()
    font_names = [str(ref.get_object().get("/BaseFont")) for ref in production_reader.pages[0]["/Resources"]["/Font"].values()]
    assert any("NotoSansKR-Bold" in name for name in font_names)


def test_box_top_is_its_own_print_page_with_bold_original_text(tmp_path):
    scene = new_scene("folding-box", 260, 360, depth_mm=130)
    top = next(face for face in scene["faces"] if face["id"] == "top")
    top["objects"] = [text_object(id="top-title", face_id="top", text="윗면 · TOP\nKing Kong Bites", y_mm=25, height_mm=40, font_weight=700)]
    output = tmp_path / "box.pdf"
    manifest = export_review_pdf(project(scene), output)
    reader = PdfReader(output)
    assert len(reader.pages) == 7
    assert "윗면 · TOP" in reader.pages[4].extract_text()
    assert "윗면 · TOP" not in reader.pages[0].extract_text()
    assert abs(float(reader.pages[4].trimbox.width)/mm-260) < .01
    assert abs(float(reader.pages[4].trimbox.height)/mm-130) < .01
    assert manifest["pages"][4]["face_id"] == "top"
