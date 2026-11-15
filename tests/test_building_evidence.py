"""Survey loading: scope, room naming, safe asset handling, on-demand images, mesh cache."""

import io
import json

import pytest
from PIL import Image

import building_evidence as be
import interiors
from tests.conftest import IDENTITY, box_mesh, jpeg


def faces(tag):
    return [f"https://cdn-2.matterport.com/{tag}/{face}.jpg?t=signed-{face}" for face in range(6)]


def location(room_id, x, y, tag, **overrides):
    pano = {"id": tag, "placement": "auto", "position": {"x": x, "y": y, "z": 1.4}, "rotation": dict(IDENTITY), "skybox": {"status": "available", "children": faces(tag)}}
    pano.update(overrides)
    return {"id": f"sweep-{tag}", "position": {"x": x, "y": y, "z": 0.0}, "room": {"id": room_id} if room_id else None, "panos": [pano]}


def room(room_id, label, area, height=2.5):
    return {"id": room_id, "label": None, "classifications": [{"label": label, "confidence": 1.0}], "dimensions": {"areaFloor": area, "height": height, "width": 3.0, "depth": 3.0}}


def model(**overrides):
    data = {
        "id": "house123", "name": "1 Elm St", "description": "mold, water damage", "visibility": "public", "state": "active", "modified": "2026-01-01T00:00:00Z",
        "address": {"streetAddressLines": ["1 Elm St"], "locality": "Los Angeles, Los Angeles", "administrativeArea": "CA", "postalCode": "90001"},
        "rooms": [room("small", "Bedroom", 9.0), room("big", "Bedroom", 12.0), room("void", "Void", 1.0), room("empty", "Closet", 2.0), room("hall", "Hallway", 3.0, None)],
        "locations": [
            location("big", 1, 1, "a"), location("big", 3, 1, "b"), location("big", 1, 3, "c"), location("big", 2.9, 1.1, "d"),
            location("small", 6, 1, "e"), location("hall", 4, 4, "f"), location("void", 9, 9, "g"), location(None, 20, 20, "yard"),
        ],
        "assets": {"meshes": [
            {"resolution": "500k", "status": "available", "filename": "chunk.obj", "url": "https://cdn-2.matterport.com/chunk.obj?t=x"},
            {"resolution": "50k", "status": "available", "filename": "defurnished_50k.obj", "url": "https://cdn-2.matterport.com/whole.obj?t=x"},
        ]},
    }
    data.update(overrides)
    return {"model": data}


@pytest.fixture
def graphql(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTMORTEM_CACHE_DIR", str(tmp_path / "cache"))

    def install(payload):
        monkeypatch.setattr(interiors, "_request_graphql", lambda query, variables: payload)

    return install


def test_survey_names_rooms_largest_first_and_spreads_scan_positions(graphql):
    graphql(model())
    survey = be.collect_survey("house123")
    assert survey.model == {"id": "house123", "name": "1 Elm St", "address": "1 Elm St, Los Angeles, CA 90001", "region": "los_angeles", "modified": "2026-01-01T00:00:00Z"}
    assert survey.field_note == "mold, water damage"
    # Voids, rooms never scanned and outdoor positions are left out.
    assert [(r["id"], r["name"]) for r in survey.rooms] == [("R1", "Bedroom 1"), ("R2", "Bedroom 2"), ("R3", "Hallway")]
    big = survey.rooms[0]
    assert big["area_m2"] == 12.0 and big["scan_positions_available"] == 4 and big["position_ids"] == ["R1-A", "R1-B", "R1-C"]
    # Of four positions, the near-duplicate of "b" is the one dropped.
    assert {survey.positions[pid]["sweep_id"] for pid in big["position_ids"]} == {"sweep-a", "sweep-b", "sweep-c"}
    assert survey.rooms[2]["height_m"] is None
    assert survey._mesh_url.startswith("https://cdn-2.matterport.com/whole.obj")
    assert survey.parse_view("R1-A-ceiling") == (survey.positions["R1-A"], 0)
    assert survey.parse_view("R1-A-wall-3")[1] == 3 and survey.parse_view("R1-A-floor")[1] == 5
    for bad in ("R9-A-ceiling", "R1-Z-ceiling", "R1-A-roof", "R1-A", "", "../R1-A-ceiling"):
        assert survey.parse_view(bad) is None
    assert "Bedroom 1" in survey.describe_view("R1-A-ceiling") and "straight up" in survey.describe_view("R1-A-ceiling")


@pytest.mark.parametrize("overrides, status", [
    ({"visibility": "private"}, 403),
    ({"state": "inactive"}, 403),
    ({"address": {"locality": "Dallas", "administrativeArea": "TX"}}, 403),
    ({"locations": []}, 422),
])
def test_out_of_scope_or_unscanned_buildings_are_refused(graphql, overrides, status):
    graphql(model(**overrides))
    with pytest.raises(be.EvidenceError) as error:
        be.collect_survey("house123")
    assert error.value.status_code == status


def test_invalid_ids_and_missing_models(graphql):
    graphql({"model": None})
    with pytest.raises(be.EvidenceError) as error:
        be.collect_survey("house123")
    assert error.value.status_code == 404
    with pytest.raises(be.EvidenceError) as error:
        be.collect_survey("../etc")
    assert error.value.status_code == 422


def test_positions_with_unsafe_or_incomplete_panoramas_are_skipped(graphql):
    unsafe = faces("x")
    unsafe[2] = "https://evil.example.com/2.jpg"
    graphql(model(locations=[
        location("big", 1, 1, "unsafe", skybox={"status": "available", "children": unsafe}),
        location("big", 2, 2, "pending", skybox={"status": "unavailable", "children": faces("p")}),
        location("big", 2, 2, "unplaced", placement="unplaced"),
        location("big", 3, 3, "good"),
    ]))
    survey = be.collect_survey("house123")
    assert [p["sweep_id"] for p in survey.positions.values()] == ["sweep-good"]


@pytest.mark.parametrize("url", [
    "http://cdn-1.matterport.com/a.jpg", "https://cdn-1.matterport.com.evil.com/a.jpg", "https://user:pw@cdn-1.matterport.com/a.jpg",
    "https://cdn-1.matterport.com:8443/a.jpg", "https://my.matterport.com/a.jpg", "https://cdn-1.matterport.com/a.jpg#frag", "",
])
def test_only_matterport_cdn_addresses_are_fetched(url):
    with pytest.raises(be.EvidenceError):
        be._safe_asset_url(url)
    assert be._safe_asset_url("https://cdn-12.matterport.com/a.jpg?t=1") == "https://cdn-12.matterport.com/a.jpg?t=1"


def test_images_are_normalised_cached_and_combined_into_a_labelled_sheet(graphql, monkeypatch):
    graphql(model())
    survey = be.collect_survey("house123")
    downloads = []

    def download(url, limit, accept):
        downloads.append(url)
        output = io.BytesIO()
        Image.new("RGB", (2048, 2048), (200, 190, 180)).save(output, format="PNG")
        return output.getvalue()

    monkeypatch.setattr(be, "_download", download)
    pos = survey.positions["R1-A"]
    face = Image.open(io.BytesIO(survey.face_jpeg(pos, 0)))
    assert face.format == "JPEG" and face.size == (1024, 1024)
    survey.face_jpeg(pos, 0)
    assert len(downloads) == 1  # cached
    sheet = Image.open(io.BytesIO(survey.overview_jpeg(pos)))
    assert sheet.size == (1536, 1024) and len(downloads) == 6
    with pytest.raises(be.EvidenceError):
        be._normalized_image(b"not an image")


def test_mesh_is_trimmed_to_the_interior_cached_on_disk_and_reused(graphql, monkeypatch):
    graphql(model())
    survey = be.collect_survey("house123")
    house = box_mesh((0, 0, 0), (7, 5, 2.5))
    tree = box_mesh((30, 30, 0), (31, 31, 9))
    lines = [f"v {x} {y} {z}" for x, y, z in house.vertices + tree.vertices]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in house.faces] + [f"f {a + 9} {b + 9} {c + 9}" for a, b, c in tree.faces]
    calls = []
    monkeypatch.setattr(be, "_download", lambda url, limit, accept: calls.append(url) or "\n".join(lines).encode())
    mesh = survey.mesh()
    assert len(mesh.faces) == 12 and len(calls) == 1  # the tree is outside the interior box
    assert survey.mesh() is mesh and len(calls) == 1
    saved = json.loads(be.mesh_path("house123").read_text())
    assert len(saved["indices"]) == 36
    assert len(be.load_cached_mesh("house123").faces) == 12
    # A later survey reads the cache instead of the (expired) signed address.
    again = be.collect_survey("house123")
    assert len(again.mesh().faces) == 12 and len(calls) == 1
    assert be.load_cached_mesh("missing") is None
    with pytest.raises(be.EvidenceError):
        be.mesh_path("../escape")


def test_unavailable_mesh_is_tolerated(graphql, monkeypatch):
    graphql(model(assets={"meshes": []}))
    assert be.collect_survey("house123").mesh() is None

    def fail(url, limit, accept):
        raise be.EvidenceError("down")

    graphql(model())
    monkeypatch.setattr(be, "_download", fail)
    assert be.collect_survey("house123").mesh() is None
    assert jpeg()  # fixture helper stays importable
