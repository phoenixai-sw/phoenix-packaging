"""Crop semantics across persistence, real PDF pixels, PPI and image derivatives."""
from copy import deepcopy
from io import BytesIO
from math import cos, radians, sin
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from pypdf import PdfReader
import pytest
from reportlab.lib.units import mm

from services.api.config import Settings
from services.api.geometry import new_scene, validate_scene, GeometryValidationError
from services.api.schemas import Scene
from services.api.image_quality import make_derivative
from services.api.image_quality_metadata import image_quality_metrics
from services.api.exporters.review_pdf import export_review_pdf, _resolve_image
from services.api.exporters.production import _production_pdf
from services.api.exporters.preflight import preflight_project
from services.api.exporters.public_profiles import BASIC_REVIEW_PROFILE_ID
from services.api.main import create_app
from services.api.tests.auth_helpers import register, google_login
from services.api.tests.test_api import project
from tests.geometry_pdf.test_finishing_pdf import render_page
from tests.geometry_pdf.test_structures_production import approved_project


def art(size=(400, 240), alpha=False):
    image = Image.new("RGBA" if alpha else "RGB", size)
    for y in range(size[1]):
        for x in range(size[0]):
            color = ((220, 20, 20) if x < size[0]/2 else (20, 180, 40)) if y < size[1]/2 else ((20, 40, 210) if x < size[0]/2 else (240, 190, 20))
            image.putpixel((x, y), (*color, 0 if x > size[0]*.8 else 255) if alpha else color)
    output = BytesIO(); image.save(output, "PNG"); return output.getvalue()


def obj(**extra):
    return {"id": "cropped", "type": "image", "face_id": "front", "asset_id": str(uuid4()),
            "x_mm": 50, "y_mm": 60, "width_mm": 40, "height_mm": 24, "rotation_deg": 0,
            "crop": {"x": .5, "y": 0, "width": .5, "height": 1}, **extra}


def scene_with(value):
    scene = new_scene("three-side-seal", 160, 230)
    scene["faces"][0]["background"] = "#ffffff"
    scene["faces"][0]["objects"] = [value]
    return scene


@pytest.mark.parametrize("crop", [
    [], "0,0,1,1", {"x": 0, "y": 0, "width": 0, "height": 1},
    {"x": -.01, "y": 0, "width": 1, "height": 1},
    {"x": .5, "y": 0, "width": .5001, "height": 1},
    {"x": 0, "y": 0, "width": True, "height": 1},
    {"x": 0, "y": 0, "width": "0.5", "height": 1},
    {"x": 0, "y": 0, "width": float("nan"), "height": 1},
    {"x": 0, "y": 0, "width": 1, "height": 1, "url": "https://invalid"},
])
def test_invalid_crop_rejected_at_api_schema_and_domain(crop):
    scene = scene_with(obj(crop=crop))
    with pytest.raises(ValidationError): Scene.model_validate(scene)
    with pytest.raises(GeometryValidationError) as error: validate_scene(scene)
    assert error.value.code == "INVALID_IMAGE_CROP"


def test_nonimage_crop_rejected_and_legacy_null_preserved():
    value = obj(type="shape", color="#000000")
    with pytest.raises(ValidationError): Scene.model_validate(scene_with(value))
    with pytest.raises(GeometryValidationError) as error: validate_scene(scene_with(value))
    assert error.value.code == "IMAGE_CROP_ONLY"
    for crop in [None, {"x": 0, "y": 0, "width": 1, "height": 1}]:
        assert Scene.model_validate(scene_with(obj(crop=crop)))
        assert validate_scene(scene_with(obj(crop=crop)))


@pytest.mark.parametrize("rotation", [0, 90, 27])
def test_review_and_production_crop_pixels_alpha_rotation_and_original_raster(tmp_path, rotation):
    raw = art(alpha=True)
    value = obj(rotation_deg=rotation)
    scene = validate_scene(scene_with(value))
    output = tmp_path / "crop-review.pdf"
    export_review_pdf({"scene": scene, "review_profile_id": BASIC_REVIEW_PROFILE_ID}, output, lambda _: raw)
    for content, bleed in [(output.read_bytes(), 3), (_production_pdf(scene, lambda _: raw), 0)]:
        image = render_page(content)
        def at(u, v):
            x = value["x_mm"] + u*cos(radians(rotation))-v*sin(radians(rotation))
            y = value["y_mm"] + u*sin(radians(rotation))+v*cos(radians(rotation))
            return image.getpixel((round((x+bleed)*mm*4), round((y+bleed)*mm*4)))
        assert at(10, 6) == pytest.approx((20, 180, 40), abs=3)  # original top-right quadrant
        assert at(10, 18) == pytest.approx((240, 190, 20), abs=3)  # original bottom-right quadrant
        assert min(at(34, 12)) > 245  # source alpha survives cropping
        assert min(at(-3, 12)) > 245 and min(at(10, 27)) > 245  # no image leaks out of rotated bbox
        reader = PdfReader(BytesIO(content))
        images = [ref.get_object() for ref in reader.pages[0]["/Resources"]["/XObject"].values()]
        assert any(item.get("/Width") == 400 and item.get("/Height") == 240 for item in images)


def test_crop_uses_visible_pixels_in_both_review_and_production_preflight():
    item, conditions = approved_project()
    value = obj(width_mm=100, height_mm=100, y_mm=60, crop=None)
    item["scene"]["faces"][0]["objects"] = [value]
    conditions["profile"]["requirements"]["min_ppi"] = 300
    raw = art((1200, 1200))
    assert preflight_project(item, conditions, lambda _: raw)["production_allowed"]
    value["crop"] = {"x": .5, "y": 0, "width": .5, "height": 1}
    report = preflight_project(item, conditions, lambda _: raw)
    assert not report["production_allowed"]
    assert {"LOW_PPI", "BASIC_LOW_PPI"} <= {issue["code"] for issue in report["issues"]}
    assert next(issue for issue in report["issues"] if issue["code"] == "LOW_PPI")["effective_ppi"] == pytest.approx(152.4)


@pytest.mark.parametrize("mode", ["edge", "mirror"])
def test_crop_then_bleed_materializes_once_and_preserves_native_density(mode):
    raw = art((40, 24), alpha=True)
    value = obj(x_mm=0, y_mm=0, width_mm=20, height_mm=24)
    before = image_quality_metrics((40, 24), value)
    result, patch, metadata = make_derivative(raw, {"width_mm": 20, "height_mm": 24}, value, {}, resample=False, bleed_mode=mode)
    assert patch["crop"] is None
    with Image.open(BytesIO(raw)) as original, Image.open(BytesIO(result)) as image:
        assert image.size == (26, 30)
        assert image.crop((3, 3, 23, 27)).tobytes() == original.crop((20, 0, 40, 24)).tobytes()
        assert image.getpixel((2, 3)) == original.getpixel((20, 0))
    after = image_quality_metrics(metadata["output_pixels"], {**value, **patch}, {"image_quality": metadata})
    assert after["original_effective_ppi"] == pytest.approx(before["original_effective_ppi"])
    assert metadata["original_source_pixels"] == [40, 24] and metadata["source_visible_pixels"] == [20, 24]
    second, next_patch, next_meta = make_derivative(result, {"width_mm": 20, "height_mm": 24}, {**value, **patch}, {"image_quality": metadata}, target_ppi=300)
    repeated = image_quality_metrics(next_meta["output_pixels"], {**value, **patch, **next_patch}, {"image_quality": next_meta})
    assert repeated["original_effective_ppi"] == pytest.approx(before["original_effective_ppi"])
    assert repeated["effective_ppi"] >= 300 and repeated["extended"] and repeated["resampled"]


def test_crop_after_resampling_cannot_restore_original_ppi():
    value = obj(width_mm=100, height_mm=100)
    quality = image_quality_metrics((2400, 2400), value, {"image_quality": {"resampled": True, "native_equivalent_pixels": [1200, 1200]}})
    assert quality["effective_ppi"] == pytest.approx(304.8)
    assert quality["original_effective_ppi"] == pytest.approx(152.4)


def test_exif_oriented_crop_matches_browser_source_dimensions():
    source = Image.new("RGB", (40, 20), "red")
    exif = source.getexif(); exif[274] = 6
    output = BytesIO(); source.save(output, "JPEG", exif=exif)
    _, pixels = _resolve_image("test", lambda _: output.getvalue())
    assert pixels == (20, 40)


def test_crop_persists_reopens_and_quality_patch_clears_it(tmp_path):
    settings = Settings(environment="test", database_url=f"sqlite:///{tmp_path/'crop.db'}", storage_dir=tmp_path/"storage")
    raw = art((40, 24))
    with TestClient(create_app(settings)) as client:
        register(client); item = project(client)
        uploaded = client.post("/v1/assets", files={"file": ("quadrants.png", raw, "image/png")}).json()["data"]
        value = obj(asset_id=uploaded["id"])
        scene = deepcopy(item["scene"]); scene["faces"][0]["objects"] = [value]
        response = client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": 1, "scene": scene})
        assert response.status_code == 200, response.text
    with TestClient(create_app(settings)) as client:
        login = google_login(client)
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["data"]["csrf_token"]
        persisted = client.get(f"/v1/projects/{item['id']}").json()["data"]
        assert persisted["scene"]["faces"][0]["objects"][0]["crop"] == value["crop"]
        body = {"project_id": item["id"], "base_revision": 2, "face_id": "front", "object_id": "cropped", "target_ppi": 72}
        inspection = client.post("/v1/image-quality/inspect", json=body).json()["data"]
        assert inspection["visible_pixels"] == [20, 24]
        assert inspection["effective_ppi"] == pytest.approx(12.7)
        preview = client.post("/v1/image-quality/preview", json={**body, "operation_key": "crop-quality", "source_sha256": inspection["source"]["sha256"]})
        assert preview.status_code == 201, preview.text
        patch = preview.json()["data"]["patch"]
        assert patch["crop"] is None and patch["asset_id"] != value["asset_id"]
        persisted["scene"]["faces"][0]["objects"][0].update(patch)
        saved = client.patch(f"/v1/projects/{item['id']}/draft", json={"base_revision": 2, "scene": persisted["scene"]})
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["scene"]["faces"][0]["objects"][0].get("crop") is None
        assert client.get(f"/v1/assets/{value['asset_id']}/content").content == raw
