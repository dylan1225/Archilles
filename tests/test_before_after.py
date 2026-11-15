"""Before/after numbers for geometry fixes: calculated, never model-generated."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import diagnose
import main

client = TestClient(main.app)


# --- The notched bar: d = 10 mm, sharp corner -> 3 mm fillet -----------------


def test_notched_bar_sharp_corner_to_3mm_fillet():
    computed = diagnose.fillet_computed(0.0, 3.0, 10.0, "user_context")

    print(
        "\nnotched bar, d = 10 mm, r 0 -> 3 mm:\n"
        + json.dumps({k: v for k, v in computed.items() if k != "basis"}, indent=1)
        + f"\nbasis: {computed['basis']}"
    )
    assert computed["kt_before"] > computed["kt_after"]
    assert 0 < computed["stress_reduction_pct"] < 100
    # A 3 mm fillet on a 10 mm section is a large improvement: r/d = 0.30.
    assert 40 <= computed["stress_reduction_pct"] <= 70
    # r = 0 sits below the tabulated r/d range (0.05 to 0.30), so it is flagged.
    assert computed["is_extrapolated"] is True
    assert computed["is_capped"] is True
    assert computed["reduction_is_lower_bound"] is True
    assert computed["calculated_by"] == "mechanics.py"
    assert computed["basis"]


def test_reduction_can_be_checked_by_hand_from_the_numbers_shown():
    computed = diagnose.fillet_computed(0.0, 3.0, 10.0, "user_context")

    by_hand = (1 - computed["kt_after"] / computed["kt_before"]) * 100

    assert computed["stress_reduction_pct"] == pytest.approx(by_hand, abs=0.05)


def test_in_range_change_is_not_flagged_as_extrapolated():
    computed = diagnose.fillet_computed(1.0, 2.0, 10.0, "user_context")

    assert computed["is_extrapolated"] is False
    assert computed["is_capped"] is False


# --- When the photo does not show the dimensions --------------------------------


def _model_answer(geometry):
    solution = {
        "title": "Add a fillet",
        "description": "Round the inside corner.",
        "pros": ["Lower peak stress"],
        "cons": ["Mating part needs a chamfer"],
        "effort": "trivial",
        "addresses_root_cause": True,
        "geometry": geometry,
        "visual": {"kind": "add_fillet", "label": "Round the corner"},
    }
    plain = {**solution, "title": "Thicker wall", "geometry": None, "addresses_root_cause": False}
    return {
        "visual_evidence": ["Beach marks at the inside corner"],
        "is_failure_analysis_applicable": True,
        "not_applicable_reason": None,
        "insufficient_image": False,
        "insufficient_reason": None,
        "geometric_features": ["Sharp inside corner where the beach marks start"],
        "material": "metal",
        "primary_mode": "fatigue_metal",
        "confidence": "medium",
        "probable_cause": "Cyclic bending at a sharp corner.",
        "rejected_alternative": {
            "mode": "brittle overload",
            "visual_feature": "Beach marks at the inside corner",
            "reason": "A single brittle overload would leave one uniform granular "
                      "surface, not beach marks growing from the corner.",
        },
        "specimen": None,
        "alternative_modes": [],
        "solutions": [solution, plain, plain],
    }


def _diagnose(body, monkeypatch):
    monkeypatch.setattr(diagnose, "load_knowledge_base", lambda: "## Mode: fatigue_metal\nx\n")
    monkeypatch.setattr(diagnose, "load_examples", lambda: [])
    response = SimpleNamespace(
        status="completed", model="fake", output_text=json.dumps(body), errors=None,
        usage=SimpleNamespace(total_input_tokens=1, total_output_tokens=1, total_thought_tokens=0),
    )
    fake = SimpleNamespace(interactions=SimpleNamespace(create=lambda **_: response))
    from PIL import Image
    import io
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
    result, _ = diagnose.diagnose_with_meta(buffer.getvalue(), client=fake, n_samples=1)
    return result


def test_unknown_dimensions_ask_the_user_instead_of_inventing_numbers(monkeypatch):
    geometry = {
        "current_radius_mm": 0.0,
        "proposed_radius_mm": 3.0,
        "section_depth_mm": None,
        "dimensions_source": "unknown",
    }

    result = _diagnose(_model_answer(geometry), monkeypatch)

    computed = result.solutions[0].computed
    assert computed["needs_input"] == ["section_depth_mm"]
    assert "kt_before" not in computed
    assert result.solutions[0].geometry["section_depth_mm"] is None


def test_a_null_dimension_is_unknown_even_if_the_model_claims_a_source(monkeypatch):
    geometry = {
        "current_radius_mm": None,
        "proposed_radius_mm": 3.0,
        "section_depth_mm": 10.0,
        "dimensions_source": "estimated_from_image",
    }

    result = _diagnose(_model_answer(geometry), monkeypatch)

    assert result.solutions[0].computed["inputs"]["dimensions_source"] == "unknown"


def test_non_geometric_solutions_carry_no_calculation(monkeypatch):
    geometry = {
        "current_radius_mm": 0.0,
        "proposed_radius_mm": 3.0,
        "section_depth_mm": 10.0,
        "dimensions_source": "estimated_from_image",
    }

    result = _diagnose(_model_answer(geometry), monkeypatch)

    assert result.solutions[0].computed["kt_before"] > result.solutions[0].computed["kt_after"]
    assert result.solutions[1].computed is None
    assert result.solutions[1].geometry is None


# --- User override endpoint -----------------------------------------------------


def test_typed_measurements_are_recomputed_by_mechanics():
    response = client.get(
        "/mechanics/fillet",
        params={"current_radius_mm": 0, "proposed_radius_mm": 3, "section_depth_mm": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == diagnose.fillet_computed(0.0, 3.0, 10.0, diagnose.USER_MEASURED)
    assert body["inputs"]["dimensions_source"] == "user_measured"


def test_impossible_measurements_get_a_clear_error():
    response = client.get(
        "/mechanics/fillet",
        params={"current_radius_mm": 0, "proposed_radius_mm": 3, "section_depth_mm": 0},
    )

    assert response.status_code == 400
    assert "section_depth_mm" in response.json()["error"]


def test_missing_measurement_is_a_bad_request():
    response = client.get("/mechanics/fillet", params={"current_radius_mm": 0})

    assert response.status_code == 422
    assert "error" in response.json()


def test_card_carries_the_reference_peak_stresses_from_mechanics():
    computed = diagnose.fillet_computed(0.0, 3.0, 10.0, "user_context")

    assert computed["reference_nominal_stress_mpa"] == 10.0
    assert computed["peak_before_mpa"] == pytest.approx(computed["kt_before"] * 10.0)
    assert computed["peak_after_mpa"] == pytest.approx(computed["kt_after"] * 10.0)
