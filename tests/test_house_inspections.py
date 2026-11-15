"""HTTP jobs and saved reviews, with the scan and the agents replaced by fixtures."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

import building_evidence
import house_analysis
import house_inspections as service
import main
from tests.test_house_analysis import confirmed_session


@pytest.fixture
def client(monkeypatch, survey):
    monkeypatch.setattr(service, "_JOBS", {})
    monkeypatch.setattr(service, "_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(house_analysis, "analysis_config", lambda: {"configured": True, "provider": "fixture", "model": "fixture-vision", "models": ["fixture-vision"], "message": "Ready"})
    monkeypatch.setattr(building_evidence, "collect_survey", lambda model_id, progress=None: survey)

    def run(scan, progress=None, on_activity=None):
        session = confirmed_session(scan)
        for event in session.activity:
            on_activity(event)
        return house_analysis.build_report(session), session

    monkeypatch.setattr(house_analysis, "run_assessment", run)
    with TestClient(main.app) as test_client:
        yield test_client


def start(client, model_id="house123"):
    response = client.post("/api/interiors/assessments", json={"model_id": model_id})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_live_review_completes_is_saved_and_replays_without_the_agents(client, monkeypatch):
    assert client.get("/api/interiors/reviews/house123").status_code == 404
    job = client.get(f"/api/interiors/assessments/{start(client)}").json()
    assert job["status"] == "completed" and job["error"] is None
    assert job["report"]["model_id"] == "house123" and job["report"]["scenario"]["finding_id"] == "F1"
    assert job["evidence_base"] == "/api/interiors/reviews/house123/evidence"
    assert [event["agent"] for event in job["activity"]][:2] == ["surveyor", "surveyor"]

    # From here on nothing may call the scan or the agents again.
    def forbidden(*args, **kwargs):
        raise AssertionError("a saved review must replay from disk")

    monkeypatch.setattr(building_evidence, "collect_survey", forbidden)
    monkeypatch.setattr(house_analysis, "run_assessment", forbidden)
    saved = client.get("/api/interiors/reviews/house123")
    assert saved.status_code == 200 and saved.headers["cache-control"] == "no-store"
    body = saved.json()
    assert body["report"] == job["report"] and body["activity"] == job["activity"] and body["saved_at"]
    for view_id in body["report"]["evidence_ids"]:
        image = client.get(f"{body['evidence_base']}/{view_id}")
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
        assert image.headers["x-content-type-options"] == "nosniff" and image.content[:2] == b"\xff\xd8"
    assert client.get("/api/interiors/reviews/house123/evidence/R2-A-ceiling").status_code == 404  # not cited
    assert "signed" not in saved.text and "cdn-1.matterport.com" not in saved.text


def test_rebuild_rederives_the_report_from_recorded_state(client, monkeypatch):
    start(client)
    monkeypatch.setattr(house_analysis, "run_assessment", None)  # no agents on a rebuild
    rebuilt = service.rebuild_review("house123")
    assert rebuilt["findings"][0]["id"] == "F1"
    assert client.get("/api/interiors/reviews/house123").json()["report"]["generated_at"] == rebuilt["generated_at"]


def test_a_tampered_or_mismatched_saved_review_is_not_served(client):
    start(client)
    path = service.review_dir("house123") / "review.json"
    payload = json.loads(path.read_text())
    payload["report"]["findings"][0]["view_id"] = "R9-Z-ceiling"
    path.write_text(json.dumps(payload))
    assert client.get("/api/interiors/reviews/house123").status_code == 404
    path.write_text("{not json")
    assert client.get("/api/interiors/reviews/house123").status_code == 404
    assert service.has_saved_review("house123") is True and service.has_saved_review("../x") is False


def test_failures_are_reported_and_publish_nothing(client, monkeypatch):
    def fail(scan, progress=None, on_activity=None):
        raise house_analysis.AnalysisError("The review team could not finish every room and ruling.")

    monkeypatch.setattr(house_analysis, "run_assessment", fail)
    job = client.get(f"/api/interiors/assessments/{start(client)}").json()
    assert job["status"] == "failed" and job["report"] is None and job["evidence_base"] is None
    assert job["error"] == "The review team could not finish every room and ruling."
    assert client.get("/api/interiors/reviews/house123").status_code == 404

    def crash(scan, progress=None, on_activity=None):
        raise RuntimeError("secret-token-in-provider-message")

    monkeypatch.setattr(house_analysis, "run_assessment", crash)
    job = client.get(f"/api/interiors/assessments/{start(client)}").json()
    assert job["status"] == "failed" and "secret-token" not in json.dumps(job)

    def out_of_scope(model_id, progress=None):
        raise building_evidence.EvidenceError("Reviews are available only for active, public Houston and Los Angeles scans in this collection.", 403)

    monkeypatch.setattr(building_evidence, "collect_survey", out_of_scope)
    job = client.get(f"/api/interiors/assessments/{start(client)}").json()
    assert job["status"] == "failed" and "Houston and Los Angeles" in job["error"]


def test_requests_are_validated_and_unknown_things_are_404(client, monkeypatch):
    assert client.post("/api/interiors/assessments", json={"model_id": "../etc"}).status_code == 422
    assert client.post("/api/interiors/assessments", json={"model_id": "ok1", "context": "x"}).status_code == 422
    assert client.get("/api/interiors/assessments/" + "0" * 32).status_code == 404
    assert client.get("/api/interiors/assessments/not-a-job").status_code == 422
    assert client.get("/api/interiors/reviews/house123/evidence/..%2Freview").status_code in (404, 422)
    assert client.get("/api/interiors/reviews/house123/evidence/R1-A-roof").status_code == 422
    assert client.get("/api/interiors/models/house123/mesh").status_code == 404
    monkeypatch.setattr(house_analysis, "analysis_config", lambda: {"configured": False, "message": "Add a key."})
    response = client.post("/api/interiors/assessments", json={"model_id": "house123"})
    assert response.status_code == 503 and response.json() == {"error": "Add a key."}
    assert client.get("/api/interiors/analysis-config").json()["configured"] is False


def test_cached_mesh_is_served(client):
    path = building_evidence.mesh_path("house123")
    path.parent.mkdir(parents=True)
    path.write_text('{"positions":[0,0,0,1,0,0,0,1,0],"indices":[0,1,2]}')
    response = client.get("/api/interiors/models/house123/mesh")
    assert response.status_code == 200 and response.json()["indices"] == [0, 1, 2]


def test_a_running_review_is_shared_and_capacity_is_bounded(client, monkeypatch):
    monkeypatch.setattr(service, "_run_job", lambda job_id, request: None)  # leave jobs queued
    first = start(client)
    assert start(client) == first  # the same building joins the running review
    start(client, "other1")
    assert client.post("/api/interiors/assessments", json={"model_id": "other2"}).status_code == 429
