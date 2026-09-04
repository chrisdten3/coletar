"""How the proxy reaches the graph (ROADMAP M4.2).

Until now the proxy called `build_store()` and talked to the database directly. That
is fine for one person on one laptop and wrong for anything else, and the reason is
not really the credentials — it is that a caller holding a connection has no
identity. It cannot be granted read without write, cannot be confined to a tenant it
did not choose, and cannot be revoked. Every *other* surface reaches the graph as an
authenticated principal; the proxy was the exception.

Two implementations behind one interface:

`LocalContextClient` keeps the zero-infrastructure promise — no server to run, the
in-process store, the wedge still works on day one. What changes is that it now
carries an explicit `Principal` and enforces its scopes, so "the local daemon" is a
named identity with stated permissions rather than an unbounded one.

`RemoteContextClient` speaks MCP to the hosted server over streamable HTTP with an
API key, and holds no database credentials at all. Notably it does *not* expose a
principal: it cannot know one. The key maps to a principal on the server, which is
the entire point — an identity a client can describe is one a client can claim.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from coletar.config import get_settings
from coletar.ingest import remember
from coletar.mcp.auth import SCOPE_READ, SCOPE_WRITE, Principal
from coletar.retrieval import retrieve
from coletar.retrieval.context import ContextLine, render_prompt_block
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import Memory, Provider, Scope
from coletar.schema.tenancy import tenant_id as parse_tenant_id
from coletar.store.base import Store

#: The proxy holds no bearer token in local mode — it is a local daemon, not a
#: remote connector — so it names itself explicitly rather than writing anonymously.
LOCAL_PRINCIPAL_ID = "local-proxy"


class ContextClientError(Exception):
    """A refusal the operator can act on, phrased for them rather than for a log."""


class ContextClient(Protocol):
    """What the proxy needs from the graph, and nothing more.

    Deliberately narrow. A protocol shaped like `Store` would have let the remote
    implementation grow past what MCP actually exposes, and the gap would surface as
    a runtime failure in the one mode that is hardest to test.
    """

    #: Shown at startup so the operator can see how this daemon reaches the graph.
    label: str

    async def context_block(self, query: str, *, scope: Scope, style: str = "full") -> str:
        """Retrieved memory, already rendered for injection."""
        ...

    async def write(self, memory: Memory, *, scope: Scope) -> None: ...

    async def capture(self, text: str, *, scope: Scope) -> str:
        """Queue one encrypted user turn and return its episode id."""
        ...

    async def aclose(self) -> None: ...


class LocalContextClient:
    """In-process, but no longer anonymous."""

    def __init__(self, store: Store, principal: Principal) -> None:
        self._store = store
        self._principal = principal
        self.label = f"in-process store as {principal.id} (tenant {principal.tenant_id})"

    @property
    def principal(self) -> Principal:
        return self._principal

    def _require(self, scope: str) -> None:
        if not self._principal.can(scope):
            raise ContextClientError(
                f"{self._principal.id} does not hold the {scope!r} scope; it holds "
                f"{', '.join(sorted(self._principal.scopes)) or 'nothing'}"
            )

    async def context_block(self, query: str, *, scope: Scope, style: str = "full") -> str:
        self._require(SCOPE_READ)
        settings = get_settings()
        context = await retrieve(
            self._store,
            self._principal.tenant_id,
            query,
            scope=scope,
            # An object kept local_only to Claude or ChatGPT must not reach a local
            # model's prompt. The surface comes from the principal, never the request.
            caller_surface=self._principal.surface,
            top_k=settings.retrieval_top_k,
            token_budget=settings.retrieval_token_budget,
            # Injecting into a local model's prompt is as consequential as an MCP
            # search, so it leaves the same record.
            surface="proxy",
            principal=self._principal.id,
        )
        return context.as_prompt_block(style=style)

    async def write(self, memory: Memory, *, scope: Scope) -> None:
        self._require(SCOPE_WRITE)
        await remember(
            self._store,
            self._principal.tenant_id,
            memory,
            event=Event(
                type=EventType.CONNECTOR_WRITE,
                object_id=memory.id,
                actor=Actor.CONNECTOR,
                provider=memory.provenance.provider,
                detail={
                    "kind": str(memory.kind),
                    "scope": str(scope),
                    "principal": self._principal.id,
                },
            ),
            caller_surface=self._principal.surface,
        )

    async def capture(self, text: str, *, scope: Scope) -> str:
        self._require(SCOPE_WRITE)
        from coletar.capture import capture_turn

        episode = await capture_turn(
            self._store,
            self._principal.tenant_id,
            text,
            surface=self._principal.surface,
            scope=scope,
            principal_id=self._principal.id,
            detail={"surface": "local-proxy"},
        )
        return episode.id

    async def aclose(self) -> None:
        return None


class RemoteContextClient:
    """Speaks MCP to the hosted server. Holds no database credentials.

    A session is opened per call rather than held open. A local daemon is idle
    between turns, and a long-lived streamable-HTTP session that has to survive
    sleep, network changes and server restarts is a reconnection problem this does
    not need — the cost is one handshake on a path that is already waiting on a
    language model.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        if not api_key:
            raise ContextClientError(
                "COLETAR_MCP_URL is set but COLETAR_MCP_API_KEY is empty; the proxy "
                "cannot reach the server without a key"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.label = f"MCP server at {self._base_url} (no database credentials held)"

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.streamable_http import (  # type: ignore[attr-defined]
            create_mcp_http_client,  # defined there, just missing from __all__
            streamable_http_client,
        )

        # The same bearer header a Claude Custom Connector sends, because this is the
        # same server and the same auth path — the proxy is not a privileged caller.
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {self._api_key}"})
        async with (
            http,
            streamable_http_client(self._base_url, http_client=http) as streams,
        ):
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
        if getattr(result, "isError", False):
            raise ContextClientError(f"{tool} failed: {_text_of(result)}")
        return _structured(result)

    async def context_block(self, query: str, *, scope: Scope, style: str = "full") -> str:
        payload = await self._call(
            "search_context",
            {
                "query": query,
                "project_id": scope.id,
                "top_k": get_settings().retrieval_top_k,
            },
        )
        # Rendered from the same shared renderer the in-process path uses, so the
        # §11 marker and the line format cannot drift between the two modes.
        return render_prompt_block(
            [
                ContextLine(
                    content=str(view["content"]),
                    kind=str(view.get("kind") or view.get("type")),
                    confidence=float(view.get("confidence", 1.0)),
                    provider=str((view.get("provenance") or {}).get("provider", "coletar")),
                )
                for view in payload.get("results", [])
            ],
            style=style,
        )

    async def write(self, memory: Memory, *, scope: Scope) -> None:
        await self._call(
            "write_memory",
            {
                "content": memory.content,
                "kind": str(memory.kind),
                "project_id": scope.id,
                "sensitivity": str(memory.sensitivity),
                **({"supersedes": memory.supersedes} if memory.supersedes else {}),
            },
        )

    async def capture(self, text: str, *, scope: Scope) -> str:
        # Capture is intentionally not an assistant-facing MCP tool. The proxy uses
        # the authenticated bridge endpoint on the same host, just as the browser
        # extension does.
        root = self._base_url.removesuffix("/mcp")
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(
                f"{root}/v1/capture",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"text": text, "project_id": scope.id, "surface": "local-proxy"},
            )
        if response.status_code != 200:
            raise ContextClientError(
                f"capture failed ({response.status_code}): {response.text[:200]}"
            )
        payload = response.json()
        episode_id = payload.get("episode_id")
        if not payload.get("queued") or not isinstance(episode_id, str):
            raise ContextClientError(
                "capture is not enabled on the MCP server; enable encrypted capture there"
            )
        return episode_id

    async def aclose(self) -> None:
        return None


def _text_of(result: Any) -> str:
    parts = [getattr(block, "text", "") for block in getattr(result, "content", [])]
    return " ".join(p for p in parts if p) or "no detail returned"


def _structured(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    import json

    try:
        parsed = json.loads(_text_of(result))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContextClientError(f"unreadable tool response: {exc}") from exc
    return parsed if isinstance(parsed, dict) else {"results": parsed}


def build_context_client() -> ContextClient:
    """Remote when a server is configured, in-process otherwise.

    The default stays local on purpose. The in-process store with no infrastructure
    is what makes the wedge dogfoodable on day one, and a proxy that refused to start
    without a hosted server would trade that away for a property only multi-user
    deployments need.
    """
    from coletar.store import build_store

    settings = get_settings()
    if settings.mcp_url:
        return RemoteContextClient(settings.mcp_url, settings.mcp_api_key)
    return LocalContextClient(
        build_store(),
        Principal(
            id=LOCAL_PRINCIPAL_ID,
            tenant_id=parse_tenant_id(settings.default_tenant_id),
            scopes=frozenset({SCOPE_READ, SCOPE_WRITE}),
            surface=Provider.LOCAL,
        ),
    )
