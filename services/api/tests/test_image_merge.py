"""Flattening stacked layers into one opaque image, so CMYK production accepts a cut-out."""
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from services.api.config import Settings
from services.api.errors import APIError
from services.api.geometry import new_scene
from services.api.image_merge import install_image_merge_routes, merge_layers, placed_bounds
from services.api.main import create_app, asset_payload
from services.api.models import Asset, Project
from services.api.tests.test_api import register, project


def png(size=(40, 40), fill=(20, 120, 200, 255)):
    stream = BytesIO()
    Image.new("RGBA", size, fill).save(stream, "PNG")
    return stream.getvalue()


def cutout_png(size=(40, 40)):
    """A red disc on a transparent background, like a subject cut out of its background."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(size[1]):
        for x in range(size[0]):
            if (x - size[0] / 2) ** 2 + (y - size[1] / 2) ** 2 <= (size[0] / 4) ** 2:
                image.putpixel((x, y), (230, 40, 40, 255))
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def layer(object_id, asset_id, z_index, **extra):
    return {"id": object_id, "type": "image", "face_id": "front", "asset_id": asset_id,
            "x_mm": 0, "y_mm": 0, "width_mm": 160, "height_mm": 230, "rotation_deg": 0,
            "z_index": z_index, "visible": True, "print_enabled": True, "locked": False, "opacity": 1, **extra}


@pytest.fixture
def merge(tmp_path):
    app = create_app(Settings(environment="test", database_url=f"sqlite:///{tmp_path/'merge.db'}", storage_dir=tmp_path/"storage"))
    if not any(getattr(route, "path", None) == "/v1/image-quality/merge" for route in app.routes):
        def session():
            with app.state.session_factory() as db:
                yield db
        install_image_merge_routes(app, session, asset_payload)
    with TestClient(app) as client:
        auth = register(client)
        created = project(client)
        ids = {}

        def store(name, raw):
            asset_id = str(uuid4())
            key = f"{auth['tenant']['id']}/assets/{asset_id}"
            app.state.storage.put(key, raw, "image/png")
            with app.state.session_factory() as db:
                with Image.open(BytesIO(raw)) as image:
                    size = image.size
                db.add(Asset(id=asset_id, tenant_id=auth["tenant"]["id"], storage_key=key, original_name=f"{name}.png",
                             content_type="image/png", byte_size=len(raw), width_px=size[0], height_px=size[1],
                             metadata_json={"sha256": sha256(raw).hexdigest()}))
                db.commit()
            ids[name] = asset_id
            return asset_id

        store("background", png())
        store("subject", cutout_png())

        def scene(objects):
            with app.state.session_factory() as db:
                row = db.get(Project, created["id"])
                value = new_scene("three-side-seal", 160, 230)
                value["faces"][0]["objects"] = objects
                row.scene = value
                db.commit()

        yield app, client, created, ids, scene


def stacked(ids, **extra):
    return [layer("base", ids["background"], 0), layer("cut", ids["subject"], 1, **extra)]


def request_body(created, object_ids, **extra):
    return {"project_id": created["id"], "base_revision": 1, "face_id": "front",
            "object_ids": object_ids, "operation_key": str(uuid4()), "target_ppi": 72, **extra}


def post(client, body):
    return client.post("/v1/image-quality/merge", json=body)


def pixels(app, asset_id, tenant_id):
    raw = app.state.storage.get_limited(f"{tenant_id}/assets/{asset_id}", 20_000_000)
    return Image.open(BytesIO(raw)).convert("RGBA")


def test_bounds_cover_a_rotated_layer():
    box = placed_bounds([{"x_mm": 10, "y_mm": 10, "width_mm": 20, "height_mm": 10, "rotation_deg": 90}])
    assert [round(value, 4) for value in box] == [0, 10, 10, 30]


def test_merge_replaces_the_stack_with_one_opaque_layer(merge):
    app, client, created, ids, scene = merge
    scene(stacked(ids))
    response = post(client, request_body(created, ["cut", "base"]))
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["keep_object_id"] == "base" and data["remove_object_ids"] == ["cut"]
    assert data["object_ids"] == ["base", "cut"] and data["background_filled"] is False
    assert data["patch"]["z_index"] == 1 and data["patch"]["opacity"] == 1 and data["credits_charged"] == 0
    assert [round(data["patch"][key], 2) for key in ("x_mm", "y_mm", "width_mm", "height_mm")] == [0, 0, 160, 230]
    merged = pixels(app, data["asset"]["id"], client.get("/v1/me").json()["data"]["tenant"]["id"])
    assert merged.getchannel("A").getextrema() == (255, 255)
    assert merged.getpixel((merged.width // 2, merged.height // 2))[:3] == (230, 40, 40)
    assert merged.getpixel((1, 1))[:3] == (20, 120, 200)
    assert data["asset"]["source"] == "image_merge" and data["asset"]["content_type"] == "image/png"


def test_merge_is_idempotent_and_conflicts_on_a_reused_key(merge):
    app, client, created, ids, scene = merge
    scene(stacked(ids))
    body = request_body(created, ["base", "cut"])
    first = post(client, body)
    assert first.status_code == 201, first.text
    again = post(client, body)
    assert again.status_code == 201 and again.json()["data"]["asset"]["id"] == first.json()["data"]["asset"]["id"]
    conflicting = post(client, {**body, "target_ppi": 150})
    assert conflicting.status_code == 409 and conflicting.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_cut_out_alone_is_flattened_onto_the_face_colour(merge):
    app, client, created, ids, scene = merge
    scene([layer("cut", ids["subject"], 0), layer("cut-two", ids["subject"], 1, x_mm=10, y_mm=10, width_mm=100, height_mm=100)])
    response = post(client, request_body(created, ["cut", "cut-two"]))
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["background_filled"] is True
    merged = pixels(app, data["asset"]["id"], client.get("/v1/me").json()["data"]["tenant"]["id"])
    assert merged.getchannel("A").getextrema() == (255, 255)
    assert merged.getpixel((1, 1))[:3] == (244, 241, 232)


def test_a_layer_left_under_a_transparent_result_is_refused(merge):
    app, client, created, ids, scene = merge
    scene([layer("base", ids["background"], 0), layer("cut", ids["subject"], 1), layer("cut-two", ids["subject"], 2)])
    response = post(client, request_body(created, ["cut", "cut-two"]))
    assert response.status_code == 422 and response.json()["code"] == "MERGE_LAYERS_BELOW"


def test_an_unselected_layer_between_the_chosen_ones_is_refused(merge):
    app, client, created, ids, scene = merge
    scene([layer("base", ids["background"], 0), layer("middle", ids["subject"], 1), layer("top", ids["subject"], 2)])
    response = post(client, request_body(created, ["base", "top"]))
    assert response.status_code == 422 and response.json()["code"] == "MERGE_LAYERS_BETWEEN"


def test_locked_hidden_duplicate_and_non_image_layers_are_refused(merge):
    app, client, created, ids, scene = merge
    scene(stacked(ids, locked=True))
    assert post(client, request_body(created, ["base", "cut"])).json()["code"] == "LAYER_LOCKED"
    scene(stacked(ids, visible=False))
    assert post(client, request_body(created, ["base", "cut"])).json()["code"] == "LAYER_NOT_PRINTED"
    scene(stacked(ids))
    assert post(client, request_body(created, ["base", "base"])).json()["code"] == "MERGE_DUPLICATE_LAYER"
    assert post(client, request_body(created, ["base", "missing"])).json()["code"] == "IMAGE_OBJECT_REQUIRED"


def test_a_stale_revision_is_refused(merge):
    app, client, created, ids, scene = merge
    scene(stacked(ids))
    response = post(client, request_body(created, ["base", "cut"], base_revision=7))
    assert response.status_code == 409 and response.json()["code"] == "REVISION_CONFLICT"


def test_merge_honours_opacity_and_keeps_the_pixel_limit(merge):
    app, client, created, ids, scene = merge
    scene(stacked(ids, opacity=0.5))
    response = post(client, request_body(created, ["base", "cut"]))
    assert response.status_code == 201, response.text
    merged = pixels(app, response.json()["data"]["asset"]["id"], client.get("/v1/me").json()["data"]["tenant"]["id"])
    middle = merged.getpixel((merged.width // 2, merged.height // 2))
    assert middle[0] > 100 and middle[2] > 80 and merged.getchannel("A").getextrema() == (255, 255)
    with pytest.raises(APIError) as refused:
        merge_layers([layer("base", "a", 0, width_mm=900, height_mm=900), layer("cut", "b", 1)], [png(), cutout_png()], "#FFFFFF", 600)
    assert refused.value.code == "IMAGE_PIXEL_LIMIT"


def test_merge_layers_is_deterministic():
    objects = [layer("base", "a", 0), layer("cut", "b", 1)]
    sources = [png(), cutout_png()]
    first = merge_layers(objects, sources, "#FFFFFF", 72)
    second = merge_layers(objects, sources, "#FFFFFF", 72)
    assert first[0] == second[0] and first[1] == (454, 652) and first[3] is False
