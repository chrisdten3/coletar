"""M2.1 acceptance criteria: the MCP server's four tools.

These call the tools the way a connected model does — through `mcp.call_tool`, with
a principal bound the way the middleware binds it — rather than calling the Python
functions directly. The point is to exercise argument coercion and error surfacing,
which is where the acceptance criteria actually live.
"""

from __future__ import annotations

import random
import time

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from coletar.mcp import server as mcp_server
from coletar.mcp.auth import Principal, principal_scope
from coletar.mcp.schemas import ObjectView, SearchContextResponse
from coletar.schema.events import Actor, EventType
from coletar.schema.objects import Memory, MemoryKind, Scope, ScopeType
from coletar.store.memory import InMemoryStore

EXPECTED_TOOLS = {"search_context", "write_memory", "get_project_state", "list_open_loops"}
LATENCY_BUDGET_MS = 500.0
FUZZ_CALLS = 200


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryStore:
    """Point the tools at a throwaway store. `build_store()` is a process-wide
    singleton, so the tools' bound reference is what gets replaced."""
    backing = InMemoryStore()
    monkeypatch.setattr(mcp_server, "build_store", lambda: backing)
    return backing


@pytest.fixture
def caller():
    """A fully-scoped principal, bound the way AuthMiddleware binds one."""
    return Principal(id="test-connector")


# -- tool discovery -----------------------------------------------------------
async def test_exactly_four_tools_are_published():
    tools = await mcp_server.mcp.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


async def test_every_tool_has_a_description_and_an_object_input_schema():
    for tool in await mcp_server.mcp.list_tools():
        assert tool.description, tool.name
        assert tool.input_schema["type"] == "object", tool.name


async def test_tool_schemas_declare_the_documented_arguments():
    by_name = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}

    search = by_name["search_context"].input_schema
    assert search["required"] == ["query"]
    assert set(search["properties"]) == {"query", "project_id", "top_k"}

    write = by_name["write_memory"].input_schema
    assert write["required"] == ["content"]
    assert set(write["properties"]) == {
        "content", "kind", "project_id", "sensitivity", "supersedes"
    }

    assert by_name["get_project_state"].input_schema["required"] == ["project_id"]
    assert by_name["list_open_loops"].input_schema.get("required", []) == []


# -- malformed input ----------------------------------------------------------
@pytest.mark.parametrize(
    ("arguments", "expected_fragment"),
    [
        ({"content": "x", "kind": "banana"}, "kind must be one of"),
        ({"content": "x", "sensitivity": "spicy"}, "sensitivity must be one of"),
        ({"content": "x", "project_id": "   "}, "non-empty project id"),
        ({"content": "   "}, "content must be a non-empty statement"),
        ({"content": "x", "supersedes": "mem_nope"}, "id of an object that exists"),
    ],
)
async def test_malformed_write_is_a_clear_error_not_a_server_error(
    store, caller, arguments, expected_fragment
):
    """`ToolError` carries its message through to the model; any other exception
    becomes a bare 'Error executing tool write_memory'. The difference is whether
    the caller can correct itself and retry."""
    with principal_scope(caller), pytest.raises(ToolError) as raised:
        await mcp_server.mcp.call_tool("write_memory", arguments)

    assert expected_fragment in str(raised.value)
    assert not isinstance(raised.value, UnexpectedToolError)


async def test_a_rejected_kind_names_the_legal_values(store, caller):
    with principal_scope(caller), pytest.raises(ToolError) as raised:
        await mcp_server.mcp.call_tool("write_memory", {"content": "x", "kind": "banana"})

    message = str(raised.value)
    for kind in MemoryKind:
        assert kind.value in message, f"{kind} missing from the error message"


@pytest.mark.parametrize("top_k", [0, -1, 5_000])
async def test_out_of_range_top_k_is_rejected(store, caller, top_k):
    with principal_scope(caller), pytest.raises(ToolError, match="top_k"):
        await mcp_server.mcp.call_tool("search_context", {"query": "x", "top_k": top_k})


async def test_oversized_content_is_rejected_with_the_limit_stated(store, caller):
    with principal_scope(caller), pytest.raises(ToolError, match="the limit is"):
        await mcp_server.mcp.call_tool(
            "write_memory", {"content": "x" * (mcp_server.MAX_CONTENT_CHARS + 1)}
        )


# -- authorization ------------------------------------------------------------
async def test_a_tool_called_without_a_principal_fails_closed(store):
    with pytest.raises(ToolError, match="Not authenticated"):
        await mcp_server.mcp.call_tool("search_context", {"query": "anything"})


async def test_a_read_only_key_cannot_write(store):
    """M7.1 needs this server-side, not hidden in a client."""
    read_only = Principal(id="chatgpt", scopes=frozenset({"read"}))

    with principal_scope(read_only):
        await mcp_server.mcp.call_tool("search_context", {"query": "fine"})
        with pytest.raises(ToolError, match="not authorized to write"):
            await mcp_server.mcp.call_tool("write_memory", {"content": "nope"})


# -- responses conform to the M1.1 schema -------------------------------------
async def test_search_results_conform_to_the_memory_schema(store, caller):
    await store.put_object(
        Memory.from_write("Chris prefers fixed-point money.", kind=MemoryKind.PREFERENCE)
    )

    with principal_scope(caller):
        result = await mcp_server.mcp.call_tool("search_context", {"query": "money"})

    payload = SearchContextResponse.model_validate(result.structured_content)
    assert payload.results, "expected the seeded memory back"
    hit = payload.results[0]
    assert isinstance(hit, ObjectView)
    # §2's fields, and provenance in particular: a caller that cannot see where a
    # memory came from cannot decide how much to trust it.
    assert hit.kind is MemoryKind.PREFERENCE
    assert hit.scope == "global"
    assert hit.provenance.provider and hit.provenance.origin_type
    assert 0.0 <= hit.confidence <= 1.0
    assert hit.score is not None


async def test_write_then_read_round_trips_through_the_tools(store, caller):
    with principal_scope(caller):
        written = await mcp_server.mcp.call_tool(
            "write_memory",
            {"content": "Ledger deploys to Fly.io.", "kind": "fact", "project_id": "proj_ledger"},
        )
        object_id = written.structured_content["id"]
        found = await mcp_server.mcp.call_tool(
            "search_context", {"query": "where does ledger deploy", "project_id": "proj_ledger"}
        )

    assert written.structured_content["scope"] == "project:proj_ledger"
    assert object_id in {r["id"] for r in found.structured_content["results"]}


async def test_get_project_state_groups_by_type(store, caller):
    scope = Scope(type=ScopeType.PROJECT, id="proj_ledger")
    await store.put_object(Memory.from_write("A project fact.", scope=scope))
    await store.put_object(Memory.from_write("A global fact."))

    with principal_scope(caller):
        result = await mcp_server.mcp.call_tool(
            "get_project_state", {"project_id": "proj_ledger"}
        )

    assert result.structured_content["count"] == 1
    assert set(result.structured_content["objects"]) == {"memory"}


async def test_list_open_loops_excludes_superseded_goals(store, caller):
    old = await store.put_object(
        Memory.from_write("Ship invoicing by March.", kind=MemoryKind.GOAL)
    )
    await store.put_object(
        Memory.from_write("Ship invoicing by June.", kind=MemoryKind.GOAL, supersedes=old.id)
    )
    await store.put_object(Memory.from_write("An ordinary fact.", kind=MemoryKind.FACT))

    with principal_scope(caller):
        result = await mcp_server.mcp.call_tool("list_open_loops", {})

    contents = [loop["content"] for loop in result.structured_content["open_loops"]]
    assert contents == ["Ship invoicing by June."]


# -- provenance and privacy ---------------------------------------------------
async def test_a_connector_write_records_which_principal_made_it(store, caller):
    with principal_scope(caller):
        await mcp_server.mcp.call_tool("write_memory", {"content": "A durable fact."})

    event = (await store.list_events())[0]
    assert event.type is EventType.CONNECTOR_WRITE
    assert event.actor is Actor.CONNECTOR
    assert event.detail["principal"] == "test-connector"


async def test_search_never_records_the_query_or_the_content(store, caller):
    """§11: retrieval telemetry must not become a second copy of the user's private
    history. Access events carry object ids, never what was asked or returned."""
    secret_content = "Chris banks with Ficticious Trust, account 12345."
    await store.put_object(Memory.from_write(secret_content))
    secret_query = "which bank does chris use"

    with principal_scope(caller):
        await mcp_server.mcp.call_tool("search_context", {"query": secret_query})

    access = [e for e in await store.list_events() if e.type is EventType.OBJECT_ACCESSED]
    assert access, "an access should be recorded"
    for event in access:
        serialized = event.model_dump_json()
        assert secret_query not in serialized
        assert "Ficticious Trust" not in serialized
        assert event.object_id is not None


# -- latency and robustness ---------------------------------------------------
async def test_tool_round_trip_p95_stays_under_500ms(store, caller):
    for i in range(500):
        await store.put_object(Memory.from_write(f"Seeded fact number {i} about ledgers."))

    latencies: list[float] = []
    with principal_scope(caller):
        for i in range(60):
            start = time.perf_counter()
            await mcp_server.mcp.call_tool(
                "search_context", {"query": f"ledger fact {i}", "top_k": 12}
            )
            latencies.append((time.perf_counter() - start) * 1000)

    latencies.sort()
    p95 = latencies[int(0.95 * len(latencies)) - 1]
    assert p95 < LATENCY_BUDGET_MS, f"p95 {p95:.0f}ms"


async def test_fuzzing_the_tools_produces_no_unhandled_exceptions(store, caller):
    """Every rejection must be a deliberate ToolError. An UnexpectedToolError means
    something crashed, which is exactly what this bar is here to catch."""
    rng = random.Random(1234)
    payloads = [
        "", "   ", "\x00", "a" * 9000, "'; DROP TABLE context_object; --",
        "🙂" * 50, "{}", "[]", "null", "-1", "\n\n\n", "ünïcödé", "%s", "{{}}",
    ]
    kinds = [*(k.value for k in MemoryKind), "banana", "", "FACT", "1"]
    scopes = [None, "proj_a", "", "  ", "x" * 500]

    crashes: list[str] = []
    with principal_scope(caller):
        for _ in range(FUZZ_CALLS):
            tool, arguments = rng.choice(
                [
                    (
                        "write_memory",
                        {
                            "content": rng.choice(payloads),
                            "kind": rng.choice(kinds),
                            "project_id": rng.choice(scopes),
                            "sensitivity": rng.choice(["normal", "sensitive", "bogus"]),
                        },
                    ),
                    (
                        "search_context",
                        {
                            "query": rng.choice(payloads),
                            "project_id": rng.choice(scopes),
                            "top_k": rng.choice([1, 12, 0, -5, 10_000]),
                        },
                    ),
                    ("get_project_state", {"project_id": rng.choice(scopes) or ""}),
                    ("list_open_loops", {"project_id": rng.choice(scopes)}),
                ]
            )
            try:
                await mcp_server.mcp.call_tool(tool, arguments)
            except ToolError as expected:
                if isinstance(expected, UnexpectedToolError):
                    crashes.append(f"{tool}({arguments}) -> {expected}")
            except Exception as unexpected:  # noqa: BLE001 - the thing under test
                crashes.append(f"{tool}({arguments}) -> {type(unexpected).__name__}: {unexpected}")

    assert not crashes, "\n".join(crashes[:10])
