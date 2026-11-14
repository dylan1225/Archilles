"""Vision diagnosis of a fracture photo against the knowledge base.

Usage:  python diagnose.py <image> [--context "what happened"]

The model observes and classifies. It never produces stress numbers: those come
from mechanics.py and are attached to a solution's `computed` field afterwards.
"""

import argparse
import base64
import io
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import anthropic
import pillow_heif
from dotenv import load_dotenv
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError

import mechanics

logger = logging.getLogger("postmortem.diagnose")
pillow_heif.register_heif_opener()

PROJECT_DIR = Path(__file__).resolve().parent
KNOWLEDGE_PATH = PROJECT_DIR / "knowledge" / "failure_modes.md"
EXAMPLES_DIR = PROJECT_DIR / "knowledge" / "examples"
EXAMPLE_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")

MODEL = "claude-opus-5-5"
EFFORT = "medium"
MAX_TOKENS = 16000
MODEL_TIMEOUT_S = 90.0
MAX_ATTEMPTS = 2  # one call, plus one retry on malformed output
# If the model declines a request, the API re-runs it on a fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

MAX_LONG_EDGE_PX = 2000
JPEG_QUALITY = 90

MODE_OTHER = "other"
MODE_UNDETERMINED = "undetermined"
MIN_SOLUTIONS = 3
MAX_SOLUTIONS = 4

# USD per million tokens for MODEL. Used only to report cost.
PRICE_INPUT = 4.00
PRICE_OUTPUT = 20.00
PRICE_CACHE_READ = 0.20
PRICE_CACHE_WRITE = 5.00


class DiagnosisError(Exception):
    """A failure with a message that is safe and useful to show the user."""


# --- Public schema ----------------------------------------------------------


class Solution(BaseModel):
    title: str
    description: str
    pros: list[str]
    cons: list[str]
    effort: Literal["trivial", "moderate", "significant"]
    addresses_root_cause: bool
    computed: dict | None


class Diagnosis(BaseModel):
    material: Literal["fdm_print", "metal", "wood", "other", "unclear"]
    primary_mode: str
    confidence: Literal["high", "medium", "low"]
    visual_evidence: list[str]
    probable_cause: str
    alternative_modes: list[tuple[str, str]]
    solutions: list[Solution]
    insufficient_image: bool
    insufficient_reason: str | None


@dataclass(frozen=True)
class RunMeta:
    model: str
    latency_s: float
    attempts: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class Example:
    name: str
    image_b64: str
    media_type: str
    label: str


# --- What the model fills in ------------------------------------------------
# Field order is generation order: observations come before any conclusion.


class _FilletChange(BaseModel):
    current_radius_mm: float = Field(description="0 for a sharp corner.")
    proposed_radius_mm: float
    section_depth_mm: float = Field(description="Depth of the section at the notch.")
    dimensions_source: Literal["user_context", "estimated_from_image"]


class _ModelSolution(BaseModel):
    title: str
    description: str
    pros: list[str]
    cons: list[str]
    effort: Literal["trivial", "moderate", "significant"]
    addresses_root_cause: bool
    fillet: _FilletChange | None = Field(
        description="Only when this solution adds or enlarges a fillet; else null."
    )


class _AlternativeMode(BaseModel):
    mode: str
    why_less_likely: str


class _ModelDiagnosis(BaseModel):
    visual_evidence: list[str] = Field(
        description="Specific features visible in this photo and where they are."
    )
    insufficient_image: bool
    insufficient_reason: str | None
    material: Literal["fdm_print", "metal", "wood", "other", "unclear"]
    primary_mode: str
    confidence: Literal["high", "medium", "low"]
    probable_cause: str
    alternative_modes: list[_AlternativeMode]
    solutions: list[_ModelSolution]


class _InvalidOutput(Exception):
    """The model's answer broke a rule; worth one retry."""


SYSTEM_RULES = """\
You are a failure analyst. You are given one photograph of a broken mechanical \
part and sometimes a short note from the person who broke it. Say how it failed, \
based on what is visible on the fracture surface, and what to change.

Your answer is shown to engineers and competition judges who are looking at the \
same photo. A confident diagnosis the photo does not support is far worse than \
saying the photo is not good enough.

Work in this order.

1. Observe. Fill visual_evidence with features you can actually see in this \
photo: surface texture, distinct zones, colour, marks and which way they run, \
where the crack went relative to layers, grain, corners or holes, and any \
deformation. Each item names the feature and where it is in the frame. Do not \
list a feature because a mode would predict it; list it because it is there.

2. Judge the photo. If the fracture surface cannot be seen well enough to make \
observations like those (out of focus, too dark, blown out, too far away, the \
surface faces away from the camera, or there is no broken part), set \
insufficient_image to true and use insufficient_reason to say what is wrong and \
what photo would work. Then set primary_mode to "undetermined", confidence to \
"low", and leave alternative_modes and solutions empty. Do not guess.

3. Only then name the material and the mode. The mode has to follow from \
visual_evidence. The shape of the part and the user's note can help choose \
between modes the evidence leaves open and can inform the cause, but neither is \
ever enough on its own. If the note disagrees with the surface, trust the \
surface and say so in probable_cause. primary_mode must be exactly one of the \
allowed ids listed below. Use "other" when the surface is clear but no mode in \
the knowledge base fits. Confidence is "high" only when the features that set \
this mode apart from its most-confused neighbour are visible.

4. alternative_modes lists the other modes the evidence does not rule out, each \
with the reason it is less likely. Use mode ids.

5. Give 3 or 4 solutions, best first. At least one must remove the root cause \
(addresses_root_cause true) rather than only adding margin; "make it thicker" \
or "use a stronger material" on its own is addresses_root_cause false. Pros and \
cons must be real consequences for this part: weight, cost, print or machining \
time, complexity, lead time, changes to mating parts. Do not invent a con for \
balance.

6. When a solution adds or enlarges a fillet at a corner or notch, fill its \
fillet field: current radius (0 for a sharp corner), proposed radius, and the \
depth of the section at the notch, in millimetres. Take dimensions from the \
user's note when it gives them (dimensions_source "user_context"); otherwise \
estimate them from the photo ("estimated_from_image"). Otherwise fillet is \
null. Never state a stress concentration factor or a percentage stress \
reduction yourself. The system calculates those from the fillet field.
"""


# --- Loading ----------------------------------------------------------------


def load_knowledge_base(path: Path = KNOWLEDGE_PATH) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DiagnosisError(f"Knowledge base not readable at {path}: {exc}") from exc


def mode_ids(knowledge_base: str) -> list[str]:
    """Ids of the filled-in modes. TODO placeholders are not valid answers."""
    ids = re.findall(r"^## Mode:\s*(\S+)\s*$", knowledge_base, flags=re.MULTILINE)
    return [mode for mode in ids if not mode.upper().startswith("TODO")]


def load_examples(directory: Path = EXAMPLES_DIR) -> list[Example]:
    """Few-shot pairs: <name>.<image ext> plus <name>.json holding its label."""
    if not directory.is_dir():
        logger.warning("Examples directory %s does not exist", directory)
        return []
    examples = []
    for image_path in sorted(directory.iterdir()):
        if image_path.suffix.lower() not in EXAMPLE_IMAGE_SUFFIXES:
            continue
        label_path = image_path.with_suffix(".json")
        if not label_path.is_file():
            logger.warning("Example %s has no %s; skipped", image_path.name, label_path.name)
            continue
        image_b64, media_type = prepare_image(image_path.read_bytes())
        label = label_path.read_text(encoding="utf-8").strip()
        examples.append(Example(image_path.stem, image_b64, media_type, label))
    if not examples:
        logger.warning("No few-shot examples in %s", directory)
    return examples


def prepare_image(image_bytes: bytes) -> tuple[str, str]:
    """Decode any supported format (HEIC, PNG, JPEG, WebP) to an upright JPEG.

    The format is read from the bytes, never from a filename or content type.
    Returns (base64 data, media type).
    """
    if not image_bytes:
        raise DiagnosisError("The upload was empty.")
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DiagnosisError(
            "That file could not be read as an image. Use a JPEG, PNG or HEIC photo."
        ) from exc
    image.thumbnail((MAX_LONG_EDGE_PX, MAX_LONG_EDGE_PX))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii"), "image/jpeg"


# --- Request building -------------------------------------------------------


def _image_block(image_b64: str, media_type: str) -> dict[str, Any]:
    source = {"type": "base64", "media_type": media_type, "data": image_b64}
    return {"type": "image", "source": source}


def _text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _system_text(knowledge_base: str, allowed_modes: list[str]) -> str:
    return (
        f"{SYSTEM_RULES}\n"
        f"Allowed values for primary_mode: {', '.join(allowed_modes)}.\n"
        "Knowledge base blocks whose id starts with TODO are unfinished "
        "placeholders. Ignore them.\n\n"
        f"<knowledge_base>\n{knowledge_base}\n</knowledge_base>"
    )


def _user_content(
    examples: list[Example], image_b64: str, media_type: str, user_context: str | None
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if examples:
        content.append(
            _text_block(
                "Reference examples follow. Each is a photo labelled by an "
                "engineer. None of them is the photo to diagnose."
            )
        )
        for number, example in enumerate(examples, start=1):
            content.append(_text_block(f"Example {number} photo:"))
            content.append(_image_block(example.image_b64, example.media_type))
            content.append(_text_block(f"Example {number} label:\n{example.label}"))
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
    content.append(_text_block("Photo to diagnose:"))
    content.append(_image_block(image_b64, media_type))
    note = (user_context or "").strip()
    content.append(
        _text_block(f"Note from the user: {note}" if note else "The user gave no note.")
    )
    return content


def output_schema(allowed_modes: list[str]) -> dict[str, Any]:
    """JSON schema the API enforces. primary_mode is restricted to known ids."""
    schema = anthropic.transform_schema(_ModelDiagnosis)
    properties = {
        **schema["properties"],
        "primary_mode": {"type": "string", "enum": allowed_modes},
    }
    return {**schema, "properties": properties}


def _has_credentials() -> bool:
    load_dotenv(PROJECT_DIR / ".env")
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def make_client() -> anthropic.Anthropic:
    if not _has_credentials():
        raise DiagnosisError(
            "ANTHROPIC_API_KEY is not set. Add it to the .env file in the project "
            "folder and restart."
        )
    return anthropic.Anthropic(timeout=MODEL_TIMEOUT_S, max_retries=0)


# --- Calling the model ------------------------------------------------------


def _call_model(client: Any, request: dict[str, Any]) -> Any:
    """One API call. Every SDK failure becomes a DiagnosisError with a clear cause."""
    try:
        return client.beta.messages.create(**request)
    except anthropic.APITimeoutError as exc:
        raise DiagnosisError(
            f"The model did not answer within {MODEL_TIMEOUT_S:.0f} s. Try again."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise DiagnosisError(
            f"Could not reach the Anthropic API ({exc.__cause__ or exc}). "
            "Check the network connection."
        ) from exc
    except anthropic.AuthenticationError as exc:
        raise DiagnosisError("The Anthropic API key was rejected. Check .env.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise DiagnosisError(f"The API key lacks permission: {exc.message}") from exc
    except anthropic.RateLimitError as exc:
        raise DiagnosisError("Rate limited by the Anthropic API. Wait and retry.") from exc
    except anthropic.APIStatusError as exc:
        raise DiagnosisError(
            f"Anthropic API error {exc.status_code}: {exc.message}"
        ) from exc


def _parse_response(response: Any) -> _ModelDiagnosis:
    if response.stop_reason == "refusal":
        raise DiagnosisError("The model declined to analyse this image.")
    if response.stop_reason == "max_tokens":
        raise _InvalidOutput("the answer was cut off before it finished")
    text = next((block.text for block in response.content if block.type == "text"), None)
    if not text:
        raise _InvalidOutput("the answer contained no text")
    try:
        return _ModelDiagnosis.model_validate_json(text)
    except ValidationError as exc:
        raise _InvalidOutput(f"the answer did not match the schema: {exc}") from exc


def _check_rules(raw: _ModelDiagnosis, allowed_modes: list[str]) -> None:
    """Rules the prompt states, enforced here instead of trusted."""
    if raw.insufficient_image:
        if not (raw.insufficient_reason or "").strip():
            raise _InvalidOutput("insufficient_image is true but no reason was given")
        return
    if not any(item.strip() for item in raw.visual_evidence):
        raise _InvalidOutput("visual_evidence is empty; observations are required")
    if raw.primary_mode not in allowed_modes:
        raise _InvalidOutput(f"primary_mode {raw.primary_mode!r} is not an allowed id")
    if raw.primary_mode == MODE_UNDETERMINED:
        return
    if len(raw.solutions) < MIN_SOLUTIONS:
        raise _InvalidOutput(f"{len(raw.solutions)} solutions given; need {MIN_SOLUTIONS}")
    if not any(solution.addresses_root_cause for solution in raw.solutions):
        raise _InvalidOutput("no solution addresses the root cause")


def _fillet_computed(fillet: _FilletChange) -> dict[str, Any]:
    """Numbers for a fillet change. Calculated by mechanics.py, never by the model."""
    inputs = fillet.model_dump()
    base = {"type": "fillet_improvement", "calculated_by": "mechanics.py", "inputs": inputs}
    try:
        result = mechanics.fillet_improvement(
            fillet.current_radius_mm, fillet.proposed_radius_mm, fillet.section_depth_mm
        )
    except ValueError as exc:
        logger.warning("Fillet calculation rejected its inputs: %s", exc)
        return {**base, "error": f"Not calculated: {exc}"}
    return {**base, "geometry": mechanics.SUPPORTED_GEOMETRY, **result}


def _to_solution(raw: _ModelSolution) -> Solution:
    computed = _fillet_computed(raw.fillet) if raw.fillet else None
    return Solution(**raw.model_dump(exclude={"fillet"}), computed=computed)


def _to_diagnosis(raw: _ModelDiagnosis) -> Diagnosis:
    if raw.insufficient_image:
        return Diagnosis(
            material=raw.material,
            primary_mode=MODE_UNDETERMINED,
            confidence="low",
            visual_evidence=raw.visual_evidence,
            probable_cause=raw.probable_cause,
            alternative_modes=[],
            solutions=[],
            insufficient_image=True,
            insufficient_reason=raw.insufficient_reason,
        )
    return Diagnosis(
        material=raw.material,
        primary_mode=raw.primary_mode,
        confidence=raw.confidence,
        visual_evidence=raw.visual_evidence,
        probable_cause=raw.probable_cause,
        alternative_modes=[(alt.mode, alt.why_less_likely) for alt in raw.alternative_modes],
        solutions=[_to_solution(s) for s in raw.solutions[:MAX_SOLUTIONS]],
        insufficient_image=False,
        insufficient_reason=None,
    )


def _usage_totals(responses: list[Any]) -> dict[str, int]:
    def total(field: str) -> int:
        return sum(getattr(r.usage, field, 0) or 0 for r in responses)

    return {
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "cache_read_tokens": total("cache_read_input_tokens"),
        "cache_write_tokens": total("cache_creation_input_tokens"),
    }


def _cost_usd(usage: dict[str, int]) -> float:
    per_million = (
        usage["input_tokens"] * PRICE_INPUT
        + usage["output_tokens"] * PRICE_OUTPUT
        + usage["cache_read_tokens"] * PRICE_CACHE_READ
        + usage["cache_write_tokens"] * PRICE_CACHE_WRITE
    )
    return round(per_million / 1_000_000, 6)


def _build_request(image_bytes: bytes, user_context: str | None) -> tuple[dict, list[str]]:
    knowledge_base = load_knowledge_base()
    allowed_modes = [*mode_ids(knowledge_base), MODE_OTHER, MODE_UNDETERMINED]
    image_b64, media_type = prepare_image(image_bytes)
    system = _text_block(_system_text(knowledge_base, allowed_modes))
    request = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
        "system": [{**system, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {
                "role": "user",
                "content": _user_content(load_examples(), image_b64, media_type, user_context),
            }
        ],
        "output_config": {
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": output_schema(allowed_modes)},
        },
    }
    return request, allowed_modes


def _with_correction(request: dict[str, Any], problem: str) -> dict[str, Any]:
    """Same request with a note appended saying why the last answer was rejected."""
    user_message = request["messages"][0]
    note = _text_block(f"Your previous answer was rejected: {problem}. Answer again.")
    corrected = {**user_message, "content": [*user_message["content"], note]}
    return {**request, "messages": [corrected]}


def diagnose_with_meta(
    image_bytes: bytes, user_context: str | None = None, client: Any = None
) -> tuple[Diagnosis, RunMeta]:
    request, allowed_modes = _build_request(image_bytes, user_context)
    client = client or make_client()
    started = time.perf_counter()
    responses: list[Any] = []
    problem = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = _call_model(client, request)
        responses.append(response)
        try:
            raw = _parse_response(response)
            _check_rules(raw, allowed_modes)
        except _InvalidOutput as exc:
            problem = str(exc)
            logger.warning("Attempt %d rejected: %s", attempt, problem)
            request = _with_correction(request, problem)
            continue
        usage = _usage_totals(responses)
        meta = RunMeta(
            model=getattr(response, "model", MODEL),
            latency_s=round(time.perf_counter() - started, 2),
            attempts=attempt,
            cost_usd=_cost_usd(usage),
            **usage,
        )
        return _to_diagnosis(raw), meta
    raise DiagnosisError(
        f"The model returned an unusable answer {MAX_ATTEMPTS} times ({problem}). "
        "Try again, or try a different photo."
    )


def diagnose(image_bytes: bytes, user_context: str | None = None) -> Diagnosis:
    diagnosis, _ = diagnose_with_meta(image_bytes, user_context)
    return diagnosis


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose a fracture photo.")
    parser.add_argument("image", type=Path)
    parser.add_argument("--context", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        diagnosis, meta = diagnose_with_meta(args.image.read_bytes(), args.context)
    except (DiagnosisError, OSError) as exc:
        sys.stderr.write(f"FAILED: {exc}\n")
        return 1
    output = {"diagnosis": diagnosis.model_dump(), "meta": asdict(meta)}
    sys.stdout.write(json.dumps(output, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
