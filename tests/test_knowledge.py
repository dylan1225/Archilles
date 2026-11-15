"""The failure-mode knowledge base is complete, and its names and sources reach the
result through code, never through the model."""

import re

import pytest
from fastapi.testclient import TestClient

import diagnose
import main

SECTIONS = ("### Visual signature", "### Typical cause", "### What to change", "### Distinguish from:")
FIELDS = ("name", "material", "source", "status")
EXPECTED_MODE_COUNT = 11

REAL_KB = diagnose.load_knowledge_base()


def blocks(knowledge_base: str) -> dict[str, str]:
    parts = re.split(r"^## Mode:\s*", knowledge_base, flags=re.MULTILINE)[1:]
    return {part.split(None, 1)[0]: part for part in parts}


# --- The real knowledge base ------------------------------------------------------


def test_every_slot_is_filled():
    assert "TODO" not in REAL_KB
    assert len(diagnose.mode_ids(REAL_KB)) == EXPECTED_MODE_COUNT


@pytest.mark.parametrize("mode_id", diagnose.mode_ids(REAL_KB))
def test_each_mode_has_every_field_and_section_in_order(mode_id):
    block = blocks(REAL_KB)[mode_id]
    info = diagnose.mode_info(REAL_KB)[mode_id]

    for field in FIELDS:
        assert info[field], f"{mode_id} has no {field.capitalize()}"
    assert info["status"] in ("draft", "reviewed")
    positions = [block.find(section) for section in SECTIONS]
    assert all(position >= 0 for position in positions), f"{mode_id} is missing a section"
    assert positions == sorted(positions), f"{mode_id} has its sections out of order"


def test_mode_ids_are_lowercase_slugs():
    for mode_id in diagnose.mode_ids(REAL_KB):
        assert re.fullmatch(r"[a-z0-9_]+", mode_id), mode_id


# --- Parsing and attaching ----------------------------------------------------------

SMALL_KB = """# KB

## Mode: fatigue_metal

- **Name:** Fatigue fracture
- **Material:** metal
- **Source:** ASM Handbook, Vol. 12
- **Status:** reviewed

### Visual signature
Beach marks.

## Mode: half_written

- **Name:** Half written

## Mode: TODO_03

- **Name:** TODO
"""


def test_mode_info_reads_fields_and_leaves_missing_ones_empty():
    info = diagnose.mode_info(SMALL_KB)

    assert info["fatigue_metal"] == {
        "name": "Fatigue fracture",
        "material": "metal",
        "source": "ASM Handbook, Vol. 12",
        "status": "reviewed",
    }
    assert info["half_written"]["name"] == "Half written"
    assert info["half_written"]["source"] is None
    assert "TODO_03" not in info


def _diagnosis(mode: str) -> diagnose.Diagnosis:
    return diagnose.Diagnosis(
        material="metal", primary_mode=mode, confidence="high", visual_evidence=["x"],
        probable_cause="x", alternative_modes=[], solutions=[], insufficient_image=False,
        insufficient_reason=None,
    )


def test_known_mode_gets_its_name_source_and_status():
    result = diagnose.with_mode_info(_diagnosis("fatigue_metal"), SMALL_KB)

    assert result.mode_name == "Fatigue fracture"
    assert result.mode_source == "ASM Handbook, Vol. 12"
    assert result.mode_status == "reviewed"


@pytest.mark.parametrize(
    ("mode", "name"),
    [("other", "Not in the knowledge base"), ("undetermined", "Undetermined")],
)
def test_built_in_answers_get_a_name_but_no_source(mode, name):
    result = diagnose.with_mode_info(_diagnosis(mode), SMALL_KB)

    assert result.mode_name == name
    assert result.mode_source is None


def test_attaching_overwrites_anything_already_there():
    planted = _diagnosis("fatigue_metal").model_copy(
        update={"mode_name": "Invented", "mode_source": "Made up, 2099"}
    )

    result = diagnose.with_mode_info(planted, SMALL_KB)

    assert result.mode_name == "Fatigue fracture"
    assert result.mode_source == "ASM Handbook, Vol. 12"


def test_bundled_example_is_served_with_the_mode_name_and_source():
    body = TestClient(main.app).get("/examples/1").json()
    info = diagnose.mode_info(REAL_KB)[body["primary_mode"]]

    assert body["mode_name"] == info["name"]
    assert body["mode_source"] == info["source"]
