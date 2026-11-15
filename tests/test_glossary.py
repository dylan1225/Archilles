"""The glossary behind the hover definitions is complete and well-formed."""

import json
import re

from fastapi.testclient import TestClient

import main

GLOSSARY = json.loads(main.GLOSSARY_PATH.read_text(encoding="utf-8"))
TERMS = GLOSSARY["terms"]


def test_glossary_has_enough_terms_and_a_status():
    assert len(TERMS) >= 30
    assert GLOSSARY["status"] in ("draft", "reviewed")


def test_every_entry_has_an_id_term_definition_and_explanation():
    for entry in TERMS:
        assert re.fullmatch(r"[a-z0-9_]+", entry["id"]), entry
        for field in ("term", "definition", "explanation"):
            assert entry[field].strip(), f"{entry['id']} has no {field}"
        assert isinstance(entry["aliases"], list)


def test_definitions_are_one_line_and_explanations_are_short():
    for entry in TERMS:
        assert len(entry["definition"]) <= 120, entry["id"]
        sentences = re.split(r"(?<=[.!?])\s+", entry["explanation"].strip())
        assert len(sentences) <= 3, entry["id"]


def test_ids_and_spellings_are_unique():
    ids = [entry["id"] for entry in TERMS]
    spellings = [s.lower() for entry in TERMS for s in [entry["term"], *entry["aliases"]]]

    assert len(ids) == len(set(ids))
    assert len(spellings) == len(set(spellings)), "a spelling points at two terms"


def test_endpoint_serves_the_file():
    body = TestClient(main.app).get("/glossary").json()

    assert body == GLOSSARY
