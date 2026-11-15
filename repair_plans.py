"""How a confirmed weakness gets fixed: do it yourself, or hire it out.

Every confirmed finding that fits a modelled mechanism gets a repair plan with
two options side by side. The professional figures are the published guide
prices already used for the repair-cost comparison. The do-it-yourself figures
are approximate retail prices for the materials, each with a link to the
retailer.

Nothing here comes from a model. The plan is chosen by the kind of damage
(`damage_kind`), which is read from the finding's own words, and the surface
it sits on. Do-it-yourself work is only ever offered for the visible surface.
Finding and fixing a leak, and anything electrical or structural, stays with a
professional.
"""

from __future__ import annotations

import math
from urllib.parse import quote

SQFT_PER_M2 = 10.764
PANEL_M2 = 0.36  # one 2 ft x 2 ft patch panel
EPA_DIY_MOULD_SQFT = 10  # US EPA: under about 10 sq ft, "in most cases, you can handle the job yourself"
RETAILER = "The Home Depot"
PRICES_CHECKED = "November 2026"

# Approximate prices, as shown for these items in web-search listings of the
# retailer's pages in November 2026. The retailer blocks automated requests, so
# no price was read from a live page, and listings disagreed by a dollar or two.
# A "product" link opens that item; a "category" link opens the shelf it sits on.
_HD = "https://www.homedepot.com"
CATALOG = {
    "respirator": {
        "name": "3M N95 sanding respirator, 2-pack", "price_usd": 7.98, "link_kind": "product",
        "url": f"{_HD}/p/3M-N95-Paint-Sanding-Respirator-Mask-2-Pack-8210PP2-DC/100559558",
    },
    "patch_panel": {
        "name": "USG Sheetrock 1/2 in. x 2 ft. x 2 ft. drywall patch panel", "price_usd": 7.85, "link_kind": "category",
        "url": f"{_HD}/b/Paint-Paint-Supplies-Patching-Repair-Drywall-Patches/Drywall-Repair-Panels/N-5yc1vZ2fkpczrZ1z18jvo",
    },
    "compound": {
        "name": "USG Sheetrock All Purpose joint compound, 3.5 qt", "price_usd": 12.34, "link_kind": "category",
        "url": f"{_HD}/b/Building-Materials-Drywall-Joint-Compound/USG-Sheetrock-Brand/N-5yc1vZard1Zomd",
    },
    "mesh_tape": {
        "name": "FibaTape self-adhesive mesh joint tape, 1-7/8 in. x 150 ft.", "price_usd": 6.98, "link_kind": "category",
        "url": f"{_HD}/b/Building-Materials-Drywall/Fiberglass-Mesh/N-5yc1vZar3dZ1z0qvc2",
    },
    "primer": {
        "name": "KILZ Original stain-blocking primer, 1 qt", "price_usd": 16.98, "link_kind": "product",
        "url": f"{_HD}/p/KILZ-Original-1-qt-White-Oil-Based-Interior-Primer-Sealer-and-Stain-Blocker-10902/100371322",
    },
    "ceiling_paint": {
        "name": "BEHR Premium Plus flat ceiling paint, 1 qt", "price_usd": 16.48, "link_kind": "product",
        "url": f"{_HD}/p/BEHR-PREMIUM-PLUS-1-qt-Ultra-Pure-White-Ceiling-Flat-Interior-Paint-55804/100178852",
    },
    "mould_cleaner": {
        "name": "Concrobium Mold Control spray, 32 oz", "price_usd": 13.97, "link_kind": "product",
        "url": f"{_HD}/p/Concrobium-32-oz-Mold-Control-25326CAL/310228466",
    },
}


def _search(name: str, term: str) -> dict:
    """Something most households own or can match in store: a search link, no price."""
    return {"name": name, "price_usd": None, "link_kind": "search", "url": f"{_HD}/s/{quote(term)}"}


TOOLS = {
    "taping_knife": _search("6 in. drywall taping knife", "6 in drywall taping knife"),
    "sanding_sponge": _search("Fine sanding sponge", "fine sanding sponge"),
    "drywall_saw": _search("Drywall jab saw", "drywall jab saw"),
    "screws": _search("1-1/4 in. drywall screws", "1-1/4 in drywall screws"),
    "roller": _search("Mini paint roller and tray", "mini paint roller tray kit"),
    "goggles": _search("Safety goggles without vent holes", "non vented safety goggles"),
    "gloves": _search("Long rubber gloves", "long rubber cleaning gloves"),
    "wall_paint": _search("Interior paint matched to the wall, 1 qt", "interior paint quart"),
    "scrub_brush": _search("Scrub brush and household detergent", "scrub brush"),
    "moisture_meter": _search("Pin moisture meter", "pin moisture meter"),
}

# Directories and public licence look-ups, by collection region. Each link was
# opened and returned a page in November 2026. No individual firm is named.
_TT = "https://www.thumbtack.com"
PRO_LINKS = {
    "los_angeles": {
        "place": "Los Angeles",
        "trades": {
            "drywall": f"{_TT}/ca/los-angeles/drywall-repair/",
            "mould": f"{_TT}/ca/los-angeles/mold-removal/",
            "roofer": f"{_TT}/ca/los-angeles/roof-repair/",
            "plumber": f"{_TT}/ca/los-angeles/plumbers/",
            "painter": f"{_TT}/ca/los-angeles/interior-painting/",
            "handyman": f"{_TT}/ca/los-angeles/handyman/",
        },
        "licence": {
            "label": "Check a contractor's licence · California CSLB",
            "url": "https://www.cslb.ca.gov/OnlineServices/CheckLicenseII/CheckLicense.aspx",
        },
    },
    "houston": {
        "place": "Houston",
        "trades": {
            "drywall": f"{_TT}/tx/houston/drywall-repair/",
            "mould": f"{_TT}/tx/houston/mold-removal/",
            "roofer": f"{_TT}/tx/houston/roof-repair/",
            "plumber": f"{_TT}/tx/houston/plumbers/",
            "painter": f"{_TT}/tx/houston/interior-painting/",
            "handyman": f"{_TT}/tx/houston/handyman/",
        },
        "licence": {
            "label": "Search state licences · Texas TDLR",
            "url": "https://www.tdlr.texas.gov/LicenseSearch/",
        },
    },
}
TRADE_LABELS = {
    "drywall": "Drywall repair", "mould": "Mould removal", "roofer": "Roof repair",
    "plumber": "Plumbers", "painter": "Interior painters", "handyman": "Handyman services",
}
SAFETY_LINKS = [
    {"label": "US EPA · Lead in older homes", "url": "https://www.epa.gov/lead/protect-your-family-sources-lead"},
    {"label": "US EPA · Asbestos in the home", "url": "https://www.epa.gov/asbestos/protect-your-family-exposures-asbestos"},
    {"label": "US EPA · Mold cleanup in your home", "url": "https://www.epa.gov/mold/mold-cleanup-your-home"},
]
OLD_FINISH_CAUTION = (
    "The scan does not say when this home was built. If it predates 1978 the paint may contain lead, and older "
    "plaster or textured finishes can contain asbestos. Have the finish tested before you cut, scrape or sand it."
)

KINDS = ("delamination", "stain", "crack", "mould")
_KEYWORDS = (
    ("crack", ("crack",)),
    ("mould", ("mould", "mold", "mildew", "dark streak", "dark spot", "spotting")),
    ("stain", ("stain", "drip", "trail", "discolo", "tide mark")),
    ("delamination", ("bulg", "rippl", "sag", "blister", "peel", "lift", "raised", "lump")),
)


def damage_kind(finding: dict) -> str:
    """Which repair a finding calls for, read from its title, then its observation."""
    for text in (finding.get("title", ""), finding.get("observation", "")):
        lowered = text.casefold()
        for kind, words in _KEYWORDS:
            if any(word in lowered for word in words):
                return kind
    return "crack" if finding.get("category") == "structure" else "delamination"


# What the 3D view plays for the repair: the scan as found, then three steps.
REPAIR_STAGES = {
    "delamination": [
        ("As scanned", "The flagged section as the scan recorded it."),
        ("Cut out the damaged section", "The loose finish is cut back to sound material, squared off between framing members. This is the moment to look into the cavity for damp."),
        ("Fit a new board", "A new piece of board is cut to the opening and screwed to the framing."),
        ("Tape, prime and paint", "Joints are taped and skimmed with compound, sanded, sealed with primer and painted to match."),
    ],
    "crack": [
        ("As scanned", "The crack as the scan recorded it."),
        ("Open and tape the crack", "Loose material is raked out and the crack is bridged with mesh tape."),
        ("Skim with compound", "Two or three thin coats of joint compound, each wider than the last, feathered into the surface."),
        ("Prime and paint", "The dry compound is sanded, primed and painted to match."),
    ],
    "stain": [
        ("As scanned", "The stained area as the scan recorded it."),
        ("Confirm it is dry", "Nothing is sealed until the source is fixed and the surface is dry. A moisture meter settles it."),
        ("Seal with stain-blocking primer", "A stain-blocking primer stops the mark bleeding back through new paint."),
        ("Repaint", "The sealed area is painted to match the surface around it."),
    ],
    "mould": [
        ("As scanned", "The darkened area as the scan recorded it."),
        ("Scrub and dry", "The surface is scrubbed with detergent and water and dried completely, as the US EPA advises for a small area of mould."),
        ("Seal with primer", "Only once it is clean and dry: a stain-blocking primer over the cleaned area."),
        ("Repaint", "The primed area is painted to match, and the room is kept ventilated."),
    ],
}

_DIY = {
    "delamination": {
        "difficulty": "Advanced", "time": "A weekend, plus drying time between coats",
        "materials": ["respirator", "patch_panel", "compound", "mesh_tape", "primer"],
        "tools": ["drywall_saw", "screws", "taping_knife", "sanding_sponge", "roller"],
        "steps": [
            "Fix the moisture source first, or the new patch will fail the same way.",
            "Cut the loose finish back to sound material, squaring the opening so its edges land on framing.",
            "Let the cavity dry, then screw a new board into the opening.",
            "Tape the joints and apply two or three thin coats of compound, sanding lightly between coats.",
            "Seal with stain-blocking primer, then paint to match.",
        ],
    },
    "crack": {
        "difficulty": "Beginner", "time": "An afternoon, plus drying time between coats",
        "materials": ["respirator", "mesh_tape", "compound", "primer"],
        "tools": ["taping_knife", "sanding_sponge", "roller"],
        "steps": [
            "Mark both ends of the crack with a pencil and date them, so you can tell if it grows.",
            "Rake out loose material and bridge the crack with mesh tape.",
            "Apply two or three thin coats of compound, each wider than the last, sanding lightly between coats.",
            "Prime and paint to match.",
        ],
    },
    "stain": {
        "difficulty": "Beginner", "time": "An afternoon, once the surface is dry",
        "materials": ["primer"],
        "tools": ["moisture_meter", "roller"],
        "steps": [
            "Fix the moisture source first. Sealing over an active leak hides the warning, not the problem.",
            "Confirm the surface is dry. Press it gently: soft, crumbling or sagging finish means this is no longer a paint job.",
            "Apply stain-blocking primer over the stain and a margin around it.",
            "Paint to match once the primer is dry.",
        ],
    },
    "mould": {
        "difficulty": "Beginner", "time": "An hour of work, then a day to dry",
        "materials": ["respirator", "mould_cleaner", "primer"],
        "tools": ["goggles", "gloves", "scrub_brush", "roller"],
        "steps": [
            "Wear an N95 respirator, goggles and long gloves, and ventilate the room.",
            "Scrub the area with detergent and water, or a mould cleaner, and dry it completely.",
            "Do not paint or caulk over mould: paint applied over a mouldy surface is likely to peel.",
            "Once clean and dry, seal with primer and repaint.",
            "Run the fan or open a window when showering, so the corner dries out between uses.",
        ],
    },
}

_PRO_TRADES = {
    "delamination": ["roofer", "plumber", "drywall"],
    "stain": ["roofer", "plumber", "painter"],
    "crack": ["drywall", "handyman"],
    "mould": ["mould", "drywall"],
}

# Words for findings the failure analyst did not write up. They state the
# mechanism conditionally and never claim a cause.
_WORDING = {
    ("ceiling", "delamination"): {
        "why": "The finish in this region is raised or loosened. If water is reaching it from above, the wetted finish could keep softening and lose its grip on the framing, and a section that has lost its hold could come away.",
        "signs": ["The raised area getting larger or hanging lower", "Brown stains or rings appearing", "New cracks around the edge of the area"],
        "action": "Have the space above this ceiling checked for an active leak before any repair.",
        "who": "A licensed roofer or plumber to trace the water source, then a drywall or plaster contractor for the repair.",
    },
    ("ceiling", "stain"): {
        "why": "A stain like this can mark water arriving from above. If it is water and it keeps arriving, the finish under the stain could soften, sag and eventually let go. If it is surface residue instead, none of this applies, which is why the first step is to find out.",
        "signs": ["The stain growing, or its edge darkening after rain or after water is used above", "The stained area feeling soft or damp to the touch", "The finish starting to sag or blister"],
        "action": "Press the stained area gently and check it with a moisture meter. A damp or soft reading means a leak to trace before any repainting.",
        "who": "A licensed roofer or plumber if it is damp. A painter, or you, if it is dry and only stained.",
    },
    ("ceiling", "crack"): {
        "why": "A crack that is wider than a hairline shows the finish has already parted along that line. If whatever opened it keeps working, or if water finds its way into it, the finish beside the crack could loosen and a section could come away.",
        "signs": ["The crack getting longer or wider", "One side of the crack sitting lower than the other", "Staining or dampness appearing along it"],
        "action": "Mark and date both ends of the crack, and have the framing above checked if it grows or one side drops.",
        "who": "A drywall or plaster contractor for the repair. A structural engineer if the crack keeps growing.",
    },
    ("wall", "mould"): {
        "why": "Dark streaking beside a shower is consistent with a surface that stays damp. Images cannot confirm what it is. If it is mould and the surface stays damp, it could spread across the wall, and the finish behind it could soften, blister and come away from the wall.",
        "signs": ["The dark area spreading or returning after cleaning", "Paint blistering or peeling nearby", "A musty smell that lingers in the room"],
        "action": "Clean and dry the area, then watch whether it returns. If it does, the wall is being wetted from somewhere a wipe cannot reach.",
        "who": "You, for a patch this size, following the US EPA guidance. A mould remediation contractor if it covers more than about 10 square feet or keeps coming back.",
    },
    ("wall", "stain"): {
        "why": "Trails like these can be left by water running down the wall from above. If water is still running, the wall finish could soften and blister where it runs, and mould could take hold on the damp surface.",
        "signs": ["Fresh or darker trails after rain", "The wall feeling damp or soft along the trails", "Paint blistering or peeling, or dark spotting appearing"],
        "action": "Check the wall and the ceiling above it with a moisture meter, and look at the same spot after the next rain.",
        "who": "A licensed roofer or plumber to trace the water if it is damp, then a painter or drywall contractor for the surface.",
    },
    ("wall", "delamination"): {
        "why": "The wall finish in this region is raised or loosened. If moisture is behind it, the finish could keep softening and come away from the wall, and mould could take hold on the damp surface.",
        "signs": ["The raised area growing", "Paint blistering or peeling nearby", "Dampness or a musty smell"],
        "action": "Have the wall checked for moisture behind the finish before any repair.",
        "who": "A licensed plumber or roofer to trace the moisture, then a drywall or plaster contractor for the repair.",
    },
}
LOW_CONFIDENCE_NOTE = " The skeptic confirmed this at low confidence, so read what follows as a reason to check, not as a diagnosis."


def wording(surface: str, kind: str, confidence: str = "medium") -> dict:
    """Analysis text for a finding the failure analyst did not write up."""
    words = _WORDING.get((surface, kind)) or _WORDING[(surface, "delamination")]
    return {
        "why_it_could_fail": words["why"] + (LOW_CONFIDENCE_NOTE if confidence == "low" else ""),
        "early_warning_signs": list(words["signs"]),
        "recommended_action": words["action"],
        "who_to_call": words["who"],
    }


def _line(key: str, quantity: int = 1) -> dict:
    item = dict(CATALOG.get(key) or TOOLS[key])
    item["quantity"] = quantity
    item["retailer"] = RETAILER
    item["total_usd"] = round(item["price_usd"] * quantity, 2) if item["price_usd"] is not None else None
    return item


def build_plan(kind: str, surface: str, area_m2: float, cost: dict, region: str | None) -> dict:
    """Both ways to fix one finding, with the same job priced on each side.

    `cost` is the scenario's repair-cost comparison. Its "repair now" bill is
    the professional option. Do-it-yourself replaces only the lines marked as
    surface work; a moisture source still has to be found and fixed by a
    professional, and stays on both bills.
    """
    if kind not in KINDS or surface not in ("ceiling", "wall") or not math.isfinite(area_m2) or area_m2 <= 0:
        raise ValueError("A repair plan needs a known damage kind, surface and a positive area.")
    spec = _DIY[kind]
    area_sqft = area_m2 * SQFT_PER_M2
    materials = []
    for key in spec["materials"]:
        quantity = max(math.ceil(area_m2 / PANEL_M2), 1) if key == "patch_panel" else 1
        materials.append(_line(key, quantity))
    materials.append(_line("ceiling_paint") if surface == "ceiling" else _line("wall_paint"))
    tools = [_line(key) for key in spec["tools"]]
    priced = [item for item in materials if item["total_usd"] is not None]
    materials_total = round(sum(item["total_usd"] for item in priced), 2)
    unpriced = [item["name"] for item in materials if item["total_usd"] is None]

    now = cost["now"]
    source = [item for item in now["items"] if item.get("role") == "source"]
    surface_items = [item for item in now["items"] if item.get("role") != "source"]
    source_low, source_high = sum(i["low"] for i in source), sum(i["high"] for i in source)

    cautions = [OLD_FINISH_CAUTION]
    if surface == "ceiling":
        cautions.append("This is overhead work from a ladder. Wear eye protection, and get help lifting board into place.")
    if kind == "mould":
        cautions.insert(0, "Dark streaking is not proof of mould, and images cannot confirm it. These steps follow the US EPA's guidance for cleaning a small area that may be mould.")
        cautions.append(f"US EPA guidance treats mould under about {EPA_DIY_MOULD_SQFT} square feet as a job most people can handle. This region is {area_sqft:.1f} sq ft. Beyond that, or if it follows a lot of water damage, hire a remediation contractor.")
    if source:
        cautions.append("Do-it-yourself covers the visible surface only. Finding and fixing the moisture source is a professional's job, and is priced on both sides.")
    if kind == "crack":
        cautions.append("Filling a crack hides it. If it reopens or one side drops, stop patching and have the framing looked at.")
    oversized = kind == "delamination" and area_sqft > 8
    if oversized:
        cautions.append(f"At {area_sqft:.0f} sq ft this is a large patch. Most people hire a section this size out.")

    links = PRO_LINKS.get(region or "")
    professional_links = []
    if links:
        for trade in _PRO_TRADES[kind]:
            professional_links.append({"label": f"{TRADE_LABELS[trade]} in {links['place']} · Thumbtack", "url": links["trades"][trade]})
        professional_links.append(dict(links["licence"]))

    return {
        "kind": kind,
        "cut_out": kind == "delamination",
        "stages": [{"title": title, "caption": caption} for title, caption in REPAIR_STAGES[kind]],
        "diy": {
            "label": "Fix it yourself",
            "difficulty": "Advanced" if oversized else spec["difficulty"],
            "time": spec["time"],
            "materials": materials,
            "tools": tools,
            "materials_total_usd": materials_total,
            "unpriced": unpriced,
            "low": round(source_low + materials_total),
            "high": round(source_high + materials_total),
            "steps": list(spec["steps"]),
            "cautions": cautions,
            "prices_checked": PRICES_CHECKED,
            "price_note": (
                f"Approximate prices, as shown in search listings for {RETAILER} in {PRICES_CHECKED}. They vary by store and date; each link shows today's price."
                + (f" Not in the total: {', '.join(name[0].lower() + name[1:] for name in unpriced)}." if unpriced else "")
            ),
        },
        "professional": {
            "label": "Hire a professional",
            "low": now["low"],
            "high": now["high"],
            "items": now["items"],
            "links": professional_links,
            "link_note": "Directory and licence look-up links, not endorsements of any firm. Get more than one quote.",
        },
        "surface_work": {
            "diy_usd": materials_total,
            "professional_low": sum(i["low"] for i in surface_items),
            "professional_high": sum(i["high"] for i in surface_items),
        },
        "source_work": {"low": source_low, "high": source_high} if source else None,
        "safety_links": [dict(link) for link in SAFETY_LINKS],
    }
