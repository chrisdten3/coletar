"""In-process store. Real enough to run the proxy and the MCP server end to end
with zero infrastructure, which is what makes the local-model wedge (§10 step 1)
dogfoodable on day one. Retrieval here is lexical overlap, not embeddings — the
Postgres backend is where the vector index lives.

Optionally snapshots to a JSON file, so consecutive CLI invocations see the same
graph. That is a development convenience, not the M1 backend: it rewrites the whole
file on every write and has no concurrency story at all.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import ContextObject, Edge, Memory, ObjectType, Scope

_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class InMemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self._objects: dict[str, ContextObject] = {}
        self._edges: list[Edge] = []
        self._events: list[Event] = []
        self._path = path
        if path is not None and path.exists():
            self._load()

    # -- snapshot -----------------------------------------------------------
    def _load(self) -> None:
        assert self._path is not None
        raw = json.loads(self._path.read_text())
        for record in raw.get("objects", []):
            # Memory is the only subtype with extra fields today; everything else
            # round-trips through the base class.
            model = Memory if record.get("type") == ObjectType.MEMORY else ContextObject
            obj = model.model_validate(record)
            self._objects[obj.id] = obj
        self._edges = [Edge.model_validate(e) for e in raw.get("edges", [])]
        self._events = [Event.model_validate(e) for e in raw.get("events", [])]

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "objects": [o.model_dump(mode="json") for o in self._objects.values()],
                    "edges": [e.model_dump(mode="json") for e in self._edges],
                    "events": [e.model_dump(mode="json") for e in self._events],
                },
                indent=2,
            )
        )

    async def put_object(self, obj: ContextObject, *, event: Event | None = None) -> ContextObject:
        existing = self._objects.get(obj.id)
        if existing is not None:
            obj.touch()
        self._objects[obj.id] = obj
        await self.append_event(
            event
            or Event(
                type=EventType.OBJECT_UPDATED if existing else EventType.OBJECT_CREATED,
                object_id=obj.id,
                actor=Actor.SYSTEM,
                provider=obj.provenance.provider,
                detail={"type": obj.type, "scope": str(obj.scope)},
            )
        )
        return obj

    async def get_object(self, object_id: str) -> ContextObject | None:
        return self._objects.get(object_id)

    async def list_objects(
        self,
        *,
        type: ObjectType | None = None,
        scope: Scope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[ContextObject]:
        out = [
            o
            for o in self._objects.values()
            if (type is None or o.type == type)
            and (scope is None or o.scope == scope)
            and (include_retired or o.is_active)
        ]
        out.sort(key=lambda o: o.updated_at, reverse=True)
        return out[:limit]

    async def retire_object(self, object_id: str, *, reason: str) -> None:
        obj = self._objects.get(object_id)
        if obj is None or not obj.is_active:
            return
        obj.retired_at = datetime.now(UTC)
        await self.append_event(
            Event(
                type=EventType.OBJECT_RETIRED,
                object_id=object_id,
                actor=Actor.JOB,
                detail={"reason": reason},
            )
        )

    async def add_edge(self, edge: Edge) -> None:
        self._edges.append(edge)
        await self.append_event(
            Event(
                type=EventType.EDGE_CREATED,
                object_id=edge.src_id,
                detail={"dst": edge.dst_id, "edge_type": edge.type},
            )
        )

    async def edges_from(self, object_id: str) -> list[Edge]:
        return [e for e in self._edges if e.src_id == object_id]

    async def append_event(self, event: Event) -> None:
        self._events.append(event)
        # Every mutation funnels through here, so this is the one place a snapshot
        # is needed to cover all of them.
        self._save()

    async def list_events(self, *, object_id: str | None = None, limit: int = 200) -> list[Event]:
        out = [e for e in self._events if object_id is None or e.object_id == object_id]
        return out[-limit:][::-1]

    async def search(
        self,
        query: str,
        *,
        scope: Scope | None = None,
        top_k: int = 12,
    ) -> list[tuple[ContextObject, float]]:
        q = _tokens(query)
        scored: list[tuple[ContextObject, float]] = []
        for obj in self._objects.values():
            if not obj.is_active:
                continue
            if scope is not None and obj.scope != scope:
                continue
            overlap = q & _tokens(obj.content)
            if not overlap:
                continue
            # Confidence is a ranking input, not just a display field — a
            # low-confidence export line should lose to an explicit connector write.
            relevance = len(overlap) / max(len(q), 1)
            scored.append((obj, relevance * (0.5 + 0.5 * obj.confidence)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]
