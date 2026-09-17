"""Public history must show image settings without exposing a private snapshot."""
from types import SimpleNamespace

from services.api.database import utcnow
from services.api.main import asset_payload, job_payload


def test_job_history_exposes_frozen_selection_but_not_private_snapshot():
    now = utcnow()
    snapshot = {"model": "gpt-image-2.5-flare", "quality": "auto",
                "requested_quality": "auto", "output_size": "2432x3344",
                "output_width_px": 2432, "output_height_px": 3344,
                "output_effective_ppi": 257.39, "output_experimental": True,
                "ai_selection_version": "packaging-image-selection-v1",
                "prompt": "private customer draft", "confirmed_source_text": "private OCR",
                "actor_id": "private-user", "storage_key": "private/key"}
    job = SimpleNamespace(id="job", project_id="project", kind="ai_generation", status="succeeded",
                          created_at=now, updated_at=now, result={"assets": [], "storage_key": "private/key"},
                          error=None, snapshot=snapshot)
    public = job_payload(job)
    assert public["image_settings"]["model"] == "gpt-image-2.5-flare"
    assert public["image_settings"]["quality"] == "auto"
    assert public["image_settings"]["output_size"] == "2432x3344"
    assert "snapshot" not in public
    assert "private" not in str(public)
    job.kind = "review_export"
    assert "image_settings" not in job_payload(job)


def test_asset_history_separates_requested_and_actual_quality_without_raw_metadata():
    asset = SimpleNamespace(id="asset", original_name="generated.png", content_type="image/png",
                            byte_size=100, width_px=32, height_px=48, source="ai",
                            metadata_json={"model": "gpt-image-2.5-flare", "requested_quality": "auto",
                                           "actual_quality": "xhigh", "output_size": "2432x3344",
                                           "actual_size": "32x48", "provider_request_id": "private-request",
                                           "reference_asset_id": "private-reference", "usage": {"input_tokens": 10}})
    public = asset_payload(asset)
    assert (public["requested_quality"], public["actual_quality"]) == ("auto", "xhigh")
    assert (public["output_size"], public["actual_size"]) == ("2432x3344", "32x48")
    assert "private" not in str(public) and "usage" not in public
    asset.metadata_json = {}
    assert "requested_quality" not in asset_payload(asset)
