"""Every calculated number carries its working, and the working cannot drift from
the number it explains. Plus the span calculation for support fixes."""

import math

import pytest
from fastapi.testclient import TestClient

import diagnose
import main
import materials
import mechanics

client = TestClient(main.app)
STEP_FIELDS = {"label", "formula", "substituted", "result", "value", "source"}


def by_label(working):
    return {step["label"]: step for step in working}


def check_steps(working):
    assert working, "no working shown"
    for step in working:
        assert STEP_FIELDS <= set(step), step
        assert step["formula"] and step["substituted"] and step["result"]


# --- Fillet --------------------------------------------------------------------------


def test_fillet_working_matches_every_number():
    result = mechanics.fillet_improvement(0.0, 3.0, 10.0)
    steps = by_label(result["working"])
    check_steps(result["working"])

    assert steps["r/d now"]["value"] == result["r_over_d_current"]
    assert steps["r/d proposed"]["value"] == result["r_over_d_proposed"]
    assert steps["Kt now"]["value"] == result["kt_current"]
    assert steps["Kt proposed"]["value"] == result["kt_proposed"]
    assert steps["Peak stress change"]["value"] == result["peak_stress_reduction_pct"]
    assert "capped" in steps["Kt now"]["note"]
    assert "Peterson" in steps["Kt now"]["source"]


def test_kt_working_reproduces_the_interpolated_value():
    result = mechanics.fillet_improvement(0.7, 2.3, 10.0)
    # The substituted line is real arithmetic: evaluate it and compare.
    for label in ("Kt now", "Kt proposed"):
        step = by_label(result["working"])[label]
        expression = step["substituted"].replace("·", "*").replace("−", "-")
        assert eval(expression, {"__builtins__": {}}) == pytest.approx(step["value"], abs=0.006)


def test_fillet_working_reaches_the_api():
    body = client.get("/mechanics/fillet", params={
        "current_radius_mm": 0, "proposed_radius_mm": 3, "section_depth_mm": 10}).json()

    assert by_label(body["working"])["Kt proposed"]["value"] == body["kt_after"]


# --- Thicker section -------------------------------------------------------------------


def test_section_working_matches_ratios_and_grams():
    result = mechanics.section_change(
        "round", {"diameter_mm": 12}, {"diameter_mm": 16}, 220, (0.42, 0.52), "Wood Handbook")
    steps = by_label(result["working"])
    check_steps(result["working"])

    assert steps["Weight ratio"]["value"] == result["weight_ratio"]
    assert steps["Bending strength ratio"]["value"] == result["strength_ratio"]
    assert steps["Stiffness ratio"]["value"] == result["stiffness_ratio"]
    assert steps["Added mass"]["value"] == result["mass_added_g"]
    assert steps["Added mass"]["source"] == "Wood Handbook"
    assert steps["Weight ratio"]["substituted"] == "(16 / 12)²"
    assert steps["Weight ratio"]["result"] == "×1.78"


def test_rectangular_section_working_names_both_dimensions():
    result = mechanics.section_change(
        "rectangular", {"width_mm": 12, "thickness_mm": 4}, {"width_mm": 12, "thickness_mm": 6})

    strength = by_label(result["working"])["Bending strength ratio"]
    assert strength["substituted"] == "(12·6²) / (12·4²)"
    assert strength["value"] == result["strength_ratio"] == 2.25
    assert "Added mass" not in by_label(result["working"])


# --- Material change -------------------------------------------------------------------


def test_material_change_gets_grams_from_the_part_volume():
    volume = math.pi * 12**2 / 4 * 220
    result = materials.compare("poplar", "aluminium_6061_t6", volume, "rod, estimated")
    steps = by_label(result["working"])

    assert result["mass_g"]["before"] == [round(volume / 1000 * 0.42, 1), round(volume / 1000 * 0.52, 1)]
    assert result["mass_g"]["after"][0] == pytest.approx(volume / 1000 * 2.7, abs=0.05)
    assert result["mass_g"]["change"][0] > 50
    assert steps["Mass change"]["value"] == result["mass_g"]["change"]
    assert steps["Strength ratio"]["value"] == result["strength_ratio"]


def test_material_change_without_a_volume_gives_ratios_only():
    result = materials.compare("pla", "petg")

    assert "mass_g" not in result
    assert {s["label"] for s in result["working"]} == {"Strength ratio", "Stiffness ratio", "Weight ratio"}


def test_part_volume_comes_from_the_specimen():
    specimen = diagnose.Specimen(
        shape="round_rod", length_mm=220, width_mm=12, thickness_mm=12, break_position=0.3,
        feature="none", load_case="unknown", dimensions_source="estimated_from_image")

    volume, note = diagnose.part_volume_mm3(specimen)

    assert volume == pytest.approx(math.pi * 36 * 220)
    assert "estimated from the photo" in note


# --- Span (add a support) ----------------------------------------------------------------


def test_halving_the_span_halves_the_stress_and_cuts_deflection_by_seven_eighths():
    result = mechanics.span_change(400, 200)
    steps = by_label(result["working"])

    assert result["moment_ratio"] == 0.5
    assert result["stress_change_pct"] == -50.0
    assert result["deflection_change_pct"] == -87.5
    assert steps["Deflection change"]["value"] == result["deflection_change_pct"]
    assert "Idealised" in result["basis"]


@pytest.mark.parametrize("spans", [(0, 200), (400, -1), (float("nan"), 200)])
def test_impossible_spans_are_rejected(spans):
    with pytest.raises(ValueError):
        mechanics.span_change(*spans)


def test_missing_span_asks_for_input():
    computed = diagnose.span_computed({"current_span_mm": 400, "proposed_span_mm": None})

    assert computed["needs_input"] == ["proposed_span_mm"]


def test_span_endpoint_recomputes_and_rejects_bad_input():
    good = client.get("/mechanics/span", params={"current_span_mm": 400, "proposed_span_mm": 200})
    bad = client.get("/mechanics/span", params={"current_span_mm": 0, "proposed_span_mm": 200})

    assert good.status_code == 200
    assert good.json()["inputs"]["dimensions_source"] == "user_measured"
    assert bad.status_code == 400


def test_span_change_from_the_model_is_calculated():
    raw = diagnose._ModelSolution(
        title="Add a centre support", description="x", pros=["a"], cons=["b"], effort="moderate",
        addresses_root_cause=True, geometry=None, section_change=None, proposed_material_id=None,
        span_change={"current_span_mm": 300, "proposed_span_mm": 150, "dimensions_source": "user_context"},
        visual={"kind": "add_support", "label": "Centre support"},
    )

    solution = diagnose._to_solution(raw)

    assert solution.span_computed["stress_change_pct"] == -50.0
    assert solution.span_change["dimensions_source"] == "user_context"


def test_rules_ask_for_span_change():
    assert "span_change" in diagnose.SYSTEM_RULES
