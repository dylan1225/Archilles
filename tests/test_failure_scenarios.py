"""The ceiling scenario's numbers come from geometry, not from a model."""

import math

import pytest

import failure_scenarios as fs
from tests.conftest import position

ANALYSIS = {
    "ceiling_finish": "unknown", "why_it_could_fail": "It could sag.",
    "early_warning_signs": ["A growing bulge", "New stains"], "recommended_action": "Have it checked.", "who_to_call": "A roofer.",
}


def finding(side=1.0, surface="ceiling", approximate=False) -> dict:
    half = side / 2
    return {"id": "F1", "placement": {
        "surface": surface, "center": [2.0, 1.5, 2.5], "normal": [0, 0, -1],
        "corners": [[2 - half, 1.5 - half, 2.5], [2 + half, 1.5 - half, 2.5], [2 + half, 1.5 + half, 2.5], [2 - half, 1.5 + half, 2.5]],
        "area_m2": side * side, "height_above_floor_m": 2.5, "approximate": approximate,
    }}


def test_free_fall_numbers():
    calc = fs.ceiling_fall(1.0, 2.5, 0.5)
    assert calc["fall_time_s"] == round(math.sqrt(2 * 2.5 / 9.81), 2) == 0.71
    assert calc["speed_at_floor_m_s"] == 7.0 and calc["speed_at_landing_m_s"] == 6.3
    masses = {mass["finish"]: mass for mass in calc["masses"]}
    # 2.2 psf and 8 psf, converted at 4.882 kg/m² per psf.
    assert masses["gypsum_board"]["mass_kg"] == 10.7 and masses["plaster_on_lath"]["mass_kg"] == 39.1
    assert masses["plaster_on_lath"]["energy_at_floor_j"] == round(39.056 * 9.81 * 2.5)
    assert [m["finish"] for m in fs.ceiling_fall(1.0, 2.5, 0.5, "gypsum_board")["masses"]] == ["gypsum_board"]


@pytest.mark.parametrize("area, drop", [(0, 2.5), (-1, 2.5), (1, 0), (float("nan"), 2.5), (1, float("inf"))])
def test_impossible_inputs_are_rejected(area, drop):
    with pytest.raises(ValueError):
        fs.ceiling_fall(area, drop, 0.5)


def test_scenario_uses_the_flagged_region_and_names_its_inputs():
    room = {"name": "Bedroom", "kind": "Bedroom"}
    positions = [position("R1-A", "R1", 2.0, 1.5), position("R1-B", "R1", 3.0, 2.0), position("R1-C", "R1", 1.0, 1.0)]
    scenario = fs.build_ceiling_scenario(finding(), room, positions, {"min": [0, 0, 0], "max": [4, 3, 2.5]}, ANALYSIS)
    assert scenario["mechanism"] == "ceiling_moisture_failure" and scenario["finding_id"] == "F1"
    assert scenario["calc"]["area_m2"] == 1.0 and scenario["calc"]["drop_height_m"] == 2.5
    assert scenario["calc"]["landing_label"] == "a bed" and scenario["calc"]["finish_known"] is False
    assert scenario["zone"]["widened"] is False and scenario["zone"]["floor_z"] == 0.0
    # The finding's own scan position projects first; at most two are used.
    assert [item["position_id"] for item in scenario["projectors"]] == ["R1-A", "R1-B"]
    assert scenario["projectors"][0]["faces"][0] == "R1-A-ceiling" and len(scenario["projectors"][0]["faces"]) == 6
    assert [stage["id"] for stage in scenario["stages"]] == ["as_scanned", "saturation", "sag", "detach", "aftermath"]
    assert "10.7 to 39.1 kg" in scenario["stages"][3]["caption"] and "0.71 s" in scenario["stages"][3]["caption"]
    assert "not a prediction" in scenario["disclaimer"]
    assert scenario["why"] == "It could sag."


def test_small_region_is_widened_to_one_framing_bay_and_says_so():
    scenario = fs.build_ceiling_scenario(finding(side=0.15), {"name": "Hallway", "kind": "Hallway"}, [position("R2-A", "R2", 2.0, 1.5)], None, ANALYSIS)
    assert scenario["zone"]["widened"] is True
    assert scenario["calc"]["area_m2"] == pytest.approx(0.45 ** 2, abs=0.01)
    assert "widened" in scenario["calc"]["inputs"][0]
    assert scenario["calc"]["landing_label"] == "a seated person's head"


def test_flat_ceiling_estimate_is_disclosed_and_walls_are_refused():
    scenario = fs.build_ceiling_scenario(finding(approximate=True), {"name": "Bedroom", "kind": "Bedroom"}, [position("R1-A", "R1", 2.0, 1.5)], None, ANALYSIS)
    assert "flat-ceiling estimate" in scenario["calc"]["inputs"][1] and scenario["zone"]["approximate"] is True
    with pytest.raises(ValueError):
        fs.build_ceiling_scenario(finding(surface="wall"), {"name": "Bedroom", "kind": "Bedroom"}, [position("R1-A", "R1", 2.0, 1.5)], None, ANALYSIS)


def test_repair_cost_now_against_after_a_collapse():
    cost = fs.repair_costs(1.0, 12.0)
    now, after = cost["now"], cost["after"]
    # 1 m² is 10.764 sq ft at $50 to $80; the leak repair is $1,000 to $4,000 either way.
    assert [(item["low"], item["high"]) for item in now["items"]] == [(1000, 4000), (540, 860)]
    assert (now["low"], now["high"]) == (1540, 4860)
    # 12 m² is 129.2 sq ft of room to dry at $3 to $6.50, plus the floor and possible mould.
    assert [(item["low"], item["high"]) for item in after["items"][2:]] == [(390, 840), (200, 500), (0, 3400)]
    assert (after["low"], after["high"]) == (2130, 9600)
    assert (cost["added_low"], cost["added_high"]) == (590, 4740)
    for bill in (now, after):
        assert bill["low"] == sum(item["low"] for item in bill["items"]) and bill["high"] == sum(item["high"] for item in bill["items"])
        assert all(item["low"] <= item["high"] and item["reference_id"] in cost["reference_ids"] for item in bill["items"])
    assert cost["context"]["amount"] == 15400 and any("not a quote" in note for note in cost["notes"])


def test_repair_cost_handles_small_sections_and_unknown_rooms():
    cost = fs.repair_costs(0.1, None)
    assert cost["now"]["items"][1]["low"] == 100  # never below a minimum call-out
    assert len(cost["after"]["items"]) == 4 and "no floor area" in cost["notes"][0]
    assert cost["added_low"] == 200 and cost["after"]["high"] > cost["now"]["high"]
    with pytest.raises(ValueError):
        fs.repair_costs(0, 12.0)


def test_scenario_carries_the_repair_cost_for_its_own_areas():
    room = {"name": "Bedroom", "kind": "Bedroom", "area_m2": 12.0}
    scenario = fs.build_ceiling_scenario(finding(), room, [position("R1-A", "R1", 2.0, 1.5)], None, ANALYSIS)
    assert scenario["repair_cost"] == fs.repair_costs(1.0, 12.0)
