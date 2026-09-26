import json
from pathlib import Path

from services.api.operations.schemas import PricingPolicy

SEED = Path(__file__).resolve().parents[3] / "config" / "pricing.seed.json"


def test_shipped_pricing_is_self_serve_and_valid_as_an_admin_policy():
    data = json.loads(SEED.read_text(encoding="utf-8"))
    plans = {p["id"]: (p["monthly_inc_vat"], p["credits"], p["seats"]) for p in data["plans"]}
    assert plans == {"starter": (29000, 300, 1), "pro": (79000, 1000, 3), "partner": (199000, 3000, 5)}
    assert data["trial"]["credits"] == 100 and data["live_billing_enabled"] is False
    actions = {"editor.manual": 0, "preview.all_faces": 0, **data["actions"]}
    PricingPolicy.model_validate({k: v for k, v in {**data, "actions": actions}.items() if k != "version"})

