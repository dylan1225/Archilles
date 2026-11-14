"""FastAPI app: serves the UI and the diagnose endpoints.

Run:  venv\\Scripts\\python -m uvicorn main:app --port 8000

Every failure returns JSON of the form {"error": "..."} with a real status code,
so the page can always show what went wrong.
"""

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import diagnose
import materials

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("postmortem.main")

PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "static"
INDEX_PATH = STATIC_DIR / "index.html"
BUNDLED_EXAMPLES_DIR = STATIC_DIR / "examples"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
EXAMPLE_COUNT = 3
# Run details (latency, model, cost) travel in a header so the body stays a
# plain Diagnosis.
META_HEADER = "X-Postmortem-Meta"

app = FastAPI(title="Postmortem")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _diagnosis_response(diagnosis: dict[str, Any], meta: dict[str, Any]) -> JSONResponse:
    return JSONResponse(diagnosis, headers={META_HEADER: json.dumps(meta)})


@app.exception_handler(diagnose.DiagnosisError)
def handle_diagnosis_error(_: Request, exc: diagnose.DiagnosisError) -> JSONResponse:
    logger.warning("Diagnosis failed: %s", exc)
    return _error(exc.status_code, str(exc))


@app.exception_handler(materials.MaterialError)
def handle_material_error(_: Request, exc: materials.MaterialError) -> JSONResponse:
    return _error(400, str(exc))


@app.exception_handler(RequestValidationError)
def handle_bad_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = ", ".join(str(error["loc"][-1]) for error in exc.errors())
    return _error(422, f"Bad request: missing or invalid field(s): {fields}.")


@app.exception_handler(Exception)
def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error")
    return _error(500, f"Internal error ({type(exc).__name__}: {exc}). See server log.")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(INDEX_PATH)


@app.post("/diagnose")
def post_diagnose(
    image: UploadFile = File(...),
    context: str | None = Form(None),
    material_id: str | None = Form(None),
) -> JSONResponse:
    material = materials.get(material_id) if material_id else None  # 400 if unknown
    data = image.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return _error(413, f"That photo is larger than {limit_mb} MB.")
    note = (context or "").strip()
    if material:
        # A photo can't tell PLA from PETG; the user's word goes to the model too.
        note = f"{note}\nMaterial, as stated by the user: {material.name}.".strip()
    result, meta, cached = diagnose.diagnose_cached(data, note or None)
    result = diagnose.with_material(result, material_id)
    return _diagnosis_response(
        result.model_dump(mode="json"),
        {**asdict(meta), "cached": cached, "source": "repeat" if cached else "live"},
    )


@app.get("/mechanics/fillet")
def get_fillet(
    current_radius_mm: float, proposed_radius_mm: float, section_depth_mm: float
) -> JSONResponse:
    """Recompute a fillet change from measurements the user typed on the card.

    Same calculation as the diagnosis uses (mechanics.py). No model call.
    """
    computed = diagnose.fillet_computed(
        current_radius_mm, proposed_radius_mm, section_depth_mm, diagnose.USER_MEASURED
    )
    if "error" in computed:
        return _error(400, computed["error"])
    return JSONResponse(computed)


@app.get("/mechanics/section")
def get_section(
    shape: str,
    current_diameter_mm: float | None = None,
    proposed_diameter_mm: float | None = None,
    current_width_mm: float | None = None,
    proposed_width_mm: float | None = None,
    current_thickness_mm: float | None = None,
    proposed_thickness_mm: float | None = None,
    length_mm: float | None = None,
    material_id: str | None = None,
) -> JSONResponse:
    """Recompute a thicker-section fix from measurements the user typed.

    Ratios need only the two sections; grams also need a length and a material.
    """
    material = materials.get(material_id) if material_id else None
    change = {
        "shape": shape,
        "current_diameter_mm": current_diameter_mm,
        "proposed_diameter_mm": proposed_diameter_mm,
        "current_width_mm": current_width_mm,
        "proposed_width_mm": proposed_width_mm,
        "current_thickness_mm": current_thickness_mm,
        "proposed_thickness_mm": proposed_thickness_mm,
        "length_mm": length_mm,
        "dimensions_source": diagnose.USER_MEASURED,
        "material_id": material.id if material else None,
    }
    computed = diagnose.section_computed(change, material.density_g_cm3 if material else None)
    if "error" in computed:
        return _error(400, computed["error"])
    return JSONResponse(computed)


GLOSSARY_PATH = PROJECT_DIR / "knowledge" / "glossary.json"


@app.get("/glossary")
def get_glossary() -> FileResponse:
    """Hand-written term definitions for the hover pop-ups. Never generated."""
    return FileResponse(GLOSSARY_PATH, media_type="application/json")


@app.get("/materials")
def get_materials() -> JSONResponse:
    """The reference materials table, for the material picker and the cards."""
    return JSONResponse([materials.facts(m) for m in materials.load_materials().values()])


@app.get("/examples/{n}")
def get_example(n: int, material_id: str | None = None) -> JSONResponse:
    """Pre-computed diagnosis for bundled demo photo n. Needs no network.

    material_id, when given, adds reference data for that material.
    """
    if material_id:
        materials.get(material_id)  # 400 for an unknown id, before anything else
    if not 1 <= n <= EXAMPLE_COUNT:
        return _error(404, f"There is no example {n}. Examples are 1 to {EXAMPLE_COUNT}.")
    path = BUNDLED_EXAMPLES_DIR / f"{n}.json"
    if not path.is_file():
        return _error(404, f"Example {n} has not been bundled yet ({path.name} is missing).")
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        result = diagnose.with_mode_info(diagnose.Diagnosis.model_validate(stored["diagnosis"]))
    except (OSError, ValueError, KeyError, ValidationError) as exc:
        logger.exception("Bundled example %d is unreadable", n)
        return _error(500, f"Bundled example {n} is corrupt: {type(exc).__name__}.")
    result = diagnose.with_material(result, material_id)
    meta = {**stored.get("meta", {}), "cached": True, "source": "example"}
    return _diagnosis_response(result.model_dump(mode="json"), meta)
