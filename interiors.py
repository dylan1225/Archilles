"""Read-only Matterport integration: the building collection and field-note leads.

The collection is the public Houston and Los Angeles scans available to the
connected account (the regions the hackathon's CoStar/Matterport access covers).
"""

import base64
import json
import os
import re
import socket
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fastapi import APIRouter, Query

load_dotenv(Path(__file__).resolve().parent / ".env")

router = APIRouter(prefix="/api/interiors", tags=["Building interiors"])
ENDPOINT = "https://api.matterport.com/api/models/graph"
REGIONS = {
    "houston": 'address.locality:"Houston" AND address.administrativeArea:(TX OR Texas)',
    "los_angeles": 'address.locality:"Los Angeles" AND address.administrativeArea:(CA OR California)',
}
COLLECTION_QUERY = "(" + " OR ".join(f"({query})" for query in REGIONS.values()) + ") AND visibility:public"
MODELS_QUERY = """
query CollectionInteriors($query: String!, $offset: String, $pageSize: Int!) {
  models(query: $query, offset: $offset, pageSize: $pageSize,
         sortBy: [{field: name, order: asc}, {field: created, order: desc}]) {
    totalResults
    nextOffset
    results {
      id
      name
      address { streetAddressLines locality administrativeArea postalCode }
      image { url }
      floors { count }
    }
  }
}
"""


class MatterportError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def region_of(address: dict | None) -> str | None:
    """"houston", "los_angeles", or None when an address is outside the collection.

    Los Angeles appears as both "Los Angeles" and "Los Angeles, Los Angeles".
    """
    address = address or {}
    locality = str(address.get("locality") or "").strip().casefold()
    state = str(address.get("administrativeArea") or "").strip().casefold()
    if locality == "houston" and state in {"tx", "texas"}:
        return "houston"
    if locality.split(",")[0].strip() == "los angeles" and state in {"ca", "california"}:
        return "los_angeles"
    return None


def format_address(address: dict | None) -> str:
    address = address or {}
    return ", ".join(filter(None, [
        " ".join(address.get("streetAddressLines") or []),
        str(address.get("locality") or "").split(",")[0].strip(),
        " ".join(filter(None, [address.get("administrativeArea"), address.get("postalCode")])),
    ]))


def _search_query(search: str, region: str = "") -> str:
    # Treat user input as a literal phrase, never as Elasticsearch operators.
    scope = f"({REGIONS[region]}) AND visibility:public" if region in REGIONS else COLLECTION_QUERY
    search = " ".join(search.split())
    if not search:
        return scope
    escaped = re.sub(r'([+\-=!(){}\[\]^"~*?:\\/<>|&])', r"\\\1", search)
    return f'{scope} AND (name:"{escaped}" OR address:"{escaped}")'


def _request_graphql(query: str, variables: dict) -> dict:
    """Execute a server-owned read-only query; never return raw provider errors."""
    key = os.getenv("MATTERPORT_API_KEY", "").strip()
    token = os.getenv("MATTERPORT_API_TOKEN", "").strip()
    if not key or not token:
        raise MatterportError(
            "The interior tour service is not configured. Add MATTERPORT_API_KEY "
            "and MATTERPORT_API_TOKEN to the server's .env file.",
            503,
        )
    auth = base64.b64encode(f"{key}:{token}".encode()).decode()
    request = Request(
        ENDPOINT,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise MatterportError("Matterport returned too much data for this request.")
            payload = json.loads(body)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise MatterportError("Matterport rejected the server credentials. Check the API key and token.", 503) from None
        if exc.code == 429:
            raise MatterportError("Matterport is receiving too many requests. Try again shortly.", 429) from None
        raise MatterportError("Matterport could not load the building list. Please try again.") from None
    except (TimeoutError, socket.timeout):
        raise MatterportError("Matterport took too long to respond. Please try again.", 504) from None
    except URLError:
        raise MatterportError("Could not reach Matterport. Check the server's connection and try again.") from None
    except (ValueError, UnicodeError):
        raise MatterportError("Matterport returned an unreadable response. Please try again.") from None

    # GraphQL can return HTTP 200 with errors, including partial results. Do not
    # forward provider messages: they can contain internal or credential data.
    if not isinstance(payload, dict):
        raise MatterportError("Matterport returned an unexpected response. Please try again.")
    if payload.get("errors"):
        codes = {
            str((e.get("extensions") or {}).get("code", ""))
            for e in payload["errors"] if isinstance(e, dict)
        }
        if codes & {"request.unauthenticated", "request.unauthorized"}:
            raise MatterportError("This Matterport account does not allow access to these models. Check its API permissions.", 503)
        raise MatterportError("Matterport could not complete the building search. Please try again.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MatterportError("Matterport returned an unexpected response. Please try again.")
    return data


def _graphql(variables: dict) -> dict:
    data = _request_graphql(MODELS_QUERY, variables)
    models = data.get("models")
    if not isinstance(models, dict) or not isinstance(models.get("results"), list):
        raise MatterportError("Matterport returned an unexpected building list. Please try again.")
    return models


@router.get("/models")
def list_models(
    q: str = Query(default="", max_length=120),
    offset: str | None = Query(default=None, max_length=16384),
    page_size: int = Query(default=12, ge=1, le=24),
    region: str = Query(default="", pattern=r"^(|houston|los_angeles)$"),
) -> dict:
    result = _graphql({"query": _search_query(q, region), "offset": offset, "pageSize": page_size})
    models = []
    for item in result["results"]:
        if not isinstance(item, dict) or not re.fullmatch(r"[a-zA-Z0-9]{1,64}", str(item.get("id", ""))):
            continue
        address = item.get("address") or {}
        models.append({
            "id": item["id"],
            "name": item.get("name") or "Untitled building",
            "address": format_address(address),
            "thumbnail": (item.get("image") or {}).get("url"),
            "floors": (item.get("floors") or {}).get("count"),
        })
    return {
        "models": models,
        "total": result.get("totalResults", 0),
        "nextOffset": result.get("nextOffset"),
        # SDK application keys are public and domain restricted. The API key
        # and API token above never leave this server.
        "sdkKey": os.getenv("MATTERPORT_SDK_KEY", "").strip(),
        "sdkDomains": [d.strip() for d in os.getenv("MATTERPORT_SDK_DOMAINS", "localhost").split(",") if d.strip()],
    }


# --- Field-note leads ---------------------------------------------------------
#
# The person who scanned a building often leaves a note for the listing team:
# "avoid master closet (ceiling leak)", "mold, water damage". Those notes sit in
# the model description and are searchable. They are a free, human triage
# signal across the whole collection, pointing at interiors worth a closer look.

NOTE_SIGNALS = [
    # (pattern, label shown to the user, category, weight)
    (r"water\s+damage", "water damage", "moisture", 3),
    (r"\bleak(?:s|ing|ed|y)?\b", "leak", "moisture", 3),
    (r"\bmou?ld(?:y)?\b|\bmildew\b", "mold", "moisture", 3),
    (r"\bflood(?:ed|ing)?\b", "flooding", "moisture", 2),
    (r"\bstain(?:s|ed)?\b", "staining", "moisture", 2),
    (r"\bsmells?\b|\bodou?r\b", "odour", "moisture", 1),
    (r"hole\s+in\s+(?:the\s+)?(?:ceiling|wall|floor)", "hole in a surface", "structure", 3),
    (r"\bsagg?ing\b", "sagging", "structure", 3),
    (r"\bfoundation\b", "foundation", "structure", 2),
    (r"\brot(?:ted|ten|ting)?\b", "rot", "structure", 2),
    (r"lights?\s+(?:don'?t|do\s+not|doesn'?t|not)\s+work", "lights not working", "fire_electrical", 1),
    (r"exposed\s+wir", "exposed wiring", "fire_electrical", 3),
]
LEADS_SEARCH = (
    'description:("water damage" OR leak OR leaks OR leaking OR mold OR mildew OR flood OR flooded '
    'OR stain OR stains OR stained OR sagging OR rot OR rotted OR "hole in ceiling" OR "hole in the ceiling" '
    'OR foundation OR "exposed wiring" OR damage)'
)
LEADS_QUERY = """
query FieldNoteLeads($query: String!, $pageSize: Int!) {
  models(query: $query, pageSize: $pageSize) {
    results {
      id name description
      address { streetAddressLines locality administrativeArea postalCode }
      image { url }
      floors { count }
    }
  }
}
"""
LEADS_TTL_SECONDS = 600
_leads_lock = threading.Lock()
_leads_cache: tuple[float, list[dict]] | None = None


def note_signals(note: str) -> list[dict]:
    """Defect words found in a scan technician's note, strongest first."""
    found = []
    for pattern, label, category, weight in NOTE_SIGNALS:
        if re.search(pattern, note or "", re.IGNORECASE):
            found.append({"label": label, "category": category, "weight": weight})
    return sorted(found, key=lambda signal: -signal["weight"])


def note_excerpt(note: str, limit: int = 260) -> str:
    """First paragraph of a field note, trimmed for display."""
    first = " ".join((note or "").strip().split("\n\n")[0].split())
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def field_note_leads() -> list[dict]:
    """Buildings whose scan technician wrote down a visible interior problem."""
    global _leads_cache
    with _leads_lock:
        if _leads_cache and time.monotonic() - _leads_cache[0] < LEADS_TTL_SECONDS:
            return _leads_cache[1]
    data = _request_graphql(LEADS_QUERY, {"query": f"{COLLECTION_QUERY} AND {LEADS_SEARCH}", "pageSize": 50})
    results = (data.get("models") or {}).get("results")
    if not isinstance(results, list):
        raise MatterportError("Matterport returned an unexpected building list. Please try again.")
    leads = []
    for item in results:
        if not isinstance(item, dict) or not re.fullmatch(r"[a-zA-Z0-9]{1,64}", str(item.get("id", ""))):
            continue
        region = region_of(item.get("address"))
        signals = note_signals(item.get("description") or "")
        score = sum(signal["weight"] for signal in signals)
        # One weak word ("smells") is not a lead; one strong word or two weak ones is.
        if not region or score < 2:
            continue
        leads.append({
            "id": item["id"],
            "name": item.get("name") or "Untitled building",
            "address": format_address(item.get("address")),
            "region": region,
            "thumbnail": (item.get("image") or {}).get("url"),
            "floors": (item.get("floors") or {}).get("count"),
            "note": note_excerpt(item.get("description") or ""),
            "signals": signals,
            "score": score,
        })
    leads.sort(key=lambda lead: (-lead["score"], lead["name"]))
    with _leads_lock:
        _leads_cache = (time.monotonic(), leads)
    return leads
