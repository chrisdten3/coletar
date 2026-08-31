"""Locality: which connected surfaces may read an object back, run identically
against both backends -- the same discipline test_tenancy.py holds for tenants.

`Scope` decides which project an object belongs to; `Locality` decides which
surface. The default (`synced`) is every object's behavior before this field
existed, so most of this suite is about the opt-in case: an object marked
`local_only` for one surface must be invisible to every other surface, through
every read path, while a trusted internal caller (`caller_surface=None`) still
sees everything -- exactly the `scope=None` convention already in place.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from coletar.retrieval import retrieve
from coletar.retrieval.embedding import HashingEmbedder
from coletar.schema.objects import Locality, LocalityMode, Memory, Provider
from coletar.schema.tenancy import tenant_id
from coletar.store.memory import InMemoryStore

TENANT = tenant_id("tenant_locality")

LOCAL_ONLY_CLAUDE = Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.CLAUDE}))


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
    name = f"coletar_locality_{uuid.uuid4().hex[:10]}"
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
    """One synced fact everyone can read, one kept local to Claude only."""
    synced = await store.put_object(TENANT, Memory.from_write("Ships everywhere."))
    local = await store.put_object(
        TENANT, Memory.from_write("Only Claude should see this.", locality=LOCAL_ONLY_CLAUDE)
    )
    return store, synced, local


# -- the model itself ----------------------------------------------------------
def test_local_only_requires_at_least_one_surface():
    with pytest.raises(ValueError, match="local_only"):
        Locality(mode=LocalityMode.LOCAL_ONLY)


def test_synced_must_not_carry_surfaces():
    with pytest.raises(ValueError, match="synced"):
        Locality(surfaces=frozenset({Provider.CLAUDE}))


def test_default_locality_is_synced():
    assert Memory.from_write("A fact.").locality == Locality()


# -- every read path, both backends --------------------------------------------
async def test_the_owning_surface_sees_its_local_only_object(populated):
    store, _, local = populated
    found = await store.get_object(TENANT, local.id, caller_surface=Provider.CLAUDE)
    assert found is not None and found.id == local.id


async def test_a_different_surface_cannot_get_it_by_id(populated):
    store, _, local = populated
    assert await store.get_object(TENANT, local.id, caller_surface=Provider.CHATGPT) is None


async def test_a_different_surface_cannot_list_it(populated):
    store, synced, local = populated
    listed = {o.id for o in await store.list_objects(TENANT, caller_surface=Provider.CHATGPT)}
    assert listed == {synced.id}


async def test_a_different_surface_never_finds_it_in_search(populated):
    store, _, local = populated
    for query in ("only claude should see this", "claude", "see this"):
        hits = {
            hit.obj.id
            for hit in await store.search(TENANT, query, caller_surface=Provider.CHATGPT, top_k=50)
        }
        assert local.id not in hits, query


async def test_the_owning_surface_finds_it_in_search(populated):
    store, _, local = populated
    hits = {
        hit.obj.id
        for hit in await store.search(
            TENANT, "only claude should see this", caller_surface=Provider.CLAUDE, top_k=50
        )
    }
    assert local.id in hits


async def test_no_caller_surface_is_unrestricted_like_a_trusted_internal_caller(populated):
    """The CLI, a background job, the compiler: `caller_surface=None` applies no
    locality restriction, mirroring the existing `scope=None` convention."""
    store, synced, local = populated
    listed = {o.id for o in await store.list_objects(TENANT)}
    assert listed == {synced.id, local.id}
    assert (await store.get_object(TENANT, local.id)) is not None
    hits = {hit.obj.id for hit in await store.search(TENANT, "only claude", top_k=50)}
    assert local.id in hits


async def test_synced_objects_are_visible_to_every_surface(populated):
    store, synced, _ = populated
    for surface in (Provider.CLAUDE, Provider.CHATGPT, Provider.LOCAL, Provider.GEMINI):
        assert await store.get_object(TENANT, synced.id, caller_surface=surface) is not None


async def test_retrieval_never_leaks_a_local_only_object_to_another_surface(populated):
    """The same guarantee, exercised through `retrieve` rather than the store
    directly -- the MCP tool and the proxy both call through here."""
    store, _, local = populated
    context = await retrieve(
        store,
        TENANT,
        "only claude should see this",
        caller_surface=Provider.CHATGPT,
        surface="mcp",
        top_k=50,
    )
    assert local.id not in {o.id for o in context.objects}
