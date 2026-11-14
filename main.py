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
    image: UploadFile = File(...), context: str | None = Form(None)
) -> JSONResponse:
    data = image.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return _error(413, f"That photo is larger than {limit_mb} MB.")
    result, meta, cached = diagnose.diagnose_cached(data, context)
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


@app.get("/examples/{n}")
def get_example(n: int) -> JSONResponse:
    """Pre-computed diagnosis for bundled demo photo n. Needs no network."""
    if not 1 <= n <= EXAMPLE_COUNT:
        return _error(404, f"There is no example {n}. Examples are 1 to {EXAMPLE_COUNT}.")
    path = BUNDLED_EXAMPLES_DIR / f"{n}.json"
    if not path.is_file():
        return _error(404, f"Example {n} has not been bundled yet ({path.name} is missing).")
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        result = diagnose.Diagnosis.model_validate(stored["diagnosis"])
    except (OSError, ValueError, KeyError, ValidationError) as exc:
        logger.exception("Bundled example %d is unreadable", n)
        return _error(500, f"Bundled example {n} is corrupt: {type(exc).__name__}.")
    meta = {**stored.get("meta", {}), "cached": True, "source": "example"}
    return _diagnosis_response(result.model_dump(mode="json"), meta)
