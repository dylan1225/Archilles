"""Repair plans: the same job priced two ways, with links that can be followed."""

import pytest

import failure_scenarios as fs
import repair_plans as rp


@pytest.mark.parametrize("title, category, kind", [
    ("Open crack running from the ceiling light fixture", "structure", "crack"),
    ("Dark streaking on wall corner beside shower tile", "moisture", "mould"),
    ("Amber-brown stain on kitchen ceiling near wall", "moisture", "stain"),
    ("Brown drip trails running down wall from ceiling line", "moisture", "stain"),
    ("Rippled, raised ceiling finish with a lump beside the smoke alarm", "moisture", "delamination"),
    ("Something unusual", "structure", "crack"),
    ("Something unusual", "moisture", "delamination"),
])
def test_damage_kind_is_read_from_the_findings_own_words(title, category, kind):
    assert rp.damage_kind({"title": title, "observation": "", "category": category}) == kind


def test_diy_replaces_the_surface_work_and_never_the_moisture_source():
    plan = rp.build_plan("delamination", "ceiling", 1.2, fs.repair_costs_for("delamination", "ceiling", 1.2, 11.7), "los_angeles")
    diy, pro = plan["diy"], plan["professional"]
    panels = next(item for item in diy["materials"] if "patch panel" in item["name"])
    assert panels["quantity"] == 4 and panels["total_usd"] == 31.4  # 1.2 m² in 0.36 m² panels
    assert diy["materials_total_usd"] == round(sum(i["total_usd"] for i in diy["materials"] if i["total_usd"] is not None), 2) == 92.16
    # The leak is a professional's job on both sides, so the totals differ by the surface work alone.
    assert plan["source_work"] == {"low": 1000, "high": 4000}
    assert (diy["low"], diy["high"]) == (1092, 4092) and (pro["low"], pro["high"]) == (1650, 5030)
    assert plan["surface_work"] == {"diy_usd": 92.16, "professional_low": 650, "professional_high": 1030}
    assert diy["difficulty"] == "Advanced" and any("large patch" in caution for caution in diy["cautions"])
    assert any("moisture source" in caution for caution in diy["cautions"]) and plan["cut_out"] is True
    assert len(plan["stages"]) == 4 and plan["stages"][0]["title"] == "As scanned"


def test_small_mould_patch_is_a_diy_job_within_the_epa_threshold():
    plan = rp.build_plan("mould", "wall", 0.066, fs.repair_costs_for("mould", "wall", 0.066, 3.55), "los_angeles")
    assert plan["source_work"] is None and plan["cut_out"] is False and plan["diy"]["difficulty"] == "Beginner"
    assert any("10 square feet" in caution and "0.7 sq ft" in caution for caution in plan["diy"]["cautions"])
    assert any("Do not paint or caulk over mould" in step for step in plan["diy"]["steps"])
    # Images cannot confirm mould, and the plan says so before anything else.
    assert plan["diy"]["cautions"][0].startswith("Dark streaking is not proof of mould")
    assert "Interior paint matched to the wall, 1 qt" in plan["diy"]["unpriced"] and "Not in the total: interior paint" in plan["diy"]["price_note"]
    assert "Approximate prices" in plan["diy"]["price_note"]


def test_every_link_is_https_and_prices_only_come_with_a_listing():
    for kind, surface in [("delamination", "ceiling"), ("crack", "ceiling"), ("stain", "ceiling"), ("stain", "wall"), ("mould", "wall"), ("delamination", "wall")]:
        for region in ("los_angeles", "houston", None):
            plan = rp.build_plan(kind, surface, 0.5, fs.repair_costs_for(kind, surface, 0.5, 10.0), region)
            items = plan["diy"]["materials"] + plan["diy"]["tools"]
            assert all(item["url"].startswith("https://www.homedepot.com/") for item in items)
            # A search link carries no price; a product or shelf link does.
            assert all((item["price_usd"] is None) == (item["link_kind"] == "search") for item in items)
            links = plan["professional"]["links"] + plan["safety_links"]
            assert all(link["url"].startswith("https://") for link in links)
            assert bool(plan["professional"]["links"]) == (region is not None)
            # The old-finish caution is conditional: the scan does not date the building.
            assert any(caution.startswith("The scan does not say when this home was built") for caution in plan["diy"]["cautions"])


def test_unknown_inputs_are_refused():
    cost = fs.repair_costs(1.0, 10.0)
    for args in [("rot", "ceiling", 1.0), ("crack", "floor", 1.0), ("crack", "ceiling", 0.0)]:
        with pytest.raises(ValueError):
            rp.build_plan(*args, cost, None)
