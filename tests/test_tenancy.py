"""M3.1: the tenant contract, run identically against both backends.

The in-process store exists so the stack works with no infrastructure. That is only
worth anything if it behaves the same as production — a backend that isolates
*differently* is worse than one that does not isolate at all, because the tests pass
locally and the graph leaks in production. So every test here is parametrized over
both stores and asserts the same thing of each.

The tests are adversarial rather than illustrative. "Alice searches and sees Alice's
things" proves nothing; what matters is what happens when Alice holds a *known* id
belonging to Bob and pushes it through every read path there is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from coletar.jobs import compress
from coletar.retrieval import retrieve
from coletar.retrieval.embedding import HashingEmbedder
from coletar.schema.events import EventType
from coletar.schema.objects import (
    Edge,
    EdgeType,
    Memory,
    MemoryKind,
    ObjectType,
    Scope,
    ScopeType,
)
from coletar.schema.tenancy import (
    CrossTenantError,
    InvalidTenantId,
    TenantId,
    tenant_id,
)
from coletar.store.memory import InMemoryStore
from coletar.store.replay import replay_history, replay_object

ALICE = tenant_id("tenant_alice")
BOB = tenant_id("tenant_bob")

SECRET = "Alice banks with Ficticious Trust, account 12345."


@pytest.fixture(params=["memory", "postgres"])
async def store(request) -> AsyncIterator[object]:
    """The same contract, both backends. Postgres skips when none is reachable.

    A fresh database per test rather than a shared one: these assertions are about
    exactly what a tenant can see, and leftovers from a previous test would make a
    passing run meaningless.
    """
    if request.param == "memory":
        yield InMemoryStore(embedder=HashingEmbedder(768))
        return

    import uuid
    from urllib.parse import urlparse, urlunparse

    import psycopg

    from coletar.store.migrate import run_migrations
    from coletar.store.postgres import PostgresStore

    dsn = request.getfixturevalue("postgres_dsn")  # skips when unreachable
    name = f"coletar_tenancy_{uuid.uuid4().hex[:10]}"
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(f'CREATE DATABASE "{name}"')
    scoped = urlunparse(urlparse(dsn)._replace(path=f"/{name}"))
    await run_migrations(scoped)

    backend = PostgresStore(scoped, embedder=HashingEmbedder(768))
    try:
        yield backend
    finally:
        await backend.close()
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (name,),
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture
async def populated(store):
    """Alice's secret and Bob's unrelated fact, in separate tenants."""
    alice = await store.put_object(ALICE, Memory.from_write(SECRET))
    bob = await store.put_object(BOB, Memory.from_write("Bob prefers tabs over spaces."))
    return store, alice, bob


# -- tenant ids ---------------------------------------------------------------
@pytest.mark.parametrize("raw", ["", "  ", "no", "UPPER", "has space", "x" * 65, "-lead"])
def test_a_malformed_tenant_id_is_refused(raw: str):
    with pytest.raises(InvalidTenantId):
        tenant_id(raw)


def test_a_valid_tenant_id_is_narrowed():
    assert tenant_id("tenant_alice") == TenantId("tenant_alice")


# -- the eight isolation cases ------------------------------------------------
async def test_alice_can_read_alice(populated):
    store, alice, _ = populated
    found = await store.get_object(ALICE, alice.id)
    assert found is not None and found.content == SECRET


async def test_alice_cannot_read_bob_by_id(populated):
    """Knowing an id grants nothing. Absent and forbidden are deliberately
    indistinguishable to the caller."""
    store, _, bob = populated
    assert await store.get_object(ALICE, bob.id) is None


async def test_alice_cannot_list_bob(populated):
    store, alice, bob = populated
    listed = {o.id for o in await store.list_objects(ALICE, limit=100)}
    assert listed == {alice.id}
    everything = {
        o.id
        for o in await store.list_objects(
            ALICE, include_retired=True, include_superseded=True, limit=100
        )
    }
    assert bob.id not in everything


async def test_alice_search_never_returns_bob(populated):
    store, _, bob = populated
    for query in ("bob prefers tabs", "tabs over spaces", "prefers"):
        hits = {hit.obj.id for hit in await store.search(ALICE, query, top_k=50)}
        assert bob.id not in hits, query


async def test_alice_cannot_supersede_bob(populated):
    """A correction may only correct something its own tenant owns. Postgres refuses
    this with a composite foreign key; the in-process store refuses it in code."""
    store, _, bob = populated
    with pytest.raises(CrossTenantError):
        await store.put_object(
            ALICE,
            Memory.from_write("Correcting Bob.", kind=MemoryKind.CORRECTION, supersedes=bob.id),
        )


async def test_alice_cannot_edge_to_bob(populated):
    store, alice, bob = populated
    edge = Edge(src_id=alice.id, dst_id=bob.id, type=EdgeType.RELATES_TO)
    with pytest.raises(CrossTenantError):
        await store.add_edge(ALICE, edge)
    assert await store.edges_from(ALICE, alice.id) == []


async def test_alice_cannot_inspect_bobs_events(populated):
    """The worst leak available: event rows carry full before/after object state, so
    an unfiltered log leaks *content*, not merely ids."""
    store, _, bob = populated

    for event in await store.list_events(ALICE, limit=100):
        assert event.object_id != bob.id
        assert "tabs over spaces" not in event.model_dump_json()

    assert await store.list_events(ALICE, object_id=bob.id) == []
    assert await replay_object(store, ALICE, bob.id) is None
    assert await replay_history(store, ALICE, bob.id) == []


async def test_retiring_alices_object_cannot_affect_bob(populated):
    store, alice, bob = populated

    await store.retire_object(BOB, alice.id, reason="wrong tenant")
    still_active = await store.get_object(ALICE, alice.id)
    assert still_active is not None and still_active.is_active

    await store.retire_object(ALICE, alice.id, reason="compressed")
    bobs = await store.get_object(BOB, bob.id)
    assert bobs is not None and bobs.is_active


async def test_the_same_object_id_may_exist_in_two_tenants(store):
    """Ids stay globally unique as generated, so logs stay unambiguous — but identity
    is the pair, so a collision across tenants is not a conflict."""
    shared = Memory.from_write("Alice's version.")
    await store.put_object(ALICE, shared)
    bobs = shared.model_copy(deep=True)
    bobs.content = "Bob's version."
    await store.put_object(BOB, bobs)

    a = await store.get_object(ALICE, shared.id)
    b = await store.get_object(BOB, shared.id)
    assert a is not None and b is not None
    assert a.content == "Alice's version."
    assert b.content == "Bob's version."


# -- tenancy holds through the layers above the store -------------------------
async def test_retrieval_and_its_trace_are_tenant_scoped(populated):
    store, alice, bob = populated

    context = await retrieve(store, ALICE, "prefers tabs", surface="cli", top_k=50)

    assert bob.id not in {o.id for o in context.objects}
    traces = [e for e in await store.list_events(ALICE) if e.type is EventType.RETRIEVAL_TRACE]
    assert len(traces) == 1
    assert bob.id not in traces[0].detail["returned_ids"]
    # And Bob sees nothing of Alice's search at all.
    assert not [e for e in await store.list_events(BOB) if e.type is EventType.RETRIEVAL_TRACE]


async def test_compression_only_touches_its_own_tenant(store):
    old = await store.put_object(ALICE, Memory.from_write("Alice works at Acme."))
    await store.put_object(
        ALICE,
        Memory.from_write("Alice is independent.", kind=MemoryKind.CORRECTION, supersedes=old.id),
    )
    bob_old = await store.put_object(BOB, Memory.from_write("Bob works at Acme."))
    await store.put_object(
        BOB,
        Memory.from_write("Bob is independent.", kind=MemoryKind.CORRECTION,
                          supersedes=bob_old.id),
    )

    report = await compress(store, ALICE)

    assert report.retired == 1
    retired = await store.get_object(ALICE, old.id)
    assert retired is not None and not retired.is_active
    untouched = await store.get_object(BOB, bob_old.id)
    assert untouched is not None and untouched.is_active


async def test_scopes_are_independent_between_tenants(store):
    """Two tenants may both hold a project called proj_ledger, with no relationship."""
    ledger = Scope(type=ScopeType.PROJECT, id="proj_ledger")
    a = await store.put_object(ALICE, Memory.from_write("Alice's ledger ships in March.",
                                                        scope=ledger))
    b = await store.put_object(BOB, Memory.from_write("Bob's ledger ships in June.",
                                                      scope=ledger))

    alice_view = {o.id for o in await store.list_objects(ALICE, scope=ledger)}
    assert alice_view == {a.id}
    hits = {hit.obj.id for hit in await store.search(BOB, "when does ledger ship",
                                                     scope=ledger, top_k=50)}
    assert a.id not in hits and b.id in hits


async def test_an_empty_tenant_is_simply_empty(store):
    await store.put_object(ALICE, Memory.from_write("Something."))
    empty = tenant_id("tenant_nobody")

    assert await store.list_objects(empty) == []
    assert await store.search(empty, "something") == []
    assert await store.list_events(empty) == []
    assert await store.list_objects(empty, type=ObjectType.MEMORY) == []


# -- the snapshot upgrade -----------------------------------------------------
def test_a_version_one_snapshot_is_upgraded_visibly(tmp_path):
    """The pre-tenancy store could only represent one effective tenant, so homing its
    records under a named one is the honest upgrade. It must not be silent."""
    import json

    from coletar.schema.tenancy import LEGACY_TENANT
    from coletar.store.memory import SNAPSHOT_FORMAT_VERSION

    legacy = Memory.from_write("A fact from before tenancy.")
    path = tmp_path / "graph.json"
    path.write_text(
        json.dumps(
            {
                "objects": [legacy.model_dump(mode="json")],
                "edges": [],
                "events": [],
            }
        )
    )

    with pytest.warns(UserWarning, match="before tenancy"):
        store = InMemoryStore(path, embedder=HashingEmbedder(768))

    rewritten = json.loads(path.read_text())
    assert rewritten["format_version"] == SNAPSHOT_FORMAT_VERSION
    assert rewritten["objects"][0]["tenant_id"] == LEGACY_TENANT
    assert store.tenants() == {LEGACY_TENANT}


async def test_the_upgrade_is_recorded_in_the_graphs_own_history(tmp_path):
    """A warning on the console is for the operator; an event is for the record."""
    import json

    from coletar.schema.tenancy import LEGACY_TENANT

    path = tmp_path / "graph.json"
    path.write_text(
        json.dumps({"objects": [Memory.from_write("Legacy.").model_dump(mode="json")]})
    )

    with pytest.warns(UserWarning):
        store = InMemoryStore(path, embedder=HashingEmbedder(768))

    migrations = [
        e for e in await store.list_events(LEGACY_TENANT) if e.type is EventType.STORE_MIGRATED
    ]
    assert len(migrations) == 1
    assert migrations[0].detail["assigned_tenant"] == LEGACY_TENANT
    assert migrations[0].detail["from_format"] == 1


async def test_a_current_snapshot_round_trips_without_warning(tmp_path):
    """Only a format-1 file warns. A snapshot this version wrote is read back
    silently, with each record still in the tenant that wrote it."""
    import warnings

    path = tmp_path / "graph.json"
    first = InMemoryStore(path, embedder=HashingEmbedder(768))
    await first.put_object(ALICE, Memory.from_write("Alice's fact."))
    bob = await first.put_object(BOB, Memory.from_write("Bob's fact."))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        second = InMemoryStore(path, embedder=HashingEmbedder(768))

    assert second.tenants() == {ALICE, BOB}
    alice_only = await second.list_objects(ALICE)
    assert [o.content for o in alice_only] == ["Alice's fact."]
    # And the reloaded store still isolates, including through the index it rebuilt
    # from the snapshot. Alice's own object may well match — the property is that
    # Bob's does not appear, not that nothing does.
    hits = {hit.obj.id for hit in await second.search(ALICE, "Bob's fact", top_k=50)}
    assert bob.id not in hits
