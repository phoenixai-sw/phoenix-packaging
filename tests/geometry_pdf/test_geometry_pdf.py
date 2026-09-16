from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
import pytest

from services.api.geometry import GeometryValidationError, default_scene, normalize_mm, validate_dimensions, validate_scene
from services.api.exporters import ExportValidationError, export_review_pdf, render_review_pdf


def test_mm_cm_geometry_and_pdf_dimensions_match():
    mm_geometry = validate_dimensions(230, 310, "mm")
    cm_geometry = validate_dimensions(23, 31, "cm")
    assert mm_geometry == cm_geometry
    for dims in ((230, 310, "mm"), (23, 31, "cm")):
        geometry = validate_dimensions(*dims)
        reader = PdfReader(BytesIO(render_review_pdf(default_scene(geometry["width_mm"], geometry["height_mm"]))))
        assert len(reader.pages) == 2
        for page in reader.pages:
            for box in (page.mediabox, page.trimbox, page.bleedbox):
                assert abs(float(box.width) * 25.4 / 72 - 230) < 0.01
                assert abs(float(box.height) * 25.4 / 72 - 310) < 0.01


@pytest.mark.parametrize("width,height,unit", [(23,31,None),(23,31,"in"),(-2,310,"mm"),(9999,310,"mm"),(230,0,"mm"),(float("nan"),310,"mm"),(True,310,"mm"),("23x31",310,"mm"),(1e308,310,"mm")])
def test_invalid_dimensions_rejected(width, height, unit):
    with pytest.raises(GeometryValidationError):
        validate_dimensions(width, height, unit)


def test_decimal_rounding_preserves_software_tolerance():
    assert normalize_mm("23.12345", "cm") == 231.2345
    assert normalize_mm("231.2345", "mm") == 231.2345


@pytest.mark.parametrize("kwargs", [{"seal_mm":-1},{"seal_mm":41},{"safe_mm":-1},{"bleed_mm":-1},{"bleed_mm":11},{"left_seal_mm":35,"right_seal_mm":35}])
def test_impossible_seal_or_margins_rejected(kwargs):
    with pytest.raises(GeometryValidationError):
        validate_dimensions(60, 80, "mm", **kwargs)


def test_korean_english_special_text_preserved_and_font_embedded(tmp_path):
    scene = default_scene()
    text = "높은 단백질 함량\nProtein 20g · 100%\n원재료: 대두 & 우유 (국산)"
    scene["faces"][0]["objects"][0]["text"] = text
    scene["faces"][0]["objects"][0]["height_mm"] = 80
    output = tmp_path / "review.pdf"
    manifest = export_review_pdf({"id":"project-test","revision":7,"scene":scene}, output)
    reader = PdfReader(output)
    extracted = reader.pages[0].extract_text()
    for line in text.splitlines():
        assert line in extracted
    assert "검토용 · 제작 사용 불가" in extracted
    assert "데모 구조 · 제조사 미승인" in extracted
    assert manifest["original_texts"][0]["text"] == text
    assert json.loads(output.with_suffix(".manifest.json").read_text(encoding="utf-8"))["revision"] == 7
    assert manifest["font"]["embedded"]
    embedded = [font.get_object().get("/FontDescriptor") for font in reader.pages[0]["/Resources"]["/Font"].values()]
    assert any(descriptor and "/FontFile2" in descriptor.get_object() for descriptor in embedded)


def test_missing_glyph_blocks_instead_of_replacement_box():
    scene = default_scene()
    scene["faces"][0]["objects"][0]["text"] = "없는 글자 \U0010ffff"
    with pytest.raises(ExportValidationError, match="U\\+10FFFF") as error:
        render_review_pdf(scene)
    assert error.value.code == "MISSING_GLYPH"


def test_wrapping_preserves_original_and_rejects_height_overflow(tmp_path):
    scene = default_scene()
    obj = scene["faces"][0]["objects"][0]
    obj.update(text="한글과 English 123 문구를 줄바꿈해서 보존", width_mm=45, height_mm=100, font_size_pt=18)
    original = deepcopy(scene)
    manifest = export_review_pdf(scene, tmp_path / "wrapped.pdf")
    assert "".join(manifest["original_texts"][0]["rendered_lines"]) == obj["text"]
    assert scene == original
    obj["height_mm"] = 3
    with pytest.raises(ExportValidationError) as error:
        render_review_pdf(scene)
    assert error.value.code == "TEXT_OVERFLOW"


def test_text_inside_safe_region_and_rotated_extent_checked():
    scene = default_scene()
    scene["faces"][0]["objects"][0]["x_mm"] = 0
    with pytest.raises(GeometryValidationError) as error:
        validate_scene(scene)
    assert error.value.code == "TEXT_OUTSIDE_SAFE_AREA"
    scene["faces"][0]["objects"][0].update(x_mm=20, rotation_deg=90)
    with pytest.raises(GeometryValidationError):
        validate_scene(scene)


def test_all_faces_required_and_dimensions_must_match():
    scene = default_scene()
    scene["faces"][1]["width_mm"] = 200
    with pytest.raises(GeometryValidationError) as error:
        validate_scene(scene)
    assert error.value.code == "FACE_SIZE_MISMATCH"
    scene["faces"] = scene["faces"][:1]
    with pytest.raises(GeometryValidationError):
        validate_scene(scene)


def test_production_never_enabled_even_with_forged_approval():
    project = {"scene":default_scene(), "approval_status":"approved", "kind":"production"}
    with pytest.raises(ExportValidationError) as error:
        render_review_pdf(project)
    assert error.value.code == "PRODUCTION_EXPORT_DISABLED"
    with pytest.raises(ExportValidationError):
        render_review_pdf(default_scene(), production=True)


def _image_object(asset_id="asset-test"):
    return {"id":"image-test","type":"image","face_id":"front","x_mm":20,"y_mm":100,"width_mm":70,"height_mm":50,"asset_id":asset_id}


@pytest.mark.parametrize("url", ["https://example.com/image.png","file:///etc/passwd","../secret.png","data:image/png;base64,abc"])
def test_external_asset_ids_rejected_without_loading(url):
    scene = default_scene()
    scene["faces"][0]["objects"].append(_image_object(url))
    with pytest.raises(GeometryValidationError) as error:
        render_review_pdf(scene, asset_resolver=lambda _: pytest.fail("must not call resolver"))
    assert error.value.code == "EXTERNAL_ASSET_FORBIDDEN"


def test_resolver_never_passes_url_to_reportlab():
    scene = default_scene()
    scene["faces"][0]["objects"].append(_image_object())
    with pytest.raises(ExportValidationError) as error:
        render_review_pdf(scene, asset_resolver=lambda _: "https://example.com/forbidden.png")
    assert error.value.code == "ASSET_UNAVAILABLE"


def test_original_raster_embedding_alpha_and_ppi_warning(tmp_path):
    scene = default_scene()
    scene["faces"][0]["objects"].append(_image_object())
    source = BytesIO()
    Image.new("RGBA", (400,300), (220,80,90,120)).save(source, "PNG")
    manifest = export_review_pdf(scene, tmp_path / "image.pdf", lambda _: source.getvalue())
    reader = PdfReader(tmp_path / "image.pdf")
    xobjects = reader.pages[0]["/Resources"]["/XObject"]
    assert any(item.get_object().get("/Width") == 400 for item in xobjects.values())
    assert any(item.get_object().get("/SMask") for item in xobjects.values())
    assert any(warning["code"] == "LOW_PPI" for warning in manifest["warnings"])


def test_hidden_or_nonprinting_text_not_rendered_but_manifest_preserves(tmp_path):
    scene = default_scene()
    scene["faces"][0]["objects"][0]["visible"] = False
    scene["faces"][1]["objects"][0]["print_enabled"] = False
    manifest = export_review_pdf(scene, tmp_path / "hidden.pdf")
    extracted = "".join(page.extract_text() for page in PdfReader(tmp_path / "hidden.pdf").pages)
    assert "높은 단백질 함량" not in extracted
    assert "제품 표시사항" not in extracted
    assert len(manifest["original_texts"]) == 2


def test_html_is_literal_vector_text_not_markup():
    scene = default_scene()
    scene["faces"][0]["objects"][0]["text"] = "<script>alert(1)</script>"
    extracted = PdfReader(BytesIO(render_review_pdf(scene))).pages[0].extract_text()
    assert "<script>alert(1)</script>" in extracted


def test_api_snapshot_revision_is_retained_in_manifest(tmp_path):
    scene = default_scene()
    manifest = export_review_pdf({"id":"project-from-api", "base_revision":9, "revision_id":"revision-id", "scene":scene}, tmp_path / "api-snapshot.pdf")
    assert manifest["revision"] == 9
    assert manifest["revision_id"] == "revision-id"
    assert manifest["geometry_hash"] == validate_dimensions(230,310,"mm")["geometry_hash"]
