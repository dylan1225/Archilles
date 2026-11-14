"""Uncertainty defences: null detection, self-consistency, rejected alternative,
geometry cross-check. A fake client stands in for the API."""

import pytest

import diagnose
import uncertainty
from diagnose import DiagnosisError
from test_diagnose import (
    TEST_KB,
    ApiStatusError,
    FakeClient,
    payload,
    rejected,
    response,
    run,
    solution,
)

NOT_A_PART = payload(
    visual_evidence=["A ceramic coffee mug on a desk, whole, with no cracks or chips"],
    is_failure_analysis_applicable=False,
    not_applicable_reason="This is an intact coffee mug, not a broken part. Nothing to analyse.",
    primary_mode="not_applicable",
    confidence="high",
    geometric_features=[],
    rejected_alternative=None,
    specimen=None,
    alternative_modes=[],
    solutions=[],
)
DELAMINATION = payload(
    material="fdm_print",
    primary_mode="layer_delamination_fdm",
    rejected_alternative=rejected(mode="fatigue_metal"),
)


@pytest.fixture(autouse=True)
def isolated_knowledge(monkeypatch):
    monkeypatch.setattr(diagnose, "load_knowledge_base", lambda: TEST_KB)
    monkeypatch.setattr(diagnose, "load_examples", lambda: [])


# --- 2. Null detection ------------------------------------------------------


def test_photo_of_something_unbroken_gets_no_diagnosis_at_all():
    result, meta = run(FakeClient(response(NOT_A_PART)))

    assert meta.attempts == 1
    assert result.is_failure_analysis_applicable is False
    assert result.primary_mode == "not_applicable"
    assert "intact coffee mug" in result.not_applicable_reason
    assert result.solutions == []
    assert result.alternative_modes == []
    assert result.rejected_alternative is None
    assert result.specimen is None
    assert result.probable_cause == ""


def test_not_applicable_wins_over_any_leftover_diagnosis_fields():
    leftovers = {**NOT_A_PART, "solutions": payload()["solutions"], "specimen": payload()["specimen"]}

    result, _ = run(FakeClient(response(leftovers)))

    assert result.solutions == []
    assert result.specimen is None


def test_prompt_makes_diagnosing_an_unbroken_part_the_worst_error():
    client = FakeClient(response(payload()))

    run(client)

    system = client.requests[0]["system_instruction"]
    assert "WORST ERROR THIS SYSTEM CAN MAKE" in system
    assert "is_failure_analysis_applicable to false" in system
    assert "not_applicable" in client.requests[0]["response_format"]["schema"]["properties"][
        "primary_mode"
    ]["enum"]


def test_schema_asks_whether_there_is_a_failure_before_naming_one():
    order = list(diagnose.output_schema(["fatigue_metal"])["properties"])

    assert order.index("visual_evidence") < order.index("is_failure_analysis_applicable")
    assert order.index("is_failure_analysis_applicable") < order.index("primary_mode")
    assert order.index("geometric_features") < order.index("primary_mode")


# --- 1. Self-consistency ----------------------------------------------------


def test_three_runs_that_agree_are_consistent_and_keep_their_confidence():
    client = FakeClient(*[response(payload())] * 3)

    result, meta = run(client, n_samples=3)

    assert result.consistency.model_dump() == {
        "runs": 3,
        "agreement": 1.0,
        "modes_seen": {"fatigue_metal": 3},
        "consistent": True,
        "failed_runs": 0,
    }
    assert result.confidence == "medium"
    assert result.warnings == []
    assert len(client.requests) == 3
    assert meta.input_tokens == 3000
    assert all(r["generation_config"]["temperature"] == 0.7 for r in client.requests)


def test_disagreeing_runs_return_the_majority_and_drop_confidence_to_low():
    client = FakeClient(response(payload(confidence="high")), response(DELAMINATION),
                        response(payload(confidence="high")))

    result, _ = run(client, n_samples=3)

    assert result.primary_mode == "fatigue_metal"
    assert result.consistency.agreement == 0.67
    assert result.consistency.modes_seen == {"fatigue_metal": 2, "layer_delamination_fdm": 1}
    assert result.consistency.consistent is False
    assert result.confidence == "low"
    assert "disagreed" in result.warnings[0]


def test_a_minority_run_that_sees_nothing_broken_is_surfaced():
    client = FakeClient(response(payload()), response(NOT_A_PART), response(payload()))

    result, _ = run(client, n_samples=3)

    assert result.primary_mode == "fatigue_metal"
    assert result.confidence == "low"
    assert any("found nothing broken" in warning for warning in result.warnings)


def test_a_three_way_split_declines_rather_than_picking_a_failure():
    client = FakeClient(response(payload()), response(DELAMINATION), response(NOT_A_PART))

    result, _ = run(client, n_samples=3)

    assert result.is_failure_analysis_applicable is False
    assert result.consistency.agreement == 0.33
    assert result.confidence == "low"


def test_one_failed_run_still_checks_the_other_two():
    client = FakeClient(ApiStatusError(503), response(payload()), response(payload()))

    result, _ = run(client, n_samples=3)

    assert result.consistency.runs == 2
    assert result.consistency.failed_runs == 1
    assert result.consistency.consistent is True
    assert any("1 of 3 runs failed" in warning for warning in result.warnings)


def test_a_single_completed_run_cannot_claim_consistency():
    client = FakeClient(ApiStatusError(503), ApiStatusError(503), response(payload()))

    result, _ = run(client, n_samples=3)

    assert result.consistency.consistent is False
    assert result.confidence == "low"
    assert "could not be checked" in result.warnings[0]


def test_all_runs_failing_raises_a_clear_error():
    client = FakeClient(*[ApiStatusError(503)] * 3)

    with pytest.raises(DiagnosisError, match="overloaded"):
        run(client, n_samples=3)


def test_a_single_run_has_no_consistency_block_and_no_temperature():
    client = FakeClient(response(payload()))

    result, _ = run(client)

    assert result.consistency is None
    assert "temperature" not in client.requests[0]["generation_config"]


# --- 3. Rejected alternative ------------------------------------------------


def test_rejected_alternative_passes_through_with_its_cited_feature():
    result, _ = run(FakeClient(response(payload())))

    assert result.rejected_alternative.mode == "brittle overload"
    assert "Beach marks" in result.rejected_alternative.visual_feature


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"mode": ""}, "mode is empty"),
        ({"mode": "fatigue_metal"}, "same as primary_mode"),
        ({"visual_feature": "texture"}, "too vague"),
        ({"visual_feature": "a dent on the rim"}, "not one of the features"),
        ({"reason": "Does not match the photo."}, "reason is too vague"),
    ],
)
def test_vague_rejections_are_named(overrides, expected):
    problem = uncertainty.rejected_alternative_problem(
        diagnose.RejectedAlternative(**rejected(**overrides)),
        "fatigue_metal",
        payload()["visual_evidence"],
    )

    assert expected in problem


def test_a_specific_rejection_is_accepted():
    problem = uncertainty.rejected_alternative_problem(
        diagnose.RejectedAlternative(**rejected()), "fatigue_metal", payload()["visual_evidence"]
    )

    assert problem is None


# --- 4. Geometry cross-check ------------------------------------------------


def test_stress_concentration_mode_without_a_stress_raiser_is_flagged():
    body = payload(confidence="high", geometric_features=[])

    result, _ = run(FakeClient(response(body)))

    assert result.confidence == "low"
    assert "Internal inconsistency" in result.warnings[0]
    assert "fatigue_metal starts at a stress raiser" in result.warnings[0]


def test_fillet_fix_without_a_stress_raiser_is_flagged_for_any_mode():
    body = {**DELAMINATION, "geometric_features": []}

    result, _ = run(FakeClient(response(body)))

    assert result.confidence == "low"
    assert "A fillet change is proposed" in result.warnings[0]


def test_no_flag_when_a_stress_raiser_was_identified():
    result, _ = run(FakeClient(response(payload(confidence="high"))))

    assert result.confidence == "high"
    assert result.warnings == []


def test_no_flag_for_a_mode_that_needs_no_stress_raiser():
    body = {**DELAMINATION, "geometric_features": [], "confidence": "high",
            "solutions": [solution(title=f"s{i}") for i in range(3)]}

    result, _ = run(FakeClient(response(body)))

    assert result.confidence == "high"
    assert result.warnings == []
