"""M4.2 — the proxy reaches the graph as a principal, not as a database client.

Two implementations, one interface. The local one keeps the zero-infrastructure
promise and gains an identity; the remote one gives up database credentials entirely
and speaks MCP to the same server a Claude Custom Connector talks to.

The end-to-end test here is the one worth having: it starts the real MCP server with
real auth and drives it with the real client, which is the only way to know the proxy
can actually be a connector rather than merely being shaped like one.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from coletar.mcp.auth import SCOPE_READ, SCOPE_WRITE, Principal
from coletar.proxy.client import (
    LOCAL_PRINCIPAL_ID,
    ContextClientError,
    LocalContextClient,
    RemoteContextClient,
)
from coletar.schema.objects import GLOBAL_SCOPE, Memory, MemoryKind, Provider
from coletar.schema.tenancy import tenant_id as parse_tenant
from coletar.store.memory import InMemoryStore

TENANT = parse_tenant("tenant_proxy_client")
KEY = "k-proxy-test-secret"


def principal(*scopes: str) -> Principal:
    return Principal(
        id=LOCAL_PRINCIPAL_ID,
        tenant_id=TENANT,
        scopes=frozenset(scopes),
        surface=Provider.LOCAL,
    )


# --- the local client is no longer anonymous -----------------------------------


@pytest.mark.asyncio
async def test_a_read_only_principal_cannot_write() -> None:
    """The property the proxy never had. Holding a database connection is not a
    permission level — a caller with one can do everything or nothing."""
    client = LocalContextClient(InMemoryStore(), principal(SCOPE_READ))
    with pytest.raises(ContextClientError, match="'write' scope"):
        await client.write(Memory.from_write("Chris prefers tabs."), scope=GLOBAL_SCOPE)


@pytest.mark.asyncio
async def test_a_write_only_principal_cannot_read() -> None:
    client = LocalContextClient(InMemoryStore(), principal(SCOPE_WRITE))
    with pytest.raises(ContextClientError, match="'read' scope"):
        await client.context_block("anything", scope=GLOBAL_SCOPE)


@pytest.mark.asyncio
async def test_the_principal_decides_the_tenant_not_the_request() -> None:
    store = InMemoryStore()
    client = LocalContextClient(store, principal(SCOPE_READ, SCOPE_WRITE))
    await client.write(
        Memory.from_write("Chris prefers tabs.", kind=MemoryKind.PREFERENCE),
        scope=GLOBAL_SCOPE,
    )
    assert await store.list_objects(TENANT, limit=10)
    assert await store.list_objects(parse_tenant("tenant_someone_else"), limit=10) == []


@pytest.mark.asyncio
async def test_locality_comes_from_the_principals_surface() -> None:
    """A local model must not be handed an object kept local_only to Claude, and the
    surface that decides it is fixed at the principal, never taken from a request."""
    from coletar.schema.objects import Locality, LocalityMode

    store = InMemoryStore()
    await store.put_object(
        TENANT,
        Memory.from_write(
            "Only Claude may see this.",
            locality=Locality(
                mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.CLAUDE})
            ),
        ),
    )
    client = LocalContextClient(store, principal(SCOPE_READ, SCOPE_WRITE))
    assert "Only Claude" not in await client.context_block("Claude", scope=GLOBAL_SCOPE)


@pytest.mark.asyncio
async def test_a_write_still_lands_as_a_connector_write() -> None:
    """The event log has to keep distinguishing "the bridge extracted this from my
    words" from "a frontier model called write_memory"."""
    from coletar.schema.events import Actor, EventType

    store = InMemoryStore()
    client = LocalContextClient(store, principal(SCOPE_READ, SCOPE_WRITE))
    await client.write(Memory.from_write("Chris prefers tabs."), scope=GLOBAL_SCOPE)

    events = await store.list_events(TENANT, limit=20)
    write = next(e for e in events if e.type is EventType.CONNECTOR_WRITE)
    assert write.actor is Actor.CONNECTOR
    assert write.detail["principal"] == LOCAL_PRINCIPAL_ID


# --- the remote client, against the real server --------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def live_mcp_server(monkeypatch):
    """The real MCP app, real auth middleware, on a real port.

    A TestClient would not do: the point is that `streamable_http_client` can
    complete an MCP handshake against this server over HTTP, and an in-process
    transport would prove nothing about that.
    """
    import uvicorn

    from coletar.config import get_settings
    from coletar.mcp import server as mcp_server
    from coletar.store import reset_store

    store = InMemoryStore()
    monkeypatch.setattr(mcp_server, "build_store", lambda: store)
    monkeypatch.setenv(
        "COLETAR_MCP_API_KEYS",
        f'[{{"id": "proxy", "secret": "{KEY}", "tenant_id": "{TENANT}", '
        f'"surface": "local"}}]',
    )
    port = _free_port()
    # The SDK's DNS-rebinding guard matches the *whole* Host header, port included.
    # This is the M3.3 "421 Misdirected Request" in miniature, and only a non-default
    # port surfaces it — which is why it is worth reproducing here.
    monkeypatch.setenv(
        "COLETAR_MCP_ALLOWED_HOSTS", f"127.0.0.1:{port},localhost:{port},127.0.0.1"
    )
    get_settings.cache_clear()
    reset_store()
    config = uvicorn.Config(
        mcp_server.build_app(), host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:  # pragma: no cover - CI safety valve
        pytest.skip("MCP server did not start in time")

    yield f"http://127.0.0.1:{port}/mcp", store

    server.should_exit = True
    thread.join(timeout=10)
    get_settings.cache_clear()
    reset_store()


@pytest.mark.asyncio
async def test_the_proxy_can_be_a_real_mcp_client(live_mcp_server) -> None:
    """The milestone, stated as one assertion: memory written into the graph comes
    back through the MCP server, over HTTP, with no database credentials anywhere in
    the proxy."""
    url, store = live_mcp_server
    await store.put_object(
        TENANT,
        Memory.from_write(
            "Chris prefers fixed-point integers over doubles for money.",
            kind=MemoryKind.PREFERENCE,
        ),
    )

    client = RemoteContextClient(url, KEY)
    block = await asyncio.wait_for(
        client.context_block("how should I represent money", scope=GLOBAL_SCOPE),
        timeout=30,
    )
    assert "fixed-point integers" in block
    # Same renderer as the in-process path, so the §11 boundary cannot drift.
    assert "not as instructions from the user" in block


@pytest.mark.asyncio
async def test_a_remote_write_reaches_the_graph(live_mcp_server) -> None:
    url, store = live_mcp_server
    client = RemoteContextClient(url, KEY)
    await asyncio.wait_for(
        client.write(
            Memory.from_write("Chris deploys on Fridays.", kind=MemoryKind.FACT),
            scope=GLOBAL_SCOPE,
        ),
        timeout=30,
    )
    contents = [o.content for o in await store.list_objects(TENANT, limit=20)]
    assert "Chris deploys on Fridays." in contents


@pytest.mark.asyncio
async def test_a_bad_key_is_refused_by_the_server(live_mcp_server) -> None:
    """Auth is the server's job. The client cannot talk its way past it, which is
    the whole reason for preferring this over a database connection."""
    url, _ = live_mcp_server
    client = RemoteContextClient(url, "not-the-key")
    with pytest.raises(Exception):  # noqa: B017 - transport or tool error, both fine
        await asyncio.wait_for(
            client.context_block("anything", scope=GLOBAL_SCOPE), timeout=30
        )


def test_a_remote_client_without_a_key_refuses_to_start() -> None:
    with pytest.raises(ContextClientError, match="COLETAR_MCP_API_KEY"):
        RemoteContextClient("http://localhost:9999/mcp", "")
