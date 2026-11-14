"""The bundled demo examples must load and carry what the 3D exhibit needs."""

import json
from pathlib import Path

import pytest

import main
from diagnose import Diagnosis

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "static" / "examples"
EXHIBIT_SOLUTIONS = 3


def load(n: int) -> Diagnosis:
    stored = json.loads((EXAMPLES_DIR / f"{n}.json").read_text(encoding="utf-8"))
    return Diagnosis.model_validate(stored["diagnosis"])


@pytest.mark.parametrize("n", range(1, main.EXAMPLE_COUNT + 1))
def test_bundled_example_is_a_valid_diagnosis(n):
    diagnosis = load(n)

    if diagnosis.insufficient_image:
        assert diagnosis.specimen is None
        assert diagnosis.solutions == []
        return
    assert diagnosis.specimen is not None
    assert 0 < diagnosis.specimen.break_position < 1
    assert len(diagnosis.solutions) >= EXHIBIT_SOLUTIONS
    for solution in diagnosis.solutions[:EXHIBIT_SOLUTIONS]:
        assert solution.visual is not None
        assert solution.visual.label.strip()
