"""HTTP API for pre-failure interior reviews: leads, live jobs and saved reviews.

A live review is a background job in this server process. When it completes,
the report, the agents' activity log and the cited scan images are written to
the local cache, and everything is served from there. A saved review therefore
replays instantly, with no model or Matterport call. Bundled reviews are used
when a building has no runtime review, including on a fresh deployment.
"""

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

import building_evidence
import house_analysis
import interiors

logger = logging.getLogger("postmortem.house_inspections")
router = APIRouter(prefix="/api/interiors", tags=["Pre-failure interior review"])
# A free-tier vision key allows a handful of requests a minute, and the agents
# queue behind that limit, so a live review can legitimately take many minutes.
JOB_TIMEOUT_SECONDS = 30 * 60
JOB_TTL_SECONDS = 60 * 60
MAX_JOBS = 16
_LOCK = threading.RLock()
_SLOTS = threading.BoundedSemaphore(2)
_JOBS: dict[str, "Job"] = {}
JobID = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]
ModelID = Annotated[str, Path(pattern=r"^[a-zA-Z0-9]{1,64}$")]
ViewID = Annotated[str, Path(pattern=r"^R\d{1,2}-[A-Z]-(ceiling|wall-[1-4]|floor)$")]


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(pattern=r"^[a-zA-Z0-9]{1,64}$")


@dataclass
class Job:
    id: str
    model_id: str
    fingerprint: str
    created: float = field(default_factory=time.monotonic)
    status: str = "queued"
    stage: str = "queued"
    message: str = "Waiting to load the building's scan."
    report: dict | None = None
    error: str | None = None
    activity: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        return {
            **{key: getattr(self, key) for key in ("id", "model_id", "status", "stage", "message", "report", "error")},
            "activity": list(self.activity),
            "evidence_base": evidence_base(self.model_id) if self.report else None,
        }


# --- Saved reviews -------------------------------------------------------------------


def review_dir(model_id: str) -> FilePath:
    if not re.fullmatch(r"[a-zA-Z0-9]{1,64}", model_id or ""):
        raise ValueError("Invalid building identifier")
    return building_evidence.cache_dir() / "reviews" / model_id


def saved_review_dir(model_id: str) -> FilePath:
    """Prefer a runtime review; keep bundled reports and their evidence together."""
    local = review_dir(model_id)  # Validates the identifier for both locations.
    if (local / "review.json").is_file():
        return local
    return building_evidence.BUNDLED_INTERIORS_DIR / "reviews" / model_id


def evidence_base(model_id: str) -> str:
    return f"/api/interiors/reviews/{model_id}/evidence"


def has_saved_review(model_id: str) -> bool:
    try:
        return (saved_review_dir(model_id) / "review.json").is_file()
    except ValueError:
        return False


def save_review(survey: building_evidence.Survey, report: dict, session: house_analysis.Session) -> None:
    """Write the report, activity log and every cited image to the local cache.

    The agents' recorded state is kept too, so `rebuild_review` can re-derive
    the report (for example after a scenario change) without a new model run.
    """
    folder = review_dir(survey.model["id"])
    images = folder / "evidence"
    images.mkdir(parents=True, exist_ok=True)
    for stale in images.glob("*.jpg"):
        stale.unlink()
    for view_id in report["evidence_ids"]:
        position, face = survey.parse_view(view_id)
        (images / f"{view_id}.jpg").write_bytes(survey.face_jpeg(position, face))
    snapshot = session.snapshot()
    payload = {"report": report, "activity": snapshot["activity"], "session": snapshot, "saved_at": datetime.now(timezone.utc).isoformat()}
    temporary = folder / "review.json.tmp"
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(folder / "review.json")


def load_review(model_id: str) -> dict | None:
    """A saved review, rechecked against the report rules, or None."""
    try:
        payload = json.loads((saved_review_dir(model_id) / "review.json").read_text(encoding="utf-8"))
        report = house_analysis.validate_report(payload["report"])
        if report["model_id"] != model_id:
            return None
        activity = [event for event in payload.get("activity") or [] if isinstance(event, dict)]
        return {"report": report, "activity": activity, "saved_at": payload.get("saved_at"), "evidence_base": evidence_base(model_id)}
    except (OSError, ValueError, KeyError, TypeError, house_analysis.AnalysisError):
        return None


def rebuild_review(model_id: str) -> dict:
    """Re-derive a saved review's report from the agents' recorded state.

    Fetches the scan again (for geometry and images) but makes no model call.
    """
    payload = json.loads((review_dir(model_id) / "review.json").read_text(encoding="utf-8"))
    survey = building_evidence.collect_survey(model_id)
    session = house_analysis.Session.restore(survey, payload["session"])
    report = house_analysis.build_report(session)
    save_review(survey, report, session)
    return report


# --- Jobs ----------------------------------------------------------------------------


def _prune_jobs(*, make_room: bool = False) -> None:
    """Called while holding _LOCK. Active jobs keep their slots until finished."""
    now = time.monotonic()
    for job_id, job in list(_JOBS.items()):
        if job.status in ("completed", "failed") and now - job.created > JOB_TTL_SECONDS:
            del _JOBS[job_id]
    if make_room and len(_JOBS) >= MAX_JOBS:
        terminal = sorted(
            (job for job in _JOBS.values() if job.status in ("completed", "failed")),
            key=lambda job: job.created,
        )
        for job in terminal[:len(_JOBS) - MAX_JOBS + 1]:
            del _JOBS[job.id]


def _progress(job_id: str, stage: str, message: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job and job.status not in ("completed", "failed"):
            job.status, job.stage, job.message = "running", stage, message


def _activity(job_id: str, event: dict) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job and job.status not in ("completed", "failed") and len(job.activity) < 400:
            job.activity.append(event)


def _fail(job_id: str, message: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job and job.status != "completed":
            job.status, job.stage, job.error, job.message = "failed", "failed", message, message


def _execute_job(job_id: str, request: AssessmentRequest) -> None:
    try:
        progress = lambda stage, message: _progress(job_id, stage, message)
        survey = building_evidence.collect_survey(request.model_id, progress=progress)
        report, session = house_analysis.run_assessment(survey, progress=progress, on_activity=lambda event: _activity(job_id, event))
        progress("saving", "Saving the report and its evidence images.")
        save_review(survey, report, session)
        with _LOCK:
            job = _JOBS.get(job_id)
            # A timed-out worker may finish later; it cannot republish a report.
            if job and job.status not in ("completed", "failed"):
                job.report = report
                job.activity = list(session.activity)
                job.status, job.stage = "completed", "completed"
                job.message = "Review complete."
    except (house_analysis.AnalysisError, interiors.MatterportError) as exc:
        _fail(job_id, str(exc))
    except Exception as exc:
        # Provider exception messages may include credentials or signed URLs.
        logger.error("House review %s failed (%s)", job_id, type(exc).__name__)
        _fail(job_id, "The review could not finish. Please try again; no findings were published.")
    finally:
        _SLOTS.release()


async def _run_job(job_id: str, request: AssessmentRequest) -> None:
    try:
        await asyncio.wait_for(asyncio.to_thread(_execute_job, job_id, request), JOB_TIMEOUT_SECONDS)
    except TimeoutError:
        _fail(job_id, "The review exceeded its time limit. No findings were published. Please try again.")


# --- Routes --------------------------------------------------------------------------


@router.get("/analysis-config")
def analysis_configuration() -> dict:
    return house_analysis.analysis_config()


@router.get("/leads")
def leads() -> dict:
    """Buildings whose scan technician noted a visible interior problem."""
    return {"leads": [{**lead, "has_saved_review": has_saved_review(lead["id"])} for lead in interiors.field_note_leads()]}


@router.post("/assessments", status_code=202)
def create_assessment(request: AssessmentRequest, background: BackgroundTasks) -> JSONResponse:
    configuration = house_analysis.analysis_config()
    if not configuration["configured"]:
        return JSONResponse({"error": configuration["message"]}, status_code=503)
    fingerprint = hashlib.sha256(f"{request.model_id}\0{configuration['model']}".encode()).hexdigest()
    with _LOCK:
        _prune_jobs(make_room=True)
        for job in _JOBS.values():
            if job.fingerprint == fingerprint and job.status in ("queued", "running"):
                return JSONResponse({"id": job.id, "status": job.status}, status_code=202)
        if len(_JOBS) >= MAX_JOBS or not _SLOTS.acquire(blocking=False):
            return JSONResponse({"error": "Two reviews are already running. Please try again shortly."}, status_code=429)
        job = Job(id=uuid.uuid4().hex, model_id=request.model_id, fingerprint=fingerprint)
        _JOBS[job.id] = job
    background.add_task(_run_job, job.id, request)
    return JSONResponse({"id": job.id, "status": job.status}, status_code=202)


@router.get("/assessments/{job_id}")
def get_assessment(job_id: JobID) -> JSONResponse:
    with _LOCK:
        _prune_jobs()
        job = _JOBS.get(job_id)
        if not job:
            return JSONResponse({"error": "This review has expired or the server restarted. Run it again."}, status_code=404)
        return JSONResponse(job.public(), headers={"Cache-Control": "no-store"})


@router.get("/reviews/{model_id}")
def get_review(model_id: ModelID) -> JSONResponse:
    review = load_review(model_id)
    if not review:
        return JSONResponse({"error": "No saved review for this building yet."}, status_code=404)
    return JSONResponse(review, headers={"Cache-Control": "no-store"})


@router.get("/reviews/{model_id}/evidence/{view_id}")
def get_evidence(model_id: ModelID, view_id: ViewID) -> Response:
    try:
        data = (saved_review_dir(model_id) / "evidence" / f"{view_id}.jpg").read_bytes()
    except (OSError, ValueError):
        return JSONResponse({"error": "This scan image is not part of a saved review."}, status_code=404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.get("/models/{model_id}/mesh")
def get_mesh(model_id: ModelID) -> Response:
    """The building's interior mesh, cached when a review placed its findings."""
    try:
        data = building_evidence.read_mesh(model_id)
    except (OSError, interiors.MatterportError):
        return JSONResponse({"error": "No 3D mesh has been cached for this building. Run a review first."}, status_code=404)
    return Response(data, media_type="application/json", headers={"Cache-Control": "private, max-age=3600"})
