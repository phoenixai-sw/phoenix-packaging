"""Registered rectangular structures are frozen review data, never implicit approval."""
from copy import deepcopy
import json
import pytest
from services.api.geometry import build_geometry, new_scene, validate_scene, geometry_for_scene, GeometryValidationError
from services.api.geometry.definitions import parse_definition
from services.api.geometry.snapshots import compile_structure, validate_snapshot, structure_ref
from services.api.exporters import preflight_project


def separated():
    return {"schema_version":"2.0","recipe_id":"three-side-seal-separated-v1","family":"three-side-seal",
            "dimension_semantics":{"basis":"finished_outer"},"width_range_mm":{"minimum":60,"maximum":600},
            "height_range_mm":{"minimum":80,"maximum":800},"seals_mm":{"left":8,"right":12,"top":6,"bottom":14}}


def fixed_box():
    geometry=build_geometry("folding-box",160,230,depth_mm=60)
    panels=[]
    for f in geometry["faces"]:
        safe=f["regions"]["safe"]
        panels.append({"id":f["id"],"width_mm":f["width_mm"],"height_mm":f["height_mm"],"net":f["net"],"assembly":f["assembly"],
                       "safe_inset_mm":{"left":safe["x_mm"],"top":safe["y_mm"],"right":f["width_mm"]-safe["x_mm"]-safe["width_mm"],"bottom":f["height_mm"]-safe["y_mm"]-safe["height_mm"]}})
    return {"schema_version":"2.0","recipe_id":"fixed-panel-net-v1","family":"folding-box","dimensions":{"width_mm":160,"height_mm":230,"depth_mm":60},
            "dimension_semantics":{"basis":"outer","material_thickness_mm":.5,"glue_allowance_mm":15,"compensation":"included_in_fixed_panels"},
            "panels":panels,"fold_lines":geometry["fold_lines"],"structural_parts":[{**{k:p[k] for k in ("id","kind","x_mm","y_mm","width_mm","height_mm")},"attached_face_id":"front" if p["kind"]=="glue_tab" else p["id"].split("-")[0]} for p in geometry["structural_parts"]]}


def registered_project(definition=None, inputs=None):
    definition=definition or separated(); inputs=inputs or {"width_mm":160,"height_mm":230}
    snapshot=compile_structure(definition,inputs,"fixture-registered-v2")
    geometry=snapshot["geometry"]
    scene={"schema_version":"1.0","template_kind":definition["family"],"template_version_id":snapshot["template_version_id"],
           "structure_ref":structure_ref(snapshot),"geometry_hash":snapshot["geometry_hash"],"active_face_id":"front","holes":[],"pouch_features":None,
           "faces":[{k:f[k] for k in ("id","name","width_mm","height_mm")}|{"background":"#e9efe5","objects":[]} for f in geometry["faces"]]}
    scene.update({key:inputs[key] for key in ("bottom_mm","depth_mm") if key in inputs})
    return {"id":"registered-fixture","scene":scene,"structure_snapshot":snapshot,"review_profile_id":"phoenix-basic-review-v1"}


def test_seals_independent_normalized_and_empty_boundary():
    snapshot=compile_structure(separated(),{"width_mm":60,"height_mm":80},"v2")
    safe=validate_snapshot(snapshot)["faces"][0]["regions"]["safe"]
    assert safe=={"x_mm":13,"y_mm":11,"width_mm":30,"height_mm":50}
    value=separated();value["seals_mm"]["right"]=42
    with pytest.raises(GeometryValidationError,match="등록 구조"):
        compile_structure(value,{"width_mm":60,"height_mm":80},"v2")
    for width in (59.9999,600.0001):
        with pytest.raises(GeometryValidationError):compile_structure(separated(),{"width_mm":width,"height_mm":100},"v2")


@pytest.mark.parametrize("value", [True,"160",float("nan"),float("inf"),[],{}])
def test_nonfinite_or_coerced_dimensions_rejected(value):
    definition=separated();definition["width_range_mm"]["maximum"]=value
    with pytest.raises(GeometryValidationError):parse_definition(definition)


@pytest.mark.parametrize("key,value",[("script","1+2"),("approved",True),("recipe_id","eval"),("feature_policy","any")])
def test_unknown_recipe_execution_and_approval_not_accepted(key,value):
    definition=separated();definition[key]=value
    with pytest.raises(GeometryValidationError):parse_definition(definition)


def test_fixed_net_has_explicit_box_semantics_and_six_faces():
    snapshot=compile_structure(fixed_box(),{"width_mm":160,"height_mm":230,"depth_mm":60},"fixed-box-v2")
    geometry=validate_snapshot(snapshot)
    assert len(geometry["faces"])==6 and len(geometry["structural_parts"])==7
    assert geometry["net_width_mm"]==455 and geometry["net_height_mm"]==350
    assert next(f for f in geometry["faces"] if f["id"]=="top")["assembly"]["rotation_deg"]==[-90,0,0]
    assert geometry["dimension_semantics"]["material_thickness_mm"]==.5
    with pytest.raises(GeometryValidationError) as exc:compile_structure(fixed_box(),{"width_mm":161,"height_mm":230,"depth_mm":60},"v2")
    assert exc.value.code=="FIXED_STRUCTURE_DIMENSIONS"


@pytest.mark.parametrize("mutation", ["overlap","detached","empty-safe","outside-fold","missing-face","missing-thickness","wrong-face-size","wrong-assembly"])
def test_fixed_invalid_geometry_rejected(mutation):
    d=fixed_box()
    if mutation=="overlap":d["panels"][1]["net"]=deepcopy(d["panels"][0]["net"])
    if mutation=="detached":d["structural_parts"][0]["x_mm"]=900
    if mutation=="empty-safe":d["panels"][1]["safe_inset_mm"]["left"]=59
    if mutation=="outside-fold":d["fold_lines"][0]["x1_mm"]=1000
    if mutation=="missing-face":d["panels"].pop()
    if mutation=="missing-thickness":del d["dimension_semantics"]["material_thickness_mm"]
    if mutation=="wrong-face-size":d["panels"][1]["height_mm"]=229
    if mutation=="wrong-assembly":d["panels"][1]["assembly"]["rotation_deg"]=[0,-90,0]
    with pytest.raises(GeometryValidationError):compile_structure(d,{"width_mm":160,"height_mm":230,"depth_mm":60},"v2")


@pytest.mark.parametrize("field", ["definition_hash","geometry_hash","physical_geometry_hash","engine_version","normalized_inputs","geometry","unknown"])
def test_snapshot_is_not_a_client_geometry_override(field):
    snap=compile_structure(separated(),{"width_mm":160,"height_mm":230},"v2")
    snap[field]={} if field in {"normalized_inputs","geometry"} else "tampered"
    with pytest.raises(GeometryValidationError):validate_snapshot(snap)


def test_json_roundtrip_scene_requires_the_same_frozen_structure():
    p=json.loads(json.dumps(registered_project()))
    snap=p["structure_snapshot"]
    assert validate_scene(p["scene"],structure_snapshot=snap)["geometry_hash"]==snap["geometry_hash"]
    with pytest.raises(GeometryValidationError) as missing:validate_scene(p["scene"])
    assert missing.value.code=="STRUCTURE_SNAPSHOT_REQUIRED"
    for change in ({"geometry_hash":"0"*64},{"holes":[{"id":"hole"}]},{"structure_ref":None}):
        scene={**p["scene"],**change}
        with pytest.raises(GeometryValidationError):validate_scene(scene,structure_snapshot=snap)


def test_snapshot_geometry_type_changes_do_not_bypass_hash_check():
    snap=compile_structure(separated(),{"width_mm":160,"height_mm":230},"v2")
    snap["geometry"]["faces"][0]["net"]["x_mm"]=False
    with pytest.raises(GeometryValidationError) as error:validate_snapshot(snap)
    assert error.value.code=="STRUCTURE_SNAPSHOT_MISMATCH"


def test_legacy_geometry_and_hash_are_not_changed_by_v2_compilation():
    before=[build_geometry("three-side-seal",160,230),build_geometry("stand-up-pouch",160,230,bottom_mm=60),build_geometry("folding-box",160,230,depth_mm=60)]
    compile_structure(separated(),{"width_mm":160,"height_mm":230},"v2")
    for before_value,kind,extras in ((before[0],"three-side-seal",{}),(before[1],"stand-up-pouch",{"bottom_mm":60}),(before[2],"folding-box",{"depth_mm":60})):
        assert before_value==build_geometry(kind,160,230,**extras)
        assert before_value["geometry_hash"]==geometry_for_scene(new_scene(kind,160,230,**extras))["geometry_hash"]


def test_v2_never_enables_manufacturing_from_a_valid_structure():
    from test_structures_production import approved_project
    from services.api.exporters.preflight import DEFAULT_CONFIRMED_FIELDS
    p=registered_project();_,conditions=approved_project()
    p["revision_id"]="revision-1";p["scene"]["confirmed_fields"]=DEFAULT_CONFIRMED_FIELDS[:]
    conditions["template"]["id"]=p["scene"]["template_version_id"]
    conditions["template"]["geometry_template_id"]="three-side-seal"
    report=preflight_project(p,conditions)
    assert not report["production_allowed"]
    assert {x["code"] for x in report["issues"] if x["severity"]=="error"}=={"STRUCTURE_V2_PRODUCTION_UNSUPPORTED"}


@pytest.mark.parametrize("kind",["separated","fixed"])
def test_registered_review_pdf_uses_frozen_faces_boxes_and_vectors(tmp_path,kind):
    from pypdf import PdfReader
    from services.api.exporters import export_review_pdf
    from services.api.geometry import barcode_geometry
    p=registered_project() if kind=="separated" else registered_project(fixed_box(),{"width_mm":160,"height_mm":230,"depth_mm":60})
    p["scene"]["faces"][0]["objects"]=[{"id":"label","type":"text","face_id":"front","x_mm":20,"y_mm":30,"width_mm":110,"height_mm":25,
                                           "text":"등록 구조 한글 · 실제 벡터","font_size_pt":18,"font_weight":700}]
    barcode=barcode_geometry("9520000000011",barcode_usage="sample")
    p["scene"]["faces"][0]["objects"].append({"id":"sample","type":"barcode","face_id":"front","x_mm":50,"y_mm":100,
        "width_mm":barcode["width_mm"],"height_mm":barcode["height_mm"],"barcode_value":barcode["value"],"barcode_usage":"sample","module_mm":.33,"bar_height_mm":22.85})
    path=tmp_path/"registered.pdf";manifest=export_review_pdf(p,path)
    reader=PdfReader(path)
    assert len(reader.pages)==(2 if kind=="separated" else 7)
    assert "등록 구조 한글" in reader.pages[0].extract_text()
    assert "데모 구조" not in reader.pages[0].extract_text()
    assert manifest["approval_status"]=="registered_review_only"
    assert manifest["structure_ref"]==p["scene"]["structure_ref"]
    assert manifest["pdf_verification"]["barcode_checks"][0]["value"]=="9520000000011"
    assert manifest["pdf_verification"]["used_fonts_embedded"]
    for page,face in zip(reader.pages,p["structure_snapshot"]["geometry"]["faces"]):
        assert float(page.trimbox.width)*25.4/72==pytest.approx(face["width_mm"],abs=.01)
        assert float(page.mediabox.height)*25.4/72==pytest.approx(face["height_mm"]+6,abs=.01)
