"""Scan geometry: place a pixel of a panorama on the building's real 3D mesh.

A Matterport panorama is six square cube faces around one scan position. The
Model API gives each scan position a pose (position + rotation quaternion) in
the same Z-up, metre-scaled frame as the downloadable OBJ mesh. That is enough
to turn "this region of this ceiling photo" into a point on the actual ceiling.

Face convention, verified against the mesh (wall-ceiling joints and room
corners in the photographs line up with the geometry):

    local frame: +x forward, +y left, +z up
    face 0 = up      image bottom edge towards +x
    face 1 = +x      then each next face turns 90 degrees to the right
    face 2 = -y
    face 3 = -x
    face 4 = +y
    face 5 = down    image top edge towards +x

Pure Python on purpose: a handful of rays against ~30k triangles takes well
under a second, and the project needs no numeric dependency for it.
"""

from __future__ import annotations

import math

FACE_NAMES = ("ceiling", "wall-1", "wall-2", "wall-3", "wall-4", "floor")
Vec = tuple[float, float, float]


def rotate(q: dict, v: Vec) -> Vec:
    """Rotate v by the unit quaternion q = {x, y, z, w}."""
    x, y, z, w = q["x"], q["y"], q["z"], q["w"]
    norm = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v);  v' = v + w * t + cross(q.xyz, t)
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def face_direction(face: int, u: float, v: float) -> Vec:
    """Direction in the panorama's local frame for a point on a cube face.

    u and v are fractions of the image width and height (0..1), origin top-left.
    """
    a, b = 2 * u - 1, 2 * v - 1  # a: image right, b: image down
    if face == 0:
        return (b, -a, 1.0)
    if face == 5:
        return (-b, -a, -1.0)
    if face == 1:
        return (1.0, -a, -b)
    if face == 2:
        return (-a, -1.0, -b)
    if face == 3:
        return (-1.0, a, -b)
    if face == 4:
        return (a, 1.0, -b)
    raise ValueError("A cube face index must be between 0 and 5.")


def world_ray(pose: dict, face: int, u: float, v: float) -> tuple[Vec, Vec]:
    """Origin and unit direction, in model coordinates, for a face pixel."""
    p = pose["position"]
    d = rotate(pose["rotation"], face_direction(face, u, v))
    length = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
    return (p["x"], p["y"], p["z"]), (d[0] / length, d[1] / length, d[2] / length)


def parse_obj(text: str) -> tuple[list[Vec], list[tuple[int, int, int]]]:
    """Vertices and triangles from an OBJ. Polygons are fan-triangulated."""
    vertices: list[Vec] = []
    faces: list[tuple[int, int, int]] = []
    for line in text.splitlines():
        if line.startswith("v "):
            parts = line.split()
            vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif line.startswith("f "):
            ids = []
            for token in line.split()[1:]:
                index = int(token.split("/")[0])
                ids.append(index - 1 if index > 0 else len(vertices) + index)
            for k in range(1, len(ids) - 1):
                faces.append((ids[0], ids[k], ids[k + 1]))
    count = len(vertices)
    if not count or not faces or any(not 0 <= i < count for face in faces for i in face):
        raise ValueError("The mesh file has no usable geometry.")
    return vertices, faces


class Mesh:
    """Triangle soup with a nearest-hit ray query."""

    def __init__(self, vertices: list[Vec], faces: list[tuple[int, int, int]]):
        self.vertices = vertices
        self.faces = faces

    def raycast(self, origin: Vec, direction: Vec, max_distance: float = 30.0) -> dict | None:
        """Nearest intersection as {point, normal, distance}, or None.

        The normal follows triangle winding. Matterport meshes face into the
        room, so a ceiling hit from below has a downward normal.
        """
        ox, oy, oz = origin
        dx, dy, dz = direction
        best_t, best = max_distance, None
        reach = max_distance * max_distance
        vertices = self.vertices
        for face in self.faces:
            ax, ay, az = vertices[face[0]]
            # Cheap reject: a triangle whose first vertex is out of reach.
            if (ax - ox) ** 2 + (ay - oy) ** 2 + (az - oz) ** 2 > reach * 4:
                continue
            bx, by, bz = vertices[face[1]]
            cx, cy, cz = vertices[face[2]]
            e1x, e1y, e1z = bx - ax, by - ay, bz - az
            e2x, e2y, e2z = cx - ax, cy - ay, cz - az
            px, py, pz = dy * e2z - dz * e2y, dz * e2x - dx * e2z, dx * e2y - dy * e2x
            det = e1x * px + e1y * py + e1z * pz
            if -1e-10 < det < 1e-10:
                continue
            inv = 1.0 / det
            tx, ty, tz = ox - ax, oy - ay, oz - az
            u = (tx * px + ty * py + tz * pz) * inv
            if u < 0 or u > 1:
                continue
            qx, qy, qz = ty * e1z - tz * e1y, tz * e1x - tx * e1z, tx * e1y - ty * e1x
            v = (dx * qx + dy * qy + dz * qz) * inv
            if v < 0 or u + v > 1:
                continue
            t = (e2x * qx + e2y * qy + e2z * qz) * inv
            if 1e-3 < t < best_t:
                best_t, best = t, (e1x, e1y, e1z, e2x, e2y, e2z)
        if best is None:
            return None
        e1x, e1y, e1z, e2x, e2y, e2z = best
        nx, ny, nz = e1y * e2z - e1z * e2y, e1z * e2x - e1x * e2z, e1x * e2y - e1y * e2x
        length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        return {
            "point": (ox + dx * best_t, oy + dy * best_t, oz + dz * best_t),
            "normal": (nx / length, ny / length, nz / length),
            "distance": best_t,
        }

    def cropped(self, low: Vec, high: Vec) -> "Mesh":
        """Triangles with every vertex inside the axis-aligned box, re-indexed."""
        def inside(p: Vec) -> bool:
            return all(low[k] <= p[k] <= high[k] for k in range(3))

        remap: dict[int, int] = {}
        vertices: list[Vec] = []
        faces = []
        for face in self.faces:
            if not all(inside(self.vertices[i]) for i in face):
                continue
            for i in face:
                if i not in remap:
                    remap[i] = len(vertices)
                    vertices.append(self.vertices[i])
            faces.append((remap[face[0]], remap[face[1]], remap[face[2]]))
        return Mesh(vertices, faces)

    def payload(self) -> dict:
        """Compact JSON for the browser (millimetre precision)."""
        return {
            "positions": [round(c, 3) for vertex in self.vertices for c in vertex],
            "indices": [i for face in self.faces for i in face],
        }


def surface_kind(normal: Vec) -> str:
    """Classify a hit by its room-facing normal."""
    if normal[2] < -0.7:
        return "ceiling"
    if normal[2] > 0.7:
        return "floor"
    return "wall"


def quad_area(corners: list[Vec]) -> float:
    """Area of a (near) planar quadrilateral given in order around its edge."""
    def tri(a: Vec, b: Vec, c: Vec) -> float:
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        return 0.5 * math.sqrt((uy * vz - uz * vy) ** 2 + (uz * vx - ux * vz) ** 2 + (ux * vy - uy * vx) ** 2)

    return tri(corners[0], corners[1], corners[2]) + tri(corners[0], corners[2], corners[3])


def locate_region(mesh: Mesh | None, pose: dict, face: int, box: dict, floor_z: float, ceiling_height: float | None) -> dict | None:
    """Place an image region (left/top/right/bottom fractions) in the model.

    With a mesh the corners are ray-cast onto the real surface. Without one,
    a region on the upward face is dropped onto a flat ceiling at the room
    height the Model API reports, and the result is marked approximate.
    """
    corner_uv = [(box["left"], box["top"]), (box["right"], box["top"]), (box["right"], box["bottom"]), (box["left"], box["bottom"])]
    centre_uv = ((box["left"] + box["right"]) / 2, (box["top"] + box["bottom"]) / 2)
    approximate = False
    hits = None
    if mesh is not None:
        hits = [mesh.raycast(*world_ray(pose, face, u, v), max_distance=12.0) for u, v in [centre_uv, *corner_uv]]
        centre = hits[0]
        # Every corner must land on the same kind of surface, near one plane.
        if centre is None or any(h is None for h in hits):
            hits = None
        else:
            kind = surface_kind(centre["normal"])
            n = centre["normal"]
            offsets = [abs(sum(n[k] * (h["point"][k] - centre["point"][k]) for k in range(3))) for h in hits[1:]]
            if any(surface_kind(h["normal"]) != kind for h in hits[1:]) or max(offsets) > 0.25:
                hits = None
    if hits is None:
        if face != 0 or not ceiling_height:
            return None
        approximate = True
        plane_z = floor_z + ceiling_height
        hits = []
        for u, v in [centre_uv, *corner_uv]:
            origin, direction = world_ray(pose, face, u, v)
            if direction[2] <= 0.05:
                return None
            t = (plane_z - origin[2]) / direction[2]
            hits.append({"point": tuple(origin[k] + direction[k] * t for k in range(3)), "normal": (0.0, 0.0, -1.0), "distance": t})
    centre, corners = hits[0], [h["point"] for h in hits[1:]]
    return {
        "surface": surface_kind(centre["normal"]),
        "center": [round(c, 3) for c in centre["point"]],
        "normal": [round(c, 3) for c in centre["normal"]],
        "corners": [[round(c, 3) for c in corner] for corner in corners],
        "area_m2": round(quad_area(corners), 3),
        "height_above_floor_m": round(centre["point"][2] - floor_z, 3),
        "approximate": approximate,
    }


def room_bounds(mesh: Mesh, origin: Vec, floor_z: float, top_z: float) -> dict:
    """Axis-aligned extent of the space visible around a scan position.

    Horizontal rays at camera height find the enclosing walls. Each side uses
    the median of the rays pointing its way, so a ray that escapes through an
    open doorway does not stretch the room. The result limits photo projection
    in the 3D view to the room the photo was taken in.
    """
    sides: dict[tuple[int, int], list[float]] = {(0, 1): [], (0, -1): [], (1, 1): [], (1, -1): []}
    for step in range(48):
        angle = step * math.pi / 24
        direction = (math.cos(angle), math.sin(angle), 0.0)
        hit = mesh.raycast(origin, direction, max_distance=9.0)
        if not hit:
            continue
        axis = 0 if abs(direction[0]) >= abs(direction[1]) else 1
        sides[(axis, 1 if direction[axis] > 0 else -1)].append(hit["point"][axis])

    def extent(axis: int, sign: int) -> float:
        values = sorted(sides[(axis, sign)])
        return values[len(values) // 2] if values else origin[axis] + sign * 2.0

    pad = 0.15
    return {
        "min": [round(extent(0, -1) - pad, 3), round(extent(1, -1) - pad, 3), round(floor_z - pad, 3)],
        "max": [round(extent(0, 1) + pad, 3), round(extent(1, 1) + pad, 3), round(top_z + pad, 3)],
    }
