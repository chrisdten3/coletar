"""Store protocol.

Deliberately narrow: the MCP server, the local proxy, the compression job and the
compiler all go through this and nothing else. Swapping Postgres+pgvector for
something else is then an implementation choice, not a scope change (§5).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from coletar.schema.events import Event
from coletar.schema.objects import ContextObject, Edge, ObjectType, Scope


@runtime_checkable
class Store(Protocol):
    async def put_object(self, obj: ContextObject, *, event: Event | None = None) -> ContextObject:
        """Insert or update one object and append the matching event. Atomic."""
        ...

    async def get_object(self, object_id: str) -> ContextObject | None: ...

    async def list_objects(
        self,
        *,
        type: ObjectType | None = None,
        scope: Scope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[ContextObject]: ...

    async def retire_object(self, object_id: str, *, reason: str) -> None:
        """Soft-retire: excluded from retrieval and compile, still readable for
        provenance. The graph never hard-deletes on its own."""
        ...

    async def add_edge(self, edge: Edge) -> None: ...

    async def edges_from(self, object_id: str) -> list[Edge]: ...

    async def append_event(self, event: Event) -> None: ...

    async def list_events(self, *, object_id: str | None = None, limit: int = 200) -> list[Event]:
        ...

    async def search(
        self,
        query: str,
        *,
        scope: Scope | None = None,
        top_k: int = 12,
    ) -> list[tuple[ContextObject, float]]:
        """Hybrid vector + graph retrieval. Returns (object, score) descending."""
        ...
