"""Read receipts (M10): locality made provable rather than merely configured.

The question these pin is a new one for the graph. Every *write* was already
answerable per object -- `event_log.object_id` is indexed -- but a *read* was not,
because SCOPE §5.1 records one trace per search rather than one row per hit. A
receipt is a projection of a trace onto a single object, so answering "which
assistants have seen this fact" costs an index rather than a second write path.

Run against both backends, the same discipline test_locality.py holds, because the
Postgres path is a JSONB containment query and the in-process path is a scan: two
implementations of one answer is exactly where they drift.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from coletar.retrieval import retrieve
from coletar.retrieval.embedding import HashingEmbedder
from coletar.schema.events import EventType
from coletar.schema.objects import Locality, LocalityMode, Memory, Provider
from coletar.schema.tenancy import tenant_id
from coletar.store.memory import InMemoryStore

TENANT = tenant_id("tenant_receipts")
OTHER = tenant_id("tenant_receipts_other")

LEDGER = "Ledger deploys to Fly.io; Postgres runs on the Hobby tier."
SALARY = "Salary band for the new backend hire is 68-74k."

#: The salary fact is kept to the local model, so a Claude search must never be
#: served it however well it matches.
LOCAL_ONLY = Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.LOCAL}))


@pytest.fixture(params=["memory", "postgres"])
async def store(request) -> AsyncIterator[object]:
    if request.param == "memory":
        yield InMemoryStore(embedder=HashingEmbedder(768))
        return

    import uuid
    from urllib.parse import urlparse, urlunparse

    import psycopg

    from coletar.store.migrate import run_migrations
    from coletar.store.postgres import PostgresStore

    dsn = request.getfixturevalue("postgres_dsn")  # skips when unreachable
    name = f"coletar_receipts_{uuid.uuid4().hex[:10]}"
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
    """One fact every surface may read, and one withheld from all but the local model."""
    read = await store.put_object(TENANT, Memory.from_write(LEDGER))
    withheld = await store.put_object(TENANT, Memory.from_write(SALARY, locality=LOCAL_ONLY))
    return store, read, withheld


async def test_a_receipt_names_which_assistant_asked(populated) -> None:
    store, read, _ = populated
    await retrieve(
        store,
        TENANT,
        "where does ledger deploy",
        caller_surface=Provider.CLAUDE,
        surface="mcp",
        principal="claude-key",
        record_query_text=True,
    )

    receipts = await store.reads_of(TENANT, read.id)
    assert len(receipts) == 1
    receipt = receipts[0]
    # Provider and surface answer different questions, and the dashboard needs
    # both: every Claude and ChatGPT surface arrives through the one `mcp` door,
    # so surface alone can never say which model saw a fact.
    assert receipt.provider == "claude"
    assert receipt.surface == "mcp"
    assert receipt.principal == "claude-key"
    assert receipt.query_text == "where does ledger deploy"
    assert receipt.returned >= 1


async def test_a_withheld_fact_accumulates_no_receipts(populated) -> None:
    """The absence is the auditable half.

    Locality already stops the read. What this adds is the evidence: a restricted
    object with an empty receipt list is a fact shown to have stayed withheld,
    rather than one merely configured to be. Search for the withheld text
    directly, so an empty result is the policy working and not a weak query.
    """
    store, read, withheld = populated

    await retrieve(
        store,
        TENANT,
        "salary band for the new backend hire",
        caller_surface=Provider.CLAUDE,
        surface="mcp",
    )

    assert await store.reads_of(TENANT, withheld.id) == []
    # The search really ran -- the empty list above is locality refusing, not a
    # retrieval that never happened. Asserted on the log rather than on a second
    # object's receipts, so the control does not depend on how ranking scored it.
    traces = await store.list_events(TENANT)
    assert [e for e in traces if e.type is EventType.RETRIEVAL_TRACE]

    # The surface it *was* kept for still gets served, and still leaves a receipt.
    await retrieve(
        store,
        TENANT,
        "salary band for the new backend hire",
        caller_surface=Provider.LOCAL,
        surface="proxy",
    )
    local_receipts = await store.reads_of(TENANT, withheld.id)
    assert [r.provider for r in local_receipts] == ["local"]


async def test_query_text_stays_absent_unless_the_caller_opted_in(populated) -> None:
    store, read, _ = populated
    await retrieve(store, TENANT, "where does ledger deploy", surface="mcp")

    receipt = (await store.reads_of(TENANT, read.id))[0]
    assert receipt.query_text is None
    assert receipt.query_digest  # correlating repeat questions still works
    # No trusted caller_surface means no trustworthy answer, and None says so
    # rather than inventing one.
    assert receipt.provider is None


async def test_receipts_do_not_cross_a_tenant_boundary(populated) -> None:
    """A receipt names an object and a caller. That is exactly the kind of row
    that must not leak, so the id being globally unique must grant nothing."""
    store, read, _ = populated
    twin = Memory.from_write(LEDGER)
    twin.id = read.id
    await store.put_object(OTHER, twin)

    await retrieve(store, OTHER, "where does ledger deploy", surface="mcp")

    assert await store.reads_of(TENANT, read.id) == []
    assert len(await store.reads_of(OTHER, read.id)) == 1


async def test_receipts_are_newest_first_and_bounded(populated) -> None:
    store, read, _ = populated
    for _ in range(4):
        await retrieve(store, TENANT, "where does ledger deploy", surface="mcp")

    receipts = await store.reads_of(TENANT, read.id)
    assert len(receipts) == 4
    assert [r.at for r in receipts] == sorted((r.at for r in receipts), reverse=True)
    assert len(await store.reads_of(TENANT, read.id, limit=2)) == 2
