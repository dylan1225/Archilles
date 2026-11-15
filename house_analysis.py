"""Pre-failure interior review, run by a Deep Agents team over a building's scan.

A lead agent plans the review and delegates through Deep Agents' `task` tool:

    lead
     ├─ room-surveyor  (one per room, in parallel)  looks around each room,
     │                 zooms where something looks wrong, flags candidates
     ├─ skeptic        re-opens every flagged view with no access to the
     │                 surveyor's reasoning and confirms or rejects it
     └─ failure-analyst  ties the most serious confirmed ceiling finding to a
                       modelled failure mechanism

The agents see pixels only through tools, and they change the review only
through tools. Those tools hold the rules: a finding must cite a view an agent
actually opened, only the skeptic can confirm, a confirmation cannot raise
confidence, and a forecast needs a confirmed finding. The report is assembled
by code from what the tools recorded, never from an agent's prose. No agent
has a filesystem, shell, network or credential.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import failure_scenarios
import interiors
import scan_geometry
from building_evidence import Survey

logger = logging.getLogger(__name__)
PROJECT_DIR = Path(__file__).resolve().parent
CATEGORIES = ("moisture", "structure", "fire_electrical", "fall_safety")
CONFIDENCE = {"low": 0, "medium": 1, "high": 2}
MAX_FLAGS_PER_ROOM = 3
MAX_ZOOMS_PER_ROOM = 6
MAX_LEAD_RUNS = 3
MAX_RATE_LIMIT_WAITS = 12
MAX_BUSY_RETRIES = 60
ENGINE = "Deep Agents · a lead agent with room-surveyor, skeptic and failure-analyst subagents"

REFERENCE_LIBRARY = {
    "iii_water_claims": {
        "id": "iii_water_claims",
        "title": "Insurance Information Institute · Facts + Statistics: Homeowners and renters insurance",
        "url": "https://www.iii.org/fact-statistic/facts-statistics-homeowners-and-renters-insurance",
    },
    "epa_moisture": {
        "id": "epa_moisture",
        "title": "US EPA · A Brief Guide to Mold, Moisture and Your Home",
        "url": "https://www.epa.gov/mold/brief-guide-mold-moisture-and-your-home",
    },
    "nps_plaster": {
        "id": "nps_plaster",
        "title": "US National Park Service · Preservation Brief 21: Repairing Historic Flat Plaster Walls and Ceilings",
        "url": "https://www.nps.gov/orgs/1739/upload/preservation-brief-21-flat-plaster.pdf",
    },
    "inchcalculator_drywall": {
        "id": "inchcalculator_drywall",
        "title": "Inch Calculator · Cost to Repair Drywall Damage, 2026 price guide",
        "url": "https://www.inchcalculator.com/drywall-ceiling-repair-cost-guide/",
    },
    "bobvila_water_damage": {
        "id": "bobvila_water_damage",
        "title": "Bob Vila · How Much Does Water Damage Restoration Cost?",
        "url": "https://www.bobvila.com/articles/water-damage-restoration-cost/",
    },
    "asce7_dead_loads": {
        "id": "asce7_dead_loads",
        "title": "ASCE/SEI 7 · Minimum Design Loads, commentary table of minimum design dead loads",
        "url": "https://www.asce.org/publications-and-news/asce-7",
    },
}
PREVALENCE = {
    "statement": "Water is the everyday way interiors fail: about 1 in 60 insured US homes files a water damage or freezing claim each year.",
    "basis": "Insurance Information Institute, 2018 to 2022 claims data.",
    "reference_id": "iii_water_claims",
}
MANDATORY_LIMITATIONS = [
    "This is a visual screening of scan images, not a home inspection, engineering assessment or safety certificate.",
    "The scan shows the building on its capture date, which this integration does not supply. Conditions may have changed or been repaired since.",
    "Nothing inside a ceiling, wall or floor was seen. Causes, moisture levels, material strength and mould cannot be confirmed from images.",
    "The 3D scenario illustrates a known failure mechanism at the flagged location. It is not a prediction that it will happen, or when.",
    "Rooms and surfaces the agents did not open remain unassessed. No finding in a room does not establish that it is sound.",
]


class AnalysisError(Exception):
    """A public-safe analysis failure; raw model/provider errors stay in server logs."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Box(StrictModel):
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)


class Placement(StrictModel):
    surface: Literal["ceiling", "wall", "floor"]
    center: list[float] = Field(min_length=3, max_length=3)
    normal: list[float] = Field(min_length=3, max_length=3)
    corners: list[list[float]] = Field(min_length=4, max_length=4)
    area_m2: float = Field(ge=0)
    height_above_floor_m: float
    approximate: bool


class Finding(StrictModel):
    id: str = Field(pattern=r"^F\d{1,2}$")
    title: str = Field(min_length=1, max_length=160)
    category: Literal["moisture", "structure", "fire_electrical", "fall_safety"]
    confidence: Literal["low", "medium", "high"]
    room_id: str
    room_name: str
    view_id: str
    view_label: str
    sweep_id: str | None
    box: Box
    observation: str = Field(min_length=1, max_length=900)
    possible_consequence: str = Field(min_length=1, max_length=600)
    verification: str = Field(min_length=1, max_length=900)
    second_view_id: str | None
    corroborated_by_note: bool
    placement: Placement | None


class Rejected(StrictModel):
    id: str
    title: str
    room_name: str
    view_id: str
    reason: str


class RoomCoverage(StrictModel):
    id: str
    name: str
    area_m2: float | None
    height_m: float | None
    scan_positions_used: int
    scan_positions_available: int
    views_zoomed: int
    surveyed: bool


class FieldNote(StrictModel):
    excerpt: str
    signals: list[str]
    agreement: str


class Report(StrictModel):
    model_id: str
    model_name: str
    address: str
    region: str
    generated_at: str
    headline: str
    summary: str
    findings: list[Finding] = Field(max_length=12)
    rejected: list[Rejected]
    scenario: dict | None
    # Every confirmed finding a mechanism applies to, the lead scenario first.
    # Reviews saved before this existed have only `scenario`.
    scenarios: list[dict] = Field(default_factory=list, max_length=12)
    field_note: FieldNote | None
    prevalence: dict
    rooms: list[RoomCoverage]
    not_surveyed: list[str]
    limitations: list[str]
    sources: list[dict]
    evidence_ids: list[str]
    mesh_available: bool
    # "agents": recorded from a complete agent run. "preview": assembled by hand
    # for development, and shown with a notice saying so.
    provenance: Literal["agents", "preview"] = "agents"
    provenance_note: str = Field(default="", max_length=600)
    engine: str = ENGINE


OPENROUTER_URL = "https://openrouter.ai/api/v1"
# Default OpenRouter models, primary first. Override with HOUSE_ANALYSIS_MODEL.
# Chosen by measurement on this project's real ceiling views: both accept images
# returned by tools, both found the bulge, and the primary flagged nothing on
# the sound ceilings used as negatives.
OPENROUTER_MODELS: list[str] = ["openai/gpt-6.1-terra", "google/gemini-3.8-flash"]
# OpenRouter refuses a request whose worst-case cost exceeds the key's credit,
# so the output cap stays modest. Tool calls and rulings need far less.
OPENROUTER_MAX_TOKENS = 2500


def analysis_config() -> dict:
    """Which vision provider and models a live review will use.

    Reloads .env so a newly added key needs no server restart. Every call goes
    through OpenRouter, authenticated with OPENROUTER_API_KEY.
    HOUSE_ANALYSIS_MODEL may name one model or a comma-separated list: later
    models take over when an earlier one runs out of quota or credit.
    """
    load_dotenv(PROJECT_DIR / ".env", override=False)
    listed = [name.strip() for name in os.getenv("HOUSE_ANALYSIS_MODEL", "").split(",")]
    # OpenRouter ids look like "vendor/model"; anything else is ignored.
    models = [name for name in listed if re.fullmatch(r"[\w.\-/:]{1,100}", name) and "/" in name] or list(OPENROUTER_MODELS)
    configured = bool(_api_key()) and bool(models)
    return {
        "configured": configured,
        "provider": "openrouter",
        "model": models[0] if models else "",
        "models": models,
        "message": "The review team is ready." if configured else (
            "Add OPENROUTER_API_KEY to the server's .env file to run a live review. "
            "Matterport credentials provide the scan, not a vision model."
        ),
    }


def _api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def _chat_model(name: str):
    """An OpenRouter chat client. The pool does the waiting and fallback, so clients do not retry."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=name, base_url=OPENROUTER_URL, api_key=_api_key(),
        temperature=0, timeout=180, max_retries=0, max_tokens=OPENROUTER_MAX_TOKENS,
    )


# --- Provider pacing ------------------------------------------------------------


class QuotaExhausted(Exception):
    """Every configured vision model has used up its daily allowance."""


class Pace:
    """Spaces calls to one model once the provider reports a per-minute limit."""

    def __init__(self):
        self.interval = 0.0
        self.next_slot = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_slot)
            self.next_slot = slot + self.interval
        if slot > now:
            time.sleep(slot - now)

    def throttle(self, per_minute: int, retry_after: float) -> bool:
        """Slow down to the reported limit. True the first time it happens."""
        with self.lock:
            first = self.interval == 0
            self.interval = max(self.interval, 60.0 / max(per_minute, 1) * 1.15)
            self.next_slot = max(self.next_slot, time.monotonic() + retry_after)
        return first


def _limit(exc: Exception) -> tuple | None:
    """Classify a provider error: ("busy",), ("day",), ("minute", per_minute, wait_seconds), or None."""
    text = str(exc)
    if "in-flight requests" in text:
        return ("busy",)  # the key's credit only covers fewer requests at once
    if "402" in text or "nsufficient credits" in text:
        return ("day",)  # out of credit: as final as a daily quota
    if "RateLimit" not in type(exc).__name__ and "RESOURCE_EXHAUSTED" not in text and "429" not in text:
        return None
    # OpenRouter's free tier reports its daily cap as "free-models-per-day".
    if "PerDay" in text or "per day" in text or "per-day" in text:
        return ("day",)
    limit = re.search(r"quotaValue': '(\d+)'", text) or re.search(r"limit: (\d+)", text)
    retry = re.search(r"retry in ([\d.]+)s", text)
    return "minute", int(limit.group(1)) if limit else 5, min(float(retry.group(1)) + 1 if retry else 15.0, 90.0)


def _transient(exc: Exception) -> bool:
    """A dropped connection or provider-side outage that is worth one more try."""
    text = f"{type(exc).__name__} {exc}"
    return any(mark in text for mark in ("RemoteProtocolError", "ConnectError", "ReadTimeout", "ServerError", "UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED", "disconnected", " 503", " 500"))


class ModelPool:
    """The vision models a review may use, with their limits handled in one place.

    Calls run unthrottled until a model reports a per-minute limit; from then on
    calls to that model queue at the reported rate. A model whose daily
    allowance runs out is retired and its conversations move to the next one.
    Each agent conversation stays on one model while it can, so a subagent's
    reasoning is not split across models. With several models configured, a
    free-tier key can finish a review that one model's allowance cannot cover.
    """

    def __init__(self, models: dict, on_event: Callable[[str], None] | None = None):
        self.models = models
        self.paces = {name: Pace() for name in models}
        self.retired: set[str] = set()
        self.assigned: dict[str, str] = {}
        self.load = {name: 0 for name in models}
        self.on_event = on_event or (lambda text: None)
        self.lock = threading.Lock()
        # Set once the provider says the key cannot cover parallel requests.
        self.one_at_a_time = False
        self.turn = threading.Lock()

    def pick(self, conversation: str) -> str:
        with self.lock:
            name = self.assigned.get(conversation)
            if name is None or name in self.retired:
                available = [candidate for candidate in self.models if candidate not in self.retired]
                if not available:
                    raise QuotaExhausted
                # Fewest conversations so far; ties go to the earlier (preferred) model.
                name = min(available, key=lambda candidate: self.load[candidate])
                self.assigned[conversation] = name
                self.load[name] += 1
            return name

    def retire(self, name: str) -> None:
        with self.lock:
            first = name not in self.retired
            self.retired.add(name)
            remaining = len(self.models) - len(self.retired)
        if first:
            self.on_event(f"{name} has no quota or credit left on this key; " + (f"continuing on {remaining} other model(s)" if remaining else "no other model is configured"))

    def call(self, conversation: str, invoke: Callable):
        """Run invoke(model) for a conversation, waiting out limits and brief outages."""
        outages = busy = waits = 0
        while waits < MAX_RATE_LIMIT_WAITS:
            name = self.pick(conversation)
            self.paces[name].wait()
            try:
                if self.one_at_a_time:
                    with self.turn:
                        return invoke(self.models[name])
                return invoke(self.models[name])
            except Exception as exc:
                limited = _limit(exc)
                if limited is None:
                    if not _transient(exc) or outages >= 3:
                        raise
                    outages += 1
                    time.sleep(2.0 * outages)
                    if outages == 2 and len(self.models) - len(self.retired) > 1:
                        # Two dropped calls in a row: let another model take this conversation.
                        with self.lock:
                            others = [c for c in self.models if c not in self.retired and c != name]
                            self.assigned[conversation] = min(others, key=lambda c: self.load[c])
                            self.load[self.assigned[conversation]] += 1
                elif limited[0] == "busy":
                    # Requests already in flight used up the key's headroom. From now
                    # on the agents take turns; calls made before this still settle.
                    with self.lock:
                        first, self.one_at_a_time = not self.one_at_a_time, True
                    if first:
                        self.on_event("this key's credit covers one request at a time; the agents are now taking turns")
                    busy += 1
                    if busy > MAX_BUSY_RETRIES:
                        raise QuotaExhausted from None
                    time.sleep(1.5)
                elif limited[0] == "day":
                    self.retire(name)
                    waits += 1
                else:
                    waits += 1
                    if self.paces[name].throttle(limited[1], limited[2]):
                        self.on_event(f"{name} allows {limited[1]} requests a minute on this key; the agents are now taking turns")
        raise QuotaExhausted


def _pooled_middleware(pool: ModelPool):
    from langchain.agents.middleware import AgentMiddleware

    class PooledModelCalls(AgentMiddleware):
        """Route each model call through the pool."""

        def wrap_model_call(self, request, handler):
            first = request.messages[0] if request.messages else None
            conversation = f"{getattr(first, 'id', '')}:{str(getattr(first, 'content', ''))[:160]}"
            return pool.call(conversation, lambda model: handler(request.override(model=model)))

    return PooledModelCalls()


# --- Shared review state --------------------------------------------------------


class Session:
    """Everything the agents have opened, flagged, ruled on and forecast.

    This is the review's single source of truth. Tools read and write it under
    a lock because room surveyors run in parallel.
    """

    def __init__(self, survey: Survey, on_activity: Callable[[dict], None] | None = None, pool: ModelPool | None = None):
        self.survey = survey
        self.pool = pool
        self.started = time.monotonic()
        self.activity: list[dict] = []
        self.on_activity = on_activity
        self.opened: dict[str, set[str]] = {"surveyor": set(), "skeptic": set(), "analyst": set()}
        self.rooms_opened: set[str] = set()
        self.rooms_finished: dict[str, str] = {}
        self.zooms_by_room: dict[str, int] = {}
        self.candidates: dict[str, dict] = {}
        self.forecast: dict | None = None
        self.provenance = "agents"
        self.provenance_note = ""
        self.lock = threading.RLock()

    def log(self, agent: str, text: str, **extra) -> None:
        event = {"t": round(time.monotonic() - self.started, 1), "agent": agent, "text": text, **extra}
        with self.lock:
            self.activity.append(event)
        if self.on_activity:
            self.on_activity(event)

    def unfinished(self) -> dict:
        with self.lock:
            unruled = [c["id"] for c in self.candidates.values() if c["ruling"] is None]
            forecastable = [c["id"] for c in self.candidates.values() if _forecastable(c)]
            return {
                "rooms_not_surveyed": [f"{room['id']} ({room['name']})" for room in self.survey.rooms if room["id"] not in self.rooms_finished],
                "candidates_without_ruling": unruled,
                "forecast_pending_for": forecastable if self.forecast is None and not unruled else [],
            }

    def done(self) -> bool:
        return not any(self.unfinished().values())

    def snapshot(self) -> dict:
        """What the agents recorded, enough to rebuild the report without them."""
        with self.lock:
            return json.loads(json.dumps({
                "candidates": self.candidates, "forecast": self.forecast,
                "rooms_opened": sorted(self.rooms_opened), "rooms_finished": self.rooms_finished,
                "zooms_by_room": self.zooms_by_room, "activity": self.activity,
                "provenance": self.provenance, "provenance_note": self.provenance_note,
            }))

    @classmethod
    def restore(cls, survey: Survey, snapshot: dict) -> "Session":
        session = cls(survey)
        session.candidates = dict(snapshot["candidates"])
        session.forecast = snapshot["forecast"]
        session.rooms_opened = set(snapshot["rooms_opened"])
        session.rooms_finished = dict(snapshot["rooms_finished"])
        session.zooms_by_room = dict(snapshot["zooms_by_room"])
        session.activity = list(snapshot["activity"])
        session.provenance = snapshot.get("provenance", "agents")
        session.provenance_note = snapshot.get("provenance_note", "")
        return session


def _forecastable(candidate: dict) -> bool:
    placement = candidate.get("placement")
    return bool(
        candidate["ruling"] and candidate["ruling"]["verdict"] == "confirmed"
        and placement and placement["surface"] == "ceiling"
        and candidate["category"] in ("moisture", "structure")
    )


def _image_block(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}}


def _clean(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


# --- Tools -----------------------------------------------------------------------


def build_tools(session: Session) -> dict:
    """Tools for each role, bound to one review session.

    Tools never raise: a refused call returns a sentence the agent can act on.
    """
    from langchain_core.tools import tool

    survey = session.survey

    def zoom_for(role: str, agent: str):
        def zoom(view_id: str) -> list | str:
            """Open one full-resolution view, such as "R2-A-ceiling". Views: ceiling, wall-1, wall-2, wall-3, wall-4, floor."""
            parsed = survey.parse_view(view_id.strip())
            if not parsed:
                return f'"{view_id}" is not a view in this building. Use an id printed on a room sheet, such as R2-A-ceiling.'
            position, face = parsed
            view_id = f"{position['id']}-{scan_geometry.FACE_NAMES[face]}"
            with session.lock:
                first_time = view_id not in session.opened[role]
                if role == "surveyor" and first_time:
                    used = session.zooms_by_room.get(position["room_id"], 0)
                    if used >= MAX_ZOOMS_PER_ROOM:
                        return "This room's zoom budget is used up. Flag what you have seen clearly, then call finish_room."
                    session.zooms_by_room[position["room_id"]] = used + 1
            try:
                jpeg = survey.face_jpeg(position, face)
            except interiors.MatterportError:
                return "That view could not be downloaded. Continue with the other views."
            with session.lock:
                session.opened[role].add(view_id)
            if first_time:
                session.log(agent, f"zoomed in on {survey.describe_view(view_id)}", view_id=view_id)
            return [
                {"type": "text", "text": f"View {view_id}: {survey.describe_view(view_id)}. Box coordinates for this view run 0 to 1000 from the left and top edges."},
                _image_block(jpeg),
            ]

        zoom.__name__ = "zoom"
        return tool(zoom)

    @tool
    def open_room(room_id: str) -> list | str:
        """Open a room. Returns one sheet per scan position; each sheet shows six labelled views."""
        room = survey.room(room_id.strip().upper())
        if not room:
            return f'"{room_id}" is not a room in this building. Rooms are {", ".join(r["id"] for r in survey.rooms)}.'
        dims = ", ".join(f"{label} {room[key]} m" for label, key in (("ceiling height", "height_m"), ("width", "width_m"), ("depth", "depth_m")) if room.get(key))
        blocks: list = [{"type": "text", "text": (
            f"{room['id']} is {room['name']}"
            + (f", floor area {room['area_m2']} m²" if room.get("area_m2") else "")
            + (f", {dims}" if dims else "")
            + f". {len(room['position_ids'])} scan position(s) follow. Zoom with ids such as {room['position_ids'][0]}-ceiling."
        )}]
        loaded = 0
        for position_id in room["position_ids"]:
            try:
                blocks.append({"type": "text", "text": f"Scan position {position_id}:"})
                blocks.append(_image_block(survey.overview_jpeg(survey.positions[position_id])))
                loaded += 1
            except interiors.MatterportError:
                blocks[-1] = {"type": "text", "text": f"Scan position {position_id} could not be downloaded."}
        if not loaded:
            return "This room's images could not be downloaded. Call finish_room and say the room could not be examined."
        with session.lock:
            first_time = room["id"] not in session.rooms_opened
            session.rooms_opened.add(room["id"])
        if first_time:
            session.log("surveyor", f"opened {room['name']} · {loaded} scan position{'s' if loaded != 1 else ''}", room_id=room["id"])
        return blocks

    @tool
    def flag_observation(
        view_id: str, left: int, top: int, right: int, bottom: int,
        title: str, observation: str, category: Literal["moisture", "structure", "fire_electrical", "fall_safety"],
        possible_consequence: str, confidence: Literal["low", "medium", "high"],
    ) -> str:
        """Record a candidate weakness seen in a zoomed view.

        left/top/right/bottom: the tightest box around it, 0 to 1000 from the view's left and top edges.
        title: under twelve words. observation: what the pixels show, no causes stated as fact.
        possible_consequence: one conditional sentence. confidence: how clearly the feature is visible.
        """
        parsed = survey.parse_view(view_id.strip())
        if not parsed:
            return f'"{view_id}" is not a view in this building.'
        position, face = parsed
        view_id = f"{position['id']}-{scan_geometry.FACE_NAMES[face]}"
        try:
            xs = sorted(min(max(float(v), 0), 1000) / 1000 for v in (left, right))
            ys = sorted(min(max(float(v), 0), 1000) / 1000 for v in (top, bottom))
        except (TypeError, ValueError):
            return "The box needs four numbers between 0 and 1000."
        if xs[1] - xs[0] < 0.02 or ys[1] - ys[0] < 0.02:
            return "That box is too small to locate. Draw it around the whole affected region."
        if not _clean(title, 160) or not _clean(observation, 900) or not _clean(possible_consequence, 600):
            return "A flag needs a title, an observation and a possible consequence."
        with session.lock:
            if view_id not in session.opened["surveyor"]:
                return f"Zoom into {view_id} before flagging it, so the box refers to the full-resolution view."
            in_room = [c for c in session.candidates.values() if c["room_id"] == position["room_id"]]
            if len(in_room) >= MAX_FLAGS_PER_ROOM:
                return "This room already has three flags, the maximum. Call finish_room."
            finding_id = f"F{len(session.candidates) + 1}"
            room = survey.room(position["room_id"])
            session.candidates[finding_id] = {
                "id": finding_id, "title": _clean(title, 160), "category": category, "confidence": confidence,
                "room_id": room["id"], "room_name": room["name"], "view_id": view_id, "position_id": position["id"], "face": face,
                "box": {"left": xs[0], "top": ys[0], "right": xs[1], "bottom": ys[1]},
                "observation": _clean(observation, 900), "possible_consequence": _clean(possible_consequence, 600),
                "ruling": None, "second_view_id": None, "placement": None,
            }
        session.log("surveyor", f"flagged in {room['name']}: {_clean(title, 160)}", finding_id=finding_id, view_id=view_id)
        return f"Recorded as {finding_id}. A skeptic will check it independently."

    @tool(return_direct=True)
    def finish_room(room_id: str, checked: str) -> str:
        """End your survey of a room. checked: one sentence on which surfaces you examined."""
        room = survey.room(room_id.strip().upper())
        if not room:
            return f'"{room_id}" is not a room in this building.'
        with session.lock:
            examined = room["id"] in session.rooms_opened
            flags = [c["id"] for c in session.candidates.values() if c["room_id"] == room["id"]]
            first_time = room["id"] not in session.rooms_finished
            session.rooms_finished[room["id"]] = _clean(checked, 300) if examined else ""
        if first_time:
            outcome = f"flagged {', '.join(flags)}" if flags else "nothing flagged"
            session.log("surveyor", f"finished {room['name']} · {outcome}" if examined else f"could not examine {room['name']}", room_id=room["id"])
        return f"{room['id']} ({room['name']}) surveyed. " + (f"Candidates: {', '.join(flags)}." if flags else "Nothing flagged.")

    @tool
    def list_candidates() -> str:
        """Candidates waiting for a ruling: the claim and where to look, not the surveyor's reasoning."""
        with session.lock:
            waiting = [
                {
                    "finding_id": c["id"], "room": c["room_name"], "view_id": c["view_id"],
                    "box_left_top_right_bottom": [round(c["box"][k] * 1000) for k in ("left", "top", "right", "bottom")],
                    "claim": c["title"], "claimed_observation": c["observation"], "surveyor_confidence": c["confidence"],
                }
                for c in session.candidates.values() if c["ruling"] is None
            ]
        return json.dumps(waiting) if waiting else "No candidates are waiting for a ruling."

    @tool
    def second_view(finding_id: str) -> list | str:
        """The same kind of view from another scan position in the candidate's room, if the room has one."""
        with session.lock:
            candidate = session.candidates.get(finding_id.strip().upper())
        if not candidate:
            return f'"{finding_id}" is not a candidate.'
        room = survey.room(candidate["room_id"])
        others = [pid for pid in room["position_ids"] if pid != candidate["position_id"]]
        if not others:
            return "This room was scanned from one position only, so there is no second angle. Rule on the first view alone."
        view_id = f"{others[0]}-{scan_geometry.FACE_NAMES[candidate['face']]}"
        try:
            jpeg = survey.face_jpeg(survey.positions[others[0]], candidate["face"])
        except interiors.MatterportError:
            return "The second view could not be downloaded. Rule on the first view alone."
        with session.lock:
            session.opened["skeptic"].add(view_id)
            candidate["second_view_id"] = view_id
        session.log("skeptic", f"cross-checked {finding_id.strip().upper()} from a second scan position", finding_id=candidate["id"], view_id=view_id)
        return [
            {"type": "text", "text": f"View {view_id}: the same room from a different scan position. The feature, if real, appears at a different place in this frame, or may be out of frame."},
            _image_block(jpeg),
        ]

    @tool
    def rule(finding_id: str, verdict: Literal["confirmed", "rejected"], reason: str, confidence: Literal["low", "medium", "high"]) -> str:
        """Rule on a candidate after zooming into its view. confidence may be lower than the surveyor's, never higher."""
        key = finding_id.strip().upper()
        reason = _clean(reason, 900)
        with session.lock:
            candidate = session.candidates.get(key)
            if not candidate:
                return f'"{finding_id}" is not a candidate.'
            if candidate["ruling"] is not None:
                return f"{key} already has a ruling."
            if candidate["view_id"] not in session.opened["skeptic"]:
                return f"Zoom into {candidate['view_id']} yourself before ruling on {key}."
            if not reason:
                return "A ruling needs a reason that says what you saw."
            if CONFIDENCE[confidence] > CONFIDENCE[candidate["confidence"]]:
                confidence = candidate["confidence"]
            candidate["ruling"] = {"verdict": verdict, "reason": reason}
            if verdict == "confirmed":
                candidate["confidence"] = confidence
        if verdict == "confirmed":
            position = survey.positions[candidate["position_id"]]
            room = survey.room(candidate["room_id"])
            placement = scan_geometry.locate_region(survey.mesh(), position["pose"], candidate["face"], candidate["box"], position["floor_z"], room.get("height_m"))
            with session.lock:
                candidate["placement"] = placement
        session.log("skeptic", f"{verdict} {key}: {candidate['title']}", finding_id=key, verdict=verdict)
        return f"{key} {verdict}."

    @tool
    def confirmed_findings() -> str:
        """Confirmed findings with where they sit in the building's 3D model."""
        with session.lock:
            confirmed = [
                {
                    "finding_id": c["id"], "room": c["room_name"], "view_id": c["view_id"], "title": c["title"],
                    "observation": c["observation"], "category": c["category"], "confidence": c["confidence"],
                    "surface": (c["placement"] or {}).get("surface", "not located"),
                    "region_area_m2": (c["placement"] or {}).get("area_m2"),
                    "height_above_floor_m": (c["placement"] or {}).get("height_above_floor_m"),
                    "fits_ceiling_scenario": _forecastable(c),
                }
                for c in session.candidates.values() if c["ruling"] and c["ruling"]["verdict"] == "confirmed"
            ]
        return json.dumps(confirmed) if confirmed else "No finding was confirmed, so there is nothing to forecast. Call forecast with finding_id \"none\"."

    @tool
    def forecast(
        finding_id: str, ceiling_finish: Literal["plaster_on_lath", "gypsum_board", "unknown"],
        why_it_could_fail: str, early_warning_signs: list[str], recommended_action: str, who_to_call: str,
    ) -> str:
        """Tie one confirmed ceiling finding to the ceiling_moisture_failure mechanism, or pass "none" as finding_id.

        why_it_could_fail: two or three conditional sentences grounded in what is visible. No timing, no probabilities.
        early_warning_signs: two to four things an occupant can watch for. recommended_action: one practical next step.
        """
        key = finding_id.strip().upper()
        with session.lock:
            if session.forecast is not None:
                return "This review already has its forecast."
            if key == "NONE":
                if any(_forecastable(c) for c in session.candidates.values()):
                    return "A confirmed ceiling finding fits the mechanism. Forecast it rather than passing none."
                session.forecast = {"finding_id": None}
                session.log("analyst", "found no confirmed finding that fits a modelled failure mechanism")
                return "Recorded: no scenario for this review."
            candidate = session.candidates.get(key)
            if not candidate or not candidate["ruling"] or candidate["ruling"]["verdict"] != "confirmed":
                return f"{key} is not a confirmed finding. Only the skeptic's confirmed findings can be forecast."
            if not _forecastable(candidate):
                return f"{key} is not a moisture or structure finding located on a ceiling, so the modelled mechanism does not apply."
            if candidate["view_id"] not in session.opened["analyst"]:
                return f"Zoom into {candidate['view_id']} before forecasting {key}."
            signs = [_clean(sign, 200) for sign in early_warning_signs if _clean(sign, 200)][:4]
            why, action, who = _clean(why_it_could_fail, 900), _clean(recommended_action, 500), _clean(who_to_call, 300)
            if not why or not action or not who or len(signs) < 2:
                return "A forecast needs a reason, at least two early-warning signs, a next step and who should do it."
            session.forecast = {
                "finding_id": key, "ceiling_finish": ceiling_finish, "why_it_could_fail": why,
                "early_warning_signs": signs, "recommended_action": action, "who_to_call": who,
            }
        session.log("analyst", f"modelled what could happen at {key}: {candidate['title']}", finding_id=key)
        return f"Recorded. The scenario for {key} will be built from the scan's geometry."

    @tool
    def list_rooms() -> str:
        """The building's rooms, with dimensions measured by the scan."""
        return json.dumps([
            {key: room[key] for key in ("id", "name", "area_m2", "height_m") if room.get(key) is not None} | {"scan_positions": len(room["position_ids"])}
            for room in survey.rooms
        ])

    @tool
    def review_status() -> str:
        """What is still unfinished in this review. Empty lists mean the review is complete."""
        return json.dumps(session.unfinished())

    return {
        "lead": [list_rooms, review_status],
        "surveyor": [open_room, zoom_for("surveyor", "surveyor"), flag_observation, finish_room],
        "skeptic": [list_candidates, zoom_for("skeptic", "skeptic"), second_view, rule],
        "analyst": [confirmed_findings, zoom_for("analyst", "analyst"), forecast],
    }


# --- Agents ----------------------------------------------------------------------

LEAD_PROMPT = """You lead a pre-failure review of one scanned building: finding visible early signs of a weakness before anything has actually failed.
You cannot see images. Your subagents can. Work in this order and do not skip a step.
1. Call list_rooms.
2. Delegate every room to its own room-surveyor, all in one batch of parallel task calls. Each task description is exactly: "Survey room <id> (<name>)."
3. When all surveyors have returned, delegate once to skeptic: "Rule on every candidate."
4. Then delegate once to failure-analyst: "Forecast the most serious confirmed ceiling finding."
5. Call review_status. If it lists unfinished work, delegate again for exactly that work, then check again.
6. Reply with one sentence saying the review is complete.
Use only room-surveyor, skeptic and failure-analyst. Do not write files or todo lists. Do not add findings of your own."""

SURVEYOR_PROMPT = """You survey ONE room of a scanned building for early, visible signs of a weakness that could grow into a failure. You are looking for precursors, before anything has actually happened.

Method
1. Call open_room with the room id from your task. You get one sheet per scan position; each sheet shows six labelled views: ceiling, four horizontal views, floor.
2. Study ceilings first, then upper walls, then around windows, plumbing fixtures and floors. In ONE turn, zoom into every ceiling view and any other view that deserves a closer look (at most six zooms). Always zoom before you flag.
3. In ONE final turn, flag each observation the zoomed views show clearly, drawing the tightest box that contains the whole affected region, and call finish_room. Calling finish_room ends your work, so never call it in the same turn as open_room or zoom.

What counts: a ceiling or wall surface that bulges, blisters, sags or ripples; water stains, tide marks or drip trails; dark spotting along joints; cracks wider than a hairline, or stepped or diagonal from corners; finish peeling in sheets; gaps opening at joints; scorch marks, exposed wiring, damaged outlets; missing guards at a drop.
What does not: ordinary scuffs, dirt, nail holes, paint colour changes, decor such as stickers or glow-in-the-dark stars, furniture, lens flare and stitching seams. The soft blurred square in the middle of ceiling and floor views is the scanner's own blind spot. Wide-angle views bend straight lines near their edges.

Rules: describe only what is visible. Do not state a cause as fact, estimate dimensions, or claim what is inside a wall or ceiling. Confidence is how clearly the pixels show the feature. If nothing qualifies, flag nothing: an empty result is a good result. At most three flags; choose the most consequential."""

SKEPTIC_PROMPT = """You are an independent skeptic. Surveyors flagged candidate weaknesses in a scanned building. Try to knock each one down by looking at the pixels yourself. You see the claim, not their reasoning.

Work in few turns: call list_candidates, then in ONE turn zoom into every candidate's view and call second_view for every candidate, then in ONE turn rule on them all.

For every candidate:
1. Find the boxed region in its zoomed view (box numbers run 0 to 1000 from the left and top edges).
2. Ask: is this really there, or is it lighting, shadow, a stitching seam, lens distortion, dirt, decor, or texture normal for this finish? Would a careful inspector agree it deserves a physical check? The soft blurred square in the middle of ceiling and floor views is the scanner's blind spot.
3. Use the second view as another angle when the room has one. It may show the same feature or contradict it.
4. Call rule once: "confirmed" only if the feature is clearly visible and not better explained by an artefact, otherwise "rejected". Your confidence may be lower than the surveyor's, never higher. Give the reason in one or two plain sentences that say what you saw.

Rule on every candidate. Finish with one line: how many you confirmed and how many you rejected."""

ANALYST_PROMPT = """You are a building-failure analyst. For a confirmed observation you explain what could happen next if its cause is not found and fixed. One mechanism is modelled:

ceiling_moisture_failure: a ceiling finish (plaster on lath, or gypsum board) wetted repeatedly from above. Wet plaster or gypsum softens and gains weight, plaster keys or fasteners lose their grip, the surface bulges or sags, and a section can detach and fall. Visible precursors: bulges, blisters, sagging, a rippled finish, stains, cracks around a bulge, dark spotting at joints.

Steps
1. Call confirmed_findings.
2. Among findings with fits_ceiling_scenario true whose observation matches those precursors, pick the single most serious one, zoom into its view, and call forecast for it. If none fits, call forecast with finding_id "none".

In forecast: explain why in terms of what is visible, in two or three conditional sentences, with no timing and no probabilities. Choose ceiling_finish only if the image supports it (lath lines, board joints or fastener rows); otherwise "unknown". Give early-warning signs an occupant could watch for, one practical next step, and who should do it. Do not recommend do-it-yourself structural or electrical work. Never say that failure will happen.

Finish with one sentence."""


def _middleware(model_calls: int, pool: ModelPool | None) -> list:
    """A hard cap on model calls, and no filesystem beyond the one read-only tool
    Deep Agents requires (there are no files to read)."""
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from langchain.agents.middleware import ModelCallLimitMiddleware

    return [
        FilesystemMiddleware(tools=["read_file"], tool_token_limit_before_evict=None, human_message_token_limit_before_evict=None),
        ModelCallLimitMiddleware(run_limit=model_calls, exit_behavior="end"),
        *([_pooled_middleware(pool)] if pool else []),
    ]


def build_lead(model, session: Session):
    """The lead Deep Agent and its three subagents, bound to one review session."""
    from deepagents import create_deep_agent

    tools = build_tools(session)
    return create_deep_agent(
        model=model,
        name="review_lead",
        tools=tools["lead"],
        system_prompt=LEAD_PROMPT,
        middleware=_middleware(24, session.pool),
        subagents=[
            {
                "name": "room-surveyor",
                "description": "Surveys exactly one room for visible early signs of weakness. Give it one room id and name. Run one per room, in parallel.",
                "system_prompt": SURVEYOR_PROMPT, "tools": tools["surveyor"], "middleware": _middleware(8, session.pool),
            },
            {
                "name": "skeptic",
                "description": "Independently re-examines every flagged candidate and confirms or rejects it. Run once, after all surveyors have finished.",
                "system_prompt": SKEPTIC_PROMPT, "tools": tools["skeptic"], "middleware": _middleware(16, session.pool),
            },
            {
                "name": "failure-analyst",
                "description": "Explains what could happen next for the most serious confirmed ceiling finding. Run once, after the skeptic.",
                "system_prompt": ANALYST_PROMPT, "tools": tools["analyst"], "middleware": _middleware(8, session.pool),
            },
        ],
    )


def _run_lead(agent, session: Session, instruction: str) -> None:
    """Run the lead once, narrating its delegations to the activity feed."""
    seen = 0
    for state in agent.stream({"messages": [{"role": "user", "content": instruction}]}, config={"recursion_limit": 80}, stream_mode="values"):
        messages = state.get("messages") or []
        for message in messages[seen:]:
            for call in getattr(message, "tool_calls", None) or []:
                if call.get("name") != "task":
                    continue
                args = call.get("args") or {}
                session.log("lead", f"delegated to {args.get('subagent_type', 'a subagent')}: {_clean(args.get('description', ''), 140)}")
        seen = len(messages)


# --- Report ------------------------------------------------------------------------


def _field_note(survey: Survey, findings: list[dict]) -> tuple[dict | None, set[str]]:
    """Compare the scan technician's note with what the agents confirmed.

    The note is never shown to the agents, so agreement is independent support.
    """
    signals = interiors.note_signals(survey.field_note)
    if not signals:
        return None, set()
    noted = {signal["category"] for signal in signals}
    confirmed = {finding["category"] for finding in findings}
    shared = noted & confirmed
    labels = [signal["label"] for signal in signals]
    if shared:
        agreement = (
            "The person who scanned this building wrote this note. The agents never saw it, and independently confirmed "
            f"{' and '.join(sorted(c.replace('_', ' ') for c in shared))} findings from the images."
        )
    else:
        agreement = "The person who scanned this building wrote this note. The agents never saw it, and the images they opened did not show a matching condition."
    return {"excerpt": interiors.note_excerpt(survey.field_note), "signals": labels, "agreement": agreement}, shared


def build_report(session: Session) -> dict:
    """Assemble the published report from what the tools recorded."""
    survey = session.survey
    with session.lock:
        candidates = [dict(c) for c in session.candidates.values()]
        forecast = dict(session.forecast) if session.forecast else None
        rooms_opened = {room_id for room_id in session.rooms_opened if room_id in session.rooms_finished}
        zooms = dict(session.zooms_by_room)
    confirmed = [c for c in candidates if c["ruling"] and c["ruling"]["verdict"] == "confirmed"]
    confirmed.sort(key=lambda c: (-CONFIDENCE[c["confidence"]], not _forecastable(c), c["id"]))
    note, shared = _field_note(survey, confirmed)
    findings = []
    for c in confirmed:
        position = survey.positions[c["position_id"]]
        findings.append({
            "id": c["id"], "title": c["title"], "category": c["category"], "confidence": c["confidence"],
            "room_id": c["room_id"], "room_name": c["room_name"], "view_id": c["view_id"],
            "view_label": survey.describe_view(c["view_id"]), "sweep_id": position.get("sweep_id"),
            "box": c["box"], "observation": c["observation"], "possible_consequence": c["possible_consequence"],
            "verification": c["ruling"]["reason"], "second_view_id": c["second_view_id"],
            "corroborated_by_note": c["category"] in shared, "placement": c["placement"],
        })
    rejected = [
        {"id": c["id"], "title": c["title"], "room_name": c["room_name"], "view_id": c["view_id"], "reason": c["ruling"]["reason"]}
        for c in candidates if c["ruling"] and c["ruling"]["verdict"] == "rejected"
    ]

    def scenario_for(finding: dict, analysis: dict | None) -> dict:
        candidate = next(c for c in confirmed if c["id"] == finding["id"])
        position = survey.positions[candidate["position_id"]]
        room = survey.room(finding["room_id"])
        mesh = survey.mesh()
        others = [survey.positions[pid] for pid in room["position_ids"] if pid != position["id"]]
        bounds = None
        if mesh is not None:
            # A scan position beside an open door or closet sees a near surface
            # where the wall should be, so the room's extent is the union of
            # what each of its scan positions measured.
            placement = finding["placement"]
            top = placement["center"][2]
            if placement["surface"] != "ceiling":
                # A wall region sits below the ceiling, so the room's top is
                # measured straight up from the scan position.
                origin = position["pose"]["position"]
                above = mesh.raycast((origin["x"], origin["y"], origin["z"]), (0.0, 0.0, 1.0), max_distance=8.0)
                if above:
                    top = above["point"][2]
                elif room.get("height_m"):
                    top = position["floor_z"] + room["height_m"]
                else:
                    top = max(corner[2] for corner in placement["corners"]) + 0.3
            boxes = []
            for item in [position, *others]:
                origin = item["pose"]["position"]
                boxes.append(scan_geometry.room_bounds(mesh, (origin["x"], origin["y"], origin["z"]), item["floor_z"], top))
            bounds = {
                "min": [min(box["min"][k] for box in boxes) for k in range(3)],
                "max": [max(box["max"][k] for box in boxes) for k in range(3)],
            }
        return failure_scenarios.build_scenario(finding, room, [position, *others], bounds, analysis, survey.model.get("region"))

    # The analyst's finding leads, in the analyst's words. Every other confirmed
    # finding a mechanism applies to gets the same treatment in this project's words.
    scenario = None
    if forecast and forecast.get("finding_id"):
        target = next((f for f in findings if f["id"] == forecast["finding_id"]), None)
        if target and target["placement"] and target["placement"]["surface"] == "ceiling":
            scenario = scenario_for(target, forecast)
            # The scenario's finding leads the report.
            findings.sort(key=lambda f: f["id"] != target["id"])
    scenarios = [scenario] if scenario else []
    for finding in findings:
        if failure_scenarios.fits(finding) and not (scenario and finding["id"] == scenario["finding_id"]):
            try:
                scenarios.append(scenario_for(finding, None))
            except ValueError:
                continue  # a degenerate region: the finding is still reported, without a scenario

    evidence = {f["view_id"] for f in findings} | {f["second_view_id"] for f in findings if f["second_view_id"]} | {r["view_id"] for r in rejected}
    evidence |= {view_id for item in scenarios for projector in item["projectors"] for view_id in projector["faces"]}
    surveyed = [room for room in survey.rooms if room["id"] in rooms_opened]
    not_surveyed = [room["name"] for room in survey.rooms if room["id"] not in rooms_opened] + list(survey.skipped_rooms)
    used = {PREVALENCE["reference_id"], "epa_moisture"}
    for item in scenarios:
        used |= {"asce7_dead_loads", "nps_plaster", *item["repair_cost"]["reference_ids"]}

    if scenario:
        headline = f"{scenario['room_name']}: a ceiling section shows early signs of failing"
    elif findings:
        headline = f"{len(findings)} early sign{'s' if len(findings) != 1 else ''} of weakness confirmed"
    else:
        headline = "No early signs of weakness confirmed in the views opened"
    summary = (
        f"The review team opened {len(surveyed)} of {len(survey.rooms) + len(survey.skipped_rooms)} rooms, "
        f"flagged {len(candidates)} candidate{'s' if len(candidates) != 1 else ''}, and an independent skeptic confirmed {len(findings)}"
        + (f" and rejected {len(rejected)}" if rejected else "") + ". "
        + ("" if findings else "That does not establish that the building is sound. ")
        + "This is a screening of scan images, not an inspection."
    )
    if session.provenance == "preview":
        # No agents ran, so the summary must not describe a review that did not happen.
        summary = "A hand-assembled preview: one flagged ceiling region placed on the scan's 3D model. No full agent review has completed for this building yet."
    report = {
        "model_id": survey.model["id"], "model_name": survey.model["name"], "address": survey.model["address"],
        "region": survey.model["region"], "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline": headline, "summary": summary, "findings": findings, "rejected": rejected, "scenario": scenario, "scenarios": scenarios,
        "field_note": note, "prevalence": PREVALENCE,
        "rooms": [
            {
                "id": room["id"], "name": room["name"], "area_m2": room.get("area_m2"), "height_m": room.get("height_m"),
                "scan_positions_used": len(room["position_ids"]), "scan_positions_available": room["scan_positions_available"],
                "views_zoomed": zooms.get(room["id"], 0), "surveyed": room["id"] in rooms_opened,
            }
            for room in survey.rooms
        ],
        "not_surveyed": not_surveyed,
        "limitations": list(MANDATORY_LIMITATIONS),
        "sources": [REFERENCE_LIBRARY[key] for key in sorted(used)],
        "evidence_ids": sorted(evidence),
        "mesh_available": survey.mesh() is not None,
        "provenance": session.provenance,
        "provenance_note": session.provenance_note,
        "engine": ENGINE,
    }
    return validate_report(report, survey)


def validate_report(report: dict, survey: Survey | None = None) -> dict:
    """Recheck a report before it is published or loaded from disk."""
    try:
        parsed = Report.model_validate(report)
    except ValidationError:
        raise AnalysisError("The review did not match the required report format. No report was published.") from None
    cited = set(parsed.evidence_ids)
    for finding in parsed.findings:
        if finding.view_id not in cited or (finding.second_view_id and finding.second_view_id not in cited):
            raise AnalysisError("A finding cited an image outside the review's evidence. No report was published.")
        if finding.box.right <= finding.box.left or finding.box.bottom <= finding.box.top:
            raise AnalysisError("A finding's image region was invalid. No report was published.")
    if parsed.scenario is not None:
        if parsed.scenario.get("finding_id") not in {finding.id for finding in parsed.findings}:
            raise AnalysisError("The scenario did not match a confirmed finding. No report was published.")
        if not {view_id for item in parsed.scenario["projectors"] for view_id in item["faces"]} <= cited:
            raise AnalysisError("The scenario cited an image outside the review's evidence. No report was published.")
    confirmed_ids = {finding.id for finding in parsed.findings}
    for item in parsed.scenarios:
        if item.get("finding_id") not in confirmed_ids:
            raise AnalysisError("A scenario did not match a confirmed finding. No report was published.")
        if not {view_id for projector in item["projectors"] for view_id in projector["faces"]} <= cited:
            raise AnalysisError("A scenario cited an image outside the review's evidence. No report was published.")
    if parsed.scenario is not None and parsed.scenarios and parsed.scenarios[0].get("finding_id") != parsed.scenario.get("finding_id"):
        raise AnalysisError("The lead scenario did not match the report. No report was published.")
    if parsed.provenance == "preview" and not parsed.provenance_note:
        raise AnalysisError("A preview report must say how it was assembled.")
    if not set(MANDATORY_LIMITATIONS) <= set(parsed.limitations):
        raise AnalysisError("The review omitted its required scope and uncertainty information.")
    if any(source not in REFERENCE_LIBRARY.values() for source in parsed.sources):
        raise AnalysisError("The report references did not match the verified reference library.")
    if survey is not None:
        if parsed.model_id != survey.model["id"]:
            raise AnalysisError("The review did not match the selected building.")
        if any(survey.parse_view(view_id) is None for view_id in cited):
            raise AnalysisError("The review cited a view that is not part of this building's scan.")
    return parsed.model_dump(mode="json")


def run_assessment(survey: Survey, progress: Callable | None = None, on_activity: Callable[[dict], None] | None = None) -> tuple[dict, Session]:
    """Run the Deep Agents review of one building and return (report, session).

    The session is the state: if a lead run stops early or a provider call
    fails, a fresh lead is told to check the status and finish what is left.
    """
    config = analysis_config()
    if not config["configured"]:
        raise AnalysisError(config["message"], 503)
    if not survey.rooms:
        raise AnalysisError("This scan has no rooms to review.", 422)

    def notify(stage: str, message: str):
        if progress:
            progress(stage, message)

    session = Session(survey, on_activity)
    try:
        session.pool = ModelPool(
            {name: _chat_model(name) for name in config["models"]},
            on_event=lambda text: session.log("system", text),
        )
        model = session.pool.models[config["models"][0]]
        notify("geometry", "Loading the building's 3D mesh so findings can be placed in the model.")
        if survey.mesh() is None:
            session.log("system", "the scan's 3D mesh was unavailable; ceiling locations use the room height instead")
        notify("reviewing", "The lead agent is sending a surveyor into every room.")
        session.log("system", f"review started · {len(survey.rooms)} rooms · {len(survey.positions)} scan positions")
        for attempt in range(MAX_LEAD_RUNS):
            instruction = (
                f"Review this building: {survey.model['name']}." if attempt == 0
                else "Resume this building's review. Call review_status and delegate exactly the unfinished work it lists."
            )
            lead = build_lead(model, session)
            try:
                _run_lead(lead, session, instruction)
            except QuotaExhausted:
                raise AnalysisError(
                    "The vision service ran out of quota or credit partway through the review. No report was published. "
                    "Add credit, list more models in HOUSE_ANALYSIS_MODEL, or try again after the quota resets.", 429,
                ) from None
            except Exception as exc:  # provider or graph failure: the session keeps what was done
                logger.warning("Review lead run %d failed (%s)", attempt + 1, type(exc).__name__)
            if session.done():
                break
            if attempt + 1 < MAX_LEAD_RUNS:
                session.log("system", "the review is not complete yet; the lead is resuming unfinished work")
        if not session.done():
            # A half-finished review is not published as if it were whole.
            raise AnalysisError(
                "The review team could not finish every room and ruling. Check the vision service's API key, model access "
                "and quota, then try again. No report was published."
            )
        notify("validating", "Placing confirmed findings in the 3D model and checking the report.")
        report = build_report(session)
        session.log("system", "report assembled from the recorded findings")
        return report, session
    except AnalysisError:
        raise
    except ImportError:
        raise AnalysisError("The review dependencies are missing. Install the project's requirements and try again.", 503) from None
    except Exception as exc:
        # Do not log raw provider exception text: it may contain data or secrets.
        logger.warning("House review failed (%s)", type(exc).__name__)
        raise AnalysisError("The review could not be completed. Check the vision service's API key, model access and quota, then try again. No report was published.") from None
