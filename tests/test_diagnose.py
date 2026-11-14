"""Offline tests for diagnose.py. A fake client stands in for the API."""

import base64
import io
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from PIL import Image

import diagnose
import mechanics
from diagnose import Diagnosis, DiagnosisError

# Captured before the autouse fixture below replaces it.
REAL_LOAD_EXAMPLES = diagnose.load_examples

TEST_KB = """# Test knowledge base

## Mode: fatigue_metal
Beach marks.

## Mode: layer_delamination_fdm
Clean layer faces.

## Mode: TODO_04
TODO
"""


def image_bytes(fmt: str = "JPEG", size=(64, 48), mode: str = "RGB", **save_kwargs) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color=128).save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def decoded(image_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.standard_b64decode(image_b64)))


def solution(**overrides) -> dict:
    base = {
        "title": "Add a fillet",
        "description": "Round the inside corner.",
        "pros": ["Lower peak stress"],
        "cons": ["Mating part needs a chamfer"],
        "effort": "trivial",
        "addresses_root_cause": True,
        "fillet": None,
    }
    return {**base, **overrides}


def payload(**overrides) -> dict:
    base = {
        "visual_evidence": ["Beach marks radiating from the lower left corner"],
        "insufficient_image": False,
        "insufficient_reason": None,
        "material": "metal",
        "primary_mode": "fatigue_metal",
        "confidence": "medium",
        "probable_cause": "Cyclic bending at a sharp corner.",
        "alternative_modes": [
            {"mode": "other", "why_less_likely": "Two distinct zones are visible."}
        ],
        "solutions": [
            solution(
                fillet={
                    "current_radius_mm": 0.0,
                    "proposed_radius_mm": 2.0,
                    "section_depth_mm": 10.0,
                    "dimensions_source": "estimated_from_image",
                }
            ),
            solution(title="Thicker section", addresses_root_cause=False),
            solution(title="Shot peen", addresses_root_cause=False),
        ],
    }
    return {**base, **overrides}


def response(body, stop_reason: str = "end_turn") -> SimpleNamespace:
    text = body if isinstance(body, str) else json.dumps(body)
    return SimpleNamespace(
        stop_reason=stop_reason,
        model="fake-model",
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class FakeClient:
    def __init__(self, *queued):
        self._queued = list(queued)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        item = self._queued.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def isolated_knowledge(monkeypatch):
    monkeypatch.setattr(diagnose, "load_knowledge_base", lambda: TEST_KB)
    monkeypatch.setattr(diagnose, "load_examples", lambda: [])


def run(client: FakeClient, context: str | None = None):
    return diagnose.diagnose_with_meta(image_bytes(), context, client=client)


# --- prepare_image ----------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "mode"),
    [("JPEG", "RGB"), ("PNG", "RGB"), ("PNG", "RGBA"), ("HEIF", "RGB"), ("WEBP", "RGB")],
)
def test_prepare_image_normalises_every_format_to_jpeg(fmt, mode):
    image_b64, media_type = diagnose.prepare_image(image_bytes(fmt, mode=mode))

    assert media_type == "image/jpeg"
    assert decoded(image_b64).format == "JPEG"


def test_prepare_image_downscales_large_photos():
    image_b64, _ = diagnose.prepare_image(image_bytes(size=(4000, 3000)))

    assert decoded(image_b64).size == (2000, 1500)


def test_prepare_image_applies_exif_rotation():
    exif = Image.Exif()
    exif[0x0112] = 6  # camera held sideways
    image_b64, _ = diagnose.prepare_image(image_bytes(size=(100, 50), exif=exif))

    assert decoded(image_b64).size == (50, 100)


@pytest.mark.parametrize("bad", [b"", b"this is not an image", b"\x00" * 64])
def test_prepare_image_rejects_non_images_with_clear_message(bad):
    with pytest.raises(DiagnosisError):
        diagnose.prepare_image(bad)


# --- knowledge base and examples -------------------------------------------


def test_mode_ids_excludes_todo_placeholders():
    assert diagnose.mode_ids(TEST_KB) == ["fatigue_metal", "layer_delamination_fdm"]


def test_real_knowledge_base_parses_to_at_least_the_three_examples():
    real = diagnose.KNOWLEDGE_PATH.read_text(encoding="utf-8")

    assert len(diagnose.mode_ids(real)) >= 3


def test_load_examples_pairs_images_with_labels_and_skips_unlabelled(tmp_path):
    (tmp_path / "a.png").write_bytes(image_bytes("PNG"))
    (tmp_path / "a.json").write_text('{"primary_mode": "fatigue_metal"}', encoding="utf-8")
    (tmp_path / "b.jpg").write_bytes(image_bytes())

    examples = REAL_LOAD_EXAMPLES(tmp_path)

    assert [example.name for example in examples] == ["a"]
    assert "fatigue_metal" in examples[0].label
    assert examples[0].media_type == "image/jpeg"


# --- schema -----------------------------------------------------------------


def test_schema_puts_evidence_before_any_conclusion():
    order = list(diagnose.output_schema(["fatigue_metal"])["properties"])

    assert order.index("visual_evidence") == 0
    assert order.index("visual_evidence") < order.index("insufficient_image")
    assert order.index("insufficient_image") < order.index("primary_mode")


def test_schema_restricts_primary_mode_to_allowed_ids():
    schema = diagnose.output_schema(["fatigue_metal", "other"])

    assert schema["properties"]["primary_mode"]["enum"] == ["fatigue_metal", "other"]


# --- diagnose ---------------------------------------------------------------


def test_valid_answer_becomes_a_diagnosis():
    result, meta = run(FakeClient(response(payload())))

    assert isinstance(result, Diagnosis)
    assert result.primary_mode == "fatigue_metal"
    assert result.alternative_modes == [("other", "Two distinct zones are visible.")]
    assert len(result.solutions) == 3
    assert meta.attempts == 1
    assert meta.cost_usd == pytest.approx(0.014)


def test_fillet_solution_gets_numbers_from_mechanics_not_the_model():
    result, _ = run(FakeClient(response(payload())))

    computed = result.solutions[0].computed
    expected = mechanics.fillet_improvement(0.0, 2.0, 10.0)
    assert computed["type"] == "fillet_improvement"
    assert computed["kt_current"] == expected["kt_current"]
    assert computed["kt_proposed"] == expected["kt_proposed"]
    assert computed["peak_stress_reduction_pct"] == expected["peak_stress_reduction_pct"]
    assert computed["inputs"]["dimensions_source"] == "estimated_from_image"
    assert result.solutions[1].computed is None


def test_impossible_fillet_dimensions_are_reported_not_hidden():
    bad_fillet = {
        "current_radius_mm": 0.0,
        "proposed_radius_mm": 2.0,
        "section_depth_mm": 0.0,
        "dimensions_source": "estimated_from_image",
    }
    body = payload(solutions=[solution(fillet=bad_fillet), solution(), solution()])

    result, _ = run(FakeClient(response(body)))

    assert "Not calculated" in result.solutions[0].computed["error"]


def test_request_carries_rules_knowledge_base_and_the_photo_last():
    client = FakeClient(response(payload()))

    run(client, context="snapped after a month of use")

    request = client.requests[0]
    system = request["system"][0]["text"]
    content = request["messages"][0]["content"]
    assert request["model"] == diagnose.MODEL
    assert "Beach marks." in system
    assert "Do not guess." in system
    assert content[-2]["type"] == "image"
    assert content[-2]["source"]["media_type"] == "image/jpeg"
    assert "snapped after a month of use" in content[-1]["text"]


def test_insufficient_image_never_carries_a_mode_or_solutions():
    body = payload(insufficient_image=True, insufficient_reason="Out of focus.")

    result, _ = run(FakeClient(response(body)))

    assert result.insufficient_image is True
    assert result.insufficient_reason == "Out of focus."
    assert result.primary_mode == "undetermined"
    assert result.confidence == "low"
    assert result.solutions == []
    assert result.alternative_modes == []


def test_malformed_answer_is_retried_once_then_succeeds():
    client = FakeClient(response("{not json"), response(payload()))

    result, meta = run(client)

    assert result.primary_mode == "fatigue_metal"
    assert meta.attempts == 2
    assert meta.input_tokens == 2000
    retry_note = client.requests[1]["messages"][0]["content"][-1]["text"]
    assert "previous answer was rejected" in retry_note


def test_two_malformed_answers_fail_with_a_clear_message():
    client = FakeClient(response("{not json"), response("still not json"))

    with pytest.raises(DiagnosisError, match="unusable answer 2 times"):
        run(client)
    assert len(client.requests) == 2


@pytest.mark.parametrize(
    "broken",
    [
        payload(visual_evidence=[]),
        payload(visual_evidence=["  "]),
        payload(primary_mode="made_up_mode"),
        payload(solutions=[solution(), solution()]),
        payload(solutions=[solution(addresses_root_cause=False)] * 3),
        payload(insufficient_image=True, insufficient_reason=None),
    ],
    ids=[
        "no-evidence",
        "blank-evidence",
        "unknown-mode",
        "too-few-solutions",
        "no-root-cause-solution",
        "insufficient-without-reason",
    ],
)
def test_rule_breaking_answers_are_rejected_and_retried(broken):
    client = FakeClient(response(broken), response(payload()))

    _, meta = run(client)

    assert meta.attempts == 2


def test_cut_off_answer_is_retried():
    client = FakeClient(response(payload(), stop_reason="max_tokens"), response(payload()))

    _, meta = run(client)

    assert meta.attempts == 2


def test_more_than_four_solutions_are_trimmed_to_four():
    body = payload(solutions=[solution(title=f"s{i}") for i in range(6)])

    result, _ = run(FakeClient(response(body)))

    assert [s.title for s in result.solutions] == ["s0", "s1", "s2", "s3"]


def test_refusal_fails_immediately_without_retry():
    client = FakeClient(response("", stop_reason="refusal"))

    with pytest.raises(DiagnosisError, match="declined"):
        run(client)
    assert len(client.requests) == 1


def test_timeout_becomes_a_clear_error():
    timeout = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x"))

    with pytest.raises(DiagnosisError, match="did not answer within"):
        run(FakeClient(timeout))


def test_missing_api_key_is_reported_before_any_call(monkeypatch):
    monkeypatch.setattr(diagnose, "_has_credentials", lambda: False)

    with pytest.raises(DiagnosisError, match="ANTHROPIC_API_KEY is not set"):
        diagnose.make_client()


def test_unreadable_upload_fails_before_any_call():
    client = FakeClient(response(payload()))

    with pytest.raises(DiagnosisError, match="could not be read as an image"):
        diagnose.diagnose_with_meta(b"not an image", None, client=client)
    assert client.requests == []
