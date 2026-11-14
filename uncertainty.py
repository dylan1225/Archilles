"""Defences against a confident failure story the photo does not support.

Pure Python, no model calls. Works on Diagnosis objects from diagnose.py
(duck-typed, so this module does not import diagnose):

- combine(): self-consistency across several independent runs on one photo
- geometry_cross_check(): a stress-raiser mode with no stress raiser observed
- rejected_alternative_problem(): a rejected alternative must cite a feature
"""

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel

NOT_APPLICABLE = "not_applicable"
INSUFFICIENT_IMAGE = "insufficient_image"
LOW = "low"

# Modes whose mechanism starts at a stress raiser (hole, notch, sharp corner,
# thread root, keyway). Diagnosing one while observing no stress raiser is an
# internal contradiction. Add mode ids here as the knowledge base grows.
STRESS_CONCENTRATION_MODES = frozenset({"fatigue_metal"})

# A rejected alternative is only specific if it cites an observed feature with
# enough words to locate it, and gives a reason of more than a stock phrase.
MIN_FEATURE_WORDS = 3
MIN_REASON_WORDS = 8
_CONTENT_WORD = re.compile(r"[a-z]{4,}")
_STOPWORDS = frozenset(
    """about also along because been both does down from have here into less likely
    more most much must only other over part rather same seen shows show some such
    than that their them then there these they this those very visible what when
    where which while with would mode photo image""".split()
)


class Consistency(BaseModel):
    """How far independent runs on the same photo agreed."""

    runs: int
    agreement: float
    modes_seen: dict[str, int]
    consistent: bool
    failed_runs: int = 0


def outcome(diagnosis: Any) -> str:
    """What one run concluded, as a single label to compare across runs."""
    if not diagnosis.is_failure_analysis_applicable:
        return NOT_APPLICABLE
    if diagnosis.insufficient_image:
        return INSUFFICIENT_IMAGE
    return diagnosis.primary_mode


def _chosen_outcome(counts: Counter) -> str:
    """The majority outcome. On a tie, the most cautious one wins: declining
    to diagnose beats naming a failure mode the runs could not agree on."""
    top = max(counts.values())
    tied = [label for label, count in counts.items() if count == top]
    for cautious in (NOT_APPLICABLE, INSUFFICIENT_IMAGE):
        if cautious in tied:
            return cautious
    return tied[0]


def _seen_text(counts: Counter) -> str:
    return ", ".join(f"{label} ×{count}" for label, count in counts.most_common())


def _consistency_warnings(
    counts: Counter, chosen: str, completed: int, failed_runs: int
) -> list[str]:
    attempted = completed + failed_runs
    if completed < 2:
        return [
            f"Only {completed} of {attempted} runs completed, so consistency could not "
            "be checked. Confidence lowered to low."
        ]
    warnings = []
    if len(counts) > 1:
        warnings.append(
            f"The {completed} independent runs disagreed ({_seen_text(counts)}). "
            "Confidence lowered to low."
        )
        declined = counts.get(NOT_APPLICABLE, 0)
        if declined and chosen != NOT_APPLICABLE:
            warnings.append(
                f"{declined} of {completed} runs found nothing broken to analyse in this photo."
            )
    if failed_runs:
        warnings.append(
            f"{failed_runs} of {attempted} runs failed; agreement is based on the "
            f"{completed} that completed."
        )
    return warnings


def combine(runs: list[Any], failed_runs: int = 0) -> Any:
    """Merge independent runs on one photo into one diagnosis.

    Returns the majority answer (never simply the first), with a Consistency
    block. Any disagreement, or too few completed runs to compare, lowers
    confidence to low and adds a warning saying why.
    """
    if not runs:
        raise ValueError("combine() needs at least one completed run")
    labels = [outcome(run) for run in runs]
    counts = Counter(labels)
    chosen_label = _chosen_outcome(counts)
    chosen = runs[labels.index(chosen_label)]
    consistent = len(runs) >= 2 and len(counts) == 1
    consistency = Consistency(
        runs=len(runs),
        agreement=round(counts[chosen_label] / len(runs), 2),
        modes_seen=dict(counts),
        consistent=consistent,
        failed_runs=failed_runs,
    )
    warnings = _consistency_warnings(counts, chosen_label, len(runs), failed_runs)
    return chosen.model_copy(
        update={
            "consistency": consistency,
            "confidence": chosen.confidence if consistent else LOW,
            "warnings": [*chosen.warnings, *warnings],
        }
    )


def _proposes_fillet(diagnosis: Any) -> bool:
    return any(
        solution.computed and solution.computed.get("type") == "fillet_improvement"
        for solution in diagnosis.solutions
    )


def geometry_cross_check(diagnosis: Any) -> Any:
    """Flag a diagnosis that relies on a stress raiser nobody observed.

    A stress-concentration mode, or a fillet fix, with no geometric feature
    identified in the photo contradicts itself: confidence drops to low and
    the contradiction is stated.
    """
    if not diagnosis.is_failure_analysis_applicable or diagnosis.insufficient_image:
        return diagnosis
    if any(feature.strip() for feature in diagnosis.geometric_features):
        return diagnosis
    if diagnosis.primary_mode in STRESS_CONCENTRATION_MODES:
        claim = f"{diagnosis.primary_mode} starts at a stress raiser"
    elif _proposes_fillet(diagnosis):
        claim = "A fillet change is proposed"
    else:
        return diagnosis
    warning = (
        f"Internal inconsistency: {claim}, but no hole, notch, sharp corner or other "
        "stress raiser was identified in the photo. Confidence lowered to low."
    )
    return diagnosis.model_copy(
        update={"confidence": LOW, "warnings": [*diagnosis.warnings, warning]}
    )


def _content_words(text: str) -> set[str]:
    return {word for word in _CONTENT_WORD.findall(text.lower()) if word not in _STOPWORDS}


def rejected_alternative_problem(
    rejected: Any, primary_mode: str, visual_evidence: list[str]
) -> str | None:
    """Why a rejected alternative is too vague to accept, or None if it is fine.

    A floor, not a fabrication detector: it catches a missing alternative, a
    rejection that cites no observed feature, and a stock one-line reason.
    """
    if rejected is None:
        return "rejected_alternative is missing; name the second most likely mode"
    mode = (rejected.mode or "").strip()
    feature = (rejected.visual_feature or "").strip()
    reason = (rejected.reason or "").strip()
    if not mode:
        return "rejected_alternative.mode is empty"
    if mode == primary_mode:
        return "rejected_alternative.mode is the same as primary_mode"
    if len(feature.split()) < MIN_FEATURE_WORDS:
        return (
            "rejected_alternative.visual_feature is too vague; cite a specific "
            "feature in this photo and where it is"
        )
    if not _content_words(feature) & _content_words(" ".join(visual_evidence)):
        return (
            "rejected_alternative.visual_feature is not one of the features listed in "
            "visual_evidence; cite something actually observed in this photo"
        )
    if len(reason.split()) < MIN_REASON_WORDS:
        return (
            "rejected_alternative.reason is too vague; say what the alternative would "
            "show instead and why the cited feature contradicts it"
        )
    return None
