"""What could happen next: a modelled failure scenario for a confirmed weakness.

The agents decide *whether* a confirmed observation fits a mechanism and
explain why in words. Every number and every 3D stage comes from this module,
computed from the scan's own geometry. No model call, same inputs, same result.

Two mechanisms are modelled: a ceiling finish wetted from above that softens,
sags and sheds a section, and a wall finish that stays damp, blisters and comes
away. Each scenario also carries what the repair costs now against later, and a
repair plan with a do-it-yourself and a professional option (`repair_plans`).
"""

from __future__ import annotations

import math

import repair_plans

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
SMALL_PATCH_USD = (60, 200)        # small patch job: stress cracks, retaping and remudding, repainting (Inch Calculator)
COSMETIC_USD = (150, 1000)         # cosmetic water damage (Bob Vila)
CEILING_REPAIR_USD = (350, 1250)   # ceiling repair after water damage (Bob Vila)
DRYWALL_REPAIR_USD = (300, 800)    # drywall repair after water damage (Bob Vila)
AVERAGE_WATER_CLAIM_USD = 15400    # average insured water damage or freezing claim, 2019-2023 (Triple-I)
MECHANISM = "ceiling_moisture_failure"
WALL_MECHANISM = "wall_moisture_damage"
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
        "reference_id": "bobvila_water_damage", "role": "source",
    }
    now_items = [leak, {
        "label": "Cut out and patch the flagged ceiling section", "low": patch[0], "high": patch[1],
        "basis": f"{area_m2:.2f} m² ({section_sqft:.0f} sq ft) at ${PATCH_USD_PER_SQFT[0]} to ${PATCH_USD_PER_SQFT[1]} per sq ft, professional drywall repair.",
        "reference_id": "inchcalculator_drywall", "role": "surface",
    }]
    after_items = [leak, {
        "label": "Close the opening the fallen section leaves", "low": patch[0], "high": patch[1],
        "basis": "Priced at the flagged section's area, as now. A real collapse can take more ceiling with it.",
        "reference_id": "inchcalculator_drywall", "role": "surface",
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
        "Rough ranges from US consumer cost guides, not a quote. Local labour rates, access and the ceiling's finish change the price.",
    ]
    return {
        "currency": "USD",
        "heading": "WHAT THE REPAIR COSTS: NOW, OR AFTER IT FALLS",
        "added_text": ["Waiting until it falls adds ", " to the bill, for the same leak and the same ceiling."],
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


def repair_costs_for(kind: str, surface: str, area_m2: float, room_area_m2: float | None) -> dict:
    """The now-against-later bill for one kind of damage on one surface.

    A loosened ceiling section keeps the original comparison. Other kinds use
    the guide price that names that job, and leave out lines that do not apply:
    a dry crack has no leak to fix and no room to dry.
    """
    base = repair_costs(area_m2, room_area_m2)
    if surface == "ceiling" and kind == "delamination":
        return base
    leak = base["now"]["items"][0]
    drying = [item for item in base["after"]["items"] if item["label"].startswith("Dry out")]
    mould_if_any = base["after"]["items"][-1]
    references = {"bobvila_water_damage"}
    notes: list[str] = []

    def line(label: str, price: tuple[int, int], basis: str, reference: str, role: str) -> dict:
        references.add(reference)
        return {"label": label, "low": price[0], "high": price[1], "basis": basis, "reference_id": reference, "role": role}

    seal = line("Seal the stain and repaint", COSMETIC_USD, "Cosmetic water damage repair, $150 to $1,000.", "bobvila_water_damage", "surface")
    replace_wall = line("Replace the damaged wall section", DRYWALL_REPAIR_USD, "Drywall repair after water damage, $300 to $800.", "bobvila_water_damage", "surface")
    if surface == "ceiling" and kind == "stain":
        now_items, after_items = [leak, seal], list(base["after"]["items"])
        heading, added = base["heading"], base["added_text"]
    elif surface == "ceiling":  # a crack
        now_items = [line("Retape and refinish the crack", SMALL_PATCH_USD, "Small patch job, $60 to $200: stress cracks, retaping and remudding, repainting.", "inchcalculator_drywall", "surface")]
        after_items = [line("Repair the ceiling after a section falls", CEILING_REPAIR_USD, "Ceiling repair, $350 to $1,250.", "bobvila_water_damage", "surface")]
        notes.append("Not priced: finding what is opening the crack. No guide price applies until the cause is known.")
        heading, added = base["heading"], ["Waiting until it falls adds ", " to the bill for the same ceiling."]
    else:
        heading = "WHAT THE REPAIR COSTS: NOW, OR AFTER IT SPREADS"
        added = ["Waiting until it spreads adds ", " to the bill for the same wall."]
        if kind == "mould":
            remove = line("Professional removal, if it is mould", MOULD_REMOVAL_USD, "Mould removal, $1,100 to $3,400. Images cannot confirm mould; a patch this small is within the US EPA's do-it-yourself range.", "bobvila_water_damage", "surface")
            now_items, after_items = [remove], [remove, replace_wall, *drying]
            notes.append("Not priced: fixing what keeps this surface damp, such as ventilation, caulking or a leak. No guide price applies until the cause is known.")
        else:
            patch = base["now"]["items"][1] if kind == "delamination" else seal
            now_items, after_items = [leak, patch], [leak, replace_wall, *drying, mould_if_any]
    references |= {item["reference_id"] for item in now_items + after_items}

    def bill(label: str, items: list[dict]) -> dict:
        return {"label": label, "low": sum(item["low"] for item in items), "high": sum(item["high"] for item in items), "items": items}

    now = bill("Repair now", now_items)
    after = bill("Repair after the section falls" if surface == "ceiling" else "Repair after it spreads", after_items)
    return {
        **base,
        "heading": heading, "added_text": added, "now": now, "after": after,
        "added_low": after["low"] - now["low"], "added_high": after["high"] - now["high"],
        # A dry crack has no room to dry, so that caveat does not belong on its bill.
        "notes": notes + [note for note in base["notes"] if not (surface == "ceiling" and kind == "crack" and "no floor area" in note)],
        "reference_ids": sorted(references),
    }


def _projector(position: dict) -> dict:
    pose = position["pose"]
    return {
        "position_id": position["id"],
        "position": [pose["position"][k] for k in "xyz"],
        "quaternion": [pose["rotation"][k] for k in "xyzw"],
        "faces": [f"{position['id']}-{name}" for name in ("ceiling", "wall-1", "wall-2", "wall-3", "wall-4", "floor")],
    }


CRACK_PREMISE = "If whatever is opening this crack is not found and fixed"
STAIN_PREMISE = "If this stain is water arriving from above, and its source is not found and fixed"
# A crack is a dry scenario: nothing in it assumes water, and the 3D view draws none.
CRACK_STAGES = {
    1: ("The crack spreads", "The crack lengthens and branches as whatever opened it keeps working."),
    2: ("Sag", "The finish beside the crack loosens from the framing and hangs lower. The crack widens around it."),
    4: ("Aftermath", "Framing is exposed to the room and debris covers the floor below."),
}
WALL_PREMISE = "If this wall keeps getting wet"
WALL_STAGES = [
    {"id": "as_scanned", "title": "As scanned", "caption": "The wall as the scan recorded it. The flagged region is outlined on the real surface."},
    {"id": "saturation", "title": "Damp spreads", "caption": "Moisture keeps arriving. The darkened area widens across the wall."},
    {"id": "sag", "title": "Finish blisters", "caption": "The wetted finish softens and lifts away from the wall behind it. Cracks open around the blister."},
    {"id": "detach", "title": "Finish sheds", "caption": "The loosened finish comes away and drops to the floor."},
    {"id": "aftermath", "title": "Aftermath", "caption": "The wall's backing is exposed, debris lies at its foot, and damp surfaces around the opening stay prone to mould."},
]


def fits(finding: dict) -> bool:
    """Whether a confirmed finding sits where a modelled mechanism applies."""
    placement = finding.get("placement")
    if not placement:
        return False
    if placement["surface"] == "ceiling":
        return finding["category"] in ("moisture", "structure")
    return placement["surface"] == "wall" and finding["category"] == "moisture"


def _mass_text(masses: list[dict]) -> str:
    heaviest = max(mass["mass_kg"] for mass in masses)
    lightest = min(mass["mass_kg"] for mass in masses)
    return f"about {heaviest:g} kg" if heaviest == lightest else f"roughly {lightest:g} to {heaviest:g} kg"


def _tile(value: float, unit: str, label: str, digits: int = 1) -> dict:
    return {"value": round(value, digits), "unit": unit, "label": label, "digits": digits}


def _assemble(finding: dict, room: dict, positions: list[dict], bounds: dict | None, analysis: dict, *,
              mechanism: str, surface: str, kind: str, title: str, premise: str, zone: dict, calc: dict,
              stages: list[dict], region: str | None, written_by_analyst: bool) -> dict:
    cost = repair_costs_for(kind, surface, zone["area_m2"], room.get("area_m2"))
    return {
        "mechanism": mechanism,
        "surface": surface,
        "kind": kind,
        "title": title,
        "premise": premise,
        "finding_id": finding["id"],
        "room_name": room["name"],
        "why": analysis["why_it_could_fail"],
        "early_warning_signs": analysis["early_warning_signs"],
        "recommended_action": analysis["recommended_action"],
        "who_to_call": analysis["who_to_call"],
        # The failure analyst writes up one finding. The rest use this module's
        # description of the mechanism, and the page says which is which.
        "written_by_analyst": written_by_analyst,
        # A dry scenario plays without staining, drips or mould.
        "dry": kind == "crack",
        "zone": {key: value for key, value in zone.items() if key != "area_m2"},
        "projectors": [_projector(position) for position in positions[:2]],
        "bounds": bounds,
        "calc": calc,
        "repair_cost": cost,
        "repair_plan": repair_plans.build_plan(kind, surface, zone["area_m2"], cost, region),
        "stages": stages,
        "disclaimer": "An illustration of a known failure mechanism at the flagged location. It is not a prediction that this will happen, or when.",
    }


def build_ceiling_scenario(finding: dict, room: dict, positions: list[dict], bounds: dict | None, analysis: dict,
                           kind: str = "delamination", region: str | None = None, written_by_analyst: bool = True) -> dict:
    """Assemble the scenario the 3D view plays, from a confirmed ceiling finding.

    `finding["placement"]` is the flagged region on the real ceiling.
    `positions` are scan positions in the room, the finding's own first; their
    panoramas are projected onto the mesh so the 3D room shows real surfaces.
    `analysis` carries the words and the finish judgement for this finding.
    """
    placement = finding["placement"]
    if placement["surface"] != "ceiling":
        raise ValueError("This scenario applies to ceiling findings only.")
    corners, widened = _widen_sides(placement["corners"], placement["center"])
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
    calc["tiles"] = [
        _tile(calc["area_m2"], " m²", "section that could detach", 2),
        _tile(calc["drop_height_m"], " m", "ceiling to floor", 2),
        _tile(calc["fall_time_s"], " s", "time to reach the floor", 2),
        _tile(calc["speed_at_landing_m_s"], " m/s", f"speed reaching {landing_label}"),
        *[_tile(mass["mass_kg"], " kg", f"dry weight if {mass['label']}") for mass in calc["masses"]],
    ]
    stages = [dict(stage) for stage in STAGES]
    if kind == "crack":
        for index, (stage_title, caption) in CRACK_STAGES.items():
            stages[index].update(title=stage_title, caption=caption)
    stages[3]["caption"] = (
        f"A section of about {calc['area_m2']} m² lets go: {_mass_text(calc['masses'])} of dry finish falling {calc['drop_height_m']} m "
        f"in {calc['fall_time_s']} s, reaching {landing_label} at {calc['speed_at_landing_m_s']} m/s."
    )
    zone = {
        "surface": "ceiling",
        "corners": corners,
        "center": placement["center"],
        "normal": placement.get("normal", [0.0, 0.0, -1.0]),
        "widened": widened,
        "approximate": placement["approximate"],
        "floor_z": round(placement["center"][2] - placement["height_above_floor_m"], 3),
        "area_m2": area,
    }
    return _assemble(
        finding, room, positions, bounds, analysis, mechanism=MECHANISM, surface="ceiling", kind=kind,
        title="A section of this ceiling could come down", premise={"crack": CRACK_PREMISE, "stain": STAIN_PREMISE}.get(kind, PREMISE),
        zone=zone, calc=calc, stages=stages, region=region, written_by_analyst=written_by_analyst,
    )


def _widen_sides(corners: list[list[float]], centre: list[float]) -> tuple[list[list[float]], bool]:
    """Stretch a quad's short sides to one stud bay, leaving its long sides alone.

    A thin streak down a wall would otherwise scale into a region far larger
    than anything that was flagged.
    """
    def unit(a, b):
        d = [b[k] - a[k] for k in range(3)]
        length = math.sqrt(sum(c * c for c in d))
        return ([c / length for c in d], length) if length > 1e-6 else (None, 0.0)

    (u, width), (v, height) = unit(corners[0], corners[1]), unit(corners[0], corners[3])
    if u is None or v is None or (width >= MIN_ZONE_SIDE_M and height >= MIN_ZONE_SIDE_M):
        return corners, False
    grow_u, grow_v = max(MIN_ZONE_SIDE_M / width, 1.0) - 1.0, max(MIN_ZONE_SIDE_M / height, 1.0) - 1.0
    widened = []
    for corner in corners:
        offset = [corner[k] - centre[k] for k in range(3)]
        along_u, along_v = sum(offset[k] * u[k] for k in range(3)), sum(offset[k] * v[k] for k in range(3))
        widened.append([round(corner[k] + u[k] * along_u * grow_u + v[k] * along_v * grow_v, 3) for k in range(3)])
    return widened, True


def build_wall_scenario(finding: dict, room: dict, positions: list[dict], bounds: dict | None, analysis: dict,
                        kind: str = "stain", region: str | None = None, written_by_analyst: bool = False) -> dict:
    """The same five stages for a damp wall: the damp spreads, the finish blisters and sheds."""
    placement = finding["placement"]
    if placement["surface"] != "wall":
        raise ValueError("This scenario applies to wall findings only.")
    corners, widened = _widen_sides(placement["corners"], placement["center"])
    flagged_area = _area(placement["corners"])
    drawn_area = _area(corners)
    if not math.isfinite(drawn_area) or drawn_area <= 0:
        raise ValueError("A wall scenario needs a region with a positive area.")
    floor_z = placement["center"][2] - placement["height_above_floor_m"]
    top = max(corner[2] for corner in corners) - floor_z
    bottom = max(min(corner[2] for corner in corners) - floor_z, 0.0)
    masses = []
    for key, spec in CEILING_FINISHES.items():
        masses.append({"finish": key, "label": spec["label"], "kg_per_m2": round(spec["psf"] * PSF_TO_KG_M2, 1), "mass_kg": round(drawn_area * spec["psf"] * PSF_TO_KG_M2, 1)})
    sqft = flagged_area * SQFT_PER_M2
    calc = {
        "area_m2": round(flagged_area, 2),
        "drawn_area_m2": round(drawn_area, 2),
        "area_sqft": round(sqft, 1),
        "top_height_m": round(top, 2),
        "bottom_height_m": round(bottom, 2),
        "masses": masses,
        "finish_known": False,
        "tiles": [
            _tile(flagged_area, " m²", "wall inside the flagged region", 2),
            *([_tile(sqft, " sq ft", f"if it is mould: against the US EPA's do-it-yourself limit of about {repair_plans.EPA_DIY_MOULD_SQFT} sq ft")] if kind == "mould" else []),
            _tile(top, " m", "top of the region above the floor", 2),
            *[_tile(mass["mass_kg"], " kg", f"dry weight of the drawn section if {mass['label']}") for mass in masses],
        ],
        "inputs": [
            f"Area {flagged_area:.2f} m²: the flagged image region projected onto the scanned wall"
            + (f". The 3D view draws it {drawn_area:.2f} m², one {MIN_ZONE_SIDE_M} m stud bay wide, so it can be seen" if widened else "") + ".",
            f"Heights are measured from the floor in the scan's 3D model: the region runs from {bottom:.2f} m to {top:.2f} m.",
            "Mass per square metre: typical dry dead loads from the ASCE/SEI 7 commentary. Absorbed water adds weight that is not counted here.",
        ],
    }
    stages = [dict(stage) for stage in WALL_STAGES]
    stages[3]["caption"] = f"Finish over about {drawn_area:.2f} m² comes away from the wall and drops from up to {top:.2f} m: {_mass_text(masses)} of dry finish."
    zone = {
        "surface": "wall",
        "corners": corners,
        "center": placement["center"],
        "normal": placement["normal"],
        "widened": widened,
        "approximate": placement["approximate"],
        "floor_z": round(floor_z, 3),
        "area_m2": flagged_area,
    }
    return _assemble(
        finding, room, positions, bounds, analysis, mechanism=WALL_MECHANISM, surface="wall", kind=kind,
        title="This wall's finish could blister and come away" if kind != "mould" else "This damp could spread and take the wall finish with it",
        premise=WALL_PREMISE, zone=zone, calc=calc, stages=stages, region=region, written_by_analyst=written_by_analyst,
    )


def build_scenario(finding: dict, room: dict, positions: list[dict], bounds: dict | None,
                   analysis: dict | None = None, region: str | None = None) -> dict:
    """The scenario for any confirmed finding that `fits`.

    With `analysis` the words are the failure analyst's. Without it they are
    this project's description of the mechanism for that kind of damage.
    """
    if not fits(finding):
        raise ValueError("No modelled mechanism applies to this finding.")
    surface = finding["placement"]["surface"]
    kind = repair_plans.damage_kind(finding)
    if surface == "wall" and kind == "crack":
        kind = "stain"
    words = analysis or {"ceiling_finish": "unknown", **repair_plans.wording(surface, kind, finding.get("confidence", "medium"))}
    builder = build_ceiling_scenario if surface == "ceiling" else build_wall_scenario
    return builder(finding, room, positions, bounds, words, kind=kind, region=region, written_by_analyst=analysis is not None)
