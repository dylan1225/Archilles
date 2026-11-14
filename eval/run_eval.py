"""Evaluate diagnose() on held-out specimens.

Usage:  python eval/run_eval.py

Reads eval/labels.csv (filename, true_mode, material, how_it_broke, difficulty)
and the photos in eval/images/. Results are cached in eval/results.json, keyed
by image content and by the knowledge base / prompt / model in force, so
re-running does not re-bill and editing the knowledge base does re-run.

The model sees the photo only. how_it_broke is not passed to it: that would
measure how well it reads the note, not the fracture surface.

Never evaluates on the few-shot images in knowledge/examples/: if any eval
image is byte-identical to one of them, the run aborts.

Null specimens are photos with nothing to diagnose (an intact part, something
that is not a part). Label them true_mode = not_applicable. The run asserts the
model declined every one of them and exits with status 1 if it did not.
"""

import csv
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_DIR = EVAL_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

import diagnose  # noqa: E402  (needs the path set above)

LABELS_PATH = EVAL_DIR / "labels.csv"
IMAGES_DIR = EVAL_DIR / "images"
RESULTS_PATH = EVAL_DIR / "results.json"
REQUIRED_COLUMNS = ("filename", "true_mode", "material", "how_it_broke", "difficulty")
PREDICTED_ERROR = "ERROR"
NULL_MODE = diagnose.MODE_NOT_APPLICABLE
CONFIDENCE_LEVELS = ("high", "medium", "low")


class EvalError(Exception):
    """A problem with the eval set itself. The run stops."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_labels(labels_path: Path, images_dir: Path) -> list[dict[str, str]]:
    if not labels_path.is_file():
        raise EvalError(f"{labels_path} not found.")
    with labels_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise EvalError(f"{labels_path.name} is missing column(s): {', '.join(missing)}")
        rows = [{key: (row[key] or "").strip() for key in REQUIRED_COLUMNS} for row in reader]
    if not rows:
        raise EvalError(f"{labels_path.name} has no rows.")
    absent = [row["filename"] for row in rows if not (images_dir / row["filename"]).is_file()]
    if absent:
        raise EvalError(f"Listed in labels.csv but not in {images_dir}: {', '.join(absent)}")
    return rows


def check_no_leakage(rows: list[dict[str, str]], images_dir: Path, examples_dir: Path) -> None:
    """Abort if an eval image is one of the few-shot examples."""
    if not examples_dir.is_dir():
        return
    example_hashes = {
        sha256_file(path): path.name
        for path in examples_dir.iterdir()
        if path.suffix.lower() in diagnose.EXAMPLE_IMAGE_SUFFIXES
    }
    leaked = [
        f"{row['filename']} (same file as knowledge/examples/{example_hashes[digest]})"
        for row in rows
        if (digest := sha256_file(images_dir / row["filename"])) in example_hashes
    ]
    if leaked:
        raise EvalError(
            "Eval images that are also few-shot examples. Testing on them makes the "
            "number meaningless. Remove them from the eval set:\n  " + "\n  ".join(leaked)
        )


def unknown_true_modes(rows: list[dict[str, str]], allowed: list[str]) -> list[str]:
    return sorted({row["true_mode"] for row in rows} - set(allowed) - {NULL_MODE})


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))["entries"]
    except (ValueError, KeyError) as exc:
        raise EvalError(f"{path} is not a valid results file ({exc}). Delete it to start over.")


def save_cache(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    path.write_text(json.dumps({"entries": entries}, indent=1), encoding="utf-8")


def run_one(image_path: Path) -> dict[str, Any]:
    """One diagnosis (all its runs), exactly as the app does it. Shares the
    app's cache, so a photo already analysed there is not billed again.
    A failure is recorded, never swallowed."""
    try:
        result, meta, _ = diagnose.diagnose_cached(image_path.read_bytes(), None)
    except diagnose.DiagnosisError as exc:
        return {"error": str(exc), "predicted_mode": PREDICTED_ERROR, "predicted_material": None,
                "insufficient_image": False, "insufficient_reason": None, "confidence": None,
                "applicable": None, "seen": None, "agreement": None,
                "latency_s": None, "cost_usd": 0.0}
    consistency = result.consistency
    return {"error": None, "predicted_mode": result.primary_mode,
            "predicted_material": result.material,
            "insufficient_image": result.insufficient_image,
            "insufficient_reason": result.insufficient_reason,
            "confidence": result.confidence,
            "applicable": result.is_failure_analysis_applicable,
            "seen": result.not_applicable_reason,
            "agreement": consistency.agreement if consistency else None,
            "latency_s": meta.latency_s, "cost_usd": meta.cost_usd}


def evaluate(
    rows: list[dict[str, str]], images_dir: Path, results_path: Path
) -> tuple[list[dict[str, Any]], int]:
    """Return one record per row and the number of new (billed) calls."""
    fingerprint = diagnose.config_fingerprint()
    entries = load_cache(results_path)
    records, new_calls = [], 0
    for number, row in enumerate(rows, start=1):
        image_path = images_dir / row["filename"]
        key = hashlib.sha256(f"{sha256_file(image_path)}:{fingerprint}".encode()).hexdigest()
        cached = entries.get(key)
        if cached is None or cached["error"]:  # errors are retried, not kept
            print(f"[{number}/{len(rows)}] {row['filename']}: calling the model", flush=True)
            cached = run_one(image_path)
            new_calls += 1
            entries = {**entries, key: {**cached, "filename": row["filename"]}}
            save_cache(results_path, entries)  # after every call: an interrupted run keeps its work
        else:
            print(f"[{number}/{len(rows)}] {row['filename']}: cached", flush=True)
        records.append({**row, **cached, "correct": cached["predicted_mode"] == row["true_mode"]})
    return records, new_calls


def _accuracy_line(label: str, group: list[dict[str, Any]]) -> str:
    correct = sum(record["correct"] for record in group)
    return f"  {label:<28} {correct:>3}/{len(group):<3} {100 * correct / len(group):5.1f}%"


def _grouped(records: list[dict[str, Any]], field: str) -> list[str]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record[field] or "(blank)"].append(record)
    return [_accuracy_line(name, groups[name]) for name in sorted(groups)]


def confusion_matrix(records: list[dict[str, Any]]) -> list[str]:
    counts = Counter((r["true_mode"], r["predicted_mode"]) for r in records)
    true_modes = sorted({r["true_mode"] for r in records})
    predicted_modes = sorted({r["predicted_mode"] for r in records})
    corner = "true / predicted"
    width = max(len(corner), *(len(mode) for mode in true_modes))
    lines = ["  columns (predicted):"]
    lines += [f"    [{i}] {mode}" for i, mode in enumerate(predicted_modes, start=1)]
    header = "".join(f"{f'[{i}]':>6}" for i in range(1, len(predicted_modes) + 1))
    lines.append(f"  {corner:<{width}} {header}")
    for true_mode in true_modes:
        cells = "".join(f"{counts[(true_mode, p)] or '.':>6}" for p in predicted_modes)
        lines.append(f"  {true_mode:<{width}} {cells}")
    return lines


def table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """A plain-text table with a header rule, columns sized to fit."""
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]

    def line(cells: list[Any]) -> str:
        return "  " + "  ".join(f"{str(cell):<{width}}" for cell, width in zip(cells, widths))

    return [line(headers), "  " + "  ".join("-" * width for width in widths), *map(line, rows)]


def calibration_table(records: list[dict[str, Any]]) -> list[str]:
    """Accuracy by the confidence the model stated. Well calibrated means
    'high' is right more often than 'medium', and 'medium' more than 'low'."""
    rows = []
    for level in (*CONFIDENCE_LEVELS, None):
        group = [r for r in records if r["confidence"] == level]
        if not group:
            continue
        correct = sum(r["correct"] for r in group)
        rows.append([level or "(error)", len(group), correct,
                     f"{100 * correct / len(group):.0f}%",
                     f"{100 * len(group) / len(records):.0f}%"])
    return table(["stated confidence", "specimens", "correct", "accuracy", "share"], rows)


def null_table(nulls: list[dict[str, Any]]) -> list[str]:
    rows = []
    for r in nulls:
        verdict = ("ERROR" if r["error"] else
                   "declined" if r["applicable"] is False else "DIAGNOSED A FAILURE")
        agreement = "-" if r["agreement"] is None else f"{r['agreement']:.2f}"
        rows.append([r["filename"], r["how_it_broke"] or "-", verdict,
                     r["predicted_mode"], r["confidence"] or "-", agreement])
    return table(["file", "what it is", "result", "predicted", "confidence", "agreement"], rows)


def null_assertion(nulls: list[dict[str, Any]]) -> tuple[bool, str]:
    """is_failure_analysis_applicable must be false for every null specimen."""
    failed = [r["filename"] for r in nulls if r["applicable"] is not False]
    if not nulls:
        return True, "ASSERT skipped: no null specimens (true_mode = not_applicable) in labels.csv"
    if failed:
        return False, (f"ASSERT FAILED: is_failure_analysis_applicable was not false for "
                       f"{len(failed)} of {len(nulls)} null specimens: {', '.join(failed)}")
    return True, (f"ASSERT PASSED: is_failure_analysis_applicable is false for all "
                  f"{len(nulls)} null specimens")


def _failure_sections(failures: list[dict[str, Any]]) -> list[str]:
    abstained = [r for r in failures if r["insufficient_image"]]
    declined = [r for r in failures if r["applicable"] is False]
    answered = [r for r in failures if not r["insufficient_image"] and r["applicable"]]
    lines = ["TOP-1 ACCURACY ON FAILURE SPECIMENS (insufficient-image, declines and errors count as wrong)",
             _accuracy_line("overall", failures)]
    if answered and len(answered) != len(failures):
        lines.append(_accuracy_line("when it gave an answer", answered))
    lines += ["", "CONFUSION MATRIX (rows = true mode)", *confusion_matrix(failures)]
    lines += ["", "ACCURACY BY MATERIAL", *_grouped(failures, "material")]
    lines += ["", "ACCURACY BY DIFFICULTY", *_grouped(failures, "difficulty")]
    lines += ["", f"INSUFFICIENT IMAGE fired on {len(abstained)} of {len(failures)}"]
    lines += [f"  {r['filename']}: {r['insufficient_reason']}" for r in abstained]
    lines += ["", f"WRONGLY DECLINED (said nothing was broken) on {len(declined)} of {len(failures)}"]
    lines += [f"  {r['filename']}: {r['seen']}" for r in declined]
    return lines


def format_report(records: list[dict[str, Any]], new_calls: int) -> str:
    failures = [r for r in records if r["true_mode"] != NULL_MODE]
    nulls = [r for r in records if r["true_mode"] == NULL_MODE]
    errored = [r for r in records if r["error"]]
    latencies = [r["latency_s"] for r in records if r["latency_s"] is not None]
    total_cost = sum(r["cost_usd"] for r in records)
    median = f"{statistics.median(latencies):.1f} s" if latencies else "n/a"

    lines = ["", "=" * 72,
             f"POSTMORTEM EVAL   {len(failures)} failure specimens, {len(nulls)} null specimens",
             "=" * 72, ""]
    if failures:
        lines += _failure_sections(failures)
    else:
        lines.append("No failure specimens in labels.csv yet.")
    lines += ["", "CALIBRATION: ACCURACY BY STATED CONFIDENCE (all specimens; a null "
                  "specimen counts as correct when declined)", *calibration_table(records)]
    if nulls:
        declined = sum(r["applicable"] is False for r in nulls)
        lines += ["", f"NULL SPECIMENS: correctly declined {declined} of {len(nulls)}",
                  *null_table(nulls)]
        lines += [f"  {r['filename']} -> {r['seen']}" for r in nulls if r["seen"]]
    if errored:
        lines += ["", f"ERRORS on {len(errored)} of {len(records)} (will be retried next run)"]
        lines += [f"  {r['filename']}: {r['error']}" for r in errored]
    lines += ["", f"MEDIAN LATENCY  {median}",
              f"TOTAL COST      ${total_cost:.4f} for all {len(records)} specimens",
              f"THIS RUN        {new_calls} new model call(s), "
              f"{len(records) - new_calls} served from {RESULTS_PATH.name}", ""]
    lines += [null_assertion(nulls)[1], ""]
    return "\n".join(lines)


def main() -> int:
    try:
        rows = load_labels(LABELS_PATH, IMAGES_DIR)
        check_no_leakage(rows, IMAGES_DIR, diagnose.EXAMPLES_DIR)
        allowed = diagnose.mode_ids(diagnose.load_knowledge_base())
        unknown = unknown_true_modes(rows, allowed)
        if unknown:
            print(f"WARNING: true_mode value(s) not in the knowledge base, so they can never "
                  f"be predicted: {', '.join(unknown)}")
        records, new_calls = evaluate(rows, IMAGES_DIR, RESULTS_PATH)
    except (EvalError, diagnose.DiagnosisError) as exc:
        print(f"EVAL ABORTED: {exc}", file=sys.stderr)
        return 2
    print(format_report(records, new_calls))
    passed, _ = null_assertion([r for r in records if r["true_mode"] == NULL_MODE])
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
