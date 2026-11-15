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
STRESS_DECIMALS = 1

# A stated reference load for showing peak stress in MPa. The photo cannot give
# the part's real load, so the card labels this as a reference, not a measurement.
REFERENCE_NOMINAL_STRESS_MPA = 10.0

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


def _chart_segment(r_over_d: float) -> tuple[tuple[float, float], tuple[float, float], bool]:
    """The two chart points Kt is read between (or extended from) for this r/d,
    and whether r/d lies outside the chart."""
    lowest, highest = KT_TABLE[0][0], KT_TABLE[-1][0]
    if r_over_d < lowest - RANGE_TOLERANCE:
        return KT_TABLE[0], KT_TABLE[1], True
    if r_over_d > highest + RANGE_TOLERANCE:
        return KT_TABLE[-2], KT_TABLE[-1], True
    clamped = min(max(r_over_d, lowest), highest)
    for p0, p1 in zip(KT_TABLE, KT_TABLE[1:]):
        if clamped <= p1[0]:
            return p0, p1, False
    return KT_TABLE[-2], KT_TABLE[-1], False


def _kt_from_table(r_over_d: float) -> tuple[float, bool]:
    """Return (kt, is_extrapolated) for a given r/d."""
    p0, p1, outside = _chart_segment(r_over_d)
    lowest, highest = KT_TABLE[0][0], KT_TABLE[-1][0]
    x = r_over_d if outside else min(max(r_over_d, lowest), highest)
    kt = _line_through(p0, p1, x)
    return (max(kt, KT_FLOOR) if r_over_d > highest else kt), outside


def fmt(value: float, digits: int = 2) -> str:
    """A number for the shown working: rounded, no trailing zeros."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text.replace("-", "−")


def fmt_range(values, digits: int = 2) -> str:
    low, high = values
    return fmt(low, digits) if fmt(low, digits) == fmt(high, digits) else f"{fmt(low, digits)}–{fmt(high, digits)}"


def step(label: str, formula: str, substituted: str, value, unit: str = "",
         digits: int = 2, source: str | None = None) -> dict:
    """One line of shown working. `value` is the very number the result reports."""
    shown = fmt_range(value, digits) if isinstance(value, (list, tuple)) else fmt(value, digits)
    return {
        "label": label,
        "formula": formula,
        "substituted": substituted,
        "result": f"×{shown}" if unit == "×" else f"{shown} {unit}".strip(),
        "value": list(value) if isinstance(value, (list, tuple)) else value,
        "source": source,
    }


_PETERSON = "Peterson's Stress Concentration Factors, notched flat bar in bending (chart values)"


def _kt_step(label: str, r_over_d: float, kt: float) -> dict:
    (x0, y0), (x1, y1), outside = _chart_segment(r_over_d)
    how = "extended past the chart" if outside else "read between chart points"
    return step(
        label,
        f"Kt {how}: Kt = Kt₀ + (Kt₁ − Kt₀) · (r/d − x₀) / (x₁ − x₀)",
        f"{fmt(y0)} + ({fmt(y1)} − {fmt(y0)}) · ({fmt(r_over_d, 4)} − {fmt(x0)}) / ({fmt(x1)} − {fmt(x0)})",
        kt, source=_PETERSON,
    )


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

    working = [
        step("r/d now", "r / d", f"{fmt(current_radius_mm, 2)} mm / {fmt(section_depth_mm, 2)} mm",
             current["r_over_d"], digits=4),
        _kt_step("Kt now", current["r_over_d"], current["kt"]),
        step("r/d proposed", "r / d", f"{fmt(proposed_radius_mm, 2)} mm / {fmt(section_depth_mm, 2)} mm",
             proposed["r_over_d"], digits=4),
        _kt_step("Kt proposed", proposed["r_over_d"], proposed["kt"]),
        step("Peak stress change", "(1 − Kt proposed / Kt now) × 100",
             f"(1 − {fmt(proposed['kt'])} / {fmt(current['kt'])}) × 100",
             round(reduction_pct, PERCENT_DECIMALS), "%", digits=1),
    ]
    if current["is_capped"]:
        working[1]["note"] = "A sharp corner (r = 0) has no finite Kt; this is a capped lower bound."
    return {
        "working": working,
        "kt_current": current["kt"],
        "kt_proposed": proposed["kt"],
        "r_over_d_current": current["r_over_d"],
        "r_over_d_proposed": proposed["r_over_d"],
        "peak_stress_reduction_pct": round(reduction_pct, PERCENT_DECIMALS),
        "reference_nominal_stress_mpa": REFERENCE_NOMINAL_STRESS_MPA,
        "peak_stress_current_mpa": round(current["kt"] * REFERENCE_NOMINAL_STRESS_MPA, STRESS_DECIMALS),
        "peak_stress_proposed_mpa": round(proposed["kt"] * REFERENCE_NOMINAL_STRESS_MPA, STRESS_DECIMALS),
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


def _section_steps(shape: str, current: dict, proposed: dict, result: dict) -> list[dict]:
    if shape == "round":
        d1, d2 = current["diameter_mm"], proposed["diameter_mm"]
        ratio = f"({fmt(d2)} / {fmt(d1)})"
        return [
            step("Weight ratio", "(d₂ / d₁)²  (area of a circle ∝ d²)", f"{ratio}²", result["weight_ratio"], "×"),
            step("Bending strength ratio", "(d₂ / d₁)³  (section modulus πd³/32)", f"{ratio}³",
                 result["strength_ratio"], "×"),
            step("Stiffness ratio", "(d₂ / d₁)⁴  (second moment πd⁴/64)", f"{ratio}⁴",
                 result["stiffness_ratio"], "×"),
        ]
    b1, h1 = current["width_mm"], current["thickness_mm"]
    b2, h2 = proposed["width_mm"], proposed["thickness_mm"]
    return [
        step("Weight ratio", "(b₂·h₂) / (b₁·h₁)  (area b·h)",
             f"({fmt(b2)}·{fmt(h2)}) / ({fmt(b1)}·{fmt(h1)})", result["weight_ratio"], "×"),
        step("Bending strength ratio", "(b₂·h₂²) / (b₁·h₁²)  (section modulus b·h²/6)",
             f"({fmt(b2)}·{fmt(h2)}²) / ({fmt(b1)}·{fmt(h1)}²)", result["strength_ratio"], "×"),
        step("Stiffness ratio", "(b₂·h₂³) / (b₁·h₁³)  (second moment b·h³/12)",
             f"({fmt(b2)}·{fmt(h2)}³) / ({fmt(b1)}·{fmt(h1)}³)", result["stiffness_ratio"], "×"),
    ]


def section_change(
    shape: str,
    current: dict,
    proposed: dict,
    length_mm: float | None = None,
    density_g_cm3: tuple[float, float] | None = None,
    density_source: str | None = None,
) -> dict:
    """Weight, bending strength and stiffness ratios for a change of section.

    current / proposed hold diameter_mm (round) or width_mm and thickness_mm
    (rectangular, bent across its thickness). With a length and a density range,
    masses are added in grams as ranges. `working` shows every step.
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
    result["working"] = _section_steps(shape, current, proposed, result)
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
        rho = f"{fmt_range(density_g_cm3)} g/cm³"
        length = f"{fmt(length_mm, 1)} mm"
        result["working"] += [
            step("Mass now", "A₁ · L · ρ ÷ 1000  (mm³ → cm³)",
                 f"{fmt(area0, 1)} mm² · {length} · {rho} ÷ 1000", result["mass_before_g"], "g", 1, density_source),
            step("Mass after", "A₂ · L · ρ ÷ 1000",
                 f"{fmt(area1, 1)} mm² · {length} · {rho} ÷ 1000", result["mass_after_g"], "g", 1, density_source),
            step("Added mass", "(A₂ − A₁) · L · ρ ÷ 1000",
                 f"({fmt(area1, 1)} − {fmt(area0, 1)}) mm² · {length} · {rho} ÷ 1000",
                 result["mass_added_g"], "g", 1, density_source),
        ]
    return result


SPAN_RATIO_DECIMALS = 2
_SPAN_BASIS = (
    "Idealised beam with the load at mid-span and simple supports at each end, same "
    "load and section. Peak bending moment M = F·L/4 grows with the span L, and so "
    "does the peak bending stress; deflection δ = F·L³/(48·E·I) grows with the span "
    "cubed. Real supports and loads differ, so treat these as ratios, not exact values."
)


def span_change(current_span_mm: float, proposed_span_mm: float) -> dict:
    """How the peak bending moment (and stress) and the deflection change when the
    unsupported span changes, for an idealised centre-loaded beam."""
    _require_positive("current_span_mm", current_span_mm)
    _require_positive("proposed_span_mm", proposed_span_mm)
    ratio = proposed_span_mm / current_span_mm
    moment_ratio = round(ratio, SPAN_RATIO_DECIMALS)
    deflection_ratio = round(ratio**3, SPAN_RATIO_DECIMALS)
    stress_change_pct = round((ratio - 1) * 100, PERCENT_DECIMALS)
    deflection_change_pct = round((ratio**3 - 1) * 100, PERCENT_DECIMALS)
    spans = f"{fmt(proposed_span_mm, 1)} mm / {fmt(current_span_mm, 1)} mm"
    return {
        "moment_ratio": moment_ratio,
        "deflection_ratio": deflection_ratio,
        "stress_change_pct": stress_change_pct,
        "deflection_change_pct": deflection_change_pct,
        "basis": _SPAN_BASIS,
        "working": [
            step("Peak moment and stress ratio", "L₂ / L₁  (M = F·L/4)", spans, moment_ratio, "×"),
            step("Peak stress change", "(L₂ / L₁ − 1) × 100", f"({spans} − 1) × 100", stress_change_pct, "%", 1),
            step("Deflection ratio", "(L₂ / L₁)³  (δ = F·L³ / 48EI)", f"({spans})³", deflection_ratio, "×"),
            step("Deflection change", "((L₂ / L₁)³ − 1) × 100", f"(({spans})³ − 1) × 100",
                 deflection_change_pct, "%", 1),
        ],
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
