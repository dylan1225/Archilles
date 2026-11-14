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
