"""Independent frozen-selection/security regressions. Local DB and fake providers only."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from services.api.billing.models import Quote
from services.api.billing.service import grant_credits
from services.api.database import utcnow
from services.api.feature_models import AiUnit, ProviderAttempt
from services.api.image_provider import resolve_image_selection, decode_edit_reference
from services.api.models import Asset, Job
from services.api.tests.test_ai import ai, credits, image_result, quote, run, state
from services.api.tests.test_image_text_removal import (
    create, patch_result, patterned_source, quote_body, upload,
)


SUNBURST = "gpt-image-2.5-sunburst"
FLARE = "gpt-image-2.5-flare"


def submit(client, estimate, key="selection-security"):
    return client.post("/v1/jobs", headers={"Idempotency-Key": key},
                       json={"quote_id": estimate["id"]})


@pytest.mark.parametrize("field,replacement", [("model", FLARE), ("quality", "medium")])
def test_frozen_quote_selection_hash_cannot_be_changed_before_reservation(ai, field, replacement):
    app, client, _, item = ai
    estimate = quote(client, item, model=SUNBURST, quality="low")
    with app.state.session_factory() as db:
        stored = db.get(Quote, estimate["id"])
        changed = deepcopy(stored.input_data)
        changed[field] = replacement
        if field == "quality":
            changed["requested_quality"] = replacement
        stored.input_data = changed
        db.commit()
    response = submit(client, estimate)
    assert response.status_code == 409 and response.json()["code"] == "QUOTE_CHANGED", response.text
    assert credits(client)["available"] == 30 and credits(client)["reserved"] == 0
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_default_model_change_does_not_replace_a_new_frozen_selection(ai):
    app, client, _, item = ai
    estimate = quote(client, item, model=SUNBURST, quality="high")
    app.state.settings.image_model = FLARE
    response = submit(client, estimate)
    assert response.status_code == 202, response.text
    created = response.json()["data"]
    calls = []
    def provider(settings, data, reference):
        calls.append(resolve_image_selection(settings, data))
        assert reference is None
        return image_result()
    assert run(app, provider, limit=1) == 1
    assert calls == [(SUNBURST, "high")]
    assert state(client, created["id"])["credit_charged"] == 10
    with app.state.session_factory() as db:
        assert db.scalar(select(ProviderAttempt)).model == SUNBURST
        assert db.get(Job, created["id"]).snapshot["model"] == SUNBURST


@pytest.mark.parametrize("during_call", [False, True])
def test_selected_model_revocation_returns_reservation_without_publishing(ai, during_call):
    app, client, _, item = ai
    estimate = quote(client, item, model=SUNBURST, quality="high")
    response = submit(client, estimate)
    assert response.status_code == 202, response.text
    created = response.json()["data"]
    calls = []
    def revoke():
        app.state.settings.image_model = FLARE
        app.state.settings.ai_image_models = (FLARE,)
    def provider(settings, data, reference):
        calls.append(data["model"])
        revoke()
        return image_result()
    if not during_call:
        revoke()
    run(app, provider, limit=1)
    assert calls == ([SUNBURST] if during_call else [])
    final = state(client, created["id"])
    assert final["credit_charged"] == 0 and final["credit_returned"] == 10
    assert final["result"]["assets"] == [] and credits(client)["available"] == 30
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        attempts = list(db.scalars(select(ProviderAttempt)))
        assert len(attempts) == int(during_call)
        if attempts:
            assert attempts[0].request_id == "req-test" and attempts[0].cost_usd == .0065
            assert attempts[0].usage["output_tokens"] == 200
            assert db.scalar(select(AiUnit)).result_metadata["provider_request_id"] == "req-test"


def test_legacy_remove_text_job_retains_roi_pixels_and_ten_credit_reservation(ai):
    app, client, _, item = ai
    asset, original_bytes = upload(client, item)
    created, _ = create(client, quote_body(item, asset))
    with app.state.session_factory() as db:
        saved = db.get(Job, created["id"])
        old_snapshot = deepcopy(saved.snapshot)
        old_snapshot.pop("ai_selection_version")
        old_snapshot.pop("requested_quality", None)
        old_snapshot["quality"] = "high"
        saved.snapshot = old_snapshot
        db.commit()
    calls = []
    def provider(settings, data, reference):
        calls.append(resolve_image_selection(settings, data))
        return patch_result()
    assert run(app, provider, limit=1) == 1
    final = state(client, created["id"])
    assert calls == [(SUNBURST, "high")]
    assert final["credit_charged"] == 10 and credits(client)["available"] == 20
    result = final["result"]["assets"][0]
    decoded = decode_edit_reference(client.get(result["url"]).content)
    source = patterned_source()
    assert decoded.size == source.size and result["outside_pixels_preserved"] is True
    for y in range(source.height):
        for x in range(source.width):
            if not (8 <= x < 24 and 8 <= y < 24):
                assert decoded.getpixel((x, y)) == source.getpixel((x, y))
    assert client.get(asset["url"]).content == original_bytes


def test_legacy_high_action_preserves_old_high_quality_and_twenty_credit_unit(ai, monkeypatch):
    app, client, auth, item = ai
    monkeypatch.setenv("AI_HIGH_ENABLED", "true")
    app.state.settings.ai_high_enabled = True
    with app.state.session_factory() as db:
        grant_credits(db, auth["tenant"]["id"], 30, kind="purchase", scope="paid",
                      expires_at=utcnow()+timedelta(days=30), grant_key="legacy-high-local-only",
                      reason="Local regression fixture; no operating credits")
        db.commit()
    estimate = quote(client, item, action="image.generate.high", model=SUNBURST, quality="xhigh")
    response = submit(client, estimate)
    assert response.status_code == 202, response.text
    created = response.json()["data"]
    with app.state.session_factory() as db:
        saved = db.get(Job, created["id"])
        data = deepcopy(saved.snapshot)
        data.pop("ai_selection_version")
        data.pop("requested_quality", None)
        data["quality"] = "high"
        saved.snapshot = data
        db.commit()
    seen = []
    def provider(settings, data, reference):
        seen.append(resolve_image_selection(settings, data))
        assert data["unit_cost"] == 20
        return image_result()
    assert run(app, provider, limit=1) == 1
    assert seen == [(SUNBURST, "high")]
    assert state(client, created["id"])["credit_charged"] == 20
    assert credits(client)["available"] == 40  # 30 unchanged trial + 10 remaining paid scope.
