"""Tests for mechanics.py. No API key or network needed."""

import math

import pytest

from mechanics import (
    fillet_improvement,
    min_wall_thickness_check,
    stress_concentration_factor,
)

GEOMETRY = "notched_flat_bar_bending"
DEPTH_MM = 20.0


def kt_at(r_over_d: float) -> dict:
    return stress_concentration_factor(r_over_d * DEPTH_MM, DEPTH_MM, GEOMETRY)


# --- stress_concentration_factor -------------------------------------------


@pytest.mark.parametrize(
    ("r_over_d", "expected_kt"),
    [(0.05, 2.7), (0.10, 2.1), (0.15, 1.8), (0.20, 1.65), (0.30, 1.45)],
)
def test_returns_chart_value_at_each_table_point(r_over_d, expected_kt):
    result = kt_at(r_over_d)

    assert result["kt"] == pytest.approx(expected_kt)
    assert result["r_over_d"] == pytest.approx(r_over_d)
    assert result["is_extrapolated"] is False
    assert result["is_capped"] is False


@pytest.mark.parametrize(
    ("r_over_d", "expected_kt"),
    [(0.075, 2.4), (0.125, 1.95), (0.175, 1.73), (0.25, 1.55)],
)
def test_interpolates_linearly_between_table_points(r_over_d, expected_kt):
    result = kt_at(r_over_d)

    assert result["kt"] == pytest.approx(expected_kt, abs=0.005)
    assert result["is_extrapolated"] is False


def test_result_has_exactly_the_documented_keys():
    result = kt_at(0.10)

    assert set(result) == {"kt", "r_over_d", "basis", "is_extrapolated", "is_capped"}


def test_basis_states_values_are_approximate_chart_values():
    basis = kt_at(0.10)["basis"].lower()

    assert "approximate" in basis
    assert "peterson" in basis


def test_flags_extrapolation_below_table_range():
    result = kt_at(0.025)

    assert result["is_extrapolated"] is True
    assert result["is_capped"] is False
    assert result["kt"] == pytest.approx(3.0)
    assert "extrapolat" in result["basis"].lower()


def test_flags_extrapolation_above_table_range():
    result = kt_at(0.40)

    assert result["is_extrapolated"] is True
    assert result["kt"] == pytest.approx(1.25)
    assert "extrapolat" in result["basis"].lower()


def test_kt_never_drops_below_one_for_very_large_radius():
    result = kt_at(1.0)

    assert result["kt"] == pytest.approx(1.0)
    assert result["is_extrapolated"] is True


def test_sharp_corner_is_capped_flagged_and_finite():
    result = stress_concentration_factor(0.0, DEPTH_MM, GEOMETRY)

    assert math.isfinite(result["kt"])
    assert result["kt"] == pytest.approx(3.3)
    assert result["is_capped"] is True
    assert result["is_extrapolated"] is True
    assert "lower bound" in result["basis"].lower()


def test_table_edge_is_not_flagged_despite_float_rounding():
    # 0.3 / 6 is 0.049999999999999996 in floating point, not 0.05.
    result = stress_concentration_factor(0.3, 6.0, GEOMETRY)

    assert result["is_extrapolated"] is False
    assert result["kt"] == pytest.approx(2.7)


def test_kt_decreases_as_radius_grows():
    kts = [kt_at(r)["kt"] for r in (0.0, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)]

    assert kts == sorted(kts, reverse=True)


def test_rejects_unknown_geometry_instead_of_inventing_values():
    with pytest.raises(ValueError, match="geometry"):
        stress_concentration_factor(1.0, 10.0, "shaft_with_shoulder_torsion")


@pytest.mark.parametrize("depth", [0.0, -5.0, math.nan, math.inf])
def test_rejects_invalid_section_depth(depth):
    with pytest.raises(ValueError):
        stress_concentration_factor(1.0, depth, GEOMETRY)


@pytest.mark.parametrize("radius", [-0.1, math.nan, math.inf])
def test_rejects_invalid_radius(radius):
    with pytest.raises(ValueError):
        stress_concentration_factor(radius, 10.0, GEOMETRY)


# --- fillet_improvement ----------------------------------------------------


def test_fillet_improvement_reports_both_kt_and_percent_reduction():
    result = fillet_improvement(0.5, 2.0, 10.0)

    assert result["kt_current"] == pytest.approx(2.7)
    assert result["kt_proposed"] == pytest.approx(1.65)
    assert result["peak_stress_reduction_pct"] == pytest.approx(38.9, abs=0.05)
    assert result["is_extrapolated"] is False
    assert result["reduction_is_lower_bound"] is False


def test_percent_reduction_matches_the_kt_values_shown():
    result = fillet_improvement(0.7, 2.3, 10.0)

    from_shown_values = (1 - result["kt_proposed"] / result["kt_current"]) * 100
    assert result["peak_stress_reduction_pct"] == pytest.approx(
        from_shown_values, abs=0.05
    )


def test_improvement_from_sharp_corner_is_flagged_as_lower_bound():
    result = fillet_improvement(0.0, 3.0, 10.0)

    assert result["kt_current"] == pytest.approx(3.3)
    assert result["kt_proposed"] == pytest.approx(1.45)
    assert result["peak_stress_reduction_pct"] == pytest.approx(56.1, abs=0.05)
    assert result["is_extrapolated"] is True
    assert result["is_capped"] is True
    assert result["reduction_is_lower_bound"] is True


def test_improvement_flags_extrapolation_when_proposed_is_out_of_range():
    result = fillet_improvement(1.0, 5.0, 10.0)

    assert result["is_extrapolated"] is True
    assert result["is_capped"] is False


def test_smaller_proposed_radius_gives_negative_reduction():
    result = fillet_improvement(2.0, 0.5, 10.0)

    assert result["peak_stress_reduction_pct"] < 0


def test_fillet_improvement_basis_states_approximation():
    assert "approximate" in fillet_improvement(0.5, 2.0, 10.0)["basis"].lower()


def test_fillet_improvement_rejects_invalid_depth():
    with pytest.raises(ValueError):
        fillet_improvement(0.5, 2.0, 0.0)


# --- min_wall_thickness_check ----------------------------------------------


@pytest.mark.parametrize(
    ("measured_mm", "expected_verdict"),
    [
        (0.4, "will_not_print_reliably"),
        (0.79, "will_not_print_reliably"),
        (0.8, "below_practical_minimum"),
        (1.0, "below_practical_minimum"),
        (1.19, "below_practical_minimum"),
        (1.2, "ok"),
        (3.0, "ok"),
    ],
)
def test_wall_thickness_verdict_at_and_around_thresholds(measured_mm, expected_verdict):
    result = min_wall_thickness_check(measured_mm)

    assert result["verdict"] == expected_verdict


def test_wall_thickness_messages_use_the_specified_wording():
    assert "will not print reliably" in min_wall_thickness_check(0.5)["message"]
    assert "below practical minimum" in min_wall_thickness_check(1.0)["message"]


def test_wall_thickness_defaults_to_fdm_and_echoes_inputs():
    result = min_wall_thickness_check(1.0)

    assert result["process"] == "fdm"
    assert result["measured_mm"] == pytest.approx(1.0)


def test_wall_thickness_rejects_unknown_process():
    with pytest.raises(ValueError, match="process"):
        min_wall_thickness_check(1.0, process="sla")


@pytest.mark.parametrize("measured", [0.0, -1.0, math.nan])
def test_wall_thickness_rejects_non_positive_measurement(measured):
    with pytest.raises(ValueError):
        min_wall_thickness_check(measured)
