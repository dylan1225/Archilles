"""The review's rules live in its tools and report builder; test them without a model."""

import pytest

import house_analysis as ha


def tools_for(session):
    return {role: {tool.name: tool for tool in tools} for role, tools in ha.build_tools(session).items()}


FLAG = dict(
    view_id="R1-A-ceiling", left=400, top=400, right=600, bottom=600, title="Ceiling bulge beside the light",
    observation="A raised lump in the ceiling surface.", category="moisture",
    possible_consequence="If water is reaching it, a section could come away.", confidence="medium",
)
FORECAST = dict(
    finding_id="F1", ceiling_finish="unknown", why_it_could_fail="A wetted finish could sag and detach.",
    early_warning_signs=["A growing bulge", "New stains"], recommended_action="Have the roof above checked.", who_to_call="A roofer.",
)


def confirmed_session(survey):
    """One bedroom ceiling finding, flagged, confirmed and forecast through the tools."""
    session = ha.Session(survey)
    t = tools_for(session)
    for room in ("R1", "R2"):
        t["surveyor"]["open_room"].invoke({"room_id": room})
    t["surveyor"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    assert t["surveyor"]["flag_observation"].invoke(FLAG).startswith("Recorded as F1")
    for room in ("R1", "R2"):
        t["surveyor"]["finish_room"].invoke({"room_id": room, "checked": "Ceiling and walls."})
    t["skeptic"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["skeptic"]["second_view"].invoke({"finding_id": "F1"})
    assert t["skeptic"]["rule"].invoke({"finding_id": "F1", "verdict": "confirmed", "reason": "The lump is visible from both positions.", "confidence": "medium"}) == "F1 confirmed."
    t["analyst"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    assert t["analyst"]["forecast"].invoke(FORECAST).startswith("Recorded")
    return session


def test_room_tools_return_images_and_reject_unknown_ids(survey):
    session = ha.Session(survey)
    t = tools_for(session)
    opened = t["surveyor"]["open_room"].invoke({"room_id": "r1"})
    assert [block["type"] for block in opened] == ["text", "text", "image_url", "text", "image_url"]
    assert "ceiling height 2.5 m" in opened[0]["text"] and opened[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "not a room" in t["surveyor"]["open_room"].invoke({"room_id": "R9"})
    zoomed = t["surveyor"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    assert zoomed[1]["type"] == "image_url" and "R1-A-ceiling" in zoomed[0]["text"]
    assert "not a view" in t["surveyor"]["zoom"].invoke({"view_id": "R1-A-roof"})
    assert [e["text"] for e in session.activity] == ["opened Bedroom · 2 scan positions", "zoomed in on Bedroom, the ceiling, looking straight up (scan position R1-A)"]


def test_a_flag_needs_a_zoomed_view_a_real_box_and_respects_the_room_limit(survey):
    session = ha.Session(survey)
    flag = tools_for(session)["surveyor"]["flag_observation"]
    assert "Zoom into R1-A-ceiling" in flag.invoke(FLAG)
    tools_for(session)["surveyor"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    assert "too small" in flag.invoke({**FLAG, "right": 405})
    assert "needs a title" in flag.invoke({**FLAG, "observation": "  "})
    assert "not a view" in flag.invoke({**FLAG, "view_id": "nope"})
    # Reversed and out-of-range coordinates are normalised, not trusted.
    assert flag.invoke({**FLAG, "left": 600, "right": 400, "top": -50, "bottom": 2000}).startswith("Recorded as F1")
    assert session.candidates["F1"]["box"] == {"left": 0.4, "top": 0.0, "right": 0.6, "bottom": 1.0}
    flag.invoke(FLAG), flag.invoke(FLAG)
    assert "maximum" in flag.invoke(FLAG) and len(session.candidates) == 3


def test_zoom_budget_per_room(survey, monkeypatch):
    monkeypatch.setattr(ha, "MAX_ZOOMS_PER_ROOM", 2)
    session = ha.Session(survey)
    zoom = tools_for(session)["surveyor"]["zoom"]
    assert isinstance(zoom.invoke({"view_id": "R1-A-ceiling"}), list)
    assert isinstance(zoom.invoke({"view_id": "R1-A-ceiling"}), list)  # a repeat is free
    assert isinstance(zoom.invoke({"view_id": "R1-B-ceiling"}), list)
    assert "budget is used up" in zoom.invoke({"view_id": "R1-A-floor"})
    assert isinstance(zoom.invoke({"view_id": "R2-A-ceiling"}), list)  # another room has its own budget


def test_only_a_skeptic_who_looked_can_rule_and_confidence_never_rises(survey):
    session = ha.Session(survey)
    t = tools_for(session)
    t["surveyor"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["surveyor"]["flag_observation"].invoke(FLAG)
    listed = t["skeptic"]["list_candidates"].invoke({})
    assert '"finding_id": "F1"' in listed and "[400, 400, 600, 600]" in listed
    rule = t["skeptic"]["rule"]
    ruling = {"finding_id": "f1", "verdict": "confirmed", "reason": "Clearly raised.", "confidence": "high"}
    assert "Zoom into R1-A-ceiling yourself" in rule.invoke(ruling)  # the surveyor's zoom does not count
    t["skeptic"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    assert "needs a reason" in rule.invoke({**ruling, "reason": " "})
    assert "not a candidate" in rule.invoke({**ruling, "finding_id": "F7"})
    assert rule.invoke(ruling) == "F1 confirmed."
    assert session.candidates["F1"]["confidence"] == "medium"  # asked for high, capped at the surveyor's
    assert session.candidates["F1"]["placement"]["surface"] == "ceiling"
    assert "already has a ruling" in rule.invoke(ruling)
    assert t["skeptic"]["list_candidates"].invoke({}) == "No candidates are waiting for a ruling."


def test_second_view_uses_another_scan_position_when_there_is_one(survey):
    session = ha.Session(survey)
    t = tools_for(session)
    t["surveyor"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["surveyor"]["zoom"].invoke({"view_id": "R2-A-ceiling"})
    t["surveyor"]["flag_observation"].invoke(FLAG)
    t["surveyor"]["flag_observation"].invoke({**FLAG, "view_id": "R2-A-ceiling"})
    assert "R1-B-ceiling" in t["skeptic"]["second_view"].invoke({"finding_id": "F1"})[0]["text"]
    assert session.candidates["F1"]["second_view_id"] == "R1-B-ceiling"
    assert "one position only" in t["skeptic"]["second_view"].invoke({"finding_id": "F2"})


def test_forecast_needs_a_confirmed_ceiling_finding_the_analyst_has_seen(survey):
    session = ha.Session(survey)
    t = tools_for(session)
    t["surveyor"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["surveyor"]["zoom"].invoke({"view_id": "R1-A-wall-1"})
    t["surveyor"]["flag_observation"].invoke(FLAG)
    t["surveyor"]["flag_observation"].invoke({**FLAG, "view_id": "R1-A-wall-1"})
    forecast = t["analyst"]["forecast"]
    assert "not a confirmed finding" in forecast.invoke(FORECAST)
    for view in ("R1-A-ceiling", "R1-A-wall-1"):
        t["skeptic"]["zoom"].invoke({"view_id": view})
    t["skeptic"]["rule"].invoke({"finding_id": "F1", "verdict": "confirmed", "reason": "Visible.", "confidence": "low"})
    t["skeptic"]["rule"].invoke({"finding_id": "F2", "verdict": "confirmed", "reason": "Visible.", "confidence": "low"})
    assert "does not apply" in forecast.invoke({**FORECAST, "finding_id": "F2"})  # a wall
    assert "Forecast it rather than passing none" in forecast.invoke({**FORECAST, "finding_id": "none"})
    assert "Zoom into R1-A-ceiling before forecasting" in forecast.invoke(FORECAST)
    t["analyst"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    assert "at least two early-warning signs" in forecast.invoke({**FORECAST, "early_warning_signs": ["one"]})
    assert forecast.invoke(FORECAST).startswith("Recorded")
    assert "already has its forecast" in forecast.invoke(FORECAST)
    confirmed = t["analyst"]["confirmed_findings"].invoke({})
    assert '"fits_ceiling_scenario": true' in confirmed and '"surface": "wall"' in confirmed


def test_status_tracks_what_is_left(survey):
    session = ha.Session(survey)
    t = tools_for(session)
    assert session.unfinished()["rooms_not_surveyed"] == ["R1 (Bedroom)", "R2 (Hallway)"]
    assert '"scan_positions": 2' in t["lead"]["list_rooms"].invoke({})
    t["surveyor"]["open_room"].invoke({"room_id": "R1"})
    t["surveyor"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["surveyor"]["flag_observation"].invoke(FLAG)
    assert t["surveyor"]["finish_room"].invoke({"room_id": "R1", "checked": "All views."}) == "R1 (Bedroom) surveyed. Candidates: F1."
    t["surveyor"]["finish_room"].invoke({"room_id": "R2", "checked": ""})  # finished without ever opening it
    assert session.unfinished() == {"rooms_not_surveyed": [], "candidates_without_ruling": ["F1"], "forecast_pending_for": []}
    t["skeptic"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["skeptic"]["rule"].invoke({"finding_id": "F1", "verdict": "confirmed", "reason": "Visible.", "confidence": "low"})
    assert session.unfinished()["forecast_pending_for"] == ["F1"] and not session.done()
    assert '"forecast_pending_for": ["F1"]' in t["lead"]["review_status"].invoke({})
    report = ha.build_report(session)
    assert [room["surveyed"] for room in report["rooms"]] == [True, False] and report["not_surveyed"] == ["Hallway"]


def test_report_is_built_from_recorded_state_and_survives_a_round_trip(survey):
    session = confirmed_session(survey)
    assert session.done()
    report = ha.build_report(session)
    assert report["headline"] == "Bedroom: a ceiling section shows early signs of failing"
    finding = report["findings"][0]
    assert finding["id"] == "F1" and finding["verification"] == "The lump is visible from both positions."
    assert finding["second_view_id"] == "R1-B-ceiling" and finding["sweep_id"] == "sweep-R1-A"
    assert finding["placement"]["center"] == [2.0, 1.5, 2.5]
    # The technician's note mentions moisture; the agents never saw it.
    assert finding["corroborated_by_note"] is True and report["field_note"]["signals"] == ["water damage", "mold"]
    assert "never saw it" in report["field_note"]["agreement"]
    scenario = report["scenario"]
    assert scenario["finding_id"] == "F1" and scenario["calc"]["drop_height_m"] == 2.5
    assert [p["position_id"] for p in scenario["projectors"]] == ["R1-A", "R1-B"]
    assert scenario["bounds"]["min"][:2] == [-0.15, -0.15]
    # The analyst's finding leads, in the analyst's words, with a way to fix it.
    assert report["scenarios"][0] == scenario and scenario["written_by_analyst"] is True
    assert scenario["repair_plan"]["professional"]["low"] == scenario["repair_cost"]["now"]["low"]
    assert {"R1-A-ceiling", "R1-B-ceiling", "R1-A-wall-1", "R1-B-floor"} <= set(report["evidence_ids"])
    assert set(ha.MANDATORY_LIMITATIONS) <= set(report["limitations"]) and report["provenance"] == "agents"
    assert {source["id"] for source in report["sources"]} == {"iii_water_claims", "epa_moisture", "asce7_dead_loads", "nps_plaster", "inchcalculator_drywall", "bobvila_water_damage"}
    restored = ha.Session.restore(survey, session.snapshot())
    rebuilt = ha.build_report(restored)
    assert {**rebuilt, "generated_at": ""} == {**report, "generated_at": ""}


def test_rejected_candidates_are_shown_as_rejected_and_nothing_else(survey):
    session = ha.Session(survey)
    t = tools_for(session)
    for room in ("R1", "R2"):
        t["surveyor"]["open_room"].invoke({"room_id": room})
        t["surveyor"]["finish_room"].invoke({"room_id": room, "checked": "All."})
    t["surveyor"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["surveyor"]["flag_observation"].invoke(FLAG)
    t["skeptic"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["skeptic"]["rule"].invoke({"finding_id": "F1", "verdict": "rejected", "reason": "A shadow from the light fitting.", "confidence": "low"})
    assert "nothing to forecast" in t["analyst"]["confirmed_findings"].invoke({})
    assert t["analyst"]["forecast"].invoke({**FORECAST, "finding_id": "none"}).startswith("Recorded: no scenario")
    assert session.done()
    report = ha.build_report(session)
    assert report["findings"] == [] and report["scenario"] is None
    assert report["rejected"][0]["reason"] == "A shadow from the light fitting."
    assert report["headline"].startswith("No early signs") and "does not establish that the building is sound" in report["summary"]
    assert "did not show a matching condition" in report["field_note"]["agreement"]


def test_report_validation_rejects_tampering(survey):
    report = ha.build_report(confirmed_session(survey))
    assert ha.validate_report(report, survey) == report

    def broken(change):
        import copy
        candidate = copy.deepcopy(report)
        change(candidate)
        with pytest.raises(ha.AnalysisError):
            ha.validate_report(candidate, survey)

    broken(lambda r: r["findings"][0].update(view_id="R2-A-ceiling"))
    broken(lambda r: r["findings"][0]["box"].update(right=0.1))
    broken(lambda r: r["scenario"].update(finding_id="F9"))
    broken(lambda r: r["scenarios"][0]["projectors"][0]["faces"].append("R7-A-ceiling"))
    broken(lambda r: r["scenarios"].append({**r["scenarios"][0], "finding_id": "F9"}))
    broken(lambda r: r["scenarios"].reverse() or r["scenarios"].insert(0, {**r["scenarios"][0], "finding_id": "F1x"}))
    broken(lambda r: r["limitations"].pop())
    broken(lambda r: r["sources"].append({"id": "x", "title": "Made up", "url": "https://example.com"}))
    broken(lambda r: r.update(model_id="other"))
    broken(lambda r: r["evidence_ids"].append("R7-A-ceiling"))
    broken(lambda r: r.update(provenance="preview"))  # a preview must say how it was made
    broken(lambda r: r.update(extra_field=True))


def test_provider_limits_are_classified():
    assert ha._limit(ValueError("bad request")) is None
    assert ha._limit(Exception("429 RESOURCE_EXHAUSTED 'quotaValue': '5' Please retry in 27.7s")) == ("minute", 5, 28.7)
    assert ha._limit(Exception("429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier")) == ("day",)
    assert ha._limit(Exception("Error code: 402 - Insufficient credits")) == ("day",)
    assert ha._transient(Exception("RemoteProtocolError: Server disconnected")) and not ha._transient(ValueError("nope"))


def test_pool_falls_back_when_a_model_is_exhausted_and_keeps_conversations_together(monkeypatch):
    monkeypatch.setattr(ha.time, "sleep", lambda seconds: None)
    events, calls = [], []
    pool = ha.ModelPool({"first": "FIRST", "second": "SECOND"}, events.append)

    def invoke(model):
        calls.append(model)
        if model == "FIRST":
            raise Exception("429 RESOURCE_EXHAUSTED PerDay")
        return f"answer from {model}"

    assert pool.call("room 1", invoke) == "answer from SECOND"
    assert calls == ["FIRST", "SECOND"] and "first has no quota or credit left" in events[0]
    assert pool.pick("room 1") == "second" and pool.pick("room 2") == "second"
    with pytest.raises(ha.QuotaExhausted):
        ha.ModelPool({"only": "FIRST"}).call("room", invoke)


def test_pool_waits_out_a_per_minute_limit_and_retries_an_outage(monkeypatch):
    monkeypatch.setattr(ha.time, "sleep", lambda seconds: None)
    events = []
    pool = ha.ModelPool({"m": "M"}, events.append)
    failures = [Exception("429 RESOURCE_EXHAUSTED 'quotaValue': '5' retry in 0.0s"), Exception("RemoteProtocolError: Server disconnected")]

    def invoke(model):
        if failures:
            raise failures.pop(0)
        return "ok"

    assert pool.call("c", invoke) == "ok"
    assert pool.paces["m"].interval == pytest.approx(60 / 5 * 1.15) and "5 requests a minute" in events[0]
    with pytest.raises(ValueError):
        pool.call("c", lambda model: (_ for _ in ()).throw(ValueError("a real bug")))


def test_pool_takes_turns_when_the_key_cannot_cover_parallel_requests(monkeypatch):
    monkeypatch.setattr(ha.time, "sleep", lambda seconds: None)
    busy = Exception("Error code: 402 - This request would exceed your available credits given your current in-flight requests.")
    assert ha._limit(busy) == ("busy",)
    events, failures = [], [busy, busy]
    pool = ha.ModelPool({"m": "M"}, events.append)

    def invoke(model):
        if failures:
            raise failures.pop(0)
        assert pool.turn.locked()  # once told, calls hold the turn
        return "ok"

    assert pool.call("c", invoke) == "ok"
    assert pool.one_at_a_time and len(events) == 1 and "one request at a time" in events[0]
    assert "m" not in pool.retired  # busy is not the same as out of credit
    monkeypatch.setattr(ha, "MAX_BUSY_RETRIES", 2)
    with pytest.raises(ha.QuotaExhausted):
        pool.call("c", lambda model: (_ for _ in ()).throw(busy))


def test_provider_selection(monkeypatch):
    monkeypatch.setattr(ha, "load_dotenv", lambda *args, **kwargs: None)
    for name in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "HOUSE_ANALYSIS_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert ha.analysis_config()["configured"] is False
    # A Gemini key alone does not configure the review: every call goes through OpenRouter.
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    config = ha.analysis_config()
    assert config["provider"] == "openrouter" and config["configured"] is False and "OPENROUTER_API_KEY" in config["message"]
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    monkeypatch.setattr(ha, "OPENROUTER_MODELS", ["vendor/default"])
    assert ha.analysis_config()["models"] == ["vendor/default"] and ha.analysis_config()["provider"] == "openrouter"
    monkeypatch.setenv("HOUSE_ANALYSIS_MODEL", "vendor/a, vendor/b:free, bare-gemini-id, bad id!")
    assert ha.analysis_config()["models"] == ["vendor/a", "vendor/b:free"]


def test_an_unfinished_review_is_never_published(survey, monkeypatch):
    monkeypatch.setattr(ha, "analysis_config", lambda: {"configured": True, "provider": "openrouter", "model": "m", "models": ["m"], "message": ""})
    monkeypatch.setattr(ha, "_chat_model", lambda name: object())
    monkeypatch.setattr(ha, "build_lead", lambda model, session: session)
    runs = []

    def lazy_lead(session, _session, instruction):
        runs.append(instruction)
        tools_for(session)["surveyor"]["open_room"].invoke({"room_id": "R1"})  # opens a room, finishes nothing

    monkeypatch.setattr(ha, "_run_lead", lazy_lead)
    events = []
    with pytest.raises(ha.AnalysisError, match="could not finish"):
        ha.run_assessment(survey, on_activity=events.append)
    assert len(runs) == ha.MAX_LEAD_RUNS and runs[1].startswith("Resume")
    assert [event["round"] for event in events if "round" in event] == [1, 2, 3]

    def quota(session, _session, instruction):
        raise ha.QuotaExhausted

    monkeypatch.setattr(ha, "_run_lead", quota)
    with pytest.raises(ha.AnalysisError, match="ran out of quota or credit") as error:
        ha.run_assessment(survey)
    assert error.value.status_code == 429


def test_lead_startup_failures_get_three_recovery_rounds(survey, monkeypatch):
    monkeypatch.setattr(ha, "analysis_config", lambda: {"configured": True, "models": ["m"]})
    monkeypatch.setattr(ha, "_chat_model", lambda name: object())
    starts = []

    def interrupted_start(model, session):
        starts.append(session)
        raise RuntimeError("temporary startup failure")

    monkeypatch.setattr(ha, "build_lead", interrupted_start)
    with pytest.raises(ha.AnalysisError, match="could not finish"):
        ha.run_assessment(survey)
    assert len(starts) == 3
    assert all(session is starts[0] for session in starts)


def test_a_review_resumes_after_a_failed_run_and_publishes_when_complete(survey, monkeypatch):
    monkeypatch.setattr(ha, "analysis_config", lambda: {"configured": True, "provider": "openrouter", "model": "m", "models": ["m"], "message": ""})
    monkeypatch.setattr(ha, "_chat_model", lambda name: object())
    monkeypatch.setattr(ha, "build_lead", lambda model, session: session)
    attempts = []

    def lead(session, _session, instruction):
        attempts.append(instruction)
        t = tools_for(session)
        if len(attempts) == 1:
            t["surveyor"]["open_room"].invoke({"room_id": "R1"})
            t["surveyor"]["finish_room"].invoke({"room_id": "R1", "checked": "All."})
            raise RuntimeError("provider dropped the connection")
        t["surveyor"]["open_room"].invoke({"room_id": "R2"})
        t["surveyor"]["finish_room"].invoke({"room_id": "R2", "checked": "All."})
        t["analyst"]["forecast"].invoke({**FORECAST, "finding_id": "none"})

    monkeypatch.setattr(ha, "_run_lead", lead)
    events = []
    report, session = ha.run_assessment(survey, on_activity=events.append)
    assert len(attempts) == 2 and report["findings"] == [] and all(room["surveyed"] for room in report["rooms"])
    assert any("resuming unfinished work" in event["text"] for event in events)


def test_unconfigured_review_fails_before_any_work(survey, monkeypatch):
    monkeypatch.setattr(ha, "analysis_config", lambda: {"configured": False, "message": "Add a key."})
    with pytest.raises(ha.AnalysisError) as error:
        ha.run_assessment(survey)
    assert error.value.status_code == 503


def test_openrouter_free_tier_daily_cap_is_final_not_a_wait():
    daily = RuntimeError("Error code: 429 - Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day")
    assert ha._limit(daily) == ("day",)
    assert ha._limit(RuntimeError("Error code: 429 - Rate limit exceeded: free-models-per-min"))[0] == "minute"


def test_every_confirmed_finding_a_mechanism_fits_gets_its_own_scenario(survey):
    session = ha.Session(survey)
    t = tools_for(session)
    t["surveyor"]["open_room"].invoke({"room_id": "R1"})
    for view in ("R1-A-ceiling", "R1-A-wall-1"):
        t["surveyor"]["zoom"].invoke({"view_id": view})
        t["skeptic"]["zoom"].invoke({"view_id": view})
    t["surveyor"]["flag_observation"].invoke(FLAG)
    t["surveyor"]["flag_observation"].invoke({**FLAG, "view_id": "R1-A-wall-1", "title": "Brown drip trails on the wall"})
    for finding_id in ("F1", "F2"):
        t["skeptic"]["rule"].invoke({"finding_id": finding_id, "verdict": "confirmed", "reason": "Visible.", "confidence": "low"})
    t["analyst"]["zoom"].invoke({"view_id": "R1-A-ceiling"})
    t["analyst"]["forecast"].invoke(FORECAST)
    report = ha.build_report(session)
    lead, wall = report["scenarios"]
    assert report["scenario"] == lead and lead["finding_id"] == "F1" and lead["surface"] == "ceiling"
    # The wall finding is not the analyst's, so it is described by the mechanism, and says so.
    assert wall["finding_id"] == "F2" and wall["mechanism"] == "wall_moisture_damage" and wall["written_by_analyst"] is False
    assert wall["kind"] == "stain" and "low confidence" in wall["why"]
    # The room is measured to its ceiling, not cut off at the wall region's height.
    assert wall["bounds"]["max"][2] == lead["bounds"]["max"][2]
    assert {face for projector in wall["projectors"] for face in projector["faces"]} <= set(report["evidence_ids"])
    assert ha.validate_report(report, survey) == report
