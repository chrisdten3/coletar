"""Postgres + pgvector backend.

NOT YET IMPLEMENTED — this is M1 work (see docs/ROADMAP.md). The table shape it
will target is fully specified in `migrations/001_init.sql`; what's missing is the
psycopg wiring and the embedding call behind `search`. Until then
`COLETAR_STORE_BACKEND=memory` runs the entire stack, which is enough to build and
dogfood the local-model wedge.

Implementation notes for whoever picks this up:
  * `put_object` must write the row and its event in one transaction. The event
    log is the provenance record; a row that exists without its event is a bug we
    cannot detect later.
  * `search` is a hybrid: cosine top-k over `object_embedding`, unioned with a
    trigram match on `content`, then re-ranked by confidence and recency the same
    way `InMemoryStore.search` does.
  * `retire_object` sets `retired_at`. Nothing in this package ever issues DELETE
    against `context_object` or `event_log`.
"""

from __future__ import annotations

from coletar.schema.events import Event
from coletar.schema.objects import ContextObject, Edge, ObjectType, Scope


class PostgresStore:
    def __init__(self, dsn: str, *, embedding_dim: int = 768) -> None:
        self.dsn = dsn
        self.embedding_dim = embedding_dim

    def _todo(self, method: str) -> NotImplementedError:
        return NotImplementedError(
            f"PostgresStore.{method} is M1 work; run with COLETAR_STORE_BACKEND=memory"
        )

    async def put_object(self, obj: ContextObject, *, event: Event | None = None) -> ContextObject:
        raise self._todo("put_object")

    async def get_object(self, object_id: str) -> ContextObject | None:
        raise self._todo("get_object")

    async def list_objects(
        self,
        *,
        type: ObjectType | None = None,
        scope: Scope | None = None,
        include_retired: bool = False,
        limit: int = 200,
    ) -> list[ContextObject]:
        raise self._todo("list_objects")

    async def retire_object(self, object_id: str, *, reason: str) -> None:
        raise self._todo("retire_object")

    async def add_edge(self, edge: Edge) -> None:
        raise self._todo("add_edge")

    async def edges_from(self, object_id: str) -> list[Edge]:
        raise self._todo("edges_from")

    async def append_event(self, event: Event) -> None:
        raise self._todo("append_event")

    async def list_events(self, *, object_id: str | None = None, limit: int = 200) -> list[Event]:
        raise self._todo("list_events")

    async def search(
        self,
        query: str,
        *,
        scope: Scope | None = None,
        top_k: int = 12,
    ) -> list[tuple[ContextObject, float]]:
        raise self._todo("search")
