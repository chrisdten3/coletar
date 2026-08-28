"""Store protocol.

Deliberately narrow: the MCP server, the local proxy, the compression job and the
compiler all go through this and nothing else. Swapping Postgres+pgvector for
something else is then an implementation choice, not a scope change (§5).

Two vocabulary points every implementation has to honour identically, because the
whole product reads through this interface:

**Active.** An object is active when nothing has retired it *and* nothing newer
supersedes it. Both halves matter. Compression (§6) retires superseded objects
eventually, but retrieval must not serve a stale fact in the window before the job
runs -- so supersession excludes an object from retrieval the moment the correction
is written, not the moment the job next happens to run.

**Scope.** `search` takes the scope a *conversation* is happening in, so a project
scope means "this project's objects and everything global", never "this project's
objects only" -- a user's global preferences do not stop applying because they
opened a project. `list_objects` is the opposite: an exact filter, because
`get_project_state` has to be able to answer "what is in this container".
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from coletar.retrieval.ranking import Scored
from coletar.schema.events import Event
from coletar.schema.objects import ContextObject, Edge, ObjectType, Scope


@runtime_checkable
class Store(Protocol):
    @property
    def embedder_model(self) -> str:
        """Which embedder produced this store's vectors.

        The retrieval trace records it, because a measured result that cannot be
        attributed to the model that produced it is not reproducible.
        """
        ...

    async def put_object(self, obj: ContextObject, *, event: Event | None = None) -> ContextObject:
        """Insert or update one object and append the matching event, atomically.

        The event carries the full before/after state. Implementations must not
        expose a path that writes the object without the event -- a row that exists
        with no event is a provenance failure we cannot detect after the fact.

        Embedding happens here, on the write path, so an object is searchable as
        soon as it is stored.
        """
        ...

    async def get_object(self, object_id: str) -> ContextObject | None: ...

    async def list_objects(
        self,
        *,
        type: ObjectType | None = None,
        scope: Scope | None = None,
        include_retired: bool = False,
        include_superseded: bool = False,
        limit: int = 200,
    ) -> list[ContextObject]:
        """Exact-scope listing. Defaults to active objects only."""
        ...

    async def retire_object(self, object_id: str, *, reason: str) -> None:
        """Soft-retire: excluded from retrieval and compile, still readable for
        provenance. The graph never hard-deletes on its own."""
        ...

    async def add_edge(self, edge: Edge) -> None:
        """Idempotent on (src_id, dst_id, type). Re-asserting an edge is not a
        second edge, and does not append a second event."""
        ...

    async def edges_from(self, object_id: str) -> list[Edge]: ...

    async def edges_to(self, object_id: str) -> list[Edge]: ...

    async def append_event(self, event: Event) -> None: ...

    async def list_events(
        self,
        *,
        object_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[Event]:
        """Newest first. Returned events are copies: the log is append-only, and a
        caller mutating what it was handed must not be able to rewrite history."""
        ...

    async def search(
        self,
        query: str,
        *,
        scope: Scope | None = None,
        top_k: int = 12,
    ) -> list[Scored]:
        """Hybrid vector + lexical retrieval over active objects, scope per the
        module docstring. Returns `Scored` descending.

        Backends narrow candidates however they can -- the in-process store scans,
        Postgres uses an ANN index unioned with a sparse match -- but all of them
        blend through `rank_score`, so a backend swap changes performance and not
        which memory a model sees (§5.1).
        """
        ...
