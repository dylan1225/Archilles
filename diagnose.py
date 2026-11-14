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
import math
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
import uncertainty
from uncertainty import Consistency

logger = logging.getLogger("postmortem.diagnose")
pillow_heif.register_heif_opener()

PROJECT_DIR = Path(__file__).resolve().parent
KNOWLEDGE_PATH = PROJECT_DIR / "knowledge" / "failure_modes.md"
EXAMPLES_DIR = PROJECT_DIR / "knowledge" / "examples"
CACHE_DIR = PROJECT_DIR / "cache" / "diagnoses"
EXAMPLE_IMAGE_SUFFIXES =(".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")

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
MODE_NOT_APPLICABLE = uncertainty.NOT_APPLICABLE

# Self-consistency: the same photo is diagnosed this many times, independently
# and in parallel, at a sampling temperature high enough that a guess would
# come out differently each time.
N_SAMPLES = 3
SAMPLE_TEMPERATURE = 0.7
MIN_SOLUTIONS = 3
MAX_SOLUTIONS = 4

# Paid-tier list price in USD per million tokens for MODEL. Used only to report
# cost; on the free tier the real charge is zero. Thinking tokens bill as output.
PRICE_INPUT = 1.50
PRICE_OUTPUT = 7.50

_CALL_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="model-call"
)
# Separate pool: each sample blocks on a _CALL_POOL call, so sharing one pool
# could deadlock.
_SAMPLE_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=6, thread_name_prefix="sample"
)


class DiagnosisError(Exception):
    """A failure with a message that is safe and useful to show the user."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


# --- Public schema ----------------------------------------------------------


SpecimenShape = Literal["round_rod", "rect_bar", "flat_plate", "l_bracket"]
SpecimenFeature = Literal["none", "hole", "notch", "sharp_corner"]
LoadCase = Literal["bending_span", "cantilever", "tension", "unknown"]
DimensionsSource = Literal["user_context", "estimated_from_image"]
# "user_measured" is never produced by the model: it marks numbers recomputed
# from values the user typed in on the result card.
GeometrySource = Literal["user_context", "estimated_from_image", "unknown"]
GEOMETRY_FIELDS = ("current_radius_mm", "proposed_radius_mm", "section_depth_mm")
USER_MEASURED = "user_measured"
VisualKind = Literal[
    "add_support",
    "move_load",
    "thicken_section",
    "change_material",
    "add_fillet",
    "reinforce_feature",
    "relocate_feature",
    "densify_infill",
    "reorient_layers",
    "add_rib",
    "surface_treatment",
    "other",
]

# Sane bounds for the schematic 3D model. Values outside are clamped, not
# rejected: a bad 3D hint must never cost a slow retry of the whole diagnosis.
SPECIMEN_MIN_MM = 0.5
SPECIMEN_MAX_MM = 2000.0
BREAK_POSITION_MIN = 0.05
BREAK_POSITION_MAX = 0.95
VISUAL_LABEL_MAX_CHARS = 80


class Specimen(BaseModel):
    """Simplified shape of the broken part, for the schematic 3D exhibit."""

    shape: SpecimenShape
    length_mm: float
    width_mm: float
    thickness_mm: float
    break_position: float
    feature: SpecimenFeature
    load_case: LoadCase
    dimensions_source: DimensionsSource


class Visual(BaseModel):
    """How a solution is drawn on the 3D exhibit."""

    kind: VisualKind
    label: str


class Solution(BaseModel):
    title: str
    description: str
    pros: list[str]
    cons: list[str]
    effort: Literal["trivial", "moderate", "significant"]
    addresses_root_cause: bool
    computed: dict | None
    visual: Visual | None = None
    geometry: dict | None = None


class RejectedAlternative(BaseModel):
    """The second most likely mode and the observed feature that rules it out."""

    mode: str
    visual_feature: str
    reason: str


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
    specimen: Specimen | None = None
    # Defaults keep results saved before these fields existed loadable.
    is_failure_analysis_applicable: bool = True
    not_applicable_reason: str | None = None
    geometric_features: list[str] = []
    rejected_alternative: RejectedAlternative | None = None
    consistency: Consistency | None = None
    warnings: list[str] = []


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


class _Geometry(BaseModel):
    current_radius_mm: float | None = Field(
        description="0 for a sharp corner; null if it cannot be estimated."
    )
    proposed_radius_mm: float | None = Field(description="null if it cannot be estimated.")
    section_depth_mm: float | None = Field(
        description="Depth of the section at the notch; null if it cannot be estimated."
    )
    dimensions_source: GeometrySource


class _Specimen(BaseModel):
    shape: SpecimenShape = Field(description="Closest simple shape to the broken part.")
    length_mm: float = Field(description="Longest dimension of the intact part.")
    width_mm: float = Field(description="Width, or the diameter for a round_rod.")
    thickness_mm: float
    break_position: float = Field(
        description="Where the break is along the length, 0 = one end, 1 = the other."
    )
    feature: SpecimenFeature = Field(description="What sits at the break, if anything.")
    load_case: LoadCase
    dimensions_source: DimensionsSource


class _Visual(BaseModel):
    kind: VisualKind = Field(description="The change this solution makes to the part.")
    label: str = Field(description="Caption of at most 6 words for the 3D callout.")


class _ModelSolution(BaseModel):
    title: str
    description: str
    pros: list[str]
    cons: list[str]
    effort: Literal["trivial", "moderate", "significant"]
    addresses_root_cause: bool
    geometry: _Geometry | None = Field(
        description=(
            "Only when this solution adds or enlarges a fillet, increases a radius "
            "or softens a corner; else null."
        )
    )
    visual: _Visual


class _AlternativeMode(BaseModel):
    mode: str
    why_less_likely: str


class _RejectedAlternative(BaseModel):
    mode: str = Field(
        description=(
            "The second most likely failure mode: a mode id from the knowledge base "
            "when one fits, otherwise its usual engineering name."
        )
    )
    visual_feature: str = Field(
        description="The specific feature listed in visual_evidence that rules it out, and where it is."
    )
    reason: str = Field(
        description="What that mode would show instead, and why the cited feature contradicts it."
    )


class _ModelDiagnosis(BaseModel):
    visual_evidence: list[str] = Field(
        description="Specific features visible in this photo and where they are."
    )
    is_failure_analysis_applicable: bool = Field(
        description=(
            "false if the photo shows an intact part, something that is not a "
            "mechanical part, or anything else that cannot be analysed as a fracture."
        )
    )
    not_applicable_reason: str | None = Field(
        description=(
            "When is_failure_analysis_applicable is false: what the photo shows and "
            "why there is nothing to analyse. Otherwise null."
        )
    )
    insufficient_image: bool
    insufficient_reason: str | None
    geometric_features: list[str] = Field(
        description=(
            "Stress raisers visible at or next to the break (hole, notch, sharp inside "
            "corner, thread root, keyway, abrupt change of section), each with where it "
            "is. Empty if none is visible."
        )
    )
    material: Literal["fdm_print", "metal", "wood", "other", "unclear"]
    primary_mode: str
    confidence: Literal["high", "medium", "low"]
    probable_cause: str
    rejected_alternative: _RejectedAlternative | None
    specimen: _Specimen | None = Field(
        description="Simplified shape of the part for a 3D sketch; null if the image is insufficient."
    )
    alternative_modes: list[_AlternativeMode]
    solutions: list[_ModelSolution]


class _InvalidOutput(Exception):
    """The model's answer broke a rule; worth one retry."""


SYSTEM_RULES = """\
You are a failure analyst. You are given one photograph that may show a broken \
mechanical part, and sometimes a short note from the person who sent it. If \
something has broken, say how it failed, based on what is visible on the \
fracture surface, and what to change.

Your answer is shown to engineers and competition judges who are looking at the \
same photo, and who will deliberately test you with photos of things that are \
not broken. A confident diagnosis the photo does not support is far worse than \
saying the photo is not good enough.

THE WORST ERROR THIS SYSTEM CAN MAKE IS TO DIAGNOSE A FAILURE IN A PHOTO OF \
SOMETHING THAT IS NOT BROKEN. Inventing a fracture, a failure mode or a cause \
for an intact part is worse than every other mistake, including missing a real \
failure. If you cannot see a crack, a break or a fracture surface in the photo, \
there is no failure to analyse.

Work in this order.

1. Observe. Fill visual_evidence with features you can actually see in this \
photo: what the object is, surface texture, distinct zones, colour, marks and \
which way they run, where any crack went relative to layers, grain, corners or \
holes, and any deformation. Each item names the feature and where it is in the \
frame. Do not list a feature because a mode would predict it; list it because \
it is there.

2. Decide whether there is a failure to analyse at all. Set \
is_failure_analysis_applicable to false when the photo shows:
- an intact part, with no crack, break or fracture surface, even if it looks \
worn, dirty, scratched, unusual or badly made;
- an object that is not a mechanical part at all (a person, animal, food, plant, \
screen, document, room or landscape);
- anything else you cannot analyse as a fracture, such as a 3D print that failed \
while printing rather than breaking in use.
A note saying the part broke never makes a photo of an unbroken part \
applicable: trust the photo. When it is false, say plainly in \
not_applicable_reason what the photo shows and that there is nothing to \
analyse. Set primary_mode to "not_applicable", confidence to how sure you are \
that nothing in the photo is broken, insufficient_image to false, specimen and \
rejected_alternative to null, and leave geometric_features, alternative_modes \
and solutions empty. Give no partial or hedged diagnosis: no likely cause, no \
"if it had broken", no solutions.

3. Judge the photo. If something is broken but the fracture surface cannot be \
seen well enough to make observations like those in step 1 (out of focus, too \
dark, blown out, too far away, or the surface faces away from the camera), set \
insufficient_image to true and use insufficient_reason to say what is wrong and \
what photo would work. Then set primary_mode to "undetermined", confidence to \
"low", rejected_alternative to null, and leave alternative_modes and solutions \
empty. Do not guess.

4. List geometric_features: every stress raiser you can see at or next to the \
break (a hole, notch, sharp inside corner, thread root, keyway, abrupt change \
of section, weld toe), each with where it is. If there is none, leave the list \
empty; never add one because the mode you have in mind needs it.

5. Only then name the material and the mode. The mode has to follow from \
visual_evidence. The shape of the part and the user's note can help choose \
between modes the evidence leaves open and can inform the cause, but neither is \
ever enough on its own. If the note disagrees with the surface, trust the \
surface and say so in probable_cause. primary_mode must be exactly one of the \
allowed ids listed below. Use "other" when the surface is clear but no mode in \
the knowledge base fits. Confidence is "high" only when the features that set \
this mode apart from its most-confused neighbour are visible.

6. Name the single next most likely mode in rejected_alternative and rule it \
out with evidence. visual_feature must be a specific feature you listed in \
visual_evidence, with where it is (for example "long splinters pulled out along \
the grain on the lower piece"). reason must say what the alternative mode would \
show instead and why that feature contradicts it. A vague rejection such as \
"the evidence does not match", "less likely" or "not consistent with the \
photo" is not acceptable and is treated as a sign of a made-up answer. If no \
visible feature rules the alternative out, you cannot tell the two apart: say \
so in reason and set confidence to "low". Use a mode id from the knowledge base \
when one fits; otherwise the mode's usual engineering name.

7. alternative_modes lists any further modes the evidence does not rule out, \
each with the reason it is less likely.

8. Give 3 or 4 solutions, best first. At least one must remove the root cause \
(addresses_root_cause true) rather than only adding margin; "make it thicker" \
or "use a stronger material" on its own is addresses_root_cause false. Pros and \
cons must be real consequences for this part: weight, cost, print or machining \
time, complexity, lead time, changes to mating parts. Do not invent a con for \
balance.

9. When a solution adds or enlarges a fillet, increases a radius or softens a \
corner at a corner or notch, fill its geometry field: current radius (0 for a \
sharp corner), proposed radius, and the depth of the section at the notch, in \
millimetres. Take dimensions from the user's note when it gives them \
(dimensions_source "user_context"); otherwise estimate them from the photo \
("estimated_from_image"). If a value cannot be estimated from the photo or the \
note, set it to null and dimensions_source to "unknown": never invent a number. \
For every other solution, including a thicker wall or section, geometry is \
null. Never state a stress concentration factor or a percentage stress \
reduction yourself. The system calculates those from the geometry field.

10. For a schematic 3D sketch, fill specimen: the closest simple shape \
(round_rod, rect_bar, flat_plate or l_bracket), the intact part's length, width \
(diameter for a rod) and thickness in millimetres, where along the length it \
broke (0 to 1), what feature sits at the break, and how it was loaded. Take \
dimensions from the user's note when given, otherwise estimate them from the \
photo and say so in dimensions_source. For every solution, set visual.kind to \
the change that best shows it on the part ("other" if none fits) and \
visual.label to a caption of at most six words. When the image is \
insufficient, specimen is null.
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
    sampling = f"samples={N_SAMPLES};temperature={SAMPLE_TEMPERATURE}"
    for part in (MODEL, sampling, SYSTEM_RULES, load_knowledge_base()):
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
    if not raw.is_failure_analysis_applicable:
        if not (raw.not_applicable_reason or "").strip():
            raise _InvalidOutput(
                "is_failure_analysis_applicable is false but not_applicable_reason "
                "does not say what the photo shows"
            )
        return
    if raw.primary_mode == MODE_NOT_APPLICABLE:
        raise _InvalidOutput(
            'primary_mode is "not_applicable" but is_failure_analysis_applicable is true'
        )
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
    problem = uncertainty.rejected_alternative_problem(
        raw.rejected_alternative, raw.primary_mode, raw.visual_evidence
    )
    if problem:
        raise _InvalidOutput(problem)


def fillet_computed(
    current_radius_mm: float | None,
    proposed_radius_mm: float | None,
    section_depth_mm: float | None,
    dimensions_source: str,
) -> dict[str, Any]:
    """Before/after numbers for a fillet change. Calculated by mechanics.py,
    never by the model.

    Missing dimensions are not guessed: the result says which ones are needed
    (`needs_input`) so the user can type them in. Impossible dimensions are
    reported in `error` rather than hidden.
    """
    inputs = {
        "current_radius_mm": current_radius_mm,
        "proposed_radius_mm": proposed_radius_mm,
        "section_depth_mm": section_depth_mm,
        "dimensions_source": dimensions_source,
    }
    base = {"type": "fillet_improvement", "calculated_by": "mechanics.py", "inputs": inputs}
    missing = [name for name in GEOMETRY_FIELDS if inputs[name] is None]
    if missing:
        return {**base, "needs_input": missing}
    try:
        result = mechanics.fillet_improvement(
            current_radius_mm, proposed_radius_mm, section_depth_mm
        )
    except ValueError as exc:
        logger.info("Fillet calculation rejected its inputs: %s", exc)
        return {**base, "error": f"Not calculated: {exc}"}
    return {
        **base,
        "geometry": mechanics.SUPPORTED_GEOMETRY,
        "kt_before": result["kt_current"],
        "kt_after": result["kt_proposed"],
        "r_over_d_before": result["r_over_d_current"],
        "r_over_d_after": result["r_over_d_proposed"],
        "stress_reduction_pct": result["peak_stress_reduction_pct"],
        "reduction_is_lower_bound": result["reduction_is_lower_bound"],
        "is_extrapolated": result["is_extrapolated"],
        "is_capped": result["is_capped"],
        "basis": result["basis"],
    }


def _geometry_computed(geometry: _Geometry) -> dict[str, Any]:
    source = geometry.dimensions_source
    values = [getattr(geometry, name) for name in GEOMETRY_FIELDS]
    if any(value is None for value in values):
        source = "unknown"
    return fillet_computed(*values, dimensions_source=source)


def _clamp(value: float, low: float, high: float, fallback: float) -> float:
    if not math.isfinite(value):
        return fallback
    return min(max(value, low), high)


def _to_specimen(raw: _Specimen | None) -> Specimen | None:
    """The model's shape hint, clamped into a range the 3D sketch can draw."""
    if raw is None:
        return None
    length = _clamp(raw.length_mm, SPECIMEN_MIN_MM, SPECIMEN_MAX_MM, 100.0)
    width = _clamp(raw.width_mm, SPECIMEN_MIN_MM, length, min(10.0, length))
    thickness = _clamp(raw.thickness_mm, SPECIMEN_MIN_MM, length, min(5.0, length))
    if raw.shape == "round_rod":
        thickness = width
    return Specimen(
        shape=raw.shape,
        length_mm=length,
        width_mm=width,
        thickness_mm=thickness,
        break_position=_clamp(raw.break_position, BREAK_POSITION_MIN, BREAK_POSITION_MAX, 0.5),
        feature=raw.feature,
        load_case=raw.load_case,
        dimensions_source=raw.dimensions_source,
    )


def _to_visual(raw: _Visual) -> Visual:
    label = " ".join(raw.label.split())[:VISUAL_LABEL_MAX_CHARS]
    return Visual(kind=raw.kind, label=label)


def _to_solution(raw: _ModelSolution) -> Solution:
    geometry = raw.geometry
    return Solution(
        **raw.model_dump(exclude={"geometry", "visual"}),
        computed=_geometry_computed(geometry) if geometry else None,
        visual=_to_visual(raw.visual),
        geometry=geometry.model_dump() if geometry else None,
    )


def _to_rejected(raw: _RejectedAlternative | None) -> RejectedAlternative | None:
    return RejectedAlternative(**raw.model_dump()) if raw else None


def _to_diagnosis(raw: _ModelDiagnosis) -> Diagnosis:
    if not raw.is_failure_analysis_applicable:
        return Diagnosis(
            material=raw.material,
            primary_mode=MODE_NOT_APPLICABLE,
            confidence=raw.confidence,
            visual_evidence=raw.visual_evidence,
            probable_cause="",
            alternative_modes=[],
            solutions=[],
            insufficient_image=False,
            insufficient_reason=None,
            is_failure_analysis_applicable=False,
            not_applicable_reason=raw.not_applicable_reason,
        )
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
        specimen=_to_specimen(raw.specimen),
        geometric_features=[f for f in raw.geometric_features if f.strip()],
        rejected_alternative=_to_rejected(raw.rejected_alternative),
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


def _build_request(
    image_bytes: bytes, user_context: str | None, temperature: float | None = None
) -> tuple[dict, list[str]]:
    knowledge_base = load_knowledge_base()
    allowed_modes = [
        *mode_ids(knowledge_base), MODE_OTHER, MODE_UNDETERMINED, MODE_NOT_APPLICABLE
    ]
    image_b64, media_type = prepare_image(image_bytes)
    generation_config: dict[str, Any] = {"max_output_tokens": MAX_OUTPUT_TOKENS}
    if temperature is not None:
        generation_config["temperature"] = temperature
    request = {
        "model": MODEL,
        "system_instruction": _system_text(knowledge_base, allowed_modes),
        "input": _user_content(load_examples(), image_b64, media_type, user_context),
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": output_schema(allowed_modes),
        },
        "generation_config": generation_config,
    }
    return request, allowed_modes


def _with_correction(request: dict[str, Any], problem: str) -> dict[str, Any]:
    """Same request with a note appended saying why the last answer was rejected."""
    note = _text_block(f"Your previous answer was rejected: {problem}. Answer again.")
    return {**request, "input": [*request["input"], note]}


@dataclass(frozen=True)
class _Sample:
    diagnosis: Diagnosis
    responses: list[Any]
    attempts: int


class _SampleFailed(DiagnosisError):
    """One run failed. Carries the responses it was billed for."""

    def __init__(self, cause: DiagnosisError, responses: list[Any]) -> None:
        super().__init__(str(cause), cause.status_code)
        self.responses = responses


def _sample(
    client: Any, request: dict[str, Any], allowed_modes: list[str], started: float
) -> _Sample:
    """One independent diagnosis: a call, plus one retry on a rule-breaking answer."""
    responses: list[Any] = []
    problem = ""
    try:
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
            return _Sample(_to_diagnosis(raw), responses, attempt)
        raise DiagnosisError(
            f"The model returned an unusable answer {MAX_ATTEMPTS} times ({problem}). "
            "Try again, or try a different photo."
        )
    except DiagnosisError as exc:
        raise _SampleFailed(exc, responses) from exc


def _run_samples(
    client: Any, request: dict[str, Any], allowed_modes: list[str], n_samples: int, started: float
) -> tuple[list[_Sample], list[_SampleFailed]]:
    futures = [
        _SAMPLE_POOL.submit(_sample, client, request, allowed_modes, started)
        for _ in range(n_samples)
    ]
    completed, failed = [], []
    for future in futures:
        try:
            completed.append(future.result())
        except _SampleFailed as exc:
            logger.warning("A run failed: %s", exc)
            failed.append(exc)
    return completed, failed


def diagnose_with_meta(
    image_bytes: bytes,
    user_context: str | None = None,
    client: Any = None,
    n_samples: int = N_SAMPLES,
) -> tuple[Diagnosis, RunMeta]:
    """Diagnose one photo with n_samples independent runs in parallel.

    With more than one run, runs use SAMPLE_TEMPERATURE and the answers are
    combined by majority with a Consistency block (uncertainty.combine): any
    disagreement lowers confidence to low and is reported, never hidden.
    Every result then goes through the geometry cross-check.
    """
    if n_samples < 1:
        raise ValueError("n_samples must be at least 1")
    temperature = SAMPLE_TEMPERATURE if n_samples > 1 else None
    request, allowed_modes = _build_request(image_bytes, user_context, temperature)
    client = client or make_client()
    started = time.perf_counter()
    completed, failed = _run_samples(client, request, allowed_modes, n_samples, started)
    if not completed:
        raise DiagnosisError(str(failed[0]), failed[0].status_code)
    diagnoses = [sample.diagnosis for sample in completed]
    combined = (
        diagnoses[0] if n_samples == 1 else uncertainty.combine(diagnoses, len(failed))
    )
    responses = [r for sample in completed for r in sample.responses]
    responses += [r for failure in failed for r in failure.responses]
    usage = _usage_totals(responses)
    meta = RunMeta(
        model=getattr(completed[0].responses[-1], "model", MODEL),
        latency_s=round(time.perf_counter() - started, 2),
        attempts=sum(sample.attempts for sample in completed),
        cost_usd=_cost_usd(usage),
        **usage,
    )
    return uncertainty.geometry_cross_check(combined), meta


def _cache_path(image_bytes: bytes, user_context: str | None, n_samples: int) -> Path:
    hasher = hashlib.sha256(image_bytes)
    hasher.update(f"\0samples={n_samples}".encode("ascii"))
    hasher.update(b"\0" + (user_context or "").strip().encode("utf-8"))
    hasher.update(b"\0" + config_fingerprint().encode("ascii"))
    return CACHE_DIR / f"{hasher.hexdigest()}.json"


def _read_cache(path: Path) -> tuple[Diagnosis, RunMeta] | None:
    if not path.is_file():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        return Diagnosis.model_validate(stored["diagnosis"]), RunMeta(**stored["meta"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Ignoring unreadable cache file %s: %s", path.name, exc)
        return None


def diagnose_cached(
    image_bytes: bytes,
    user_context: str | None = None,
    client: Any = None,
    n_samples: int = N_SAMPLES,
) -> tuple[Diagnosis, RunMeta, bool]:
    """diagnose_with_meta, but the same photo, note and configuration is only
    ever sent to the model once. Returns (diagnosis, meta, was_cached).

    Failures are never cached. A cache that cannot be written is logged and the
    fresh answer is still returned.
    """
    path = _cache_path(image_bytes, user_context, n_samples)
    hit = _read_cache(path)
    if hit:
        return (*hit, True)
    result, meta = diagnose_with_meta(image_bytes, user_context, client, n_samples)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        body = {"diagnosis": result.model_dump(mode="json"), "meta": asdict(meta)}
        path.write_text(json.dumps(body, indent=1), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write cache file %s: %s", path, exc)
    return result, meta, False


def diagnose(
    image_bytes: bytes, user_context: str | None = None, n_samples: int = N_SAMPLES
) -> Diagnosis:
    diagnosis, _ = diagnose_with_meta(image_bytes, user_context, n_samples=n_samples)
    return diagnosis


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose a fracture photo.")
    parser.add_argument("image", type=Path)
    parser.add_argument("--context", default=None)
    parser.add_argument("--samples", type=int, default=N_SAMPLES, help="independent runs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        diagnosis, meta, cached = diagnose_cached(
            args.image.read_bytes(), args.context, n_samples=args.samples
        )
    except (DiagnosisError, OSError) as exc:
        sys.stderr.write(f"FAILED: {exc}\n")
        return 1
    output = {"diagnosis": diagnosis.model_dump(mode="json"), "meta": asdict(meta), "cached": cached}
    sys.stdout.write(json.dumps(output, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
