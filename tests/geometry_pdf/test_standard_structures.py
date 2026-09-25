import json
from pathlib import Path

import pytest

from services.api.geometry.definitions import parse_definition
from services.api.geometry.print_paths import structure_paths
from services.api.geometry.snapshots import compile_structure

ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "structures"
ITEMS = json.loads((ROOT / "standard-registrations.json").read_text(encoding="utf-8"))
STAND_UP = [item for item in ITEMS if item["geometry_template_id"] == "stand-up-pouch"]


@pytest.mark.parametrize("item", ITEMS, ids=lambda item: item["billing_family_key"])
def test_standard_registration_compiles_like_the_admin_endpoint(item):
    definition = parse_definition(item["structure_definition"])
    assert definition["family"] == item["geometry_template_id"] and item["review_available"] is True
    sample = definition.get("dimensions") or {"width_mm": 160, "height_mm": 230}
    assert compile_structure(definition, sample, "standard-check")["geometry"]["faces"]


@pytest.mark.parametrize("item", STAND_UP, ids=lambda item: item["billing_family_key"])
def test_stand_up_net_is_one_closed_cut_with_three_gusset_folds(item):
    definition = parse_definition(item["structure_definition"])
    w, h, b = (definition["dimensions"][k] for k in ("width_mm", "height_mm", "bottom_mm"))
    geometry = compile_structure(definition, definition["dimensions"], "standard-check")["geometry"]
    assert [geometry["net_width_mm"], geometry["net_height_mm"]] == [w, 2 * h + b]
    page, = structure_paths(geometry, "net")
    starts = sorted(tuple(line[:2]) for line in page["cut"])
    assert starts == sorted(tuple(line[2:]) for line in page["cut"]) and len(set(starts)) == len(starts)
    assert {line[1] for line in page["cut"]} | {line[3] for line in page["cut"]} == {0, h, h + b, 2 * h + b}
    assert sorted(line["y1_mm"] for line in definition["fold_lines"]) == [h, h + b / 2, h + b]


@pytest.mark.parametrize("item", STAND_UP, ids=lambda item: item["billing_family_key"])
def test_new_project_default_text_fits_the_standard_safe_area(item):
    from types import SimpleNamespace
    from services.api.main import initial_scene
    definition = parse_definition(item["structure_definition"])
    d = definition["dimensions"]
    project = SimpleNamespace(template_id="stand-up-pouch", width_mm=d["width_mm"], height_mm=d["height_mm"], bottom_mm=d["bottom_mm"],
                              depth_mm=None, brand_name="브랜드 이름", product_name="그래놀라 500g",
                              brand_id=None, product_variant_id=None, workspace_id=None)
    safe = {face["id"]: face["regions"]["safe"] for face in compile_structure(definition, d, "standard-check")["geometry"]["faces"]}
    for face in initial_scene(project)["faces"]:
        box = safe[face["id"]]
        for obj in face["objects"]:
            assert box["x_mm"] <= obj["x_mm"] and obj["x_mm"] + obj["width_mm"] <= box["x_mm"] + box["width_mm"], (face["id"], obj["id"])
            assert box["y_mm"] <= obj["y_mm"] and obj["y_mm"] + obj["height_mm"] <= box["y_mm"] + box["height_mm"], (face["id"], obj["id"])
