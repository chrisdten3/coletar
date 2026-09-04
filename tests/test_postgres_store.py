"""M1.1b: the Postgres + pgvector backend and the migration runner.

Gated on a reachable database rather than mocked. A mocked cursor proves nothing
about SQL, and the whole point of these tests is the SQL -- the transaction
boundary, the soft-retire, the active predicate, and the fact that a backend swap
does not change which memory a model sees.

Run them with `docker compose up -d`, or point COLETAR_TEST_DATABASE_URL at any
Postgres with the vector and pg_trgm extensions available.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

from coletar.capture import capture_turn
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
from coletar.retrieval.embedding import HashingEmbedder
from coletar.schema.events import EventType
from coletar.schema.objects import (
    ContextObject,
    Edge,
    EdgeType,
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
from coletar.store.migrate import discover, run_migrations
from coletar.store.postgres import PostgresStore
from coletar.store.replay import replay_object
from conftest import TENANT, RelevanceSet, build_corpus_object, scope_from

MIGRATION_BUDGET_SECONDS = 5.0


def _with_database(dsn: str, database: str) -> str:
    parts = urlparse(dsn)
    return urlunparse(parts._replace(path=f"/{database}"))


@pytest.fixture
async def empty_database(postgres_dsn: str) -> AsyncIterator[str]:
    """A freshly created, genuinely empty database, dropped afterwards.

    "Migration runs clean on an empty database" is only meaningful against one that
    was actually empty, so the compose file's pre-initialized `coletar` database
    cannot be the thing under test.
    """
    import psycopg

    name = f"coletar_test_{uuid.uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as conn:
        await conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield _with_database(postgres_dsn, name)
    finally:
        async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as conn:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (name,),
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture
async def store(empty_database: str) -> AsyncIterator[PostgresStore]:
    await run_migrations(empty_database)
    backend = PostgresStore(empty_database, embedder=HashingEmbedder(768))
    try:
        yield backend
    finally:
        await backend.close()


# -- migrations ---------------------------------------------------------------
async def test_migrations_stand_the_schema_up_from_empty_within_budget(empty_database: str):
    start = time.perf_counter()
    applied = await run_migrations(empty_database)
    elapsed = time.perf_counter() - start

    assert applied == [m.filename for m in discover()]
    assert elapsed < MIGRATION_BUDGET_SECONDS, f"migration took {elapsed:.1f}s"


async def test_running_migrations_twice_applies_nothing_the_second_time(empty_database: str):
    await run_migrations(empty_database)
    assert await run_migrations(empty_database) == []


async def test_editing_an_applied_migration_is_refused(empty_database: str, tmp_path):
    original = tmp_path / "001_thing.sql"
    original.write_text("CREATE TABLE IF NOT EXISTS thing (id TEXT PRIMARY KEY);")
    await run_migrations(empty_database, directory=tmp_path)

    original.write_text("CREATE TABLE IF NOT EXISTS thing (id TEXT PRIMARY KEY, extra TEXT);")
    with pytest.raises(RuntimeError, match="changed after it was applied"):
        await run_migrations(empty_database, directory=tmp_path)


# -- the same M1.1 invariants, against real SQL --------------------------------
@pytest.mark.parametrize("object_type", list(ObjectType))
async def test_every_object_type_round_trips_exactly(store: PostgresStore, object_type):
    if object_type is ObjectType.MEMORY:
        original = Memory.from_write("Chris ships on Fridays.", kind=MemoryKind.FACT)
    else:
        original = ContextObject(
            type=object_type,
            content=f"A {object_type} object.",
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            provenance=Provenance(origin_type=OriginType.USER, provider=Provider.COLETAR),
        )
    await store.put_object(TENANT, original)

    read_back = await store.get_object(TENANT, original.id)

    assert read_back is not None
    assert read_back.model_dump() == original.model_dump()
    assert type(read_back) is type(original)


async def test_object_and_event_commit_together(store: PostgresStore):
    memory = await store.put_object(TENANT, Memory.from_write("One write, one event."))
    events = await store.list_events(TENANT, object_id=memory.id)
    assert len(events) == 1
    assert events[0].type is EventType.OBJECT_CREATED
    assert events[0].after is not None
    assert events[0].after["content"] == "One write, one event."


async def test_active_excludes_superseded_and_retired(store: PostgresStore):
    old = await store.put_object(TENANT, Memory.from_write("Chris works at Acme."))
    new = await store.put_object(TENANT, 
        Memory.from_write(
            "Chris is independent.", kind=MemoryKind.CORRECTION, supersedes=old.id
        )
    )

    active = {o.id for o in await store.list_objects(TENANT, type=ObjectType.MEMORY)}
    assert active == {new.id}
    assert {o.id for o in await store.list_objects(
        TENANT, include_superseded=True)} == {old.id, new.id}


async def test_retire_is_soft_and_the_object_stays_readable(store: PostgresStore):
    memory = await store.put_object(TENANT, Memory.from_write("Retire me."))
    await store.retire_object(TENANT, memory.id, reason="compressed")

    still_there = await store.get_object(TENANT, memory.id)
    assert still_there is not None and not still_there.is_active
    assert await store.list_objects(TENANT, type=ObjectType.MEMORY) == []


async def test_duplicate_edge_is_idempotent(store: PostgresStore):
    a = await store.put_object(TENANT, Memory.from_write("First."))
    b = await store.put_object(TENANT, Memory.from_write("Second."))
    edge = Edge(src_id=a.id, dst_id=b.id, type=EdgeType.RELATES_TO)

    await store.add_edge(TENANT, edge)
    await store.add_edge(TENANT, edge)

    assert len(await store.edges_from(TENANT, a.id)) == 1
    assert len(await store.edges_to(TENANT, b.id)) == 1
    edge_events = [e for e in await store.list_events(TENANT) if e.type is EventType.EDGE_CREATED]
    assert len(edge_events) == 1


async def test_replay_works_against_the_postgres_log(store: PostgresStore):
    memory = await store.put_object(TENANT, Memory.from_write("Chris works at Acme."))
    stored = await store.get_object(TENANT, memory.id)
    assert stored is not None
    stored.content = "Chris is independent."
    await store.put_object(TENANT, stored)

    current = await replay_object(store, TENANT, memory.id)
    assert current is not None and current.content == "Chris is independent."


async def test_a_write_is_searchable_on_the_very_next_call(store: PostgresStore):
    memory = await store.put_object(TENANT, Memory.from_write("Ledger deploys to Fly.io."))
    found = {hit.obj.id for hit in await store.search(TENANT, "where does ledger deploy")}
    assert memory.id in found


async def test_encrypted_episode_is_not_searchable_and_its_key_can_be_shredded(
    store: PostgresStore,
):
    episode = await capture_turn(
        store,
        TENANT,
        "private launch codename albatross",
        surface=Provider.CHATGPT,
    )
    assert await decrypt_episode(store, TENANT, episode) == "private launch codename albatross"
    hits = await store.search(TENANT, "private launch codename albatross", top_k=50)
    assert episode.id not in {hit.obj.id for hit in hits}

    assert await store.shred_object_key(TENANT, episode.id, reason="test")
    with pytest.raises(EpisodeKeyUnavailable):
        await decrypt_episode(store, TENANT, episode)
    events = await store.list_events(TENANT, object_id=episode.id)
    assert any(event.type is EventType.OBJECT_SHREDDED for event in events)


async def test_project_scoped_search_sees_global_but_not_another_project(store: PostgresStore):
    ledger = Scope(type=ScopeType.PROJECT, id="proj_ledger")
    mine = await store.put_object(TENANT, Memory.from_write("Ledger ships in March.", scope=ledger))
    globally = await store.put_object(TENANT, Memory.from_write("Chris ships things in March."))
    await store.put_object(TENANT, 
        Memory.from_write(
            "Atlas ships in March.", scope=Scope(type=ScopeType.PROJECT, id="proj_atlas")
        )
    )

    results = await store.search(TENANT, "what ships in march", scope=ledger, top_k=20)
    found = {hit.obj.id for hit in results}

    assert found == {mine.id, globally.id}


async def test_ranking_matches_the_in_process_store(
    store: PostgresStore, relevance_set: RelevanceSet
):
    """The differentiating property of the Store protocol: swapping the backend
    changes performance, not which memory a model sees."""
    reference = InMemoryStore(embedder=HashingEmbedder(768))
    for item in relevance_set.corpus:
        obj = build_corpus_object(item)
        await reference.put_object(TENANT, obj)
        await store.put_object(TENANT, obj)

    disagreements: list[str] = []
    for query in relevance_set.queries:
        scope = scope_from(query.get("scope"))
        text = str(query["query"])
        expected_hits = await reference.search(TENANT, text, scope=scope, top_k=5)
        expected = [hit.obj.content for hit in expected_hits]
        actual_hits = await store.search(TENANT, text, scope=scope, top_k=5)
        actual = [hit.obj.content for hit in actual_hits]
        if expected[:3] != actual[:3]:
            disagreements.append(f"{query['query']!r}: {expected[:3]} vs {actual[:3]}")

    assert not disagreements, "\n".join(disagreements)


# -- M2.3: candidate generation must not lose what an exact scan would find -----
async def test_postgres_candidate_recall_matches_exact_in_process_search(store):
    """§5.1's candidate-recall boundary, across the backend seam.

    Postgres narrows with an ANN index unioned with a sparse match; the in-process
    store scans every object exactly. If narrowing drops an object the exact search
    keeps, no reranker downstream can recover it — so this compares the two directly
    rather than comparing Postgres to a fixed number.
    """
    from coletar.retrieval.evaluation import CANDIDATE_DEPTH, load_eval_set, scope_from, seed_corpus

    data = load_eval_set(Path(__file__).parent / "fixtures" / "retrieval_eval.json")
    reference = InMemoryStore(embedder=HashingEmbedder(768))
    ref_ids = await seed_corpus(reference, TENANT, data["corpus"])
    pg_ids = await seed_corpus(store, TENANT, data["corpus"])
    ref_key = {v: k for k, v in ref_ids.items()}
    pg_key = {v: k for k, v in pg_ids.items()}

    retained = 0
    total = 0
    leaks: list[str] = []
    for query in data["queries"]:
        scope = scope_from(query.get("scope"))
        text = str(query["query"])
        expected_keys = {
            ref_key[hit.obj.id]
            for hit in await reference.search(TENANT, text, scope=scope, top_k=CANDIDATE_DEPTH)
        }
        actual_keys = {
            pg_key[hit.obj.id]
            for hit in await store.search(TENANT, text, scope=scope, top_k=CANDIDATE_DEPTH)
        }
        if not expected_keys:
            continue
        total += len(expected_keys)
        retained += len(expected_keys & actual_keys)
        forbidden = query.get("expect_absent")
        if forbidden and forbidden in actual_keys:
            leaks.append(f"{text} -> {forbidden}")

    recall = retained / total if total else 1.0
    assert not leaks, f"superseded or cross-scope objects surfaced: {leaks[:5]}"
    assert recall >= 0.98, f"candidate recall@{CANDIDATE_DEPTH} was {recall:.1%}"
