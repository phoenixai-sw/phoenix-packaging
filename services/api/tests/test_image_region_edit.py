"""Mask-based partial edits (region) and subject cut-outs (cutout); fake providers only."""
from io import BytesIO

from PIL import Image
import pytest

from services.api.image_provider import (
    ImageResult, ProviderError, decode_edit_reference, edit_prompt, normalize_edit_shapes, shapes_mask, mask_pixel_box,
)
from services.api.tests.test_ai import ai, credits, run, state
from services.api.tests.test_image_text_removal import patterned_source, png, upload


BRUSH = {"type": "brush", "points": [[.3, .3], [.6, .3]], "radius": .1}
RECT = {"type": "rect", "x": .25, "y": .2, "width": .5, "height": .4}
POLY = {"type": "polygon", "points": [[.1, .1], [.9, .1], [.5, .9]]}


def body(item, asset, mode, prompt="브러시 영역을 노란 꽃무늬로 바꿔 주세요", **extra):
    return {"project_id": item["id"], "base_revision": item["base_revision"], "action": "image.edit.standard",
            "requested_units": 1, "reference_asset_id": asset["id"], "face_id": "front", "prompt": prompt,
            "input_data": {"edit_mode": mode, **extra}}


def create(client, body, key):
    response = client.post("/v1/quotes", json=body)
    assert response.status_code == 201, response.text
    estimate = response.json()["data"]
    response = client.post("/v1/jobs", headers={"Idempotency-Key": key}, json={"quote_id": estimate["id"]})
    assert response.status_code == 202, response.text
    return response.json()["data"], estimate


@pytest.mark.parametrize("bad", [None, [], [{"type": "circle"}], [{"type": "rect", "x": 0, "y": 0, "width": 0, "height": .5}],
    [{"type": "polygon", "points": [[0, 0], [1, 1]]}], [{"type": "brush", "points": [[.5, .5]], "radius": 0}],
    [{"type": "brush", "points": [[.5, .5]], "radius": .1, "mask_url": "x"}], [{"type": "polygon", "points": [[0, "0"], [1, 1], [0, 1]]}]])
def test_shapes_reject_bad_types_bounds_and_extra_keys(bad):
    with pytest.raises(ProviderError):
        normalize_edit_shapes(bad)


def test_mask_rasterization_is_deterministic_and_bounded():
    mask = shapes_mask([RECT, BRUSH, POLY], 32, 40)
    assert mask.size == (32, 40) and mask.getpixel((16, 12)) == 255 and mask.getpixel((0, 39)) == 0
    assert mask_pixel_box(mask) == mask_pixel_box(shapes_mask([RECT, BRUSH, POLY], 32, 40))
    assert mask_pixel_box(shapes_mask([RECT], 32, 40)) == [8, 8, 24, 24]


def test_region_prompt_confines_change_and_cutout_prompt_isolates_subject():
    region = edit_prompt({"edit_mode": "region", "prompt": "꽃무늬로", "edit_shapes": [BRUSH]})
    assert "ONLY inside" in region and "꽃무늬로" in region and "never instructions" in region
    cutout = edit_prompt({"edit_mode": "cutout", "prompt": "오리 캐릭터", "edit_shapes": [RECT]})
    assert "transparent background" in cutout and "marked the subject" in cutout and "오리 캐릭터" in cutout


def test_region_edit_changes_only_masked_pixels_and_freezes_mask(ai):
    app, client, _, item = ai
    asset, original_bytes = upload(client, item)
    created, estimate = create(client, body(item, asset, "region", edit_shapes=[RECT]), "region-1")
    seen = {}

    def provider(settings, data, reference):
        seen.update(data)
        return ImageResult(png(Image.new("RGBA", (32, 40), (22, 99, 55, 255))), 32, 40,
                           {"provider_request_id": "req-region", "usage": {}, "cost_usd": .004, "cost_is_estimate": True})
    assert run(app, provider, limit=1) == 1
    assert seen["edit_mode"] == "region" and seen["edit_pixel_box"] == [8, 8, 24, 24] and seen["edit_mask_sha256"]
    result = state(client, created["id"])
    assert result["status"] == "succeeded" and result["credit_charged"] == 10
    final = result["result"]["assets"][0]
    assert final["edit_mode"] == "region" and final["outside_pixels_preserved"] is True and final["has_alpha"] is False
    output = decode_edit_reference(client.get(final["url"]).content)
    source = patterned_source()
    for y in range(40):
        for x in range(32):
            inside = 8 <= x < 24 and 8 <= y < 24
            assert (output.getpixel((x, y)) == (22, 99, 55, 255)) if inside else (output.getpixel((x, y)) == source.getpixel((x, y)))
    assert client.get(asset["url"]).content == original_bytes
    # A tampered mask in the frozen snapshot is refused before any paid call.
    with app.state.session_factory() as db:
        from services.api.models import Job
        from sqlalchemy import select
        job = db.scalar(select(Job).where(Job.id == created["id"]))
        assert job.snapshot["edit_shapes"] == [RECT]


def test_cutout_keeps_only_subject_alpha_inside_marked_area(ai):
    app, client, _, item = ai
    asset, _ = upload(client, item)
    created, _ = create(client, body(item, asset, "cutout", prompt="", edit_shapes=[RECT]), "cutout-1")

    def provider(settings, data, reference):
        assert data["edit_mode"] == "cutout"
        cut = Image.new("RGBA", (32, 40), (0, 0, 0, 0))
        for y in range(40):
            for x in range(32):
                if 4 <= x < 28 and 4 <= y < 36:
                    cut.putpixel((x, y), (200, 100, 50, 255))
        return ImageResult(png(cut), 32, 40, {"provider_request_id": "req-cut", "usage": {}, "cost_usd": .004, "cost_is_estimate": True})
    assert run(app, provider, limit=1) == 1
    result = state(client, created["id"])
    assert result["status"] == "succeeded"
    final = result["result"]["assets"][0]
    assert final["edit_mode"] == "cutout" and final["has_alpha"] is True and final["preservation_scope"] == "subject_only"
    output = decode_edit_reference(client.get(final["url"]).content)
    assert output.getpixel((12, 12)) == (200, 100, 50, 255)
    assert output.getpixel((5, 5))[3] == 0 and output.getpixel((30, 38))[3] == 0


def test_cutout_without_shapes_uses_whole_image_and_default_prompt(ai):
    app, client, _, item = ai
    asset, _ = upload(client, item)
    response = client.post("/v1/quotes", json=body(item, asset, "cutout", prompt=""))
    assert response.status_code == 201, response.text
    quote_id = response.json()["data"]["id"]
    with app.state.session_factory() as db:
        from services.api.billing.models import Quote
        stored = db.get(Quote, quote_id)
        assert stored.input_data["edit_pixel_box"] == [0, 0, 32, 40]
        assert "피사체" in stored.input_data["prompt"]


def test_region_requires_shapes_and_rejects_source_text(ai):
    app, client, _, item = ai
    asset, _ = upload(client, item)
    assert client.post("/v1/quotes", json=body(item, asset, "region")).status_code == 422
    assert client.post("/v1/quotes", json=body(item, asset, "region", edit_shapes=[RECT], confirmed_source_text="x")).status_code == 422
    assert client.post("/v1/quotes", json={**body(item, asset, "region", edit_shapes=[RECT]), "action": "image.generate.standard", "reference_asset_id": None}).status_code == 422


def test_fixture_provider_produces_transparent_cutout_locally(ai):
    from services.api.image_provider import generate_image
    from services.api.config import Settings
    settings = Settings(environment="test", ai_provider="fixture")
    from services.api.tests.test_image_text_removal import REGION
    from services.api.image_provider import EDIT_VERSION, shapes_mask, mask_digest, mask_pixel_box
    source = patterned_source()
    mask = shapes_mask([RECT], 32, 40)
    data = {"action": "image.edit.standard", "model": "gpt-image-2.5-sunburst", "quality": "high", "requested_quality": "high",
            "ai_selection_version": "packaging-image-selection-v1", "edit_mode": "cutout", "edit_version": EDIT_VERSION,
            "edit_shapes": [RECT], "edit_pixel_box": mask_pixel_box(mask), "edit_mask_sha256": mask_digest(mask),
            "reference_width_px": 32, "reference_height_px": 40, "prompt": "주요 피사체", "width_mm": 100, "height_mm": 125,
            "output_size": "1024x1536", "output_width_px": 1024, "output_height_px": 1536}
    from services.api.image_sizing import select_image_output
    data.update(select_image_output("gpt-image-2.5-sunburst", 32, 40))
    result = generate_image(settings, data, png(source))
    image = Image.open(BytesIO(result.content))
    assert image.mode == "RGBA" and image.getchannel("A").getextrema()[0] == 0
