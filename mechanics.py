"""Deterministic mechanics calculations. Pure Python, no model calls.

Every number returned here is computed, and every result says how far it can be
trusted: chart values are approximate, and anything outside the chart's range is
flagged rather than presented as exact.
"""

import math

SUPPORTED_GEOMETRY = "notched_flat_bar_bending"

# (r/d, Kt) read from Peterson's stress concentration charts for a notched flat
# bar in bending. Chart readings, good to roughly two significant figures.
KT_TABLE: tuple[tuple[float, float], ...] = (
    (0.05, 2.7),
    (0.10, 2.1),
    (0.15, 1.8),
    (0.20, 1.65),
    (0.30, 1.45),
)

KT_FLOOR = 1.0  # No stress concentration at all.
RANGE_TOLERANCE = 1e-9  # Absorbs float error at the table edges (0.3 / 6 != 0.05).
KT_DECIMALS = 2
RATIO_DECIMALS = 4
PERCENT_DECIMALS = 1

FDM_PRACTICAL_MIN_WALL_MM = 1.2
FDM_RELIABLE_PRINT_MIN_WALL_MM = 0.8

_BASIS_IN_RANGE = (
    "Approximate value, linearly interpolated from Peterson stress concentration "
    "charts for a notched flat bar in bending (r/d 0.05 to 0.30). Chart readings "
    "are good to about two significant figures."
)
_BASIS_EXTRAPOLATED = (
    "Approximate value, linearly EXTRAPOLATED beyond the Peterson chart range "
    "(r/d 0.05 to 0.30) for a notched flat bar in bending. Treat as indicative only."
)
_BASIS_CAPPED = (
    "Sharp corner (r = 0): the theoretical Kt is unbounded. The value shown is a "
    "capped lower bound, extrapolated from approximate Peterson chart values for "
    "a notched flat bar in bending. The true peak stress is higher."
)


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")


def _line_through(
    p0: tuple[float, float], p1: tuple[float, float], x: float
) -> float:
    (x0, y0), (x1, y1) = p0, p1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _kt_from_table(r_over_d: float) -> tuple[float, bool]:
    """Return (kt, is_extrapolated) for a given r/d."""
    lowest, highest = KT_TABLE[0][0], KT_TABLE[-1][0]

    if r_over_d < lowest - RANGE_TOLERANCE:
        return _line_through(KT_TABLE[0], KT_TABLE[1], r_over_d), True

    if r_over_d > highest + RANGE_TOLERANCE:
        kt = _line_through(KT_TABLE[-2], KT_TABLE[-1], r_over_d)
        return max(kt, KT_FLOOR), True

    clamped = min(max(r_over_d, lowest), highest)
    for p0, p1 in zip(KT_TABLE, KT_TABLE[1:]):
        if clamped <= p1[0]:
            return _line_through(p0, p1, clamped), False
    return KT_TABLE[-1][1], False


def stress_concentration_factor(
    notch_radius_mm: float, section_depth_mm: float, geometry: str
) -> dict:
    """Kt for a notch of the given radius in a section of the given depth.

    Linear interpolation over KT_TABLE. Outside the table the value is linearly
    extrapolated and is_extrapolated is True. A sharp corner (radius 0) is capped
    at the extrapolated r/d = 0 value and is_capped is True: never infinity.
    """
    if geometry != SUPPORTED_GEOMETRY:
        raise ValueError(
            f"Unsupported geometry {geometry!r}. Only {SUPPORTED_GEOMETRY!r} has "
            "chart values in this module."
        )
    _require_finite("notch_radius_mm", notch_radius_mm)
    _require_finite("section_depth_mm", section_depth_mm)
    if notch_radius_mm < 0:
        raise ValueError(f"notch_radius_mm must be >= 0, got {notch_radius_mm}")
    if section_depth_mm <= 0:
        raise ValueError(f"section_depth_mm must be > 0, got {section_depth_mm}")

    r_over_d = notch_radius_mm / section_depth_mm
    kt, is_extrapolated = _kt_from_table(r_over_d)
    is_capped = notch_radius_mm == 0

    if is_capped:
        basis = _BASIS_CAPPED
    elif is_extrapolated:
        basis = _BASIS_EXTRAPOLATED
    else:
        basis = _BASIS_IN_RANGE

    return {
        "kt": round(kt, KT_DECIMALS),
        "r_over_d": round(r_over_d, RATIO_DECIMALS),
        "basis": basis,
        "is_extrapolated": is_extrapolated,
        "is_capped": is_capped,
    }


def fillet_improvement(
    current_radius_mm: float, proposed_radius_mm: float, section_depth_mm: float
) -> dict:
    """Compare Kt before and after a fillet change at the same section depth.

    Peak stress = Kt x nominal stress. With the load and section unchanged, the
    nominal stress is the same, so the peak stress falls by 1 - Kt_new / Kt_old.
    The percentage is computed from the rounded Kt values that are returned, so
    it can be checked by hand from the numbers shown.
    """
    current = stress_concentration_factor(
        current_radius_mm, section_depth_mm, SUPPORTED_GEOMETRY
    )
    proposed = stress_concentration_factor(
        proposed_radius_mm, section_depth_mm, SUPPORTED_GEOMETRY
    )
    reduction_pct = (1 - proposed["kt"] / current["kt"]) * 100
    is_lower_bound = current["is_capped"] and not proposed["is_capped"]

    basis = (
        "Peak stress = Kt x nominal stress, same load and section depth before "
        "and after. Kt values are approximate Peterson chart values for a notched "
        "flat bar in bending."
    )
    if current["is_extrapolated"] or proposed["is_extrapolated"]:
        basis += " At least one Kt is extrapolated beyond the chart range."
    if is_lower_bound:
        basis += (
            " The current corner is sharp, so its Kt is a capped lower bound and "
            "the real reduction is at least the figure shown."
        )

    return {
        "kt_current": current["kt"],
        "kt_proposed": proposed["kt"],
        "r_over_d_current": current["r_over_d"],
        "r_over_d_proposed": proposed["r_over_d"],
        "peak_stress_reduction_pct": round(reduction_pct, PERCENT_DECIMALS),
        "reduction_is_lower_bound": is_lower_bound,
        "is_extrapolated": current["is_extrapolated"] or proposed["is_extrapolated"],
        "is_capped": current["is_capped"] or proposed["is_capped"],
        "basis": basis,
    }


SECTION_SHAPES = ("round", "rectangular")
SECTION_RATIO_DECIMALS = 2
GRAMS_DECIMALS = 1
MM3_PER_CM3 = 1000.0

_SECTION_BASIS = (
    "Same material, length and load. Weight scales with cross-section area. Bending "
    "strength scales with the section modulus (diameter cubed for a round section; "
    "width x thickness squared for a rectangle bent across its thickness). Stiffness "
    "scales with the second moment of area (diameter to the fourth; width x "
    "thickness cubed). These are ratios for the same load, not stress-concentration "
    "values: holes, notches and sharp corners are not included."
)


def _section_properties(shape: str, dims: dict) -> tuple[float, float, float]:
    """(area mm^2, section modulus mm^3, second moment mm^4) for one section."""
    if shape == "round":
        d = dims["diameter_mm"]
        _require_positive("diameter_mm", d)
        return math.pi * d**2 / 4, math.pi * d**3 / 32, math.pi * d**4 / 64
    if shape == "rectangular":
        b, h = dims["width_mm"], dims["thickness_mm"]
        _require_positive("width_mm", b)
        _require_positive("thickness_mm", h)
        return b * h, b * h**2 / 6, b * h**3 / 12
    raise ValueError(f"shape must be one of {SECTION_SHAPES}, got {shape!r}")


def _require_positive(name: str, value: float) -> None:
    _require_finite(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


def section_change(
    shape: str,
    current: dict,
    proposed: dict,
    length_mm: float | None = None,
    density_g_cm3: tuple[float, float] | None = None,
) -> dict:
    """Weight, bending strength and stiffness ratios for a change of section.

    current / proposed hold diameter_mm (round) or width_mm and thickness_mm
    (rectangular, bent across its thickness). With a length and a density range,
    masses are added in grams as ranges.
    """
    area0, modulus0, inertia0 = _section_properties(shape, current)
    area1, modulus1, inertia1 = _section_properties(shape, proposed)
    result = {
        "weight_ratio": round(area1 / area0, SECTION_RATIO_DECIMALS),
        "strength_ratio": round(modulus1 / modulus0, SECTION_RATIO_DECIMALS),
        "stiffness_ratio": round(inertia1 / inertia0, SECTION_RATIO_DECIMALS),
        "area_mm2": [round(area0, 2), round(area1, 2)],
        "basis": _SECTION_BASIS,
    }
    if length_mm is not None and density_g_cm3 is not None:
        _require_positive("length_mm", length_mm)
        low, high = density_g_cm3
        _require_positive("density", low)
        _require_positive("density", high)

        def grams(area: float) -> list[float]:
            volume_cm3 = area * length_mm / MM3_PER_CM3
            return [round(volume_cm3 * low, GRAMS_DECIMALS), round(volume_cm3 * high, GRAMS_DECIMALS)]

        result["mass_before_g"] = grams(area0)
        result["mass_after_g"] = grams(area1)
        result["mass_added_g"] = grams(area1 - area0)
    return result


def min_wall_thickness_check(measured_mm: float, process: str = "fdm") -> dict:
    """Check a measured wall thickness against practical limits for the process."""
    if process != "fdm":
        raise ValueError(
            f"Unsupported process {process!r}. Only 'fdm' has limits in this module."
        )
    _require_finite("measured_mm", measured_mm)
    if measured_mm <= 0:
        raise ValueError(f"measured_mm must be > 0, got {measured_mm}")

    if measured_mm < FDM_RELIABLE_PRINT_MIN_WALL_MM:
        verdict = "will_not_print_reliably"
        message = (
            f"{measured_mm} mm will not print reliably "
            f"(below {FDM_RELIABLE_PRINT_MIN_WALL_MM} mm)."
        )
    elif measured_mm < FDM_PRACTICAL_MIN_WALL_MM:
        verdict = "below_practical_minimum"
        message = (
            f"{measured_mm} mm is below practical minimum "
            f"({FDM_PRACTICAL_MIN_WALL_MM} mm)."
        )
    else:
        verdict = "ok"
        message = (
            f"{measured_mm} mm meets the practical minimum "
            f"({FDM_PRACTICAL_MIN_WALL_MM} mm)."
        )

    return {
        "measured_mm": measured_mm,
        "process": process,
        "verdict": verdict,
        "message": message,
        "practical_minimum_mm": FDM_PRACTICAL_MIN_WALL_MM,
        "reliable_print_minimum_mm": FDM_RELIABLE_PRINT_MIN_WALL_MM,
    }
