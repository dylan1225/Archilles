"""Survey data for one scanned building, from the Matterport Model API.

Four things the API offers are combined here, beyond a photo gallery:

* the scan technician's field note (the model description),
* every room's classification and measured dimensions,
* each scan position's pose and its six panorama cube faces, so an agent can
  look in any direction, including straight up at the ceiling, and
* the building's 3D mesh, so a region of a photo can be placed in the model.

Images are fetched on demand, only from Matterport's CDN, and normalised
through Pillow. Signed source URLs never leave this module.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

import interiors
import scan_geometry

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_MESH_BYTES = 40 * 1024 * 1024
MAX_SOURCE_PIXELS = 40_000_000
FACE_EDGE = 1024
OVERVIEW_TILE = 512
MAX_ROOMS = 10
MAX_POSITIONS_PER_ROOM = 3
ASSET_HOST = re.compile(r"cdn-[0-9]+\.matterport\.com\Z", re.IGNORECASE)
MODEL_ID = re.compile(r"[a-zA-Z0-9]{1,64}\Z")
BUNDLED_INTERIORS_DIR = Path(__file__).resolve().parent / "bundled" / "interiors"
NON_ROOM_LABELS = {"void"}
VIEW_TITLES = {
    "ceiling": "the ceiling, looking straight up",
    "wall-1": "the first horizontal view",
    "wall-2": "the view a quarter turn to the right of wall-1",
    "wall-3": "the view opposite wall-1",
    "wall-4": "the view a quarter turn to the left of wall-1",
    "floor": "the floor, looking straight down",
}

SURVEY_QUERY = """
query BuildingSurvey($modelId: ID!) {
  model(id: $modelId) {
    id name description visibility state modified
    address { streetAddressLines locality administrativeArea postalCode }
    rooms {
      id label
      classifications { label confidence }
      dimensions(units: metric) { areaFloor height width depth }
    }
    locations {
      id
      position { x y z }
      room { id }
      panos {
        id placement
        position { x y z }
        rotation { x y z w }
        skybox(resolution: "high") { status children }
      }
    }
    assets { meshes(formats: ["obj"]) { resolution status filename url } }
  }
}
"""


def cache_dir() -> Path:
    """Local, git-ignored store for meshes and saved reviews."""
    return Path(os.getenv("POSTMORTEM_CACHE_DIR", "") or Path(__file__).resolve().parent / "cache")


class EvidenceError(interiors.MatterportError):
    """User-safe failure collecting enough actual scan evidence."""


def _safe_asset_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not ASSET_HOST.fullmatch(parts.hostname or "")
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or parts.fragment
            or len(value) > 8192
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise EvidenceError("A scan asset did not have an approved Matterport address.") from None
    return value


def _normalized_image(content: bytes) -> Image.Image:
    """Use Pillow's decoder/verification and strip metadata before vision use."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as check:
                if check.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError
                if check.width * check.height > MAX_SOURCE_PIXELS:
                    raise ValueError
                check.verify()
            with Image.open(io.BytesIO(content)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((FACE_EDGE, FACE_EDGE), Image.Resampling.LANCZOS)
                if image.width < 128 or image.height < 128:
                    raise ValueError
                return image
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise EvidenceError("A scan image was unreadable or too small for visual assessment.") from None


def _jpeg(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=86, optimize=True)
    return output.getvalue()


def _download(url: str, limit: int, accept: str) -> bytes:
    # Never attach Model API credentials to a CDN request. Redirects and ambient
    # proxy environment settings are disabled; only provider-owned CDN hosts pass.
    _safe_asset_url(url)
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(30, connect=5)) as client:
            with client.stream("GET", url, headers={"Accept": accept}) as response:
                if response.status_code != 200:
                    raise EvidenceError("A Matterport scan asset is currently unavailable.")
                if int(response.headers.get("content-length", "0")) > limit:
                    raise EvidenceError("A scan asset exceeded the download size limit.")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise EvidenceError("A scan asset exceeded the download size limit.")
                    chunks.append(chunk)
    except (httpx.HTTPError, ValueError):
        # httpx exceptions include request URLs, which can contain signed tokens.
        raise EvidenceError("Could not download a Matterport scan asset. Please try again.") from None
    return b"".join(chunks)


def _point(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        point = {k: float(value[k]) for k in ("x", "y", "z")}
    except (KeyError, TypeError, ValueError):
        return None
    return point if all(math.isfinite(c) for c in point.values()) else None


def _spread(items: list[dict], limit: int) -> list[dict]:
    """Up to `limit` scan positions, chosen far apart so views overlap least."""
    if len(items) <= limit:
        return items
    chosen = [items[0]]
    while len(chosen) < limit:
        def distance(item):
            return min(sum((item["position"][k] - other["position"][k]) ** 2 for k in "xyz") for other in chosen)
        chosen.append(max((item for item in items if item not in chosen), key=distance))
    return chosen


class Survey:
    """One building's scan, addressed by short room and view identifiers.

    Rooms are R1, R2, ...; scan positions are R1-A, R1-B, ...; a single view is
    a position plus a direction, such as R1-A-ceiling. Short identifiers keep
    agent tool calls reliable and are what findings cite.
    """

    def __init__(self, model: dict, field_note: str, rooms: list[dict], positions: dict[str, dict], mesh_url: str | None, skipped_rooms: list[str]):
        self.model = model
        self.field_note = field_note
        self.rooms = rooms
        self.positions = positions
        self.skipped_rooms = skipped_rooms
        self.collected_at = datetime.now(timezone.utc).isoformat()
        self._mesh_url = mesh_url
        self._mesh: scan_geometry.Mesh | None = None
        self._mesh_tried = False
        self._images: dict[str, bytes] = {}
        self._lock = threading.Lock()

    # -- lookups ---------------------------------------------------------------

    def room(self, room_id: str) -> dict | None:
        return next((room for room in self.rooms if room["id"] == room_id), None)

    def parse_view(self, view_id: str) -> tuple[dict, int] | None:
        """(scan position, cube face) for an id such as "R2-A-ceiling"."""
        match = re.fullmatch(r"(R\d{1,2}-[A-Z])-(ceiling|wall-[1-4]|floor)", view_id or "")
        position = self.positions.get(match.group(1)) if match else None
        if not position:
            return None
        return position, scan_geometry.FACE_NAMES.index(match.group(2))

    def describe_view(self, view_id: str) -> str:
        parsed = self.parse_view(view_id)
        if not parsed:
            return view_id
        position, face = parsed
        room = self.room(position["room_id"])
        return f"{room['name'] if room else 'Room'}, {VIEW_TITLES[scan_geometry.FACE_NAMES[face]]} (scan position {position['id']})"

    # -- imagery ---------------------------------------------------------------

    def _cached(self, key: str, build) -> bytes:
        with self._lock:
            if key in self._images:
                return self._images[key]
        data = build()
        with self._lock:
            return self._images.setdefault(key, data)

    def _face_image(self, position: dict, face: int) -> Image.Image:
        return Image.open(io.BytesIO(self.face_jpeg(position, face))).convert("RGB")

    def face_jpeg(self, position: dict, face: int) -> bytes:
        """One 1024 px cube face of a scan position, as a normalised JPEG."""
        def build():
            raw = _download(position["face_urls"][face], MAX_IMAGE_BYTES, "image/jpeg,image/png,image/webp")
            return _jpeg(_normalized_image(raw))

        return self._cached(f"{position['id']}:{face}", build)

    def overview_jpeg(self, position: dict) -> bytes:
        """All six views from a scan position on one labelled sheet."""
        def build():
            tile = OVERVIEW_TILE
            sheet = Image.new("RGB", (tile * 3, tile * 2), "black")
            draw = ImageDraw.Draw(sheet)
            font = ImageFont.load_default(size=22)
            # Ceiling and floor first: moisture and settlement show there.
            for slot, face in enumerate((0, 1, 2, 5, 3, 4)):
                x, y = (slot % 3) * tile, (slot // 3) * tile
                sheet.paste(self._face_image(position, face).resize((tile, tile), Image.Resampling.LANCZOS), (x, y))
                label = f"{position['id']}-{scan_geometry.FACE_NAMES[face]}"
                width = draw.textlength(label, font=font)
                draw.rectangle((x + 6, y + 6, x + 18 + width, y + 38), fill=(0, 0, 0))
                draw.text((x + 12, y + 9), label, fill=(255, 255, 255), font=font)
            return _jpeg(sheet)

        return self._cached(f"{position['id']}:overview", build)

    # -- geometry --------------------------------------------------------------

    def interior_box(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Box around the scanned interior, used to trim yards and trees off the mesh."""
        points = [position["pose"]["position"] for position in self.positions.values()]
        floors = [position["floor_z"] for position in self.positions.values()]
        heights = [room["height_m"] for room in self.rooms if room.get("height_m")]
        top = max(floors) + (max(heights) if heights else 2.7) + 0.9
        margin = 2.5
        return (
            (min(p["x"] for p in points) - margin, min(p["y"] for p in points) - margin, min(floors) - 0.5),
            (max(p["x"] for p in points) + margin, max(p["y"] for p in points) + margin, top),
        )

    def mesh(self) -> scan_geometry.Mesh | None:
        """The building's mesh trimmed to its interior, or None if unavailable.

        The trimmed geometry is cached on disk so the 3D view and saved reviews
        keep working after the signed download address has expired.
        """
        with self._lock:
            if self._mesh_tried:
                return self._mesh
            self._mesh_tried = True
        mesh = load_cached_mesh(self.model["id"])
        if mesh is None and self._mesh_url:
            try:
                text = _download(self._mesh_url, MAX_MESH_BYTES, "*/*").decode("utf-8", "replace")
                mesh = scan_geometry.Mesh(*scan_geometry.parse_obj(text)).cropped(*self.interior_box())
                if not mesh.faces:
                    mesh = None
                else:
                    path = mesh_path(self.model["id"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(mesh.payload(), separators=(",", ":")), encoding="utf-8")
            except (EvidenceError, ValueError, OSError):
                mesh = None
        self._mesh = mesh
        return mesh


def mesh_path(model_id: str) -> Path:
    if not MODEL_ID.fullmatch(model_id or ""):
        raise EvidenceError("Choose a valid building from the collection.", 422)
    return cache_dir() / "meshes" / f"{model_id}.json"


def read_mesh(model_id: str) -> bytes:
    """Read a runtime mesh or the bundled mesh shipped with a saved review."""
    local = mesh_path(model_id)  # Validate the identifier before building either path.
    for path in (local, BUNDLED_INTERIORS_DIR / "meshes" / local.name):
        try:
            return path.read_bytes()
        except FileNotFoundError:
            continue
    raise EvidenceError("No 3D mesh has been cached for this building. Run a review first.", 404)


def load_cached_mesh(model_id: str) -> scan_geometry.Mesh | None:
    try:
        payload = json.loads(read_mesh(model_id))
        flat, indices = payload["positions"], payload["indices"]
        vertices = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)]
        faces = [(indices[i], indices[i + 1], indices[i + 2]) for i in range(0, len(indices), 3)]
        return scan_geometry.Mesh(vertices, faces) if vertices and faces else None
    except (OSError, ValueError, KeyError, IndexError, TypeError, EvidenceError):
        return None


def _room_name(room: dict) -> str | None:
    """Best classification label for a room, or None for voids and unknowns."""
    ranked = sorted(
        (c for c in room.get("classifications") or [] if isinstance(c, dict) and c.get("label")),
        key=lambda c: -(c.get("confidence") or 0),
    )
    label = str(ranked[0]["label"]).strip()[:60] if ranked else ""
    return label if label and label.casefold() not in NON_ROOM_LABELS else None


def collect_survey(model_id: str, progress=None) -> Survey:
    """Load a building's rooms, scan positions and field note. No vision yet."""
    if not isinstance(model_id, str) or not MODEL_ID.fullmatch(model_id):
        raise EvidenceError("Choose a valid building from the collection.", 422)
    if progress:
        progress("collecting", "Loading the building's rooms, scan positions and field note from Matterport.")
    data = interiors._request_graphql(SURVEY_QUERY, {"modelId": model_id})
    model = data.get("model")
    if not isinstance(model, dict):
        raise EvidenceError("This building's scan is not available to the connected Matterport account.", 404)
    region = interiors.region_of(model.get("address"))
    if model.get("visibility") != "public" or model.get("state") != "active" or not region:
        raise EvidenceError("Reviews are available only for active, public Houston and Los Angeles scans in this collection.", 403)

    names: dict[str, dict] = {}
    for room in model.get("rooms") or []:
        name = _room_name(room) if isinstance(room, dict) else None
        if not name or not isinstance(room.get("id"), str):
            continue
        dims = room.get("dimensions") or {}
        names[room["id"]] = {
            "source_id": room["id"], "kind": name,
            "area_m2": dims.get("areaFloor"), "height_m": dims.get("height"),
            "width_m": dims.get("width"), "depth_m": dims.get("depth"),
            "raw_positions": [],
        }
    for location in model.get("locations") or []:
        if not isinstance(location, dict):
            continue
        room = names.get((location.get("room") or {}).get("id"))
        anchor = _point(location.get("position"))
        if not room or not anchor:
            continue
        for pano in location.get("panos") or []:
            if not isinstance(pano, dict) or str(pano.get("placement", "")).casefold() == "unplaced":
                continue
            skybox = pano.get("skybox") or {}
            urls = skybox.get("children") or []
            position, rotation = _point(pano.get("position")), pano.get("rotation")
            if skybox.get("status") != "available" or len(urls) != 6 or not position or not isinstance(rotation, dict):
                continue
            try:
                rotation = {k: float(rotation[k]) for k in ("x", "y", "z", "w")}
                for url in urls:
                    _safe_asset_url(url)
            except (KeyError, TypeError, ValueError, EvidenceError):
                continue
            room["raw_positions"].append({
                "sweep_id": location.get("id"), "position": position,
                "pose": {"position": position, "rotation": rotation},
                "floor_z": anchor["z"], "face_urls": list(urls),
            })
            break

    scanned = sorted((room for room in names.values() if room["raw_positions"]), key=lambda room: -(room["area_m2"] or 0))
    if not scanned:
        raise EvidenceError(
            "This scan did not provide interior panoramas with room information, so there is nothing to review. "
            "Choose another building or arrange an in-person inspection.", 422,
        )
    kind_counts: dict[str, int] = {}
    for room in scanned:
        kind_counts[room["kind"]] = kind_counts.get(room["kind"], 0) + 1
    seen: dict[str, int] = {}
    rooms, positions, skipped = [], {}, []
    for index, room in enumerate(scanned):
        seen[room["kind"]] = seen.get(room["kind"], 0) + 1
        name = f"{room['kind']} {seen[room['kind']]}" if kind_counts[room["kind"]] > 1 else room["kind"]
        if index >= MAX_ROOMS:
            skipped.append(name)
            continue
        room_id = f"R{index + 1}"
        position_ids = []
        for letter, raw in zip("ABCDEFGH", _spread(room["raw_positions"], MAX_POSITIONS_PER_ROOM)):
            position_id = f"{room_id}-{letter}"
            positions[position_id] = {**raw, "id": position_id, "room_id": room_id}
            position_ids.append(position_id)
        rooms.append({
            "id": room_id, "name": name, "kind": room["kind"],
            **{key: (round(room[key], 2) if isinstance(room[key], (int, float)) else None) for key in ("area_m2", "height_m", "width_m", "depth_m")},
            "position_ids": position_ids,
            "scan_positions_available": len(room["raw_positions"]),
        })

    meshes = [m for m in (model.get("assets") or {}).get("meshes") or [] if isinstance(m, dict) and m.get("status") == "available" and m.get("url")]
    # The "50k" OBJ is the whole building; other entries can be single chunks.
    meshes.sort(key=lambda m: m.get("resolution") != "50k")
    mesh_url = None
    for mesh in meshes:
        try:
            mesh_url = _safe_asset_url(mesh["url"])
            break
        except EvidenceError:
            continue
    note = str(model.get("description") or "").strip()[:2000]
    return Survey(
        model={
            "id": model_id,
            "name": str(model.get("name") or "Scanned building").strip()[:160],
            "address": interiors.format_address(model.get("address")),
            "region": region,
            "modified": model.get("modified"),
        },
        field_note=note,
        rooms=rooms,
        positions=positions,
        mesh_url=mesh_url,
        skipped_rooms=skipped,
    )
