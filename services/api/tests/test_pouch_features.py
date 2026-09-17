from copy import deepcopy

import pytest
from pydantic import ValidationError

from services.api.billing.service import production_fingerprint
from services.api.geometry import GeometryValidationError, build_geometry, geometry_for_scene, new_scene, validate_scene
from services.api.geometry.barcodes import barcode_geometry, sample_ean13, validate_ean13
from services.api.geometry.collisions import collision_report
from services.api.schemas import PouchFeatures, Scene
from services.api.tests.test_api import app, client, register, project


def pouch_scene():
    scene = new_scene("stand-up-pouch", 240, 330, bottom_mm=120)
    scene["pouch_features"] = {}
    return scene


def text_object(**changes):
    return {"id": "notice", "type": "text", "face_id": "front", "x_mm": 40, "y_mm": 50,
            "width_mm": 60, "height_mm": 12, "rotation_deg": 0, "text": "검토 문구", "font_id": "NotoSansKR", "font_size_pt": 10,
            "font_weight": 700, "visible": True, "print_enabled": True, **changes}


def test_finishing_geometry_has_symmetric_real_notches_guards_and_header_hole():
    scene = pouch_scene()
    scene["holes"] = [{"id": "hanger", "face_id": "front", "center_x_mm": 80, "center_y_mm": 15, "diameter_mm": 6}]
    scene = validate_scene(scene)
    geometry = geometry_for_scene(scene)
    front, back, bottom = geometry["faces"]
    assert front["regions"]["header"] == {"x_mm": 0, "y_mm": 0, "width_mm": 240, "height_mm": 30}
    assert front["regions"]["zipper"]["band"]["y_mm"] == 32
    assert front["regions"]["safe"]["y_mm"] == 43
    assert front["regions"]["tear_line"]["y1_mm"] == 24
    left, right = front["regions"]["tear_notches"]
    assert [[round(240 - x,4), y] for x, y in left["points_mm"]] == right["points_mm"]
    assert left["shape"] == "round" and len(left["points_mm"]) == 13
    assert [3, 24] in front["regions"]["cut_contour"]["points_mm"]
    assert [237, 24] in front["regions"]["cut_contour"]["points_mm"]
    assert front["regions"]["hole"][0]["center_x_mm"] == 80
    assert back["regions"]["hole"][0]["center_x_mm"] == 160
    assert "header" not in bottom["regions"]
    assert {r["kind"] for r in front["regions"]["structural_guards"]} == {"header_guard", "zipper_guard", "tear_guard", "notch_guard"}


@pytest.mark.parametrize("kind,extra", [("three-side-seal", {}), ("stand-up-pouch", {"bottom_mm": 60}), ("folding-box", {"depth_mm": 60})])
def test_absent_finishing_does_not_change_legacy_geometry(kind, extra):
    legacy = build_geometry(kind, 160, 230, **extra)
    assert legacy == build_geometry(kind, 160, 230, pouch_features=None, **extra)
    assert "pouch_features" not in legacy
    if kind == "folding-box":
        scene = new_scene(kind, 160, 230, **extra)
        top = next(f for f in scene["faces"] if f["id"] == "top")
        top["objects"] = [text_object(face_id="top", x_mm=8, y_mm=8, width_mm=100, height_mm=25)]
        assert validate_scene(Scene.model_validate(scene).model_dump(mode="json", exclude_none=True))["faces"][4]["objects"][0]["font_weight"] == 700
        with pytest.raises(GeometryValidationError, match="파우치"):
            build_geometry(kind, 160, 230, pouch_features={}, **extra)


def test_geometry_hash_is_canonical_and_changes_with_active_finishing():
    build = lambda features: build_geometry("stand-up-pouch", 240, 330, bottom_mm=120, pouch_features=features)["geometry_hash"]
    assert build(None) != build({})
    assert build({}) == build(PouchFeatures().model_dump())
    assert build({}) != build({"zipper_y_mm": 36})
    assert build({}) != build({"tear_y_mm": 25})
    assert build({}) != build({"notch_shape": "v"})
    assert build({"zipper_enabled": False}) == build({"zipper_enabled": False, "zipper_y_mm": 70})
    identity = {"brand_id": "brand", "product_variant_id": "variant", "billing_family_key": "pouch", "content_amount": 300, "content_unit": "g", "width_mm": 240, "height_mm": 330, "bottom_mm": 120}
    original = production_fingerprint("tenant", identity)
    assert original != production_fingerprint("tenant", {**identity, "pouch_features": {}})
    assert production_fingerprint("tenant", {**identity, "pouch_features": {}}) != production_fingerprint("tenant", {**identity, "pouch_features": {"notch_depth_mm": 4}})


@pytest.mark.parametrize("features", [{"header_height_mm": 79}, {"zipper_y_mm": 27}, {"zipper_y_mm": 70}, {"tear_y_mm": 31}, {"tear_y_mm": 14}, {"zipper_enabled": 1}, {"header_height_mm": True}, {"header_height_mm": float("nan")}, {"header_height_mm": "30"}, {"notch_depth_mm": 20}, {"script": "alert(1)"}])
def test_finishing_invalid_inputs_fail_closed(features):
    with pytest.raises(GeometryValidationError):
        build_geometry("three-side-seal", 60, 80, pouch_features=features)


@pytest.mark.parametrize("y", [15, 24, 34])
def test_protected_header_tear_zipper_prevent_printed_text_and_barcode(y):
    scene = pouch_scene()
    scene["faces"][0]["objects"] = [text_object(y_mm=y)]
    assert any(issue["code"] == "POUCH_FEATURE_OBJECT_COLLISION" for issue in collision_report(scene, geometry_for_scene(scene)))
    with pytest.raises(GeometryValidationError):
        validate_scene(scene)
    # Intermediate drafts remain movable; output validation stays strict.
    validate_scene(scene, check_safe_area=False)
    scene["faces"][0]["objects"][0]["visible"] = False
    validate_scene(scene)


def test_hanger_must_stay_clear_of_seal_and_tear_line():
    scene = pouch_scene()
    scene["holes"] = [{"id": "hanger", "face_id": "front", "center_x_mm": 120, "center_y_mm": 24, "diameter_mm": 6}]
    with pytest.raises(GeometryValidationError) as exc:
        geometry_for_scene(scene)
    assert exc.value.code in {"HOLE_OUTSIDE_ALLOWED", "HOLE_SEAL_COLLISION"}


def test_sample_symbol_is_valid_labeled_unowned_and_cannot_be_retagged_retail():
    value = sample_ean13()
    assert value.startswith("952") and validate_ean13(value) == value
    symbol = barcode_geometry(value, barcode_usage="sample")
    assert symbol["height_mm"] == symbol["bar_height_mm"] + 9
    assert symbol["barcode_owned"] is False and symbol["sample_label"] == "SAMPLE / 검토용"
    with pytest.raises(GeometryValidationError) as exc:
        barcode_geometry(value, barcode_usage="retail")
    assert exc.value.code == "BARCODE_SAMPLE_REQUIRED"
    scene = new_scene("three-side-seal", 160, 230)
    scene["faces"][0]["objects"] = [{"id": "sample", "type": "barcode", "face_id": "front", "x_mm": 30, "y_mm": 80,
        "width_mm": symbol["width_mm"], "height_mm": symbol["height_mm"], "barcode_value": value,
        "barcode_usage": "sample", "barcode_owned": True, "module_mm": symbol["module_mm"], "bar_height_mm": symbol["bar_height_mm"]}]
    parsed = Scene.model_validate(scene).model_dump(mode="json", exclude_none=True)
    assert parsed["faces"][0]["objects"][0]["barcode_owned"] is False
    assert validate_scene(scene)["faces"][0]["objects"][0]["barcode_owned"] is False


@pytest.mark.parametrize("usage", [[], {}, None, True, 1, "owned"])
def test_barcode_usage_untrusted_json_returns_domain_validation(usage):
    with pytest.raises(GeometryValidationError) as exc:
        barcode_geometry(sample_ean13(), barcode_usage=usage)
    assert exc.value.code == "BARCODE_USAGE_INVALID"


def test_preflight_keeps_finishing_and_owned_sample_review_only():
    from services.api.exporters.preflight import preflight_project, DEFAULT_CONFIRMED_FIELDS
    scene = new_scene("three-side-seal", 160, 230)
    scene["template_version_id"] = "manufacturer-proof-fixture"
    scene["confirmed_fields"] = DEFAULT_CONFIRMED_FIELDS[:]
    evidence = {"evidence_asset_id": "test-proof", "approved_by": "tester", "approved_at": "2026-09-17T00:00:00Z", "source": "fixture", "license": "fixture"}
    conditions = {"registry_verified": True, "material": "fixture", "confirmed_revision_id": "r1", "reviewed_face_ids": ["front", "back"],
        "template": {"id": scene["template_version_id"], "status": "approved", "is_demo": False, "billing_family_key": "pouch", "geometry_template_id": "three-side-seal-demo-v1", "manufacturer": "fixture", "material": "fixture", "approved_dimensions": {"width_mm": 160, "height_mm": 230}, "approval": evidence},
        "profile": {"id": "profile", "status": "approved", "is_demo": False, "manufacturer": "fixture", "material": "fixture", "requirements": {"pdf_standard": "PDF", "color_space": "RGB", "font_mode": "embedded", "min_ppi": 150}, "approval": evidence}}
    payload = {"id": "project", "revision_id": "r1", "scene": scene}
    assert preflight_project(payload, conditions)["production_allowed"]
    scene["pouch_features"] = {}
    result = preflight_project(payload, conditions)
    assert result["review_allowed"] and not result["production_allowed"]
    assert "POUCH_FEATURES_PRODUCTION_UNSUPPORTED" in {issue["code"] for issue in result["issues"]}
    scene.pop("pouch_features")
    symbol = barcode_geometry(sample_ean13(), barcode_usage="sample")
    scene["faces"][0]["objects"] = [{"id": "sample", "type": "barcode", "face_id": "front", "x_mm": 30, "y_mm": 80,
        "width_mm": symbol["width_mm"], "height_mm": symbol["height_mm"], "barcode_value": symbol["value"],
        "barcode_usage": "sample", "barcode_owned": True}]
    result = preflight_project(payload, conditions)
    assert result["review_allowed"] and not result["production_allowed"]
    assert "SAMPLE_BARCODE_PRODUCTION_FORBIDDEN" in {issue["code"] for issue in result["issues"]}


def test_api_geometry_sample_placement_persistence_and_every_autosave_revision(client):
    register(client)
    item = project(client)
    url = f"/v1/projects/{item['id']}"
    scene = deepcopy(item["scene"])
    scene["pouch_features"] = {}
    for face in scene["faces"]:
        face["objects"] = []
    geometry = client.post("/v1/geometry/validate", json={"template_id": "three-side-seal", "width_mm": 160, "height_mm": 230, "pouch_features": {}})
    assert geometry.status_code == 200, geometry.text
    result = client.post("/v1/geometry/barcode", json={"barcode_usage": "sample", "scene": scene, "face_id": "back"})
    assert result.status_code == 200, result.text
    spec = result.json()["data"]
    assert spec["barcode_owned"] is False and spec["placement"]["y_mm"] >= 43
    scene["faces"][0]["objects"] = [text_object(text="변경 전", font_weight=400)]
    saved = client.patch(url + "/draft", json={"base_revision": 1, "scene": scene})
    assert saved.status_code == 200, saved.text
    before = saved.json()["data"]
    after_scene = deepcopy(before["scene"])
    after_scene["faces"][0]["objects"][0].update(text="변경 후", font_weight=700, color="#112233", x_mm=45)
    saved = client.patch(url + "/draft", json={"base_revision": 2, "scene": after_scene})
    assert saved.status_code == 200, saved.text
    after = saved.json()["data"]
    assert after["geometry"]["pouch_features"]["header_height_mm"] == 30
    assert after["scene"]["geometry_hash"] == geometry.json()["data"]["geometry_hash"]
    assert client.patch(url + "/draft", json={"base_revision": 2, "scene": scene}).status_code == 409
    history = client.get(url + "/revisions").json()["data"]["items"]
    assert [row["number"] for row in history] == [3, 2, 1]
    assert history[0]["scene"] == after["scene"] and history[1]["scene"] == before["scene"]
    assert history[1]["scene"]["faces"][0]["objects"][0]["text"] == "변경 전"
    assert history[0]["scene"]["faces"][0]["objects"][0]["font_weight"] == 700
    assert history[0]["reason"] == history[1]["reason"] == "autosave"
    # A manual snapshot reuses the immutable same revision; it does not duplicate.
    assert client.post(url + "/revisions", json={"base_revision": 3}).status_code == 201
    assert len(client.get(url + "/revisions").json()["data"]["items"]) == 3


def test_autosave_preserves_legacy_unsnapshotted_draft_and_prior_history(client, app):
    from services.api.models import Project
    register(client)
    item = project(client)
    with app.state.session_factory() as db:
        row = db.get(Project, item["id"])
        prior = deepcopy(row.scene)
        prior["faces"][0]["objects"][0]["text"] = "이전 시스템에서 저장된 문구"
        row.scene = prior
        row.base_revision = 7
        db.commit()
    changed = deepcopy(prior)
    changed["faces"][0]["objects"][0]["text"] = "새 이력에 남는 문구"
    url = f"/v1/projects/{item['id']}"
    result = client.patch(url + "/draft", json={"base_revision": 7, "scene": changed})
    assert result.status_code == 200, result.text
    history = client.get(url + "/revisions").json()["data"]["items"]
    assert [row["number"] for row in history] == [8, 7, 1]
    assert history[1]["scene"] == prior
    assert history[1]["reason"] == "before_autosave"
