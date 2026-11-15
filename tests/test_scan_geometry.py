"""Panorama pixel to 3D model: face convention, ray casting, region placement."""

import math

import pytest

import scan_geometry as sg
from tests.conftest import IDENTITY, box_mesh

POSE = {"position": {"x": 2.0, "y": 1.5, "z": 1.4}, "rotation": dict(IDENTITY)}


def test_face_centres_point_along_the_documented_axes():
    centres = [sg.face_direction(face, 0.5, 0.5) for face in range(6)]
    assert centres == [(0, 0, 1.0), (1.0, 0, 0), (0, -1.0, 0), (-1.0, 0, 0), (0, 1.0, 0), (0, 0, -1.0)]
    with pytest.raises(ValueError):
        sg.face_direction(6, 0.5, 0.5)


def test_face_edges_follow_the_calibrated_convention():
    # Ceiling view: image bottom is forward (+x), image right is -y.
    assert sg.face_direction(0, 0.5, 1.0) == (1.0, 0, 1.0)
    assert sg.face_direction(0, 1.0, 0.5) == (0, -1.0, 1.0)
    # Forward view: image right is -y, image down is -z.
    assert sg.face_direction(1, 1.0, 0.5) == (1.0, -1.0, 0)
    assert sg.face_direction(1, 0.5, 1.0) == (1.0, 0, -1.0)
    # Each next horizontal face is a quarter turn to the right.
    assert sg.face_direction(2, 1.0, 0.5) == (-1.0, -1.0, 0)


def test_rotation_turns_a_ray_with_the_scan_position():
    quarter = {"x": 0.0, "y": 0.0, "z": math.sin(math.pi / 4), "w": math.cos(math.pi / 4)}  # +90 degrees about z
    x, y, z = sg.rotate(quarter, (1.0, 0.0, 0.0))
    assert (round(x, 6), round(y, 6), round(z, 6)) == (0.0, 1.0, 0.0)
    origin, direction = sg.world_ray({"position": POSE["position"], "rotation": quarter}, 1, 0.5, 0.5)
    assert origin == (2.0, 1.5, 1.4) and round(direction[1], 6) == 1.0


def test_parse_obj_triangulates_and_rejects_empty_files():
    vertices, faces = sg.parse_obj("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1/1 2/2 3/3 4/4\nf -4 -3 -2\n")
    assert len(vertices) == 4 and faces == [(0, 1, 2), (0, 2, 3), (0, 1, 2)]
    for bad in ("", "v 0 0 0\n", "v 0 0 0\nf 1 2 3\n"):
        with pytest.raises(ValueError):
            sg.parse_obj(bad)


def test_raycast_hits_the_nearest_surface_with_a_room_facing_normal():
    mesh = box_mesh()
    up = mesh.raycast((2.0, 1.5, 1.4), (0.0, 0.0, 1.0))
    assert round(up["distance"], 6) == 1.1 and up["normal"] == (0.0, 0.0, -1.0)
    assert sg.surface_kind(up["normal"]) == "ceiling"
    assert sg.surface_kind(mesh.raycast((2.0, 1.5, 1.4), (0.0, 0.0, -1.0))["normal"]) == "floor"
    assert sg.surface_kind(mesh.raycast((2.0, 1.5, 1.4), (1.0, 0.0, 0.0))["normal"]) == "wall"
    assert mesh.raycast((2.0, 1.5, 1.4), (0.0, 0.0, 1.0), max_distance=0.5) is None


def test_region_on_the_ceiling_view_lands_on_the_ceiling():
    box = {"left": 0.4, "top": 0.4, "right": 0.6, "bottom": 0.6}
    placed = sg.locate_region(box_mesh(), POSE, 0, box, floor_z=0.0, ceiling_height=2.5)
    assert placed["surface"] == "ceiling" and placed["approximate"] is False
    assert placed["center"] == [2.0, 1.5, 2.5] and placed["height_above_floor_m"] == 2.5
    # The face spans 2 x 1.1 m at the ceiling, so a 20% box is 0.44 m square.
    assert placed["area_m2"] == pytest.approx(0.44 ** 2, abs=1e-3)
    # Without a mesh the same region drops onto a flat ceiling at the API's room height.
    flat = sg.locate_region(None, POSE, 0, box, floor_z=0.0, ceiling_height=2.5)
    assert flat["approximate"] is True and flat["center"] == placed["center"] and flat["area_m2"] == placed["area_m2"]


def test_region_without_mesh_or_height_cannot_be_placed():
    box = {"left": 0.4, "top": 0.4, "right": 0.6, "bottom": 0.6}
    assert sg.locate_region(None, POSE, 0, box, 0.0, None) is None
    assert sg.locate_region(None, POSE, 1, box, 0.0, 2.5) is None


def test_region_straddling_two_surfaces_is_not_trusted():
    # A box on the forward view that runs from the wall up onto the ceiling.
    box = {"left": 0.4, "top": 0.0, "right": 0.6, "bottom": 0.5}
    assert sg.locate_region(box_mesh(), POSE, 1, box, 0.0, None) is None
    wall = sg.locate_region(box_mesh(), POSE, 1, {"left": 0.4, "top": 0.4, "right": 0.6, "bottom": 0.6}, 0.0, None)
    assert wall["surface"] == "wall" and wall["center"][0] == 4.0


def test_room_bounds_follow_the_walls_and_crop_keeps_inner_triangles():
    mesh = box_mesh()
    bounds = sg.room_bounds(mesh, (2.0, 1.5, 1.4), 0.0, 2.5)
    assert bounds["min"][:2] == [-0.15, -0.15] and bounds["max"][:2] == [4.15, 3.15]
    assert len(mesh.cropped((-1, -1, -1), (5, 4, 3)).faces) == 12
    assert len(mesh.cropped((-1, -1, -1), (5, 4, 1)).faces) == 2  # only the floor fits
    payload = mesh.payload()
    assert len(payload["positions"]) == 24 and len(payload["indices"]) == 36
