from copy import deepcopy
from io import BytesIO

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject, NameObject
import pypdfium2 as pdfium
import pytest

from services.api.geometry import new_scene, barcode_geometry, GeometryValidationError
from services.api.exporters import export_review_pdf, preflight_project
from services.api.exporters.pdf_verification import verify_review_pdf
from services.api.exporters.public_profiles import BASIC_REVIEW_PROFILE_ID, public_profile, inspect_basic_review, compare_supplier_capabilities
from services.api.production_routes import public_preflight


def project(kind="three-side-seal", **dimensions):
    return {"id": "spec-fixture", "review_profile_id": BASIC_REVIEW_PROFILE_ID,
            "scene": new_scene(kind, 160, 230, **dimensions)}


def text_object(**updates):
    return {"id": "korean", "type": "text", "face_id": "front", "x_mm": 20, "y_mm": 40,
            "width_mm": 100, "height_mm": 30, "text": "한글 원문 · PHOENIX 100%", "font_size_pt": 14, **updates}


def barcode_object(module=.33, height=22.85, rotation=0):
    barcode = barcode_geometry("0123456789012", module, height)
    return {"id": "ean", "type": "barcode", "face_id": "front", "x_mm": 100 if rotation==180 else 85, "y_mm": 90,
            "width_mm": barcode["width_mm"], "height_mm": barcode["height_mm"], "rotation_deg": rotation,
            "barcode_value": barcode["value"], "module_mm": module, "bar_height_mm": height}


@pytest.mark.parametrize("module,height,valid", [(.2639,22.85,False),(.264,18.2799,False),(.264,18.28,True),
    (.33,22.8499,False),(.33,22.85,True),(.66,45.7,True),(.6601,45.7,False),(.33,45.7001,False)])
def test_gs1_consumer_barcode_boundary(module, height, valid):
    if not valid:
        with pytest.raises(GeometryValidationError): barcode_geometry("0123456789012", module, height)
    else:
        result = barcode_geometry("0123456789012", module, height)
        assert result["quiet_left_mm"] == pytest.approx(11*module)
        assert result["quiet_right_mm"] == pytest.approx(7*module)
        assert result["width_mm"] == pytest.approx(113*module)


@pytest.mark.parametrize("kind,extra,count", [("three-side-seal",{},2),("stand-up-pouch",{"bottom_mm":60},4),("folding-box",{"depth_mm":60},7)])
def test_actual_pdf_boxes_embedding_korean_and_bleed_pixels(tmp_path, kind, extra, count):
    value = project(kind, **extra)
    value["scene"]["faces"][0]["objects"] = [text_object()]
    value["scene"]["faces"][0]["background"] = "#214c39"
    output = tmp_path / "review.pdf"
    manifest = export_review_pdf(value, output)
    reader = PdfReader(output)
    assert reader.pdf_header == "%PDF-1.5" and len(reader.pages) == count
    assert text_object()["text"] in reader.pages[0].extract_text()
    assert manifest["pdf_verification"]["used_fonts_embedded"]
    for page, dimensions in zip(reader.pages, manifest["pages"]):
        bleed = 0 if dimensions["face_id"] == "net" else 3
        assert float(page.trimbox.width)*25.4/72 == pytest.approx(dimensions["width_mm"], abs=.01)
        assert float(page.trimbox.height)*25.4/72 == pytest.approx(dimensions["height_mm"], abs=.01)
        assert float(page.trimbox.left)*25.4/72 == pytest.approx(bleed, abs=.01)
        assert float(page.mediabox.width)*25.4/72 == pytest.approx(dimensions["width_mm"]+2*bleed, abs=.01)
    document = pdfium.PdfDocument(output)
    page = document[0]; bitmap = page.render(scale=1)
    try:
        image = bitmap.to_pil().convert("RGB")
        assert image.getpixel((1,1)) == (33,76,57)  # Actual ink outside TrimBox, not only metadata.
    finally: bitmap.close(); page.close(); document.close()


@pytest.mark.parametrize("module,height,rotation",[(.264,18.28,0),(.33,22.85,90),(.66,45.7,180)])
def test_final_bleed_pdf_decodes_actual_min_nominal_max_barcodes(tmp_path,module,height,rotation):
    value = project()
    value["scene"]["faces"][0]["objects"] = [barcode_object(module,height,rotation)]
    manifest = export_review_pdf(value,tmp_path/"barcode.pdf")
    checks = manifest["pdf_verification"]["barcode_checks"]
    assert len(checks)==1 and checks[0]["value"]=="0123456789012" and checks[0]["digital_decode"]=="passed"


@pytest.mark.parametrize("width,expected",[(50.8,True),(50.8001,False)])
def test_300_effective_ppi_real_pixel_boundary_remains_reviewable(tmp_path,width,expected):
    value = project(); stream = BytesIO(); Image.new("RGB",(600,600),"red").save(stream,"PNG")
    value["scene"]["faces"][0]["objects"] = [{"id":"photo","type":"image","face_id":"front","asset_id":"asset",
        "x_mm":20,"y_mm":40,"width_mm":width,"height_mm":50.8}]
    basic = inspect_basic_review(value,lambda _:stream.getvalue())
    assert basic["measurements"][0]["passed"] is expected
    assert ("BASIC_LOW_PPI" in {i["code"] for i in basic["issues"]}) is not expected
    manifest = export_review_pdf(value,tmp_path/"image.pdf",lambda _:stream.getvalue())
    assert manifest["production_enabled"] is False


def test_font_overflow_asset_failure_and_fake_profile_do_not_write_pdf(tmp_path):
    for mode, code in (("glyph","MISSING_GLYPH"),("overflow","TEXT_OVERFLOW"),("asset","ASSET_UNAVAILABLE"),("supplier","UNKNOWN_REVIEW_PROFILE")):
        value = project(); obj = text_object()
        if mode=="glyph": obj["text"]="\U0010ffff"
        if mode=="overflow": obj["height_mm"]=1
        if mode=="asset": obj={"id":"photo","type":"image","face_id":"front","asset_id":"asset","x_mm":20,"y_mm":40,"width_mm":30,"height_mm":30}
        if mode=="supplier": value["review_profile_id"]="hansung-flexible-public-20260917"
        value["scene"]["faces"][0]["objects"]=[obj]
        with pytest.raises(GeometryValidationError) as exc: export_review_pdf(value,tmp_path/f"{mode}.pdf",lambda _:"https://untrusted.example/image.png")
        assert exc.value.code==code and not (tmp_path/f"{mode}.pdf").exists()


def test_pdf_verifier_rejects_tampered_boxes_and_missing_embedded_fonts(tmp_path):
    value=project(); value["scene"]["faces"][0]["objects"]=[text_object()]
    path=tmp_path/"review.pdf"; manifest=export_review_pdf(value,path)
    for mode, code in (("box","PDF_PHYSICAL_BOX_MISMATCH"),("font","PDF_FONT_NOT_EMBEDDED")):
        reader=PdfReader(path); writer=PdfWriter(); writer.append(reader)
        if mode=="box": writer.pages[0][NameObject("/TrimBox")]=RectangleObject([0,0,100,100])
        else:
            for font in writer.pages[0]["/Resources"]["/Font"].values():
                descriptor=font.get_object().get("/FontDescriptor")
                if descriptor and "/FontFile2" in descriptor.get_object(): del descriptor.get_object()["/FontFile2"]
        stream=BytesIO(); writer.write(stream)
        with pytest.raises(GeometryValidationError) as exc: verify_review_pdf(stream.getvalue(),value["scene"],manifest["pages"],3)
        assert exc.value.code==code


def test_review_preflight_is_usable_without_manufacturer_approval_requests():
    report=public_preflight(preflight_project(project()),"review")
    assert report["status"]=="pass" and report["blockers"]==[] and report["manufacturing_gates"]
    assert all(item["scope"]=="review" for item in report["checks"])
    assert report["basic_review"]["profile"]["id"]==BASIC_REVIEW_PROFILE_ID
    assert not report["production_allowed"]


def test_safe_text_line_and_artwork_bleed_boundaries_report_actual_input():
    value=project("folding-box",depth_mm=60)
    value["scene"]["faces"][0]["objects"]=[text_object(x_mm=5,font_size_pt=7),
        {"id":"edge","type":"shape","face_id":"front","x_mm":0,"y_mm":100,"width_mm":160,"height_mm":40,
         "stroke":"#000000","stroke_width_mm":.1764}]
    basic=inspect_basic_review(value)
    assert {issue["code"] for issue in basic["issues"]}=={"BASIC_ARTWORK_BLEED"}
    value["scene"]["faces"][0]["objects"][0].update(x_mm=4.9999,font_size_pt=6.9999)
    value["scene"]["faces"][0]["objects"][1].update(x_mm=-3,width_mm=166,stroke_width_mm=.1763)
    basic=inspect_basic_review(value)
    assert {issue["code"] for issue in basic["issues"]}=={"BASIC_SAFE_MARGIN","BASIC_SMALL_TEXT","BASIC_THIN_LINE"}
    value["scene"]["faces"][0]["objects"][0]["x_mm"]=4.998
    with pytest.raises(GeometryValidationError) as exc: inspect_basic_review(value)
    assert exc.value.code=="TEXT_OUTSIDE_SAFE_AREA"


def test_supplier_values_unknowns_and_unsupported_modes_are_not_merged_or_approved():
    hansung=compare_supplier_capabilities("hansung-flexible-public-20260917")
    sheet=compare_supplier_capabilities("redprinting-sheet-public-20260917")
    cake=compare_supplier_capabilities("redprinting-cakebox-public-20260917")
    assert hansung["profile"]["bleed_mm"]==3 and sheet["profile"]["bleed_mm"]==2
    assert cake["profile"]["bleed_mm"] is None and "bleed_mm" in cake["unpublished"]
    assert "FONT_OUTLINING_NOT_IMPLEMENTED" in hansung["unsupported_requirements"]
    assert "FONT_OUTLINING_NOT_IMPLEMENTED" not in sheet["unsupported_requirements"]
    for item in (hansung,sheet,cake):
        assert not item["supplier_compliance_claimed"] and not item["profile"]["production_authorization"]
    with pytest.raises(GeometryValidationError): public_profile("made-up-approval")


def test_review_job_freezes_basic_profile_and_produces_bleed_pdf(tmp_path):
    from fastapi.testclient import TestClient
    from services.api.config import Settings
    from services.api.main import create_app
    from services.api.models import Job
    from services.api.tests.auth_helpers import register
    app=create_app(Settings(environment="test",database_url=f"sqlite:///{tmp_path/'api.db'}",storage_dir=tmp_path/"storage",worker_secret="spec-worker"))
    with TestClient(app) as client:
        register(client)
        value=client.post("/v1/projects",json={"name":"기본 규격 검토","product_name":"현미","width_mm":160,"height_mm":230}).json()["data"]
        response=client.post("/v1/exports",json={"project_id":value["id"],"base_revision":1})
        assert response.status_code==202,response.text
        job_id=response.json()["data"]["id"]
        with app.state.session_factory() as db:
            assert db.get(Job,job_id).snapshot["review_profile_id"]==BASIC_REVIEW_PROFILE_ID
        assert client.post("/v1/internal/jobs/process",headers={"Authorization":"Bearer spec-worker"}).status_code==200
        download=client.get(f"/v1/exports/{job_id}/download")
        assert download.status_code==200,download.text
        pdf=PdfReader(BytesIO(download.content))
        assert float(pdf.pages[0].mediabox.width)*25.4/72==pytest.approx(166,abs=.01)
