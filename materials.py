"""Reference material properties for common robotics materials. Pure Python, no
model calls.

Every value comes from knowledge/materials.csv, where each row names its source
and a review status. Values that vary by grade or species are stored as ranges
("50-60") and stay ranges through every calculation: nothing here claims more
precision than the source gives.
"""

import csv
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MATERIALS_PATH = Path(__file__).resolve().parent / "knowledge" / "materials.csv"
FAMILIES = ("printed_plastic", "plastic_sheet", "metal", "wood")
STATUSES = ("draft", "reviewed")
RATIO_DECIMALS = 2

Range = tuple[float, float]


class MaterialError(ValueError):
    """A problem with the materials table or a material id."""


@dataclass(frozen=True)
class Material:
    id: str
    name: str
    family: str
    strength_mpa: Range
    strength_basis: str
    modulus_gpa: Range
    elongation_pct: Range | None
    density_g_cm3: Range
    note: str
    source: str
    status: str


def parse_range(text: str, field: str, row_id: str) -> Range:
    """'50-60' -> (50.0, 60.0); '1.24' -> (1.24, 1.24). Positive and ordered."""
    parts = [part.strip() for part in text.split("-")] if text else []
    if not 1 <= len(parts) <= 2 or not all(parts):
        raise MaterialError(f"{row_id}: {field} must be a number or a range like 50-60, got {text!r}")
    try:
        values = [float(part) for part in parts]
    except ValueError as exc:
        raise MaterialError(f"{row_id}: {field} is not a number: {text!r}") from exc
    low, high = values[0], values[-1]
    if not all(math.isfinite(v) and v > 0 for v in (low, high)):
        raise MaterialError(f"{row_id}: {field} must be positive, got {text!r}")
    if low > high:
        raise MaterialError(f"{row_id}: {field} range is backwards: {text!r}")
    return low, high


def _material(row: dict[str, str]) -> Material:
    row_id = (row.get("id") or "").strip()
    if not re.fullmatch(r"[a-z0-9_]+", row_id):
        raise MaterialError(f"Material id must be a lowercase slug, got {row_id!r}")
    for field in ("name", "source", "strength_basis"):
        if not (row.get(field) or "").strip():
            raise MaterialError(f"{row_id}: {field} is missing")
    family = row["family"].strip()
    if family not in FAMILIES:
        raise MaterialError(f"{row_id}: family must be one of {FAMILIES}, got {family!r}")
    status = row["status"].strip()
    if status not in STATUSES:
        raise MaterialError(f"{row_id}: status must be one of {STATUSES}, got {status!r}")
    elongation = row.get("elongation_pct", "").strip()
    return Material(
        id=row_id,
        name=row["name"].strip(),
        family=family,
        strength_mpa=parse_range(row["strength_mpa"].strip(), "strength_mpa", row_id),
        strength_basis=row["strength_basis"].strip(),
        modulus_gpa=parse_range(row["modulus_gpa"].strip(), "modulus_gpa", row_id),
        elongation_pct=parse_range(elongation, "elongation_pct", row_id) if elongation else None,
        density_g_cm3=parse_range(row["density_g_cm3"].strip(), "density_g_cm3", row_id),
        note=(row.get("note") or "").strip(),
        source=row["source"].strip(),
        status=status,
    )


def load_materials(path: Path = MATERIALS_PATH) -> dict[str, Material]:
    """All materials by id. A bad row stops the load with a message naming it."""
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    materials: dict[str, Material] = {}
    for row in rows:
        material = _material(row)
        if material.id in materials:
            raise MaterialError(f"Duplicate material id {material.id!r}")
        materials[material.id] = material
    if not materials:
        raise MaterialError(f"{path.name} has no rows")
    return materials


@lru_cache(maxsize=1)
def _table() -> dict[str, Material]:
    return load_materials()


def ids() -> list[str]:
    return list(_table())


def get(material_id: str) -> Material:
    try:
        return _table()[material_id]
    except KeyError as exc:
        raise MaterialError(
            f"Unknown material {material_id!r}. Known: {', '.join(_table())}."
        ) from exc


def facts(material: Material) -> dict:
    """JSON-ready reference data for one material."""
    return {
        "id": material.id,
        "name": material.name,
        "family": material.family,
        "strength_mpa": list(material.strength_mpa),
        "strength_basis": material.strength_basis,
        "modulus_gpa": list(material.modulus_gpa),
        "elongation_pct": list(material.elongation_pct) if material.elongation_pct else None,
        "density_g_cm3": list(material.density_g_cm3),
        "note": material.note,
        "source": material.source,
        "status": material.status,
    }


def ratio_range(new: Range, old: Range) -> list[float]:
    """Smallest and largest possible new/old ratio given both ranges."""
    return [round(new[0] / old[1], RATIO_DECIMALS), round(new[1] / old[0], RATIO_DECIMALS)]


def compare(from_id: str, to_id: str) -> dict:
    """How a change of material changes strength, stiffness and weight.

    Ratios are ranges because the inputs are. If the two strengths were measured
    differently (tensile for plastics and metals, bending for wood) the strength
    ratio is not like-for-like and is flagged.
    """
    old, new = get(from_id), get(to_id)
    like_for_like = old.strength_basis == new.strength_basis
    basis = (
        "Ratios of reference values for the two materials, same shape and load. "
        "Ranges come from the spread of values the sources give."
    )
    if not like_for_like:
        basis += (
            f" Strength is not like-for-like: {old.name} is quoted as "
            f"{old.strength_basis}, {new.name} as {new.strength_basis}."
        )
    if "printed_plastic" in (old.family, new.family):
        basis += " Printed parts are weaker than these moulded-sample values, most of all across layers."
    return {
        "from": facts(old),
        "to": facts(new),
        "strength_ratio": ratio_range(new.strength_mpa, old.strength_mpa),
        "strength_like_for_like": like_for_like,
        "stiffness_ratio": ratio_range(new.modulus_gpa, old.modulus_gpa),
        "weight_ratio": ratio_range(new.density_g_cm3, old.density_g_cm3),
        "basis": basis,
        "calculated_by": "materials.py",
    }
