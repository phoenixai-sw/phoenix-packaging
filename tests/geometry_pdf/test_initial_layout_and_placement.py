from copy import deepcopy
from types import SimpleNamespace
from io import BytesIO

from pypdf import PdfReader
import pytest

from services.api.main import initial_scene
from services.api.schemas import CreateProjectInput
from services.api.geometry import barcode_geometry, validate_scene, GeometryValidationError
from services.api.geometry.barcode_placement import place_barcode
from services.api.exporters import export_review_pdf, preflight_project
from services.api.exporters.public_profiles import BASIC_REVIEW_PROFILE_ID


def initial(kind="three-side-seal", width=160, height=230, **extra):
    body=CreateProjectInput(name="QA 최소 규격",product_name="현미",brand_name="QA",template_id=kind,width_mm=width,height_mm=height,**extra)
    return initial_scene(SimpleNamespace(**body.model_dump()))


def object_for(barcode, placement):
    return {"id":"placed-ean13","type":"barcode","face_id":"front","z_index":6,
            "x_mm":placement["x_mm"],"y_mm":placement["y_mm"],"width_mm":barcode["width_mm"],"height_mm":barcode["height_mm"],
            "barcode_value":barcode["value"],"module_mm":barcode["module_mm"],"bar_height_mm":barcode["bar_height_mm"]}


@pytest.mark.parametrize("kind,extra,pages",[("three-side-seal",{},2),("stand-up-pouch",{"bottom_mm":30},4),("folding-box",{"depth_mm":30},7)])
def test_minimum_project_starts_inside_safe_area_and_actual_pdf_passes(tmp_path,kind,extra,pages):
    scene=initial(kind,60,80,**extra)
    project={"scene":scene,"review_profile_id":BASIC_REVIEW_PROFILE_ID}
    assert validate_scene(scene)
    assert preflight_project(project)["review_allowed"]
    output=tmp_path/"minimum.pdf"
    manifest=export_review_pdf(project,output)
    reader=PdfReader(output)
    assert len(reader.pages)==pages
    assert "현미" in reader.pages[0].extract_text()
    assert "원재료" in reader.pages[1 if kind!="folding-box" else 2].extract_text()
    assert all(item["passed"] for item in manifest["pdf_verification"]["page_boxes"])
    assert float(reader.pages[0].trimbox.width)*25.4/72==pytest.approx(60,abs=.01)
    assert float(reader.pages[0].trimbox.height)*25.4/72==pytest.approx(80,abs=.01)


def test_barcode_autoplacement_avoids_default_title_and_final_pdf_decodes(tmp_path):
    scene=initial();original=deepcopy(scene);barcode=barcode_geometry("0123456789012")
    placement=place_barcode(scene,"front",barcode)
    assert placement["mode"]=="automatic" and scene==original
    scene["faces"][0]["objects"].append(object_for(barcode,placement))
    assert validate_scene(scene)
    manifest=export_review_pdf({"scene":scene,"review_profile_id":BASIC_REVIEW_PROFILE_ID},tmp_path/"barcode.pdf")
    assert manifest["pdf_verification"]["barcode_checks"][0]["digital_decode"]=="passed"


def test_manual_barcode_position_reuses_quiet_zone_validation_without_moving_it():
    scene=initial();barcode=barcode_geometry("0123456789012")
    with pytest.raises(GeometryValidationError) as error: place_barcode(scene,"front",barcode,x_mm=20,y_mm=20)
    assert error.value.code=="BARCODE_QUIET_ZONE_COLLISION"
    assert place_barcode(scene,"front",barcode,x_mm=20,y_mm=180)=={"x_mm":20,"y_mm":180,"mode":"manual"}
    with pytest.raises(GeometryValidationError) as error: place_barcode(scene,"front",barcode,x_mm=20)
    assert error.value.code=="BARCODE_POSITION_REQUIRED"


def test_barcode_no_space_and_invalid_existing_scene_fail_without_changes():
    scene=initial(width=60,height=80);original=deepcopy(scene);barcode=barcode_geometry("0123456789012")
    with pytest.raises(GeometryValidationError) as error: place_barcode(scene,"front",barcode)
    assert error.value.code=="BARCODE_NO_SPACE" and scene==original
    scene=initial();scene["faces"][0]["objects"][0]["x_mm"]=0
    with pytest.raises(GeometryValidationError) as error: place_barcode(scene,"front",barcode)
    assert error.value.code=="TEXT_OUTSIDE_SAFE_AREA"


def test_automatic_search_stops_at_its_candidate_limit(monkeypatch):
    import services.api.geometry.barcode_placement as module
    scene=initial();scene["faces"][0]["objects"].append({"id":"occupied-bottom-left","type":"text","face_id":"front",
        "x_mm":15,"y_mm":180,"width_mm":50,"height_mm":30,"text":"문구","font_size_pt":12})
    barcode=barcode_geometry("0123456789012")
    assert place_barcode(scene,"front",barcode)["x_mm"]>=65
    monkeypatch.setattr(module,"MAX_CANDIDATES",1)
    with pytest.raises(GeometryValidationError) as error: place_barcode(scene,"front",barcode)
    assert error.value.code=="BARCODE_NO_SPACE"


def test_existing_barcode_api_contract_and_optional_placement_use_same_validator(tmp_path):
    from fastapi.testclient import TestClient
    from services.api.main import create_app
    from services.api.config import Settings
    app=create_app(Settings(environment="test",database_url=f"sqlite:///{tmp_path/'placement.db'}",storage_dir=tmp_path/"storage"))
    with TestClient(app) as client:
        old=client.post("/v1/geometry/barcode",json={"value":"0123456789012"})
        assert old.status_code==200 and "bars" in old.json()["data"]
        missing=client.post("/v1/geometry/barcode",json={"value":"0123456789012","x_mm":20,"y_mm":20})
        assert missing.status_code==422 and missing.json()["code"]=="BARCODE_SCENE_REQUIRED"
        scene=initial()
        placed=client.post("/v1/geometry/barcode",json={"value":"0123456789012","scene":scene,"face_id":"front"})
        assert placed.status_code==200,placed.text
        assert placed.json()["data"]["placement"]["mode"]=="automatic"
        blocked=client.post("/v1/geometry/barcode",json={"value":"0123456789012","scene":scene,"face_id":"front","x_mm":20,"y_mm":20})
        assert blocked.status_code==422 and blocked.json()["code"]=="BARCODE_QUIET_ZONE_COLLISION"


@pytest.mark.parametrize("invalid",["face-id-list","faces-not-list","object-id-list","unknown-scene-field"])
def test_barcode_api_rejects_malformed_scene_before_domain_validator(tmp_path,invalid):
    from fastapi.testclient import TestClient
    from services.api.main import create_app
    from services.api.config import Settings
    app=create_app(Settings(environment="test",database_url=f"sqlite:///{tmp_path/'malformed.db'}",storage_dir=tmp_path/"storage"))
    scene=initial()
    if invalid=="face-id-list": scene["faces"][1]["id"]=[]
    elif invalid=="faces-not-list": scene["faces"]="malformed"
    elif invalid=="object-id-list": scene["faces"][0]["objects"][0]["id"]=[]
    else: scene["approved_by_client"]=True
    with TestClient(app) as client:
        response=client.post("/v1/geometry/barcode",json={"value":"0123456789012","scene":scene,"face_id":"front"})
        assert response.status_code==422
        assert response.json()["code"]=="BARCODE_SCENE_INVALID"
        assert any(field.startswith("scene.") for field in response.json()["field_errors"])
