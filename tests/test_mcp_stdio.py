"""The stdio transport, driven by a real MCP client over a real subprocess.

Claude Desktop launches a command and speaks MCP on its stdin and stdout. That makes
this the only connector path needing **no deployment** — no host, no TLS, no public
URL — which is the whole reason it exists alongside streamable HTTP.

Every test here spawns the actual process. A mocked stdio server would prove the mock
agrees with us, and the two failure modes that matter — a dirty stdout and a missing
principal — are both invisible to a mock.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from coletar.mcp.server import STDIO_DEFAULT_SURFACE, stdio_principal
from coletar.schema.objects import Provider

REPO = Path(__file__).resolve().parents[1]


def _env(store: Path, tenant: str = "tenant_stdio") -> dict[str, str]:
    return {
        **os.environ,
        "COLETAR_STORE_PATH": str(store),
        "COLETAR_STORE_BACKEND": "memory",
        "COLETAR_DEFAULT_TENANT_ID": tenant,
    }


# --- identity without a token ------------------------------------------------------


def test_a_stdio_caller_has_an_identity(monkeypatch) -> None:
    """A different trust model, not a missing one.

    Over HTTP the bearer token is the identity because anyone can open a socket. A
    stdio server is launched *by* the user as a subprocess of their own client, so
    the OS already decided who this is — and a token pasted into the config file
    beside the command that reads it proves nothing.
    """
    from coletar.config import get_settings

    monkeypatch.setenv("COLETAR_DEFAULT_TENANT_ID", "tenant_alice")
    get_settings.cache_clear()
    try:
        principal = stdio_principal()
        assert principal.tenant_id == "tenant_alice"
        assert principal.can("read") and principal.can("write")
        # Locality decisions hang off this; guessing wrong silently widens what a
        # client may read.
        assert principal.surface is Provider.CLAUDE
        assert STDIO_DEFAULT_SURFACE is Provider.CLAUDE
    finally:
        get_settings.cache_clear()


# --- the real subprocess ------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_desktop_can_speak_to_it(tmp_path: Path) -> None:
    """The milestone, end to end: launch the command Claude Desktop would launch,
    complete a handshake, and get a memory back."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from coletar.schema.objects import Memory
    from coletar.schema.tenancy import tenant_id
    from coletar.store.memory import InMemoryStore

    store_path = tmp_path / "graph.json"
    seed = InMemoryStore(store_path)
    await seed.put_object(
        tenant_id("tenant_stdio"),
        Memory.from_write("Chris prefers fixed-point integers over doubles for money."),
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "coletar.cli", "serve-mcp-stdio"],
        env=_env(store_path),
        cwd=str(REPO),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = {t.name for t in (await session.list_tools()).tools}
        assert {"search_context", "write_memory"} <= tools

        result = await session.call_tool(
            "search_context", {"query": "how should I represent money"}
        )
        assert not getattr(result, "isError", False)
        text = " ".join(getattr(b, "text", "") for b in result.content)
        assert "fixed-point" in text


@pytest.mark.asyncio
async def test_a_write_lands_in_the_configured_tenant(tmp_path: Path) -> None:
    """The tenant comes from configuration here rather than from a token, so it is
    worth proving it is actually honoured rather than defaulted."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from coletar.schema.tenancy import tenant_id
    from coletar.store.memory import InMemoryStore

    store_path = tmp_path / "graph.json"
    InMemoryStore(store_path)  # create an empty snapshot

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "coletar.cli", "serve-mcp-stdio"],
        env=_env(store_path, tenant="tenant_bob"),
        cwd=str(REPO),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(
            "write_memory", {"content": "Bob ships on Thursdays.", "kind": "fact"}
        )
        assert not getattr(result, "isError", False)

    reopened = InMemoryStore(store_path)
    assert await reopened.list_objects(tenant_id("tenant_bob"), limit=10)
    assert await reopened.list_objects(tenant_id("tenant_someone_else"), limit=10) == []


@pytest.mark.asyncio
async def test_nothing_pollutes_stdout(tmp_path: Path) -> None:
    """stdout *is* the protocol channel.

    A startup banner like the HTTP server's would be parsed as a malformed JSON-RPC
    frame and the client would drop the connection with no useful error. The
    handshake below only completes if stdout carried protocol and nothing else — and
    the diagnostic line still has to exist, on stderr.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from coletar.store.memory import InMemoryStore

    store_path = tmp_path / "graph.json"
    InMemoryStore(store_path)
    errlog = tmp_path / "stderr.txt"

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "coletar.cli", "serve-mcp-stdio"],
        env=_env(store_path),
        cwd=str(REPO),
    )
    with errlog.open("wb") as sink:
        async with (
            stdio_client(params, errlog=sink) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

    diagnostics = errlog.read_text()
    assert "coletar mcp (stdio)" in diagnostics
    assert "tenant_stdio" in diagnostics
