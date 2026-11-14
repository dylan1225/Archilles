"""Vision diagnosis of a fracture photo against the knowledge base.

Usage:  python diagnose.py <image> [--context "what happened"]

The model observes and classifies. It never produces stress numbers: those come
from mechanics.py and are attached to a solution's `computed` field afterwards.
"""

import argparse
import base64
import concurrent.futures
import hashlib
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

import pillow_heif
from dotenv import load_dotenv
from google import genai
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError

import mechanics

logger = logging.getLogger("postmortem.diagnose")
pillow_heif.register_heif_opener()

PROJECT_DIR = Path(__file__).resolve().parent
KNOWLEDGE_PATH = PROJECT_DIR / "knowledge" / "failure_modes.md"
EXAMPLES_DIR = PROJECT_DIR / "knowledge" / "examples"
EXAMPLE_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")

MODEL = "gemini-4-hydrogen"
API_KEY_VARIABLES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
MAX_OUTPUT_TOKENS = 16000
# Wall-clock budget for one diagnosis, including the SDK's own retries and our
# retry on a malformed answer. Enforced here, not left to the SDK.
MODEL_TIMEOUT_S = 120.0
MAX_ATTEMPTS = 2  # one call, plus one retry on malformed output
STATUS_COMPLETED = "completed"
STATUS_INCOMPLETE = "incomplete"

MAX_LONG_EDGE_PX = 2000
JPEG_QUALITY = 90

MODE_OTHER = "other"
MODE_UNDETERMINED = "undetermined"
MIN_SOLUTIONS = 3
MAX_SOLUTIONS = 4

# Paid-tier list price in USD per million tokens for MODEL. Used only to report
# cost; on the free tier the real charge is zero. Thinking tokens bill as output.
PRICE_INPUT = 1.50
PRICE_OUTPUT = 7.50

_CALL_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="model-call"
)


class DiagnosisError(Exception):
    """A failure with a message that is safe and useful to show the user."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


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
    thinking_tokens: int
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
        raise DiagnosisError("The upload was empty.", status_code=400)
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DiagnosisError(
            "That file could not be read as an image. Use a JPEG, PNG or HEIC photo.",
            status_code=400,
        ) from exc
    image.thumbnail((MAX_LONG_EDGE_PX, MAX_LONG_EDGE_PX))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii"), "image/jpeg"


# --- Request building -------------------------------------------------------


def _image_block(image_b64: str, media_type: str) -> dict[str, Any]:
    return {"type": "image", "data": image_b64, "mime_type": media_type}


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
    content.append(_text_block("Photo to diagnose:"))
    content.append(_image_block(image_b64, media_type))
    note = (user_context or "").strip()
    content.append(
        _text_block(f"Note from the user: {note}" if note else "The user gave no note.")
    )
    return content


def output_schema(allowed_modes: list[str]) -> dict[str, Any]:
    """JSON schema the API enforces. primary_mode is restricted to known ids."""
    schema = _ModelDiagnosis.model_json_schema()
    properties = {
        **schema["properties"],
        "primary_mode": {"type": "string", "enum": allowed_modes},
    }
    return {**schema, "properties": properties}


def config_fingerprint() -> str:
    """Hash of everything besides the photo that shapes an answer.

    Cache keys include it, so editing the knowledge base, the examples, the
    rules or the model invalidates old results instead of silently reusing them.
    """
    hasher = hashlib.sha256()
    for part in (MODEL, SYSTEM_RULES, load_knowledge_base()):
        hasher.update(part.encode("utf-8") + b"\0")
    for example in load_examples():
        hasher.update(example.image_b64.encode("ascii") + b"\0")
        hasher.update(example.label.encode("utf-8") + b"\0")
    return hasher.hexdigest()


def _has_credentials() -> bool:
    env_path = PROJECT_DIR / ".env"
    if env_path.is_dir():
        raise DiagnosisError(
            f"{env_path} is a folder, not a file. Replace it with a text file "
            f"containing {API_KEY_VARIABLES[0]}=...",
            status_code=503,
        )
    try:
        load_dotenv(env_path)
    except OSError as exc:
        raise DiagnosisError(
            f"{env_path} exists but could not be read ({exc}).", status_code=503
        ) from exc
    return any(os.environ.get(name) for name in API_KEY_VARIABLES)


def make_client() -> genai.Client:
    if not _has_credentials():
        raise DiagnosisError(
            f"{API_KEY_VARIABLES[0]} is not set. Add it to the .env file in the "
            "project folder and restart.",
            status_code=503,
        )
    api_key = next(os.environ[name] for name in API_KEY_VARIABLES if os.environ.get(name))
    return genai.Client(api_key=api_key)


# --- Calling the model ------------------------------------------------------


def _timeout_error() -> DiagnosisError:
    return DiagnosisError(
        f"The model did not answer within {MODEL_TIMEOUT_S:.0f} s. The Gemini API "
        "may be overloaded. Try again.",
        status_code=504,
    )


def _api_error(exc: Exception) -> DiagnosisError:
    """Turn any SDK failure into a message that names the cause."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    detail = str(getattr(exc, "message", "") or exc)[:300]
    if "Timeout" in name:
        return _timeout_error()
    if "Connection" in name:
        return DiagnosisError(
            f"Could not reach the Gemini API ({exc.__cause__ or exc}). Check the network."
        )
    if status in (401, 403):
        return DiagnosisError(f"The Gemini API key was rejected ({status}). Check .env.")
    if status == 429:
        return DiagnosisError(
            "The Gemini API rate limit or free-tier quota was hit (429). Wait a "
            "minute and retry."
        )
    if isinstance(status, int) and status >= 500:
        return DiagnosisError(
            f"The Gemini API is unavailable or overloaded ({status}). Try again."
        )
    return DiagnosisError(f"Gemini API error ({name}, status {status}): {detail}")


def _call_model(client: Any, request: dict[str, Any], budget_s: float) -> Any:
    """One API call with a hard wall-clock limit.

    The SDK retries 503s on its own, slowly. The call runs on a worker thread so
    the limit holds whatever the SDK does. Every failure becomes a DiagnosisError.
    """
    if budget_s <= 0:
        raise _timeout_error()
    future = _CALL_POOL.submit(client.interactions.create, **request, timeout=budget_s)
    try:
        return future.result(timeout=budget_s)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise _timeout_error() from exc
    except Exception as exc:  # noqa: BLE001 - converted to a visible error, never swallowed
        logger.warning("Model call failed: %s: %s", type(exc).__name__, exc)
        raise _api_error(exc) from exc


def _parse_response(response: Any) -> _ModelDiagnosis:
    status = getattr(response, "status", None)
    if status == STATUS_INCOMPLETE:
        raise _InvalidOutput("the answer was cut off before it finished")
    if status != STATUS_COMPLETED:
        errors = getattr(response, "errors", None)
        raise DiagnosisError(
            f"The model did not complete the analysis (status: {status}"
            + (f", {errors}" if errors else "")
            + "). It may have declined this image."
        )
    text = getattr(response, "output_text", None)
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
        "input_tokens": total("total_input_tokens"),
        "output_tokens": total("total_output_tokens"),
        "thinking_tokens": total("total_thought_tokens"),
    }


def _cost_usd(usage: dict[str, int]) -> float:
    per_million = usage["input_tokens"] * PRICE_INPUT + (
        usage["output_tokens"] + usage["thinking_tokens"]
    ) * PRICE_OUTPUT
    return round(per_million / 1_000_000, 6)


def _build_request(image_bytes: bytes, user_context: str | None) -> tuple[dict, list[str]]:
    knowledge_base = load_knowledge_base()
    allowed_modes = [*mode_ids(knowledge_base), MODE_OTHER, MODE_UNDETERMINED]
    image_b64, media_type = prepare_image(image_bytes)
    request = {
        "model": MODEL,
        "system_instruction": _system_text(knowledge_base, allowed_modes),
        "input": _user_content(load_examples(), image_b64, media_type, user_context),
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": output_schema(allowed_modes),
        },
        "generation_config": {"max_output_tokens": MAX_OUTPUT_TOKENS},
    }
    return request, allowed_modes


def _with_correction(request: dict[str, Any], problem: str) -> dict[str, Any]:
    """Same request with a note appended saying why the last answer was rejected."""
    note = _text_block(f"Your previous answer was rejected: {problem}. Answer again.")
    return {**request, "input": [*request["input"], note]}


def diagnose_with_meta(
    image_bytes: bytes, user_context: str | None = None, client: Any = None
) -> tuple[Diagnosis, RunMeta]:
    request, allowed_modes = _build_request(image_bytes, user_context)
    client = client or make_client()
    started = time.perf_counter()
    responses: list[Any] = []
    problem = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = MODEL_TIMEOUT_S - (time.perf_counter() - started)
        response = _call_model(client, request, remaining)
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
