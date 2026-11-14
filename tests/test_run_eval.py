"""Offline tests for eval/run_eval.py. The model is replaced by a fake."""

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import diagnose

_SPEC = importlib.util.spec_from_file_location(
    "run_eval", Path(__file__).resolve().parent.parent / "eval" / "run_eval.py"
)
run_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_eval)

HEADER = "filename,true_mode,material,how_it_broke,difficulty\n"


def write_image(path: Path, shade: int) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(shade, shade, shade)).save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())


@pytest.fixture
def eval_set(tmp_path):
    """Four specimens: two fatigue, one delamination, one wood."""
    images = tmp_path / "images"
    images.mkdir()
    rows = [
        ("a.png", "fatigue_metal", "metal", "cyclic", "easy"),
        ("b.png", "fatigue_metal", "metal", "cyclic", "hard"),
        ("c.png", "layer_delamination_fdm", "fdm_print", "pulled", "easy"),
        ("d.png", "bending_overload_wood", "wood", "bent", "hard"),
    ]
    for shade, row in enumerate(rows):
        write_image(images / row[0], shade * 40)
    labels = tmp_path / "labels.csv"
    labels.write_text(HEADER + "".join(",".join(r) + "\n" for r in rows), encoding="utf-8")
    return SimpleNamespace(
        labels=labels, images=images, results=tmp_path / "results.json", root=tmp_path
    )


@pytest.fixture
def fake_model(monkeypatch):
    """a: right. b: wrong mode. c: insufficient image. d: API error."""
    calls: list[int] = []
    answers = {
        0: ("fatigue_metal", False),
        40: ("bending_overload_wood", False),
        80: ("undetermined", True),
    }

    def fake(image_bytes, context=None, client=None):
        shade = Image.open(io.BytesIO(image_bytes)).getpixel((0, 0))[0]
        calls.append(shade)
        if shade not in answers:
            raise diagnose.DiagnosisError("Rate limited by the Anthropic API.")
        mode, insufficient = answers[shade]
        result = SimpleNamespace(
            primary_mode=mode,
            material="metal",
            confidence="medium",
            insufficient_image=insufficient,
            insufficient_reason="Out of focus." if insufficient else None,
        )
        return result, SimpleNamespace(latency_s=10.0 + shade / 10, cost_usd=0.02)

    monkeypatch.setattr(diagnose, "diagnose_with_meta", fake)
    monkeypatch.setattr(diagnose, "config_fingerprint", lambda: "fingerprint-1")
    return calls


def run(eval_set):
    rows = run_eval.load_labels(eval_set.labels, eval_set.images)
    return run_eval.evaluate(rows, eval_set.images, eval_set.results)


# --- inputs -----------------------------------------------------------------


def test_missing_column_stops_the_run(tmp_path):
    labels = tmp_path / "labels.csv"
    labels.write_text("filename,true_mode\na.png,fatigue_metal\n", encoding="utf-8")

    with pytest.raises(run_eval.EvalError, match="missing column"):
        run_eval.load_labels(labels, tmp_path)


def test_label_without_an_image_stops_the_run(eval_set):
    (eval_set.images / "b.png").unlink()

    with pytest.raises(run_eval.EvalError, match="b.png"):
        run_eval.load_labels(eval_set.labels, eval_set.images)


def test_few_shot_image_in_the_eval_set_aborts_and_names_the_file(eval_set):
    examples = eval_set.root / "examples"
    examples.mkdir()
    (examples / "shot1.png").write_bytes((eval_set.images / "c.png").read_bytes())
    rows = run_eval.load_labels(eval_set.labels, eval_set.images)

    with pytest.raises(run_eval.EvalError) as raised:
        run_eval.check_no_leakage(rows, eval_set.images, examples)

    assert "c.png" in str(raised.value)
    assert "shot1.png" in str(raised.value)


def test_distinct_examples_do_not_trigger_the_leak_check(eval_set):
    examples = eval_set.root / "examples"
    examples.mkdir()
    write_image(examples / "shot1.png", 250)
    rows = run_eval.load_labels(eval_set.labels, eval_set.images)

    run_eval.check_no_leakage(rows, eval_set.images, examples)


def test_true_modes_absent_from_knowledge_base_are_reported(eval_set):
    rows = run_eval.load_labels(eval_set.labels, eval_set.images)

    assert run_eval.unknown_true_modes(rows, ["fatigue_metal"]) == [
        "bending_overload_wood",
        "layer_delamination_fdm",
    ]


# --- running and caching ----------------------------------------------------


def test_scores_each_specimen_against_its_label(eval_set, fake_model):
    records, new_calls = run(eval_set)

    assert new_calls == 4
    assert [r["correct"] for r in records] == [True, False, False, False]
    assert records[2]["insufficient_image"] is True
    assert records[3]["predicted_mode"] == "ERROR"
    assert "Rate limited" in records[3]["error"]


def test_second_run_makes_no_calls_except_retrying_errors(eval_set, fake_model):
    run(eval_set)
    fake_model.clear()

    records, new_calls = run(eval_set)

    assert fake_model == [120]  # only d.png, which errored last time
    assert new_calls == 1
    assert [r["correct"] for r in records] == [True, False, False, False]


def test_changing_the_knowledge_base_reruns_everything(eval_set, fake_model, monkeypatch):
    run(eval_set)
    fake_model.clear()
    monkeypatch.setattr(diagnose, "config_fingerprint", lambda: "fingerprint-2")

    _, new_calls = run(eval_set)

    assert new_calls == 4


def test_corrupt_results_file_is_reported_not_overwritten(eval_set, fake_model):
    eval_set.results.write_text("{broken", encoding="utf-8")

    with pytest.raises(run_eval.EvalError, match="not a valid results file"):
        run(eval_set)
    assert eval_set.results.read_text(encoding="utf-8") == "{broken"


# --- report -----------------------------------------------------------------


def test_report_contains_every_requested_figure(eval_set, fake_model):
    records, new_calls = run(eval_set)

    report = run_eval.format_report(records, new_calls)

    assert "overall" in report and "1/4" in report and "25.0%" in report
    assert "when it gave an answer" in report and "1/2" in report
    assert "CONFUSION MATRIX" in report
    assert "ACCURACY BY MATERIAL" in report and "ACCURACY BY DIFFICULTY" in report
    assert "INSUFFICIENT IMAGE fired on 1 of 4" in report
    assert "c.png: Out of focus." in report
    assert "d.png: Rate limited" in report
    assert "MEDIAN LATENCY  14.0 s" in report
    assert "TOTAL COST      $0.0600" in report
    assert "4 new model call(s)" in report


def test_confusion_matrix_counts_true_against_predicted(eval_set, fake_model):
    records, _ = run(eval_set)

    matrix = "\n".join(run_eval.confusion_matrix(records))

    fatigue_row = next(line for line in matrix.splitlines() if line.strip().startswith("fatigue_metal"))
    assert fatigue_row.split()[1:] == [".", "1", "1", "."]
