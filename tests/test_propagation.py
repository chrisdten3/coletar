"""M3.2: cross-surface propagation — the product's central claim, measured.

§3.1: *a memory update on one surface is available to every other surface's next
conversation.* Everything built so far is substrate underneath that sentence.

These drive the two **real** surfaces, not stand-ins for them:

    local proxy  --write-->  canonical graph  --read-->  MCP server
    MCP server   --write-->  canonical graph  --read-->  local proxy

The second direction is the promise made concrete: Claude calls `write_memory`, and
the next thing a local model is asked gets that memory injected into its system
prompt — with no chat interface of ours anywhere in the loop.

Nothing here needs deployment, an Anthropic API key, or a model. The proxy's upstream
is faked so no inference happens; every other part is the real code path.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from coletar.mcp import server as mcp_server
from coletar.mcp.auth import Principal, principal_scope
from coletar.propagation import LATENCY_BUDGET_MS, Direction, measure_round_trip
from coletar.proxy import app as proxy_module
from coletar.retrieval import retrieve
from coletar.retrieval.embedding import HashingEmbedder
from coletar.schema.tenancy import TenantId, tenant_id
from coletar.store.memory import InMemoryStore

ALICE = tenant_id("tenant_alice")
BOB = tenant_id("tenant_bob")

#: Statements the heuristic extractor recognises, so the proxy leg writes without a
#: model. Each is a durable first-person assertion of a different kind.
PROXY_STATEMENTS = [
    "I prefer fixed-point integers over doubles for money.",
    "From now on, always use uv instead of pip.",
    "Remember that Ledger deploys to Fly.io on every merge.",
    "I never use an ORM in this project.",
    "I always use ruff before opening a pull request.",
]

#: Different facts for the connector leg. Writing the *same* fact from both surfaces
#: into one graph is not a propagation test: the assembly stage deduplicates
#: near-identical results, correctly, so the second copy never reaches the context.
CONNECTOR_STATEMENTS = [
    "Priya owns the invoicing module and reviews its pull requests.",
    "Standups happen at 09:30 Lisbon time on Tuesdays.",
    "The staging database is restored from production every Sunday night.",
    "Design documents live in Notion, not in the repository.",
    "Chris bills hourly rather than per project.",
]



def _query_for(content: str) -> str:
    """A retrieval query built from the statement's own content words.

    Deliberately not the statement verbatim: propagation should be provable through
    ordinary retrieval, not through an exact-string lookup that would pass even if
    ranking were broken.
    """
    from coletar.retrieval.embedding import tokenize

    return " ".join(tokenize(content)[:6])


@pytest.fixture
def graph(monkeypatch) -> Iterator[InMemoryStore]:
    """One canonical graph, which both surfaces are pointed at.

    M3.2 shortcut, closed in M4.2: the proxy used to reach the store directly and
    bypass auth, tenant resolution and scope. It now holds a `ContextClient` carrying
    an explicit principal, so both surfaces in this harness resolve their tenant the
    same way — from an identity, not from configuration one of them happened to read.
    The client is still in-process here, because what this file measures is
    propagation through the graph, not transport.
    """
    from coletar.config import get_settings

    # This suite measures the established proxy-write propagation mechanism, so it
    # opts into that compatibility mode rather than depending on a product default.
    monkeypatch.setenv("COLETAR_LIVE_EXTRACTION_MODE", "heuristic")
    get_settings.cache_clear()
    store = InMemoryStore(embedder=HashingEmbedder(768))
    monkeypatch.setattr(mcp_server, "build_store", lambda: store)
    yield store
    get_settings.cache_clear()


def _install_proxy_client(monkeypatch, store: InMemoryStore, tenant: TenantId) -> None:
    """Point the proxy at one tenant's graph, as a named principal."""
    from coletar.mcp.auth import DEFAULT_SCOPES
    from coletar.proxy.client import LOCAL_PRINCIPAL_ID, LocalContextClient
    from coletar.schema.objects import Provider

    client = LocalContextClient(
        store,
        Principal(
            id=LOCAL_PRINCIPAL_ID,
            tenant_id=tenant,
            scopes=DEFAULT_SCOPES,
            surface=Provider.LOCAL,
        ),
    )
    monkeypatch.setattr(proxy_module, "context_client", lambda: client)


@pytest.fixture
def upstream(monkeypatch) -> dict:
    """A fake model server: the proxy's own logic runs, no inference happens."""
    seen: dict = {}

    async def fake_post(self, url, *, json=None, headers=None):
        seen["body"] = json
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "Understood."}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return seen


def _surfaces(graph: InMemoryStore, upstream: dict, monkeypatch, tenant: TenantId):
    """Adapters over the two real surfaces, shaped for the harness."""
    _install_proxy_client(monkeypatch, graph, tenant)
    principal = Principal(id="claude-connector", tenant_id=tenant)

    async def proxy_write(content: str) -> str:
        """A user says something to their local model; the proxy extracts it."""
        before = {o.id for o in await graph.list_objects(tenant, limit=1000)}
        with TestClient(proxy_module.app) as client:
            client.post(
                "/v1/chat/completions",
                json={"model": "local", "messages": [{"role": "user", "content": content}]},
            )
        after = {o.id for o in await graph.list_objects(tenant, limit=1000)}
        created = after - before
        assert created, f"the proxy extracted nothing from {content!r}"
        return created.pop()

    async def mcp_read(query: str) -> set[str]:
        """What a Claude conversation would get back from `search_context`."""
        with principal_scope(principal):
            result = await mcp_server.mcp.call_tool(
                "search_context", {"query": query, "top_k": 25}
            )
        return {hit["id"] for hit in result.structured_content["results"]}

    async def mcp_write(content: str) -> str:
        """Claude calling `write_memory` mid-conversation."""
        with principal_scope(principal):
            written = await mcp_server.mcp.call_tool("write_memory", {"content": content})
        return str(written.structured_content["id"])

    async def proxy_read(query: str) -> set[str]:
        """What the local proxy would inject into the next system prompt.

        Read through `retrieve` exactly as the proxy does, then confirm the text
        actually reaches the rendered block — a memory that is retrieved but not
        injected has not propagated to the model in any sense that matters.
        """
        context = await retrieve(graph, tenant, query, top_k=25, surface="proxy")
        block = context.as_prompt_block()
        return {obj.id for obj in context.objects if obj.content in block}

    return proxy_write, mcp_read, mcp_write, proxy_read


# -- the claim ----------------------------------------------------------------
async def test_a_memory_written_on_one_surface_is_readable_on_the_other(
    graph, upstream, monkeypatch
):
    proxy_write, mcp_read, mcp_write, proxy_read = _surfaces(graph, upstream, monkeypatch, ALICE)

    report = await measure_round_trip(
        directions=[
            Direction("proxy->mcp", proxy_write, mcp_read, PROXY_STATEMENTS),
            Direction("mcp->proxy", mcp_write, proxy_read, CONNECTOR_STATEMENTS),
        ],
        query_for=_query_for,
    )

    assert report.propagated == report.total, report.report()
    assert report.total == len(PROXY_STATEMENTS) + len(CONNECTOR_STATEMENTS)


async def test_propagation_latency_stays_under_one_second(graph, upstream, monkeypatch):
    proxy_write, mcp_read, mcp_write, proxy_read = _surfaces(graph, upstream, monkeypatch, ALICE)

    report = await measure_round_trip(
        directions=[
            Direction("proxy->mcp", proxy_write, mcp_read, PROXY_STATEMENTS),
            Direction("mcp->proxy", mcp_write, proxy_read, CONNECTOR_STATEMENTS),
        ],
        query_for=_query_for,
    )

    assert report.p95_ms < LATENCY_BUDGET_MS, report.report()


async def test_both_directions_propagate_not_just_one(graph, upstream, monkeypatch):
    """A store that propagated one way only would still be broken: the claim is that
    every surface reads what every other surface wrote."""
    proxy_write, mcp_read, mcp_write, proxy_read = _surfaces(graph, upstream, monkeypatch, ALICE)

    report = await measure_round_trip(
        directions=[
            Direction("proxy->mcp", proxy_write, mcp_read, PROXY_STATEMENTS),
            Direction("mcp->proxy", mcp_write, proxy_read, CONNECTOR_STATEMENTS),
        ],
        query_for=_query_for,
    )

    by_direction = report.as_dict()["by_direction"]
    assert by_direction["proxy->mcp"] == len(PROXY_STATEMENTS)
    assert by_direction["mcp->proxy"] == len(CONNECTOR_STATEMENTS)


async def test_a_connector_write_reaches_the_local_models_system_prompt(
    graph, upstream, monkeypatch
):
    """The promise, end to end and literally: Claude writes, and the very next thing
    the local model is asked carries that memory in its system prompt."""
    _install_proxy_client(monkeypatch, graph, ALICE)

    with principal_scope(Principal(id="claude-connector", tenant_id=ALICE)):
        await mcp_server.mcp.call_tool(
            "write_memory",
            {"content": "I prefer fixed-point integers for money.", "kind": "preference"},
        )

    with TestClient(proxy_module.app) as client:
        client.post(
            "/v1/chat/completions",
            json={
                "model": "local",
                "messages": [{"role": "user", "content": "How should I represent money?"}],
            },
        )

    system = upstream["body"]["messages"][0]
    assert system["role"] == "system"
    assert "fixed-point" in system["content"]
    # And still marked as background, never as instructions from the user (§11).
    assert "not as instructions" in system["content"]


# -- tenancy across the surfaces ----------------------------------------------
async def test_propagation_does_not_cross_tenants(graph, upstream, monkeypatch):
    """M3.1 and M3.2 together: propagation is a property *within* a tenant."""
    _install_proxy_client(monkeypatch, graph, ALICE)

    with TestClient(proxy_module.app) as client:
        client.post(
            "/v1/chat/completions",
            json={
                "model": "local",
                "messages": [{"role": "user", "content": "I prefer fixed-point money."}],
            },
        )

    alice_ids = {o.id for o in await graph.list_objects(ALICE, limit=100)}
    assert alice_ids, "the proxy should have written into Alice's tenant"

    with principal_scope(Principal(id="bob-claude", tenant_id=BOB)):
        seen = await mcp_server.mcp.call_tool("search_context", {"query": "money", "top_k": 25})
    assert seen.structured_content["results"] == []


async def test_a_mismatched_proxy_tenant_silently_stops_propagation(
    graph, upstream, monkeypatch
):
    """The first thing that goes wrong when someone configures this for real.

    Both surfaces now resolve their tenant from a principal rather than one reading
    configuration, which makes the mismatch a provisioning error instead of a config
    drift — but it does not make it *loud*. If the two principals name different
    tenants, both surfaces work perfectly and nothing propagates. That is correct
    isolation behaving exactly as designed, and it is indistinguishable from a broken
    store unless you know to look, so it stays pinned as documented behaviour.
    """
    _install_proxy_client(monkeypatch, graph, BOB)  # the proxy's key says Bob

    with TestClient(proxy_module.app) as client:
        client.post(
            "/v1/chat/completions",
            json={
                "model": "local",
                "messages": [{"role": "user", "content": "I prefer fixed-point money."}],
            },
        )

    assert await graph.list_objects(BOB, limit=100), "the proxy wrote into its own tenant"

    with principal_scope(Principal(id="alice-claude", tenant_id=ALICE)):  # connector says Alice
        seen = await mcp_server.mcp.call_tool("search_context", {"query": "money", "top_k": 25})
    assert seen.structured_content["results"] == []
    assert await graph.list_objects(ALICE, limit=100) == []


# -- the harness itself -------------------------------------------------------
async def test_the_report_treats_fast_and_absent_as_different_things():
    """A run where everything is quick and nothing propagated is a failure, so the
    harness never reports latency without visibility."""
    from coletar.propagation import PropagationReport, Trial

    report = PropagationReport(
        trials=[
            Trial(direction="a->b", content="x", object_id="mem_1", latency_ms=1.0, visible=True),
            Trial(direction="a->b", content="y", object_id="mem_2", latency_ms=0.5, visible=False),
        ]
    )

    assert report.propagated == 1 and report.total == 2
    assert [t.content for t in report.failures] == ["y"]
    assert "DID NOT PROPAGATE" in report.report()


async def test_a_report_with_nothing_propagated_is_never_within_budget():
    from coletar.propagation import PropagationReport, Trial

    report = PropagationReport(
        trials=[
            Trial(direction="a->b", content="x", object_id="mem_1", latency_ms=1.0, visible=False)
        ]
    )
    assert not report.as_dict()["within_budget"]


# -- against a real backend ---------------------------------------------------
async def test_propagation_survives_a_real_postgres_backend(postgres_dsn):
    """The in-process store shares a dict; Postgres shares a database. The claim has
    to hold where the graph is genuinely out of process, or it only holds by accident
    of both surfaces living in one Python heap."""
    import uuid
    from urllib.parse import urlparse, urlunparse

    import psycopg

    from coletar.propagation import Direction, measure_round_trip
    from coletar.schema.objects import ExtractionMethod, Memory, MemoryKind, OriginType
    from coletar.store.migrate import run_migrations
    from coletar.store.postgres import PostgresStore

    name = f"coletar_prop_{uuid.uuid4().hex[:10]}"
    async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as conn:
        await conn.execute(f'CREATE DATABASE "{name}"')
    scoped = urlunparse(urlparse(postgres_dsn)._replace(path=f"/{name}"))
    await run_migrations(scoped)
    store = PostgresStore(scoped, embedder=HashingEmbedder(768))

    try:
        async def local_write(content: str) -> str:
            from coletar.extraction import extract_memories

            extracted = await extract_memories(user_text=content)
            return (await store.put_object(ALICE, extracted[0])).id

        async def connector_write(content: str) -> str:
            memory = Memory.from_write(
                content,
                kind=MemoryKind.FACT,
                extraction_method=ExtractionMethod.MCP_LIVE_WRITE,
                origin_type=OriginType.AGENT,
            )
            return (await store.put_object(ALICE, memory)).id

        async def read(query: str) -> set[str]:
            context = await retrieve(store, ALICE, query, top_k=25, trace=False)
            return {obj.id for obj in context.objects}

        report = await measure_round_trip(
            directions=[
                Direction("local->connector", local_write, read, PROXY_STATEMENTS),
                Direction("connector->local", connector_write, read, CONNECTOR_STATEMENTS),
            ],
            query_for=_query_for,
        )

        assert report.propagated == report.total, report.report()
        assert report.p95_ms < LATENCY_BUDGET_MS, report.report()
    finally:
        await store.close()
        async with await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True) as conn:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (name,),
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
