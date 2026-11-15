"""What could happen next: a modelled failure scenario for a confirmed weakness.

The agents decide *whether* a confirmed observation fits a mechanism and
explain why in words. Every number and every 3D stage comes from this module,
computed from the scan's own geometry. No model call, same inputs, same result.

One mechanism is modelled: a ceiling finish wetted from above that softens,
sags and sheds a section.
"""

from __future__ import annotations

import math

G = 9.81  # m/s^2
PSF_TO_KG_M2 = 4.882
MIN_ZONE_SIDE_M = 0.45

# Typical dead loads of dry ceiling finishes, from the minimum design dead
# loads tabulated in the ASCE/SEI 7 commentary (Table C3.1-1a).
CEILING_FINISHES = {
    "gypsum_board": {"label": "12.7 mm (1/2 in) gypsum board", "psf": 2.2},
    "plaster_on_lath": {"label": "plaster on wood lath", "psf": 8.0},
}
SQFT_PER_M2 = 10.764
# Published US consumer cost guides, in US dollars. Every figure below is quoted
# from the guide named beside it; nothing here is an estimate of our own.
PATCH_USD_PER_SQFT = (50, 80)      # professional drywall repair (Inch Calculator)
PATCH_MINIMUM_USD = 100            # low end of a typical drywall repair job (Inch Calculator)
LEAK_REPAIR_USD = (1000, 4000)     # roof leak averages $1,000; plumbing repairs $1,000 to $4,000 (Bob Vila)
DRYING_USD_PER_SQFT = (3.0, 6.5)   # drying after clean ($3-4) to gray ($4-6.50) water (Bob Vila)
FLOOR_RESTORATION_USD = (200, 500)  # floor restoration after water damage (Bob Vila)
MOULD_REMOVAL_USD = (1100, 3400)   # mould removal, when it has taken hold (Bob Vila)
AVERAGE_WATER_CLAIM_USD = 15400    # average insured water damage or freezing claim, 2019-2023 (Triple-I)
MECHANISM = "ceiling_moisture_failure"
PREMISE = "If the moisture source above this ceiling is not found and fixed"
STAGES = [
    {
        "id": "as_scanned",
        "title": "As scanned",
        "caption": "The ceiling as the scan recorded it. The flagged region is outlined on the real surface.",
    },
    {
        "id": "saturation",
        "title": "Repeated wetting",
        "caption": "Water arriving from above spreads through the finish. Stains widen and the material softens and gains weight.",
    },
    {
        "id": "sag",
        "title": "Sag",
        "caption": "The softened section loses its hold on the framing and hangs lower. Cracks open around its edge.",
    },
    {
        "id": "detach",
        "title": "Detachment",
        "caption": "A section lets go and falls into the room.",
    },
    {
        "id": "aftermath",
        "title": "Aftermath",
        "caption": "Framing is exposed to the room, debris covers the floor below, and damp surfaces around the opening stay prone to mould.",
    },
]


def _widen(corners: list[list[float]], centre: list[float]) -> tuple[list[list[float]], bool]:
    """Scale a quad about its centre so its shorter side is at least one joist bay."""
    def length(a, b):
        return math.dist(a, b)

    shortest = min(length(corners[i], corners[(i + 1) % 4]) for i in range(4))
    if shortest >= MIN_ZONE_SIDE_M or shortest <= 1e-6:
        return corners, False
    factor = MIN_ZONE_SIDE_M / shortest
    return [[round(centre[k] + (corner[k] - centre[k]) * factor, 3) for k in range(3)] for corner in corners], True


def _area(corners: list[list[float]]) -> float:
    def tri(a, b, c):
        u = [b[k] - a[k] for k in range(3)]
        v = [c[k] - a[k] for k in range(3)]
        return 0.5 * math.sqrt((u[1] * v[2] - u[2] * v[1]) ** 2 + (u[2] * v[0] - u[0] * v[2]) ** 2 + (u[0] * v[1] - u[1] * v[0]) ** 2)

    return tri(corners[0], corners[1], corners[2]) + tri(corners[0], corners[2], corners[3])


def ceiling_fall(area_m2: float, drop_m: float, landing_height_m: float, finish: str = "unknown") -> dict:
    """Free-fall numbers for a ceiling section of a given area.

    Air resistance and the extra weight of absorbed water are ignored, so the
    masses are lower bounds for a wet section.
    """
    if not (math.isfinite(area_m2) and math.isfinite(drop_m)) or area_m2 <= 0 or drop_m <= 0:
        raise ValueError("A ceiling scenario needs a positive area and drop height.")
    landing = min(max(landing_height_m, 0.0), drop_m * 0.9)
    finishes = [finish] if finish in CEILING_FINISHES else list(CEILING_FINISHES)
    masses = []
    for key in finishes:
        spec = CEILING_FINISHES[key]
        mass = area_m2 * spec["psf"] * PSF_TO_KG_M2
        masses.append({
            "finish": key,
            "label": spec["label"],
            "kg_per_m2": round(spec["psf"] * PSF_TO_KG_M2, 1),
            "mass_kg": round(mass, 1),
            "energy_at_floor_j": round(mass * G * drop_m),
        })
    return {
        "area_m2": round(area_m2, 2),
        "drop_height_m": round(drop_m, 2),
        "fall_time_s": round(math.sqrt(2 * drop_m / G), 2),
        "speed_at_floor_m_s": round(math.sqrt(2 * G * drop_m), 1),
        "landing_height_m": round(landing, 2),
        "speed_at_landing_m_s": round(math.sqrt(2 * G * (drop_m - landing)), 1),
        "masses": masses,
    }


def _dollars(value: float) -> int:
    return int(round(value / 10.0)) * 10


def repair_costs(area_m2: float, room_area_m2: float | None) -> dict:
    """What the repair costs now, against what it costs once the section has fallen.

    Ranges are published guide prices applied to the scan's own areas. The
    "after" bill contains every "now" line plus the work a collapse adds, so
    the difference is the cost of waiting. Injury, damaged belongings and
    living elsewhere during the work are not priced.
    """
    if not math.isfinite(area_m2) or area_m2 <= 0:
        raise ValueError("A repair estimate needs a positive ceiling area.")
    section_sqft = area_m2 * SQFT_PER_M2
    patch = [max(_dollars(section_sqft * rate), PATCH_MINIMUM_USD) for rate in PATCH_USD_PER_SQFT]
    leak = {
        "label": "Find and fix the moisture source", "low": LEAK_REPAIR_USD[0], "high": LEAK_REPAIR_USD[1],
        "basis": "Roof leak repair averages $1,000; plumbing repairs run $1,000 to $4,000. Needed either way.",
        "reference_id": "bobvila_water_damage",
    }
    now_items = [leak, {
        "label": "Cut out and patch the flagged ceiling section", "low": patch[0], "high": patch[1],
        "basis": f"{area_m2:.2f} m² ({section_sqft:.0f} sq ft) at ${PATCH_USD_PER_SQFT[0]} to ${PATCH_USD_PER_SQFT[1]} per sq ft, professional drywall repair.",
        "reference_id": "inchcalculator_drywall",
    }]
    after_items = [leak, {
        "label": "Close the opening the fallen section leaves", "low": patch[0], "high": patch[1],
        "basis": "Priced at the flagged section's area, as now. A real collapse often takes more ceiling with it.",
        "reference_id": "inchcalculator_drywall",
    }]
    notes = []
    if room_area_m2 and math.isfinite(room_area_m2) and room_area_m2 > 0:
        room_sqft = room_area_m2 * SQFT_PER_M2
        after_items.append({
            "label": "Dry out and clean the room", "low": _dollars(room_sqft * DRYING_USD_PER_SQFT[0]), "high": _dollars(room_sqft * DRYING_USD_PER_SQFT[1]),
            "basis": f"The room's {room_area_m2:.1f} m² ({room_sqft:.0f} sq ft) floor at $3 to $6.50 per sq ft, clean to gray water.",
            "reference_id": "bobvila_water_damage",
        })
    else:
        notes.append("The scan gave no floor area for this room, so drying and cleaning the room is not priced.")
    after_items += [
        {
            "label": "Restore the floor under the fall", "low": FLOOR_RESTORATION_USD[0], "high": FLOOR_RESTORATION_USD[1],
            "basis": "Floor restoration after water damage, $200 to $500.", "reference_id": "bobvila_water_damage",
        },
        {
            "label": "Remove mould, if it has taken hold", "low": 0, "high": MOULD_REMOVAL_USD[1],
            "basis": "Mould removal costs $1,100 to $3,400 when it is needed; $0 if the room is dried in time.",
            "reference_id": "bobvila_water_damage",
        },
    ]

    def bill(label: str, items: list[dict]) -> dict:
        return {"label": label, "low": sum(item["low"] for item in items), "high": sum(item["high"] for item in items), "items": items}

    now, after = bill("Repair now", now_items), bill("Repair after the section falls", after_items)
    notes += [
        "Not priced: injury, damaged furniture and belongings, or living elsewhere during the work.",
        "Rough ranges from US consumer cost guides, not a quote. Local labour rates, access and the ceiling's finish change the price; plaster usually costs more than drywall.",
    ]
    return {
        "currency": "USD",
        "now": now,
        "after": after,
        "added_low": after["low"] - now["low"],
        "added_high": after["high"] - now["high"],
        "context": {
            "amount": AVERAGE_WATER_CLAIM_USD,
            "statement": "For scale, the average insured US home water damage or freezing claim, 2019 to 2023:",
            "reference_id": "iii_water_claims",
        },
        "notes": notes,
        "reference_ids": ["bobvila_water_damage", "inchcalculator_drywall"],
    }


def _projector(position: dict) -> dict:
    pose = position["pose"]
    return {
        "position_id": position["id"],
        "position": [pose["position"][k] for k in "xyz"],
        "quaternion": [pose["rotation"][k] for k in "xyzw"],
        "faces": [f"{position['id']}-{name}" for name in ("ceiling", "wall-1", "wall-2", "wall-3", "wall-4", "floor")],
    }


def build_ceiling_scenario(finding: dict, room: dict, positions: list[dict], bounds: dict | None, analysis: dict) -> dict:
    """Assemble the scenario the 3D view plays, from a confirmed ceiling finding.

    `finding["placement"]` is the flagged region on the real ceiling.
    `positions` are scan positions in the room, the finding's own first; their
    panoramas are projected onto the mesh so the 3D room shows real surfaces.
    `analysis` carries the failure analyst's words and finish judgement.
    """
    placement = finding["placement"]
    if placement["surface"] != "ceiling":
        raise ValueError("This scenario applies to ceiling findings only.")
    corners, widened = _widen(placement["corners"], placement["center"])
    area = _area(corners)
    bedroom = "bed" in room["kind"].casefold()
    landing_height, landing_label = (0.5, "a bed") if bedroom else (1.2, "a seated person's head")
    finish = analysis.get("ceiling_finish", "unknown")
    calc = ceiling_fall(area, placement["height_above_floor_m"], landing_height, finish)
    calc["landing_label"] = landing_label
    calc["inputs"] = [
        f"Area {calc['area_m2']} m²: the flagged image region projected onto the scanned ceiling"
        + (f", widened to one {MIN_ZONE_SIDE_M} m framing bay" if widened else "") + ".",
        f"Drop {calc['drop_height_m']} m: ceiling to floor, measured in the scan's 3D model"
        + (" (flat-ceiling estimate from the room height)" if placement["approximate"] else "") + ".",
        "Mass per square metre: typical dry dead loads from the ASCE/SEI 7 commentary. Absorbed water adds weight that is not counted here.",
    ]
    calc["finish_known"] = finish in CEILING_FINISHES
    stages = [dict(stage) for stage in STAGES]
    heaviest = max(mass["mass_kg"] for mass in calc["masses"])
    lightest = min(mass["mass_kg"] for mass in calc["masses"])
    mass_text = f"about {heaviest:g} kg" if heaviest == lightest else f"roughly {lightest:g} to {heaviest:g} kg"
    stages[3]["caption"] = (
        f"A section of about {calc['area_m2']} m² lets go: {mass_text} of dry finish falling {calc['drop_height_m']} m "
        f"in {calc['fall_time_s']} s, reaching {landing_label} at {calc['speed_at_landing_m_s']} m/s."
    )
    return {
        "mechanism": MECHANISM,
        "title": "A section of this ceiling could come down",
        "premise": PREMISE,
        "finding_id": finding["id"],
        "room_name": room["name"],
        "why": analysis["why_it_could_fail"],
        "early_warning_signs": analysis["early_warning_signs"],
        "recommended_action": analysis["recommended_action"],
        "who_to_call": analysis["who_to_call"],
        "zone": {
            "corners": corners,
            "center": placement["center"],
            "widened": widened,
            "approximate": placement["approximate"],
            "floor_z": round(placement["center"][2] - placement["height_above_floor_m"], 3),
        },
        "projectors": [_projector(position) for position in positions[:2]],
        "bounds": bounds,
        "calc": calc,
        "repair_cost": repair_costs(area, room.get("area_m2")),
        "stages": stages,
        "disclaimer": "An illustration of a known failure mechanism at the flagged location. It is not a prediction that this will happen, or when.",
    }
