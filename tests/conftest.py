"""Shared fixtures.

The relevance corpus lives in `fixtures/relevance_set.json` rather than in a test
module because three separate milestones measure against it: M1.2's top-5 bar,
M4.1's "compression must not drop a top-5 object", and M6.2's extraction quality.
One corpus, or the three numbers are not comparable.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest

from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    ExtractionMethod,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
    Scope,
    ScopeType,
)
from coletar.store.memory import InMemoryStore

FIXTURES = Path(__file__).parent / "fixtures"


def scope_from(raw: str | None) -> Scope:
    return Scope(type=ScopeType.PROJECT, id=raw) if raw else GLOBAL_SCOPE


@dataclass(frozen=True)
class RelevanceSet:
    corpus: list[dict[str, object]]
    queries: list[dict[str, object]]


@pytest.fixture(scope="session")
def relevance_set() -> RelevanceSet:
    raw = json.loads((FIXTURES / "relevance_set.json").read_text())
    return RelevanceSet(corpus=raw["corpus"], queries=raw["queries"])


def build_corpus_object(item: dict[str, object]) -> ContextObject:
    scope = scope_from(item.get("scope"))  # type: ignore[arg-type]
    object_type = ObjectType(item["type"])
    content = str(item["content"])
    if object_type is ObjectType.MEMORY:
        return Memory.from_write(
            content,
            kind=MemoryKind(item["kind"]),
            scope=scope,
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            origin_type=OriginType.USER,
        )
    return ContextObject(
        type=object_type,
        content=content,
        scope=scope,
        confidence=0.9,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        provenance=Provenance(origin_type=OriginType.USER, provider=Provider.COLETAR),
    )


@pytest.fixture
async def relevance_store(relevance_set: RelevanceSet) -> tuple[InMemoryStore, dict[str, str]]:
    """The corpus loaded into a store, plus a key->id map so a test can name the
    object it expects without depending on id generation."""
    store = InMemoryStore()
    ids: dict[str, str] = {}
    for item in relevance_set.corpus:
        stored = await store.put_object(build_corpus_object(item))
        ids[str(item["key"])] = stored.id
    return store, ids


def _dsn_reachable(dsn: str) -> bool:
    parsed = urlparse(dsn)
    if parsed.hostname is None:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """A DSN for the Postgres backend, or a skip.

    Gated rather than mocked: a mocked database proves nothing about SQL. The suite
    stays green on a machine with no Docker, and these tests actually run in CI and
    anywhere `docker compose up` has been run.
    """
    dsn = os.environ.get(
        "COLETAR_TEST_DATABASE_URL", "postgresql://coletar:coletar@localhost:5433/coletar"
    )
    if not _dsn_reachable(dsn):
        pytest.skip(f"no Postgres reachable at {dsn} — run `docker compose up -d`")
    return dsn
