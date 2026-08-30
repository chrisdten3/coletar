"""Context Inspector first cut: upload a snapshot, see the three README boxes."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from coletar.inspector.app import app
from coletar.schema import (
    Edge,
    EdgeType,
    Event,
    EventType,
    ExtractionMethod,
    Memory,
    MemoryKind,
    OriginType,
)

client = TestClient(app)


def _snapshot() -> dict:
    fact = Memory.from_write(
        "Chris prefers fixed-point integers over doubles for money.",
        kind=MemoryKind.PREFERENCE,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        origin_type=OriginType.USER,
    )
    correction = Memory.from_write(
        "Chris now consults independently.",
        kind=MemoryKind.CORRECTION,
        supersedes=fact.id,
    )
    edge = Edge(src_id=correction.id, dst_id=fact.id, type=EdgeType.SUPERSEDES)
    event = Event(type=EventType.OBJECT_CREATED, object_id=fact.id)
    return {
        "objects": [fact.model_dump(mode="json"), correction.model_dump(mode="json")],
        "edges": [edge.model_dump(mode="json")],
        "events": [event.model_dump(mode="json")],
    }


def test_index_serves_upload_form() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "upload" in response.text


def test_upload_renders_graph_log_and_index() -> None:
    snapshot = _snapshot()
    files = {"snapshot": ("snapshot.json", json.dumps(snapshot), "application/json")}

    response = client.post("/upload", files=files)

    assert response.status_code == 200
    body = response.text
    assert "Canonical Context Graph" in body
    assert "Event/Revision Log" in body
    assert "Search Index" in body
    fact_id = snapshot["objects"][0]["id"]
    assert fact_id in body
    assert "supersedes" in body
    assert EventType.OBJECT_CREATED in body
    assert "fixed-point" in body  # a search-index term survived tokenization


def test_upload_rejects_bad_json_without_crashing() -> None:
    files = {"snapshot": ("snapshot.json", b"not json", "application/json")}

    response = client.post("/upload", files=files)

    assert response.status_code == 200
    assert "Could not read this snapshot" in response.text
