"""In-process store. Real enough to run the proxy and the MCP server end to end
with zero infrastructure, which is what makes the local-model wedge (§10 step 1)
dogfoodable on day one -- including a real vector index, since the default embedder
needs nothing installed (see `coletar.retrieval.embedding`).

**It implements exactly the tenant semantics Postgres does.** A backend that isolates
differently is worse than one that does not isolate at all: tests pass locally while
production behaves otherwise, which is the precise opposite of why this store exists.
So every structure below is namespaced by tenant, and each tenant gets its *own*
vector index rather than a shared one filtered afterwards -- cross-tenant candidates
never enter scoring in the first place, which mirrors the tenant-partitioned queries
Postgres runs and makes the isolation assertions the same on both sides.

Optionally snapshots to a JSON file, so consecutive CLI invocations see the same
graph. That is a development convenience, not the M1 backend: it rewrites the whole
file on every write and has no concurrency story at all.
"""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime
from pathlib import Path

from coletar.retrieval.embedding import Embedder, build_embedder, tokenize
from coletar.retrieval.index import VectorIndex
from coletar.retrieval.ranking import Scored, lexical_score, rank_score
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    ContextObject,
    Edge,
    EdgeType,
    ObjectType,
    Provider,
    Scope,
    ScopeType,
    object_from_record,
)
from coletar.schema.tenancy import LEGACY_TENANT, CrossTenantError, TenantId

#: Bumped when tenancy landed. A version-1 snapshot has no tenant on any record.
SNAPSHOT_FORMAT_VERSION = 2

_EdgeKey = tuple[TenantId, str, str, EdgeType]


class InMemoryStore:
    def __init__(self, path: Path | None = None, *, embedder: Embedder | None = None) -> None:
        self._objects: dict[tuple[TenantId, str], ContextObject] = {}
        # Keyed by the edge's identity within its tenant, so re-asserting an edge is
        # a no-op rather than a duplicate row.
        self._edges: dict[_EdgeKey, Edge] = {}
        self._events: dict[TenantId, list[Event]] = {}
        self._embedder = embedder or build_embedder()
        # One index per tenant: another tenant's vectors are never candidates, rather
        # than being candidates that a later filter is trusted to remove.
        self._indexes: dict[TenantId, VectorIndex] = {}
        # Content tokens, cached on write. Re-tokenizing every object's content on
        # every query is ~65ms at 10k objects, and the content has not changed.
        self._tokens: dict[tuple[TenantId, str], set[str]] = {}
        # Objects restored from a snapshot, whose vectors are recomputed in one
        # batch on first search rather than written into the snapshot file. Keeping
        # 768 floats per object out of a file a human is meant to be able to read
        # is worth one deferred batch call.
        self._unembedded: dict[TenantId, list[str]] = {}
        self._path = path
        if path is not None and path.exists():
            self._load()

    @property
    def embedder_model(self) -> str:
        return self._embedder.model

    def _index(self, tenant_id: TenantId) -> VectorIndex:
        index = self._indexes.get(tenant_id)
        if index is None:
            index = VectorIndex(self._embedder.dim)
            self._indexes[tenant_id] = index
        return index

    def tenants(self) -> set[TenantId]:
        """Every tenant with anything stored. For the CLI's visibility commands, not
        for retrieval -- nothing in a request path should enumerate tenants."""
        return {tenant for tenant, _ in self._objects} | set(self._events)

    # -- snapshot -----------------------------------------------------------
    def _load(self) -> None:
        assert self._path is not None
        raw = json.loads(self._path.read_text())
        version = int(raw.get("format_version", 1))
        legacy = version < SNAPSHOT_FORMAT_VERSION

        if legacy and (raw.get("objects") or raw.get("events")):
            # Visible, not silent. The old store could only represent one effective
            # tenant, so homing its records under a named tenant is the honest
            # upgrade rather than an invention -- but the user is told which name.
            warnings.warn(
                f"{self._path} is a format-{version} snapshot from before tenancy. "
                f"Its records are being assigned to {LEGACY_TENANT!r} and rewritten "
                f"as format {SNAPSHOT_FORMAT_VERSION}.",
                stacklevel=2,
            )

        def tenant_of(record: dict[str, object]) -> TenantId:
            return TenantId(str(record.get("tenant_id") or LEGACY_TENANT))

        for record in raw.get("objects", []):
            tenant = tenant_of(record)
            obj = object_from_record({k: v for k, v in record.items() if k != "tenant_id"})
            self._objects[(tenant, obj.id)] = obj
            self._unembedded.setdefault(tenant, []).append(obj.id)
        for record in raw.get("edges", []):
            tenant = tenant_of(record)
            edge = Edge.model_validate({k: v for k, v in record.items() if k != "tenant_id"})
            self._edges[(tenant, edge.src_id, edge.dst_id, edge.type)] = edge
        for record in raw.get("events", []):
            tenant = tenant_of(record)
            event = Event.model_validate({k: v for k, v in record.items() if k != "tenant_id"})
            self._events.setdefault(tenant, []).append(event)

        if legacy:
            # A record in the log as well as a warning on the console: the graph's
            # own history should say that its records were re-homed.
            self._events.setdefault(LEGACY_TENANT, []).append(
                Event(
                    type=EventType.STORE_MIGRATED,
                    actor=Actor.MIGRATION,
                    detail={
                        "from_format": version,
                        "to_format": SNAPSHOT_FORMAT_VERSION,
                        "assigned_tenant": str(LEGACY_TENANT),
                        "path": str(self._path),
                    },
                )
            )
            self._save()

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    "format_version": SNAPSHOT_FORMAT_VERSION,
                    "objects": [
                        {"tenant_id": tenant, **obj.model_dump(mode="json")}
                        for (tenant, _), obj in self._objects.items()
                    ],
                    "edges": [
                        {"tenant_id": key[0], **edge.model_dump(mode="json")}
                        for key, edge in self._edges.items()
                    ],
                    "events": [
                        {"tenant_id": tenant, **event.model_dump(mode="json")}
                        for tenant, events in self._events.items()
                        for event in events
                    ],
                },
                indent=2,
            )
        )

    # -- objects ------------------------------------------------------------
    async def put_object(
        self, tenant_id: TenantId, obj: ContextObject, *, event: Event | None = None
    ) -> ContextObject:
        if obj.supersedes is not None and (tenant_id, obj.supersedes) not in self._objects:
            # A correction may only correct something its own tenant owns. Postgres
            # refuses this with a composite foreign key; refusing it here too is what
            # keeps the two backends telling the same story.
            raise CrossTenantError(
                f"supersedes {obj.supersedes!r} is not an object in tenant {tenant_id!r}"
            )

        key = (tenant_id, obj.id)
        existing = self._objects.get(key)
        before = existing.model_dump(mode="json") if existing is not None else None
        # Store a detached copy, and never hand the stored instance back out. A
        # real database cannot be mutated by whoever last read from it, and neither
        # can this: aliasing would let a caller change the graph with no event
        # behind it, which is the one thing the substrate must not allow.
        stored = obj.model_copy(deep=True)
        if existing is not None:
            stored.touch()
        self._objects[key] = stored

        # On the write path, not deferred: an object is searchable as soon as it is
        # stored, so the bound on "when does a write become visible" is one embed
        # call rather than an unspecified background window.
        self._index(tenant_id).put(obj.id, (await self._embedder.embed([stored.content]))[0])
        self._tokens[key] = set(tokenize(stored.content))

        base = event or Event(
            type=EventType.OBJECT_UPDATED if existing else EventType.OBJECT_CREATED,
            object_id=obj.id,
            actor=Actor.SYSTEM,
            provider=stored.provenance.provider,
            detail={"type": stored.type, "scope": str(stored.scope)},
        )
        # before/after are the store's to fill in -- a caller supplying its own
        # event (the MCP server tagging a connector write) should not have to
        # remember to snapshot state, or be able to get it wrong.
        await self.append_event(
            tenant_id,
            base.model_copy(update={"before": before, "after": stored.model_dump(mode="json")}),
        )
        return stored.model_copy(deep=True)

    async def get_object(
        self, tenant_id: TenantId, object_id: str, *, caller_surface: Provider | None = None
    ) -> ContextObject | None:
        obj = self._objects.get((tenant_id, object_id))
        if obj is None or not obj.locality.visible_to(caller_surface):
            return None
        return obj.model_copy(deep=True)

    def _superseded_ids(self, tenant_id: TenantId) -> set[str]:
        return {
            obj.supersedes
            for (tenant, _), obj in self._objects.items()
            if tenant == tenant_id and obj.supersedes
        }

    async def list_objects(
        self,
        tenant_id: TenantId,
        *,
        type: ObjectType | None = None,
        scope: Scope | None = None,
        caller_surface: Provider | None = None,
        include_retired: bool = False,
        include_superseded: bool = False,
        limit: int = 200,
    ) -> list[ContextObject]:
        superseded = set() if include_superseded else self._superseded_ids(tenant_id)
        out = [
            obj
            for (tenant, _), obj in self._objects.items()
            if tenant == tenant_id
            and (type is None or obj.type == type)
            and (scope is None or obj.scope == scope)
            and obj.locality.visible_to(caller_surface)
            and (include_retired or obj.is_active)
            and obj.id not in superseded
        ]
        out.sort(key=lambda o: o.updated_at, reverse=True)
        return [o.model_copy(deep=True) for o in out[:limit]]

    async def retire_object(self, tenant_id: TenantId, object_id: str, *, reason: str) -> None:
        obj = self._objects.get((tenant_id, object_id))
        if obj is None or not obj.is_active:
            return
        before = obj.model_dump(mode="json")
        obj.retired_at = datetime.now(UTC)
        await self.append_event(
            tenant_id,
            Event(
                type=EventType.OBJECT_RETIRED,
                object_id=object_id,
                actor=Actor.JOB,
                before=before,
                after=obj.model_dump(mode="json"),
                detail={"reason": reason},
            ),
        )

    # -- edges --------------------------------------------------------------
    async def add_edge(self, tenant_id: TenantId, edge: Edge) -> None:
        for endpoint in (edge.src_id, edge.dst_id):
            if (tenant_id, endpoint) not in self._objects:
                raise CrossTenantError(
                    f"edge endpoint {endpoint!r} is not in tenant {tenant_id!r}"
                )
        key: _EdgeKey = (tenant_id, edge.src_id, edge.dst_id, edge.type)
        if key in self._edges:
            return  # idempotent: re-asserting an edge is not a second edge
        self._edges[key] = edge
        await self.append_event(
            tenant_id,
            Event(
                type=EventType.EDGE_CREATED,
                object_id=edge.src_id,
                detail={"dst": edge.dst_id, "edge_type": edge.type},
            ),
        )

    async def edges_from(self, tenant_id: TenantId, object_id: str) -> list[Edge]:
        return [e for (t, s, _, _), e in self._edges.items() if t == tenant_id and s == object_id]

    async def edges_to(self, tenant_id: TenantId, object_id: str) -> list[Edge]:
        return [e for (t, _, d, _), e in self._edges.items() if t == tenant_id and d == object_id]

    # -- event log ----------------------------------------------------------
    async def append_event(self, tenant_id: TenantId, event: Event) -> None:
        self._events.setdefault(tenant_id, []).append(event)
        # Every mutation funnels through here, so this is the one place a snapshot
        # is needed to cover all of them.
        self._save()

    async def list_events(
        self,
        tenant_id: TenantId,
        *,
        object_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[Event]:
        out = [
            e
            for e in self._events.get(tenant_id, [])
            if (object_id is None or e.object_id == object_id)
            and (since is None or e.at >= since)
            and (until is None or e.at <= until)
        ]
        # Deep copies: the log is append-only, and a caller that mutates a returned
        # event's `before`/`after` dict must not be able to rewrite history.
        return [e.model_copy(deep=True) for e in out[-limit:][::-1]]

    # -- retrieval ----------------------------------------------------------
    async def _ensure_embeddings(self, tenant_id: TenantId) -> None:
        pending = [
            oid for oid in self._unembedded.pop(tenant_id, []) if (tenant_id, oid) in self._objects
        ]
        if not pending:
            return
        contents = [self._objects[(tenant_id, oid)].content for oid in pending]
        vectors = await self._embedder.embed(contents)
        index = self._index(tenant_id)
        for object_id, vector, content in zip(pending, vectors, contents, strict=True):
            index.put(object_id, vector)
            self._tokens[(tenant_id, object_id)] = set(tokenize(content))

    async def search(
        self,
        tenant_id: TenantId,
        query: str,
        *,
        scope: Scope | None = None,
        caller_surface: Provider | None = None,
        top_k: int = 12,
    ) -> list[Scored]:
        await self._ensure_embeddings(tenant_id)
        query_vector = (await self._embedder.embed([query]))[0]
        query_tokens = set(tokenize(query))
        # One matrix-vector product over *this tenant's* index. Another tenant's
        # vectors are not candidates, rather than being candidates a later filter is
        # trusted to remove.
        similarities = self._index(tenant_id).similarities(query_vector)
        superseded = self._superseded_ids(tenant_id)
        now = datetime.now(UTC)

        scored: list[Scored] = []
        for (tenant, object_id), obj in self._objects.items():
            if tenant != tenant_id:
                continue
            # Stage 1, the policy filter (§5.1): retired and superseded objects and
            # anything out of scope never reach candidate generation at all, so no
            # later stage has to remember to exclude them.
            if not obj.is_active or object_id in superseded:
                continue
            if not _in_search_scope(obj.scope, scope):
                continue
            if not obj.locality.visible_to(caller_surface):
                continue
            lexical = lexical_score(query_tokens, self._tokens.get((tenant_id, object_id), set()))
            vector = similarities.get(object_id, 0.0)
            if lexical <= 0.0 and vector <= 0.0:
                continue
            scored.append(
                Scored(
                    obj=obj,
                    components=rank_score(
                        lexical=lexical,
                        vector=vector,
                        confidence=obj.confidence,
                        updated_at=obj.updated_at,
                        now=now,
                    ),
                )
            )
        scored.sort(key=lambda hit: (hit.score, hit.obj.id), reverse=True)
        # Only the returned slice is copied, so the cost is bounded by top_k rather
        # than by the size of the corpus.
        return [
            Scored(obj=hit.obj.model_copy(deep=True), components=hit.components)
            for hit in scored[:top_k]
        ]


def _in_search_scope(object_scope: Scope, query_scope: Scope | None) -> bool:
    """A conversation inside a project still sees the user's global context; it
    never sees another project's. See the Store protocol docstring."""
    if query_scope is None:
        return True
    if object_scope == query_scope:
        return True
    return object_scope.type is ScopeType.GLOBAL
