"""In-process store. Real enough to run the proxy and the MCP server end to end
with zero infrastructure, which is what makes the local-model wedge (§10 step 1)
dogfoodable on day one -- including a real vector index, since the default embedder
needs nothing installed (see `coletar.retrieval.embedding`).

Optionally snapshots to a JSON file, so consecutive CLI invocations see the same
graph. That is a development convenience, not the M1 backend: it rewrites the whole
file on every write and has no concurrency story at all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from coletar.retrieval.embedding import Embedder, build_embedder, tokenize
from coletar.retrieval.index import VectorIndex
from coletar.retrieval.ranking import lexical_score, rank_score
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    ContextObject,
    Edge,
    EdgeType,
    ObjectType,
    Scope,
    ScopeType,
    object_from_record,
)


class InMemoryStore:
    def __init__(self, path: Path | None = None, *, embedder: Embedder | None = None) -> None:
        self._objects: dict[str, ContextObject] = {}
        # Keyed by the edge's identity, so re-asserting an edge is a no-op rather
        # than a duplicate row.
        self._edges: dict[tuple[str, str, EdgeType], Edge] = {}
        self._events: list[Event] = []
        self._embedder = embedder or build_embedder()
        self._index = VectorIndex(self._embedder.dim)
        # Content tokens, cached on write. Re-tokenizing every object's content on
        # every query is ~65ms at 10k objects, and the content has not changed.
        self._tokens: dict[str, set[str]] = {}
        # Objects restored from a snapshot, whose vectors are recomputed in one
        # batch on first search rather than written into the snapshot file. Keeping
        # 768 floats per object out of a file a human is meant to be able to read
        # is worth one deferred batch call.
        self._unembedded: list[str] = []
        self._path = path
        if path is not None and path.exists():
            self._load()

    # -- snapshot -----------------------------------------------------------
    def _load(self) -> None:
        assert self._path is not None
        raw = json.loads(self._path.read_text())
        for record in raw.get("objects", []):
            obj = object_from_record(record)
            self._objects[obj.id] = obj
            self._unembedded.append(obj.id)
        for record in raw.get("edges", []):
            edge = Edge.model_validate(record)
            self._edges[(edge.src_id, edge.dst_id, edge.type)] = edge
        self._events = [Event.model_validate(e) for e in raw.get("events", [])]

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "objects": [o.model_dump(mode="json") for o in self._objects.values()],
                    "edges": [e.model_dump(mode="json") for e in self._edges.values()],
                    "events": [e.model_dump(mode="json") for e in self._events],
                },
                indent=2,
            )
        )

    # -- objects ------------------------------------------------------------
    async def put_object(self, obj: ContextObject, *, event: Event | None = None) -> ContextObject:
        existing = self._objects.get(obj.id)
        before = existing.model_dump(mode="json") if existing is not None else None
        # Store a detached copy, and never hand the stored instance back out. A
        # real database cannot be mutated by whoever last read from it, and neither
        # can this: aliasing would let a caller change the graph with no event
        # behind it, which is the one thing the substrate must not allow.
        stored = obj.model_copy(deep=True)
        if existing is not None:
            stored.touch()
        self._objects[obj.id] = stored
        obj = stored

        # On the write path, not deferred: an object is searchable as soon as it is
        # stored, so the bound on "when does a write become visible" is one embed
        # call rather than an unspecified background window.
        self._index.put(obj.id, (await self._embedder.embed([obj.content]))[0])
        self._tokens[obj.id] = set(tokenize(obj.content))

        base = event or Event(
            type=EventType.OBJECT_UPDATED if existing else EventType.OBJECT_CREATED,
            object_id=obj.id,
            actor=Actor.SYSTEM,
            provider=obj.provenance.provider,
            detail={"type": obj.type, "scope": str(obj.scope)},
        )
        # before/after are the store's to fill in -- a caller supplying its own
        # event (the MCP server tagging a connector write) should not have to
        # remember to snapshot state, or be able to get it wrong.
        await self.append_event(
            base.model_copy(update={"before": before, "after": obj.model_dump(mode="json")})
        )
        return stored.model_copy(deep=True)

    async def get_object(self, object_id: str) -> ContextObject | None:
        obj = self._objects.get(object_id)
        return obj.model_copy(deep=True) if obj is not None else None

    def _superseded_ids(self) -> set[str]:
        return {o.supersedes for o in self._objects.values() if o.supersedes}

    async def list_objects(
        self,
        *,
        type: ObjectType | None = None,
        scope: Scope | None = None,
        include_retired: bool = False,
        include_superseded: bool = False,
        limit: int = 200,
    ) -> list[ContextObject]:
        superseded = set() if include_superseded else self._superseded_ids()
        out = [
            o
            for o in self._objects.values()
            if (type is None or o.type == type)
            and (scope is None or o.scope == scope)
            and (include_retired or o.is_active)
            and o.id not in superseded
        ]
        out.sort(key=lambda o: o.updated_at, reverse=True)
        return [o.model_copy(deep=True) for o in out[:limit]]

    async def retire_object(self, object_id: str, *, reason: str) -> None:
        obj = self._objects.get(object_id)
        if obj is None or not obj.is_active:
            return
        before = obj.model_dump(mode="json")
        obj.retired_at = datetime.now(UTC)
        await self.append_event(
            Event(
                type=EventType.OBJECT_RETIRED,
                object_id=object_id,
                actor=Actor.JOB,
                before=before,
                after=obj.model_dump(mode="json"),
                detail={"reason": reason},
            )
        )

    # -- edges --------------------------------------------------------------
    async def add_edge(self, edge: Edge) -> None:
        key = (edge.src_id, edge.dst_id, edge.type)
        if key in self._edges:
            return  # idempotent: re-asserting an edge is not a second edge
        self._edges[key] = edge
        await self.append_event(
            Event(
                type=EventType.EDGE_CREATED,
                object_id=edge.src_id,
                detail={"dst": edge.dst_id, "edge_type": edge.type},
            )
        )

    async def edges_from(self, object_id: str) -> list[Edge]:
        return [e for e in self._edges.values() if e.src_id == object_id]

    async def edges_to(self, object_id: str) -> list[Edge]:
        return [e for e in self._edges.values() if e.dst_id == object_id]

    # -- event log ----------------------------------------------------------
    async def append_event(self, event: Event) -> None:
        self._events.append(event)
        # Every mutation funnels through here, so this is the one place a snapshot
        # is needed to cover all of them.
        self._save()

    async def list_events(
        self,
        *,
        object_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[Event]:
        out = [
            e
            for e in self._events
            if (object_id is None or e.object_id == object_id)
            and (since is None or e.at >= since)
            and (until is None or e.at <= until)
        ]
        # Deep copies: the log is append-only, and a caller that mutates a returned
        # event's `before`/`after` dict must not be able to rewrite history.
        return [e.model_copy(deep=True) for e in out[-limit:][::-1]]

    # -- retrieval ----------------------------------------------------------
    async def _ensure_embeddings(self) -> None:
        pending = [oid for oid in self._unembedded if oid in self._objects]
        self._unembedded = []
        if not pending:
            return
        vectors = await self._embedder.embed([self._objects[oid].content for oid in pending])
        for object_id, vector in zip(pending, vectors, strict=True):
            self._index.put(object_id, vector)
            self._tokens[object_id] = set(tokenize(self._objects[object_id].content))

    async def search(
        self,
        query: str,
        *,
        scope: Scope | None = None,
        top_k: int = 12,
    ) -> list[tuple[ContextObject, float]]:
        await self._ensure_embeddings()
        query_vector = (await self._embedder.embed([query]))[0]
        query_tokens = set(tokenize(query))
        # One matrix-vector product for the whole corpus, rather than a similarity
        # call per candidate inside the loop below.
        similarities = self._index.similarities(query_vector)
        superseded = self._superseded_ids()
        now = datetime.now(UTC)

        scored: list[tuple[ContextObject, float]] = []
        for obj in self._objects.values():
            if not obj.is_active or obj.id in superseded:
                continue
            if not _in_search_scope(obj.scope, scope):
                continue
            lexical = lexical_score(query_tokens, self._tokens.get(obj.id, set()))
            vector = similarities.get(obj.id, 0.0)
            if lexical <= 0.0 and vector <= 0.0:
                continue
            scored.append(
                (
                    obj,
                    rank_score(
                        lexical=lexical,
                        vector=vector,
                        confidence=obj.confidence,
                        updated_at=obj.updated_at,
                        now=now,
                    ),
                )
            )
        scored.sort(key=lambda pair: (pair[1], pair[0].id), reverse=True)
        # Only the returned slice is copied, so the cost is bounded by top_k rather
        # than by the size of the corpus.
        return [(obj.model_copy(deep=True), score) for obj, score in scored[:top_k]]


def _in_search_scope(object_scope: Scope, query_scope: Scope | None) -> bool:
    """A conversation inside a project still sees the user's global context; it
    never sees another project's. See the Store protocol docstring."""
    if query_scope is None:
        return True
    if object_scope == query_scope:
        return True
    return object_scope.type is ScopeType.GLOBAL
