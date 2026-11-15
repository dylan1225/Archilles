"""Read-only API contract, Houston and Los Angeles scope, field-note leads, error handling, credential isolation."""

import io
import json
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

import interiors
import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MATTERPORT_API_KEY", "server-key")
    monkeypatch.setenv("MATTERPORT_API_TOKEN", "server-secret")
    monkeypatch.setenv("MATTERPORT_SDK_KEY", "public-sdk")
    monkeypatch.setenv("MATTERPORT_SDK_DOMAINS", "localhost")
    with TestClient(main.app) as client:
        yield client


def mock_response(monkeypatch, payload):
    requests = []

    def urlopen(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(interiors, "urlopen", urlopen)
    return requests


def result(models=None, **kwargs):
    return {"data": {"models": {
        "totalResults": 1,
        "nextOffset": None,
        "results": models if models is not None else [{
            "id": "abc123",
            "name": "Houston home",
            "address": {"streetAddressLines": ["123 Main St"], "locality": "Houston", "administrativeArea": "TX", "postalCode": "77002"},
            "image": {"url": "https://cdn-1.matterport.com/photo.jpg"},
            "floors": {"count": 2},
        }],
        **kwargs,
    }}}


def test_standalone_page_and_reusable_component(client):
    response = client.get("/interiors")
    assert response.status_code == 200
    assert '<building-interior-viewer api-base="/api/interiors">' in response.text
    assert client.get("/static/building-interior-viewer.js").status_code == 200
    assert 'href="/interiors"' in client.get("/").text


def test_models_are_scoped_to_the_public_collection_and_secrets_stay_on_server(client, monkeypatch):
    requests = mock_response(monkeypatch, result())
    response = client.get("/api/interiors/models")
    assert response.status_code == 200
    data = response.json()
    assert data["models"][0] == {
        "id": "abc123", "name": "Houston home", "address": "123 Main St, Houston, TX 77002",
        "thumbnail": "https://cdn-1.matterport.com/photo.jpg", "floors": 2,
    }
    assert data["sdkKey"] == "public-sdk"
    assert data["sdkDomains"] == ["localhost"]
    assert "server-secret" not in response.text
    assert "server-key" not in response.text
    request, timeout = requests[0]
    assert request.full_url == interiors.ENDPOINT
    assert request.get_method() == "POST"
    assert request.get_header("Authorization").startswith("Basic ")
    body = json.loads(request.data)
    assert body["variables"]["query"] == interiors.COLLECTION_QUERY
    assert "Houston" in interiors.COLLECTION_QUERY and "Los Angeles" in interiors.COLLECTION_QUERY and "visibility:public" in interiors.COLLECTION_QUERY
    assert "mutation" not in body["query"]
    assert timeout == 20


def test_literal_search_and_pagination(client, monkeypatch):
    requests = mock_response(monkeypatch, result(nextOffset="next-page", totalResults=25))
    response = client.get("/api/interiors/models", params={"q": '  Main " OR *  ', "offset": "opaque-cursor", "page_size": 6})
    assert response.status_code == 200
    variables = json.loads(requests[0][0].data)["variables"]
    assert variables["offset"] == "opaque-cursor"
    assert variables["pageSize"] == 6
    assert variables["query"].startswith(interiors.COLLECTION_QUERY + " AND (")
    assert r'Main \" OR \*' in variables["query"]
    assert response.json()["nextOffset"] == "next-page"
    assert response.json()["total"] == 25


@pytest.mark.parametrize("params", [{"page_size": 0}, {"page_size": 25}, {"q": "x" * 121}, {"offset": "x" * 16385}])
def test_invalid_requests_never_call_provider(client, monkeypatch, params):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid requests must not reach Matterport")
    monkeypatch.setattr(interiors, "urlopen", unexpected)
    response = client.get("/api/interiors/models", params=params)
    assert response.status_code == 422
    assert "error" in response.json()


def test_empty_and_optional_model_metadata(client, monkeypatch):
    mock_response(monkeypatch, result(models=[{"id": "xyz789", "address": None, "image": None, "floors": None}]))
    model = client.get("/api/interiors/models").json()["models"][0]
    assert model["name"] == "Untitled building"
    assert model["address"] == ""
    assert model["thumbnail"] is None
    mock_response(monkeypatch, result(models=[], totalResults=0))
    response = client.get("/api/interiors/models")
    assert response.json()["models"] == []
    assert response.json()["total"] == 0


def test_unconfigured_server(client, monkeypatch):
    monkeypatch.delenv("MATTERPORT_API_TOKEN")
    response = client.get("/api/interiors/models")
    assert response.status_code == 503
    assert "not configured" in response.json()["error"]
    assert "server-key" not in response.text


@pytest.mark.parametrize("error,status", [
    (HTTPError(interiors.ENDPOINT, 401, "server-secret", {}, None), 503),
    (HTTPError(interiors.ENDPOINT, 403, "server-secret", {}, None), 503),
    (HTTPError(interiors.ENDPOINT, 429, "server-secret", {}, None), 429),
    (HTTPError(interiors.ENDPOINT, 500, "server-secret", {}, None), 502),
    (URLError("server-secret"), 502),
    (TimeoutError("server-secret"), 504),
])
def test_transport_errors_are_safe_json(client, monkeypatch, error, status):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(interiors, "urlopen", fail)
    response = client.get("/api/interiors/models")
    assert response.status_code == status
    assert response.json()["error"]
    assert "server-secret" not in response.text


@pytest.mark.parametrize("payload,status", [
    ({"errors": [{"message": "server-secret", "extensions": {"code": "request.unauthorized"}}]}, 503),
    ({"errors": [{"message": "server-secret"}], **result()}, 502),
    ({"data": {"models": None}}, 502),
    ({"data": None}, 502),
    ([], 502),
])
def test_graphql_errors_and_invalid_shapes(client, monkeypatch, payload, status):
    mock_response(monkeypatch, payload)
    response = client.get("/api/interiors/models")
    assert response.status_code == status
    assert "server-secret" not in response.text


def test_invalid_provider_json(client, monkeypatch):
    monkeypatch.setattr(interiors, "urlopen", lambda *args, **kwargs: io.BytesIO(b"<html>Service down</html>"))
    response = client.get("/api/interiors/models")
    assert response.status_code == 502
    assert "unreadable response" in response.json()["error"]


def test_region_filter_and_region_of(client, monkeypatch):
    requests = mock_response(monkeypatch, result())
    assert client.get("/api/interiors/models", params={"region": "los_angeles"}).status_code == 200
    query = json.loads(requests[0][0].data)["variables"]["query"]
    assert "Los Angeles" in query and "Houston" not in query
    assert client.get("/api/interiors/models", params={"region": "dallas"}).status_code == 422
    assert interiors.region_of({"locality": "Houston", "administrativeArea": "Texas"}) == "houston"
    assert interiors.region_of({"locality": "Los Angeles, Los Angeles", "administrativeArea": "CA"}) == "los_angeles"
    assert interiors.region_of({"locality": "Dallas", "administrativeArea": "TX"}) is None
    assert interiors.region_of(None) is None


def test_note_signals_find_defect_words_and_ignore_lookalikes():
    labels = [signal["label"] for signal in interiors.note_signals("house smells horrible, mold, water damage, lights dont work")]
    assert labels[:2] == ["water damage", "mold"] and "lights not working" in labels
    assert [s["label"] for s in interiors.note_signals("avoid master closet (ceiling leak)")] == ["leak"]
    # A door left "cracked", a water hose and a place name are not defects.
    assert interiors.note_signals("left the door cracked; water hose on back porch; The Water Mill") == []
    assert interiors.note_excerpt("first paragraph\n\nSECOND PARAGRAPH") == "first paragraph"


def test_leads_rank_field_notes_and_drop_weak_or_out_of_scope(client, monkeypatch):
    monkeypatch.setattr(interiors, "_leads_cache", None)
    address = {"streetAddressLines": ["1 Elm St"], "locality": "Los Angeles", "administrativeArea": "CA", "postalCode": "90001"}
    requests = mock_response(monkeypatch, {"data": {"models": {"results": [
        {"id": "weak1", "name": "Weak", "description": "house smells a bit", "address": address},
        {"id": "leak1", "name": "Leak", "description": "avoid closet (ceiling leak)", "address": address},
        {"id": "wet1", "name": "Wet", "description": "mold, water damage\n\nUNRELATED SECOND PARAGRAPH", "address": address},
        {"id": "far1", "name": "Far", "description": "water damage", "address": {"locality": "Dallas", "administrativeArea": "TX"}},
    ]}}})
    leads = client.get("/api/interiors/leads").json()["leads"]
    assert [lead["id"] for lead in leads] == ["wet1", "leak1"]
    assert leads[0]["note"] == "mold, water damage" and leads[0]["region"] == "los_angeles"
    assert leads[0]["has_saved_review"] is False
    query = json.loads(requests[0][0].data)["variables"]["query"]
    assert query.startswith(interiors.COLLECTION_QUERY) and "description:(" in query
    # Cached: a second request does not call the provider again.
    client.get("/api/interiors/leads")
    assert len(requests) == 1
