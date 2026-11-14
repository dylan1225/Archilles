"""Materials table, material comparisons, and the weight and strength card for
thicker sections. All numbers come from code and the table, never the model."""

import io
import json
import math
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import diagnose
import main
import materials
import mechanics

client = TestClient(main.app)
HEADER = "id,name,family,strength_mpa,strength_basis,modulus_gpa,elongation_pct,density_g_cm3,note,source,status\n"
GOOD_ROW = "pla,PLA,printed_plastic,50-60,tensile,3.5,4-6,1.24,note,Some datasheet,draft\n"


def write_table(tmp_path, *rows):
    path = tmp_path / "materials.csv"
    path.write_text(HEADER + "".join(rows), encoding="utf-8")
    return path


# --- The table ----------------------------------------------------------------------


def test_real_table_loads_and_every_row_has_a_source_and_status():
    table = materials.load_materials()

    assert len(table) >= 10
    for material in table.values():
        assert material.source
        assert material.status in materials.STATUSES
        assert material.density_g_cm3[0] > 0


def test_ranges_parse_and_single_values_become_equal_ranges():
    assert materials.parse_range("50-60", "x", "r") == (50.0, 60.0)
    assert materials.parse_range("1.24", "x", "r") == (1.24, 1.24)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("pla,PLA,printed_plastic,50-60,tensile,3.5,4-6,1.24,note,,draft\n", "source"),
        ("pla,PLA,printed_plastic,0,tensile,3.5,4-6,1.24,note,src,draft\n", "positive"),
        ("pla,PLA,printed_plastic,60-50,tensile,3.5,4-6,1.24,note,src,draft\n", "backwards"),
        ("pla,PLA,printed_plastic,50-60,tensile,3.5,4-6,1.24,note,src,guessed\n", "status"),
        ("pla,PLA,rubber,50-60,tensile,3.5,4-6,1.24,note,src,draft\n", "family"),
        ("Bad Id,PLA,printed_plastic,50-60,tensile,3.5,4-6,1.24,note,src,draft\n", "slug"),
    ],
)
def test_bad_rows_are_rejected_with_a_reason(tmp_path, row, message):
    with pytest.raises(materials.MaterialError, match=message):
        materials.load_materials(write_table(tmp_path, row))


def test_duplicate_ids_are_rejected(tmp_path):
    with pytest.raises(materials.MaterialError, match="Duplicate"):
        materials.load_materials(write_table(tmp_path, GOOD_ROW, GOOD_ROW))


def test_unknown_material_names_the_known_ones():
    with pytest.raises(materials.MaterialError, match="pla"):
        materials.get("unobtainium")


# --- Comparing materials ----------------------------------------------------------------


def test_compare_gives_ratio_ranges_from_both_ranges():
    result = materials.compare("pla", "petg")
    pla, petg = materials.get("pla"), materials.get("petg")

    assert result["strength_ratio"] == [
        round(petg.strength_mpa[0] / pla.strength_mpa[1], 2),
        round(petg.strength_mpa[1] / pla.strength_mpa[0], 2),
    ]
    assert result["strength_like_for_like"] is True
    assert result["calculated_by"] == "materials.py"


def test_wood_against_metal_strength_is_flagged_not_like_for_like():
    result = materials.compare("pine", "aluminium_6061_t6")

    assert result["strength_like_for_like"] is False
    assert "not like-for-like" in result["basis"]


# --- Thicker section: weight and strength -------------------------------------------------


def test_dowel_12_to_16_mm_weight_strength_and_stiffness():
    result = mechanics.section_change("round", {"diameter_mm": 12}, {"diameter_mm": 16})

    assert result["weight_ratio"] == pytest.approx(1.78, abs=0.005)    # (16/12)^2
    assert result["strength_ratio"] == pytest.approx(2.37, abs=0.005)  # (16/12)^3
    assert result["stiffness_ratio"] == pytest.approx(3.16, abs=0.005) # (16/12)^4
    assert "not stress-concentration" in result["basis"]


def test_rectangle_bent_across_its_thickness():
    result = mechanics.section_change(
        "rectangular", {"width_mm": 12, "thickness_mm": 4}, {"width_mm": 12, "thickness_mm": 6}
    )

    assert result["weight_ratio"] == pytest.approx(1.5)
    assert result["strength_ratio"] == pytest.approx(2.25)  # (6/4)^2
    assert result["stiffness_ratio"] == pytest.approx(3.38, abs=0.005)  # (6/4)^3


def test_grams_are_a_range_from_the_density_range():
    result = mechanics.section_change(
        "round", {"diameter_mm": 12}, {"diameter_mm": 16}, length_mm=220, density_g_cm3=(0.42, 0.52)
    )

    volume_before = math.pi * 6**2 * 220 / 1000
    assert result["mass_before_g"] == [round(volume_before * 0.42, 1), round(volume_before * 0.52, 1)]
    added = result["mass_added_g"]
    assert 0 < added[0] < added[1]


def test_missing_dimensions_ask_for_input_instead_of_guessing():
    computed = diagnose.section_computed(
        {"shape": "round", "current_diameter_mm": 12, "proposed_diameter_mm": None, "length_mm": None}
    )

    assert computed["needs_input"] == ["proposed_diameter_mm"]
    assert "weight_ratio" not in computed


def test_impossible_dimensions_are_reported():
    computed = diagnose.section_computed(
        {"shape": "round", "current_diameter_mm": 0, "proposed_diameter_mm": 16}
    )

    assert "diameter_mm must be > 0" in computed["error"]


def test_no_grams_without_a_material():
    computed = diagnose.section_computed(
        {"shape": "round", "current_diameter_mm": 12, "proposed_diameter_mm": 16, "length_mm": 220}
    )

    assert "mass_added_g" not in computed
    assert computed["weight_ratio"] == pytest.approx(1.78, abs=0.005)


# --- Through the diagnosis --------------------------------------------------------------


def _answer(**solution_overrides):
    solution = {
        "title": "Thicker dowel", "description": "x", "pros": ["a"], "cons": ["b"],
        "effort": "moderate", "addresses_root_cause": False, "geometry": None,
        "section_change": None, "proposed_material_id": None,
        "visual": {"kind": "thicken_section", "label": "Thicker dowel"},
    }
    root = {**solution, "title": "Add support", "addresses_root_cause": True}
    return {
        "visual_evidence": ["Long splinter on the tension side"], "insufficient_image": False,
        "insufficient_reason": None, "material": "wood", "primary_mode": "bending_overload_wood",
        "confidence": "high", "probable_cause": "x", "specimen": None, "alternative_modes": [],
        "solutions": [{**solution, **solution_overrides}, root, root],
    }


def _run(body, monkeypatch):
    monkeypatch.setattr(diagnose, "load_examples", lambda: [])
    response = SimpleNamespace(
        status="completed", model="fake", output_text=json.dumps(body), errors=None,
        usage=SimpleNamespace(total_input_tokens=1, total_output_tokens=1, total_thought_tokens=0),
    )
    fake = SimpleNamespace(interactions=SimpleNamespace(create=lambda **_: response))
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
    result, _ = diagnose.diagnose_with_meta(buffer.getvalue(), client=fake)
    return result


DOWEL = {
    "shape": "round", "current_diameter_mm": 12, "proposed_diameter_mm": 16,
    "current_width_mm": None, "proposed_width_mm": None, "current_thickness_mm": None,
    "proposed_thickness_mm": None, "length_mm": 220, "dimensions_source": "estimated_from_image",
}


def test_thicker_section_fix_gets_ratios_then_grams_once_material_is_known(monkeypatch):
    result = _run(_answer(section_change=DOWEL), monkeypatch)
    plain = result.solutions[0].section_computed

    assert plain["weight_ratio"] == pytest.approx(1.78, abs=0.005)
    assert "mass_added_g" not in plain

    with_poplar = diagnose.with_material(result, "poplar")
    computed = with_poplar.solutions[0].section_computed
    assert computed["mass_added_g"][0] > 0
    assert with_poplar.material_facts["name"] == materials.get("poplar").name


def test_proposed_material_is_compared_and_unknown_ids_are_dropped(monkeypatch):
    result = _run(_answer(proposed_material_id="birch"), monkeypatch)
    compared = diagnose.with_material(result, "poplar").solutions[0].material_comparison

    assert compared["to"]["id"] == "birch"
    assert compared["strength_ratio"][0] > 1

    invented = _run(_answer(proposed_material_id="vibranium"), monkeypatch)
    assert invented.solutions[0].proposed_material_id is None


# --- Endpoints ------------------------------------------------------------------------


def test_materials_endpoint_lists_the_table():
    body = client.get("/materials").json()

    assert {row["id"] for row in body} == set(materials.ids())


def test_section_endpoint_recomputes_with_grams():
    body = client.get("/mechanics/section", params={
        "shape": "round", "current_diameter_mm": 12, "proposed_diameter_mm": 16,
        "length_mm": 220, "material_id": "poplar",
    }).json()

    assert body["strength_ratio"] == pytest.approx(2.37, abs=0.005)
    assert body["inputs"]["dimensions_source"] == "user_measured"
    assert len(body["mass_added_g"]) == 2


def test_section_endpoint_rejects_impossible_and_unknown_inputs():
    zero = client.get("/mechanics/section", params={
        "shape": "round", "current_diameter_mm": 0, "proposed_diameter_mm": 16})
    unknown = client.get("/mechanics/section", params={
        "shape": "round", "current_diameter_mm": 12, "proposed_diameter_mm": 16,
        "material_id": "vibranium"})

    assert zero.status_code == 400
    assert unknown.status_code == 400
    assert "vibranium" in unknown.json()["error"]


def test_example_with_a_material_gets_reference_data():
    body = client.get("/examples/1", params={"material_id": "birch"}).json()

    assert body["material_facts"]["id"] == "birch"
    assert body["material_facts"]["source"]


def test_unknown_material_on_upload_is_rejected_before_any_model_call():
    response = client.post(
        "/diagnose", files={"image": ("x.jpg", b"not read", "image/jpeg")},
        data={"material_id": "vibranium"},
    )

    assert response.status_code == 400
    assert "vibranium" in response.json()["error"]
