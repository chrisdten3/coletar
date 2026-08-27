"""Canonical store: the graph, the vector index, and the append-only event log."""

from __future__ import annotations

from coletar.config import get_settings
from coletar.store.base import Store
from coletar.store.memory import InMemoryStore
from coletar.store.migrate import Migration, discover, run_migrations
from coletar.store.postgres import PostgresStore
from coletar.store.replay import Revision, replay_history, replay_object

__all__ = [
    "InMemoryStore",
    "Migration",
    "PostgresStore",
    "Revision",
    "Store",
    "build_store",
    "discover",
    "replay_history",
    "replay_object",
    "reset_store",
    "run_migrations",
]

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


def reset_store() -> None:
    """Drop the process-wide store. Tests and the CLI's one-shot commands need this;
    nothing in a running server should call it."""
    global _singleton
    _singleton = None
