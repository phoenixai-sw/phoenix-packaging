from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import shutil
import subprocess

from PIL import Image
from pypdf import PdfReader
import pypdfium2 as pdfium
import pytest
import zxingcpp

from services.api.geometry import build_geometry,new_scene,validate_scene,GeometryValidationError,barcode_geometry,holes_for_face
from services.api.exporters import render_review_pdf,preflight_project,export_production_bundle
from services.api.exporters.preflight import DEFAULT_CONFIRMED_FIELDS

def barcode(value="0123456789012",**override):
    spec=barcode_geometry(value)
    return {"id":"ean","type":"barcode","face_id":"front","x_mm":30,"y_mm":70,"width_mm":spec["width_mm"],"height_mm":spec["height_mm"],"rotation_deg":0,"z_index":5,"barcode_value":value,"module_mm":spec["module_mm"],"bar_height_mm":spec["bar_height_mm"],"barcode_owned":True,**override}

def approved_project():
    scene=new_scene("three-side-seal",160,230)
    scene["template_version_id"]="manufacturer-fixture-version-1"
    scene["confirmed_fields"]=DEFAULT_CONFIRMED_FIELDS[:]
    scene["faces"][0]["objects"]=[barcode()]
    evidence={"evidence_asset_id":"test-evidence","approved_by":"fixture-admin","approved_at":"2026-09-16T00:00:00Z","source":"own-test-fixture","license":"test-only"}
    conditions={"registry_verified":True,"template":{"id":scene["template_version_id"],"status":"approved","is_demo":False,"billing_family_key":"fixture-family","geometry_template_id":"three-side-seal-demo-v1","approval":evidence,"manufacturer":"fixture-maker","material":"test-material","approved_dimensions":{"width_mm":160,"height_mm":230}},
                "profile":{"id":"fixture-rgb-v1","status":"approved","is_demo":False,"requirements":{"pdf_standard":"PDF","color_space":"RGB","font_mode":"embedded","min_ppi":150},"approval":evidence,"manufacturer":"fixture-maker","material":"test-material"},
                "material":"test-material","confirmed_revision_id":"revision-1","reviewed_face_ids":["front","back"]}
    return {"id":"test-project","revision_id":"revision-1","scene":scene},conditions

def test_expanded_bottom_is_explicit_and_cm_converts():
    with pytest.raises(GeometryValidationError,match="펼친 바닥"):
        build_geometry("stand-up-pouch",160,230)
    geometry=build_geometry("stand-up-pouch",16,23,"cm",bottom_mm=60)
    assert geometry["bottom_mm"]==60 and geometry["bottom_half_mm"]==30
    assert [(f["id"],f["width_mm"],f["height_mm"]) for f in geometry["faces"]]==[("front",160,230),("back",160,230),("bottom",160,60)]
    assert geometry["net_height_mm"]==520
    assert geometry["faces"][1]["net"]["rotation_deg"]==180
    assert not geometry["production_enabled"]
    assert validate_scene(new_scene("stand-up-pouch",160,230,bottom_mm=60))

@pytest.mark.parametrize("kind,extra,pages",[("stand-up-pouch",{"bottom_mm":60},4),("folding-box",{"depth_mm":60},7)])
def test_review_all_faces_and_actual_size_net(kind,extra,pages):
    scene=new_scene(kind,160,230,**extra)
    result=PdfReader(BytesIO(render_review_pdf(scene)))
    geometry=build_geometry(kind,160,230,**extra)
    assert len(result.pages)==pages
    expected=[(f["width_mm"],f["height_mm"]) for f in geometry["faces"]]+[(geometry["net_width_mm"],geometry["net_height_mm"])]
    for page,(w,h) in zip(result.pages,expected):
        assert abs(float(page.mediabox.width)*25.4/72-w)<.01
        assert abs(float(page.mediabox.height)*25.4/72-h)<.01
        assert "검토용" in page.extract_text()

def rotated(vector,angles):
    x,y,z=vector
    a,b,c=(math.radians(n) for n in angles)
    # All demo transforms use a single principal-axis rotation.
    y,z=y*math.cos(a)-z*math.sin(a),y*math.sin(a)+z*math.cos(a)
    x,z=x*math.cos(b)+z*math.sin(b),-x*math.sin(b)+z*math.cos(b)
    return (round(x*math.cos(c)-y*math.sin(c),6),round(x*math.sin(c)+y*math.cos(c),6),round(z,6))

def test_box_six_normals_up_and_net_parts_do_not_overlap():
    geometry=build_geometry("folding-box",160,230,depth_mm=60)
    normals={"front":(0,0,1),"back":(0,0,-1),"left":(-1,0,0),"right":(1,0,0),"top":(0,1,0),"bottom":(0,-1,0)}
    for face in geometry["faces"]:
        assembly=face["assembly"]
        assert rotated((0,0,1),assembly["rotation_deg"])==normals[face["id"]]
        assert not assembly["mirror_u"] and not assembly["mirror_v"]
        if face["id"] in ("front","back","left","right"):
            assert rotated((0,1,0),assembly["rotation_deg"])==(0,1,0)
        elif face["id"]=="bottom": assert rotated((0,1,0),assembly["rotation_deg"])==(0,0,1)
    panels=[dict(x_mm=f["net"]["x_mm"],y_mm=f["net"]["y_mm"],width_mm=f["width_mm"],height_mm=f["height_mm"]) for f in geometry["faces"]]
    parts=panels+geometry["structural_parts"]
    assert any(p.get("kind")=="glue_tab" for p in parts) and sum(p.get("kind")=="flap" for p in parts)==6
    for i,a in enumerate(parts):
        for b in parts[i+1:]:
            assert not (a["x_mm"]<b["x_mm"]+b["width_mm"] and b["x_mm"]<a["x_mm"]+a["width_mm"] and a["y_mm"]<b["y_mm"]+b["height_mm"] and b["y_mm"]<a["y_mm"]+a["height_mm"])

def test_hole_positions_mirror_physical_location_and_block_important_objects():
    scene=new_scene("stand-up-pouch",160,230,bottom_mm=60)
    scene["holes"]=[{"id":"hole1","face_id":"front","center_x_mm":60,"center_y_mm":22,"diameter_mm":6}]
    assert validate_scene(scene)
    assert holes_for_face(scene,"back")[0]["center_x_mm"]==100
    scene["faces"][0]["objects"]=[{"id":"title","type":"text","face_id":"front","x_mm":50,"y_mm":20,"width_mm":40,"height_mm":10,"text":"고객 문구","font_size_pt":10}]
    with pytest.raises(GeometryValidationError) as exc:validate_scene(scene)
    assert exc.value.code=="HOLE_OBJECT_COLLISION"
    scene["faces"][0]["objects"]=[];scene["holes"][0]["center_y_mm"]=9
    with pytest.raises(GeometryValidationError) as exc:validate_scene(scene)
    assert exc.value.code=="HOLE_OUTSIDE_ALLOWED"

@pytest.mark.parametrize("value",["0123456789013",123456789012,"123","９７８００００００００００"])
def test_invalid_ean_is_not_silently_changed(value):
    with pytest.raises(GeometryValidationError):barcode_geometry(value)

def test_barcode_printed_pdf_decodes_leading_zero_and_quiet_zones():
    scene=new_scene("three-side-seal",160,230);scene["faces"][0]["objects"]=[barcode()]
    spec=barcode_geometry("0123456789012")
    assert spec["quiet_left_mm"]==pytest.approx(11*.33)
    assert spec["quiet_right_mm"]==pytest.approx(7*.33)
    assert min(b["x_mm"] for b in spec["bars"])==pytest.approx(11*.33)
    assert max(b["x_mm"]+b["width_mm"] for b in spec["bars"])==pytest.approx((113-7)*.33)
    document=pdfium.PdfDocument(render_review_pdf(scene));page=document[0];bitmap=page.render(scale=4)
    try:
        result=zxingcpp.read_barcodes(bitmap.to_pil(),formats=zxingcpp.BarcodeFormat.EAN13)
        assert len(result)==1 and result[0].text=="0123456789012"
    finally:bitmap.close();page.close();document.close()
    scene["faces"][0]["objects"][0]["width_mm"]*=.9
    with pytest.raises(GeometryValidationError) as exc:validate_scene(scene)
    assert exc.value.code=="BARCODE_FREE_SCALE"

def test_barcode_same_z_later_overlay_is_blocked():
    scene=new_scene("three-side-seal",160,230)
    scene["faces"][0]["objects"]=[barcode(),{"id":"cover","type":"shape","face_id":"front","x_mm":31,"y_mm":71,"width_mm":10,"height_mm":10,"z_index":5}]
    with pytest.raises(GeometryValidationError) as exc:validate_scene(scene)
    assert exc.value.code=="BARCODE_QUIET_ZONE_COLLISION"

def test_preview_barcode_parity_against_reportlab():
    node=shutil.which("node")
    if not node:pytest.skip("Node needed for TS preview parity")
    root=Path(__file__).resolve().parents[2]
    values=["0123456789012","4006381333931","9780201379624"]
    script="import {ean13Geometry} from './packages/preview3d/src/barcode.ts'; console.log(JSON.stringify("+json.dumps(values)+".map(v=>ean13Geometry(v).bars)));"
    output=subprocess.check_output([node,"--experimental-strip-types","--input-type=module","-e",script],cwd=root,text=True)
    assert json.loads(output)==[barcode_geometry(v)["bars"] for v in values]

def test_production_six_files_hashes_no_review_labels_and_preflight(tmp_path):
    project,conditions=approved_project()
    assert preflight_project(project,conditions)["production_allowed"]
    out=tmp_path/"bundle"
    manifest=export_production_bundle(project,out,conditions,approval_recheck=lambda:conditions)
    assert {p.name for p in out.iterdir()}=={"production.pdf","preview.png","job-ticket.json","job-ticket.pdf","preflight.json","manifest.json"}
    for item in manifest["files"]:
        assert hashlib.sha256((out/item["name"]).read_bytes()).hexdigest()==item["sha256"]
    pdf=PdfReader(out/"production.pdf")
    assert len(pdf.pages)==2 and "검토용" not in pdf.pages[0].extract_text()
    assert "0123456789012" in pdf.pages[0].extract_text()
    assert abs(float(pdf.pages[0].trimbox.width)*25.4/72-160)<.01
    assert Image.open(out/"preview.png").width>500

@pytest.mark.parametrize("field,value",[("pdf_standard","PDF/X-4"),("color_space","CMYK"),("font_mode","outlined"),("spot_colors",True),("icc_profile","some.icc"),("bleed_mm",3)])
def test_unsupported_production_capability_still_allows_review(field,value,tmp_path):
    project,conditions=approved_project();conditions["profile"]["requirements"][field]=value
    result=preflight_project(project,conditions)
    assert not result["production_allowed"] and result["review_allowed"]
    with pytest.raises(GeometryValidationError):export_production_bundle(project,tmp_path/"forbidden",conditions)
    assert not (tmp_path/"forbidden").exists()

def test_demo_fabricated_approval_and_revoked_publication_blocked(tmp_path):
    project,conditions=approved_project()
    project["scene"]["template_version_id"]=conditions["template"]["id"]="three-side-seal-demo-v1"
    result=preflight_project(project,conditions)
    assert any(i["code"]=="DEMO_PRODUCTION_FORBIDDEN" for i in result["issues"])
    project,conditions=approved_project();revoked=deepcopy(conditions);revoked["template"]["status"]="revoked"
    with pytest.raises(GeometryValidationError) as exc:export_production_bundle(project,tmp_path/"revoked",conditions,approval_recheck=lambda:revoked)
    assert exc.value.code=="APPROVAL_CHANGED"
    assert not list(tmp_path.iterdir())

def test_low_ppi_customer_confirmations_and_face_review_are_blockers():
    project,conditions=approved_project();project["scene"]["confirmed_fields"]=[];conditions["reviewed_face_ids"]=["front"]
    project["scene"]["faces"][1]["objects"]=[{"id":"tiny","type":"image","face_id":"back","x_mm":20,"y_mm":40,"width_mm":100,"height_mm":100,"asset_id":"asset1"}]
    stream=BytesIO();Image.new("RGB",(100,100),"white").save(stream,format="PNG")
    result=preflight_project(project,conditions,lambda _:stream.getvalue())
    assert result["review_allowed"] and not result["production_allowed"]
    assert {"LOW_PPI","FIELD_CONFIRMATION_REQUIRED","FACE_REVIEW_REQUIRED"}.issubset({i["code"] for i in result["issues"]})

@pytest.mark.parametrize("change,code",[("dimensions","APPROVED_DIMENSIONS_MISMATCH"),("missing-dimensions","APPROVED_DIMENSIONS_REQUIRED"),("material","MATERIAL_APPROVAL_MISMATCH"),("manufacturer","MANUFACTURER_MISMATCH")])
def test_manufacturer_spec_is_exact_not_only_family(change,code):
    project,conditions=approved_project()
    if change=="dimensions":conditions["template"]["approved_dimensions"]["width_mm"]=159
    elif change=="missing-dimensions":del conditions["template"]["approved_dimensions"]
    elif change=="material":conditions["profile"]["material"]="unapproved-material"
    else:conditions["profile"]["manufacturer"]="other-factory"
    report=preflight_project(project,conditions)
    assert not report["production_allowed"] and report["review_allowed"]
    assert code in {i["code"] for i in report["issues"]}

def test_standup_barcode_cannot_cross_gusset_fold():
    scene=new_scene("stand-up-pouch",160,230,bottom_mm=60)
    scene["faces"][2]["objects"]=[barcode(face_id="bottom",y_mm=12)]
    with pytest.raises(GeometryValidationError) as exc:validate_scene(scene)
    assert exc.value.code=="BARCODE_FOLD_COLLISION"
