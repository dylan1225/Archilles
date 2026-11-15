"""Shared fixtures: a small synthetic scan, so no test touches Matterport or a vision model."""

import io

import pytest
from PIL import Image

import building_evidence
import scan_geometry

IDENTITY = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}


def jpeg(colour=(180, 180, 170), size=64) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(output, format="JPEG")
    return output.getvalue()


def box_mesh(low=(0.0, 0.0, 0.0), high=(4.0, 3.0, 2.5)) -> scan_geometry.Mesh:
    """A closed room whose triangles face inwards, as Matterport's do."""
    (x0, y0, z0), (x1, y1, z1) = low, high
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [
        (0, 1, 2, 3),  # floor, normal +z
        (4, 7, 6, 5),  # ceiling, normal -z
        (0, 4, 5, 1),  # y = y0 wall, normal +y
        (3, 2, 6, 7),  # y = y1 wall, normal -y
        (0, 3, 7, 4),  # x = x0 wall, normal +x
        (1, 5, 6, 2),  # x = x1 wall, normal -x
    ]
    faces = [tri for a, b, c, d in quads for tri in ((a, b, c), (a, c, d))]
    return scan_geometry.Mesh(v, faces)


def position(position_id: str, room_id: str, x: float, y: float) -> dict:
    point = {"x": x, "y": y, "z": 1.4}
    return {
        "id": position_id, "room_id": room_id, "sweep_id": f"sweep-{position_id}", "position": point,
        "pose": {"position": point, "rotation": dict(IDENTITY)}, "floor_z": 0.0,
        "face_urls": [f"https://cdn-1.matterport.com/{position_id}/{face}.jpg?t=signed" for face in range(6)],
    }


@pytest.fixture
def survey(monkeypatch, tmp_path):
    """Two rooms: a bedroom scanned twice and a hallway scanned once."""
    monkeypatch.setenv("POSTMORTEM_CACHE_DIR", str(tmp_path / "cache"))
    rooms = [
        {"id": "R1", "name": "Bedroom", "kind": "Bedroom", "area_m2": 12.0, "height_m": 2.5, "width_m": 4.0, "depth_m": 3.0,
         "position_ids": ["R1-A", "R1-B"], "scan_positions_available": 2},
        {"id": "R2", "name": "Hallway", "kind": "Hallway", "area_m2": 3.0, "height_m": None, "width_m": 1.0, "depth_m": 3.0,
         "position_ids": ["R2-A"], "scan_positions_available": 1},
    ]
    positions = {
        "R1-A": position("R1-A", "R1", 2.0, 1.5),
        "R1-B": position("R1-B", "R1", 3.0, 2.0),
        "R2-A": position("R2-A", "R2", 1.0, 1.0),
    }
    scan = building_evidence.Survey(
        model={"id": "house123", "name": "Fixture house", "address": "1 Elm St, Los Angeles, CA 90001", "region": "los_angeles", "modified": None},
        field_note="mold, water damage in the bedroom",
        rooms=rooms, positions=positions, mesh_url=None, skipped_rooms=[],
    )
    monkeypatch.setattr(scan, "face_jpeg", lambda pos, face: jpeg())
    monkeypatch.setattr(scan, "overview_jpeg", lambda pos: jpeg(size=96))
    monkeypatch.setattr(scan, "mesh", box_mesh)
    return scan
