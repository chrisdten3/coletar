"""Canonical store: the graph, the vector index, and the append-only event log."""

from __future__ import annotations

from coletar.config import get_settings
from coletar.store.base import Store
from coletar.store.memory import InMemoryStore
from coletar.store.postgres import PostgresStore

__all__ = ["InMemoryStore", "PostgresStore", "Store", "build_store"]

_singleton: Store | None = None


def build_store() -> Store:
    """Process-wide store, chosen by `COLETAR_STORE_BACKEND`."""
    global _singleton
    if _singleton is None:
        settings = get_settings()
        if settings.store_backend == "postgres":
            _singleton = PostgresStore(
                settings.database_url, embedding_dim=settings.embedding_dim
            )
        else:
            _singleton = InMemoryStore(settings.store_path)
    return _singleton
