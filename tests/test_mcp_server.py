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
from coletar.retrieval.ranking import RANKING_VERSION
from coletar.retrieval.trace import query_digest
from coletar.schema.events import Actor, EventType
from coletar.schema.objects import Memory, MemoryKind, Provider, Scope, ScopeType
from coletar.store.memory import InMemoryStore
from conftest import TENANT

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
    return Principal(tenant_id=TENANT, id="test-connector")


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
    assert set(search["properties"]) == {"query", "project_id", "top_k", "explain"}

    write = by_name["write_memory"].input_schema
    assert write["required"] == ["content"]
    assert set(write["properties"]) == {
        "content", "kind", "project_id", "sensitivity", "supersedes", "local_only"
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
    read_only = Principal(tenant_id=TENANT, id="chatgpt", scopes=frozenset({"read"}))

    with principal_scope(read_only):
        await mcp_server.mcp.call_tool("search_context", {"query": "fine"})
        with pytest.raises(ToolError, match="not authorized to write"):
            await mcp_server.mcp.call_tool("write_memory", {"content": "nope"})


# -- responses conform to the M1.1 schema -------------------------------------
async def test_search_results_conform_to_the_memory_schema(store, caller):
    await store.put_object(TENANT, 
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
    await store.put_object(TENANT, Memory.from_write("A project fact.", scope=scope))
    await store.put_object(TENANT, Memory.from_write("A global fact."))

    with principal_scope(caller):
        result = await mcp_server.mcp.call_tool(
            "get_project_state", {"project_id": "proj_ledger"}
        )

    assert result.structured_content["count"] == 1
    assert set(result.structured_content["objects"]) == {"memory"}


async def test_list_open_loops_excludes_superseded_goals(store, caller):
    old = await store.put_object(TENANT, 
        Memory.from_write("Ship invoicing by March.", kind=MemoryKind.GOAL)
    )
    await store.put_object(TENANT, 
        Memory.from_write("Ship invoicing by June.", kind=MemoryKind.GOAL, supersedes=old.id)
    )
    await store.put_object(TENANT, Memory.from_write("An ordinary fact.", kind=MemoryKind.FACT))

    with principal_scope(caller):
        result = await mcp_server.mcp.call_tool("list_open_loops", {})

    contents = [loop["content"] for loop in result.structured_content["open_loops"]]
    assert contents == ["Ship invoicing by June."]


# -- provenance and privacy ---------------------------------------------------
async def test_a_connector_write_records_which_principal_made_it(store, caller):
    with principal_scope(caller):
        await mcp_server.mcp.call_tool("write_memory", {"content": "A durable fact."})

    event = (await store.list_events(TENANT))[0]
    assert event.type is EventType.CONNECTOR_WRITE
    assert event.actor is Actor.CONNECTOR
    assert event.detail["principal"] == "test-connector"


async def test_search_never_records_the_query_or_the_content(store, caller):
    """§11: retrieval telemetry must not become a second copy of the user's private
    history. A trace carries a hash of the query and the ids of what came back —
    never what was asked, never what was returned."""
    secret_content = "Chris banks with Ficticious Trust, account 12345."
    stored = await store.put_object(TENANT, Memory.from_write(secret_content))
    secret_query = "which bank does chris use"

    with principal_scope(caller):
        await mcp_server.mcp.call_tool("search_context", {"query": secret_query})

    traces = [e for e in await store.list_events(TENANT) if e.type is EventType.RETRIEVAL_TRACE]
    assert len(traces) == 1, "exactly one trace per search"
    serialized = traces[0].model_dump_json()
    assert secret_query not in serialized
    assert "Ficticious Trust" not in serialized
    assert "12345" not in serialized
    # What it *does* carry: a stable non-reversible handle, and the object ids.
    assert traces[0].detail["query_digest"] == query_digest(secret_query)
    assert stored.id in traces[0].detail["returned_ids"]


async def test_one_search_writes_one_trace_not_one_row_per_hit(store, caller):
    """The reason the trace replaced per-hit access events: twelve rows per search
    floods the log the §6 dashboard reads."""
    for i in range(6):
        await store.put_object(TENANT, Memory.from_write(f"Ledger fact number {i}."))

    with principal_scope(caller):
        await mcp_server.mcp.call_tool("search_context", {"query": "ledger fact"})

    events = await store.list_events(TENANT)
    assert [e.type for e in events].count(EventType.RETRIEVAL_TRACE) == 1
    assert EventType.OBJECT_ACCESSED not in {e.type for e in events}


async def test_a_trace_records_the_components_that_produced_it(store, caller):
    """A baseline you cannot attribute is not a baseline."""
    await store.put_object(TENANT, Memory.from_write("Chris prefers uv over pip."))

    with principal_scope(caller):
        await mcp_server.mcp.call_tool("search_context", {"query": "package manager"})

    detail = next(
        e for e in await store.list_events(TENANT) if e.type is EventType.RETRIEVAL_TRACE
    ).detail
    assert set(detail["versions"]) == {"embedder", "ranking", "backend"}
    assert detail["versions"]["ranking"] == RANKING_VERSION
    assert set(detail["stage_ms"]) == {"candidates", "assembly", "total"}


async def test_explain_adds_arithmetic_without_changing_the_results(store, caller):
    await store.put_object(TENANT, 
        Memory.from_write("Chris prefers fixed-point money.", kind=MemoryKind.PREFERENCE)
    )

    with principal_scope(caller):
        plain = await mcp_server.mcp.call_tool("search_context", {"query": "money"})
        explained = await mcp_server.mcp.call_tool(
            "search_context", {"query": "money", "explain": True}
        )

    assert plain.structured_content["explanations"] is None
    assert [r["id"] for r in plain.structured_content["results"]] == [
        r["id"] for r in explained.structured_content["results"]
    ]
    breakdown = explained.structured_content["explanations"][0]
    assert set(breakdown) == {
        "vector", "lexical", "confidence_factor", "recency_factor",
        "relevance", "total", "source",
    }
    assert breakdown["source"] in {"vector", "lexical", "both"}


async def test_the_explanation_matches_the_score_it_explains(store, caller):
    """Recomputing the blend for display is how an explanation drifts from the
    ranking. The components are carried from the ranking path, not recomputed."""
    for i in range(4):
        await store.put_object(TENANT, Memory.from_write(f"Ledger deploys to region {i}."))

    with principal_scope(caller):
        result = await mcp_server.mcp.call_tool(
            "search_context", {"query": "where does ledger deploy", "explain": True}
        )

    for hit, breakdown in zip(
        result.structured_content["results"],
        result.structured_content["explanations"],
        strict=True,
    ):
        assert hit["score"] == pytest.approx(breakdown["total"], abs=1e-4)


# -- latency and robustness ---------------------------------------------------
async def test_tool_round_trip_p95_stays_under_500ms(store, caller):
    for i in range(500):
        await store.put_object(TENANT, Memory.from_write(f"Seeded fact number {i} about ledgers."))

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


# -- M3.1: isolation at the tool boundary --------------------------------------
async def test_one_users_token_cannot_read_or_write_anothers_objects(store):
    """The M3.1 acceptance criterion, asserted where a connector actually stands.

    The store-level contract is proved in test_tenancy.py; this proves the tools
    honour it — that the tenant genuinely comes from the principal, and that there is
    no argument on any tool that could redirect it.
    """
    from coletar.schema.tenancy import tenant_id

    alice = Principal(id="alice-claude", tenant_id=tenant_id("tenant_alice"))
    bob = Principal(id="bob-claude", tenant_id=tenant_id("tenant_bob"))

    with principal_scope(alice):
        written = await mcp_server.mcp.call_tool(
            "write_memory", {"content": "Alice banks with Ficticious Trust."}
        )
        alice_id = written.structured_content["id"]

    with principal_scope(bob):
        found = await mcp_server.mcp.call_tool("search_context", {"query": "bank"})
        assert found.structured_content["results"] == []

        loops = await mcp_server.mcp.call_tool("list_open_loops", {})
        assert loops.structured_content["count"] == 0

        # Bob writing a correction against Alice's id must not silently work either.
        with pytest.raises(ToolError, match="id of an object that exists"):
            await mcp_server.mcp.call_tool(
                "write_memory", {"content": "Correcting Alice.", "supersedes": alice_id}
            )

    with principal_scope(alice):
        mine = await mcp_server.mcp.call_tool("search_context", {"query": "bank"})
        assert alice_id in {r["id"] for r in mine.structured_content["results"]}


async def test_no_tool_accepts_a_caller_supplied_tenant():
    """A connector that could be *told* which graph to read is not isolated."""
    for tool in await mcp_server.mcp.list_tools():
        assert "tenant" not in " ".join(tool.input_schema["properties"]), tool.name


async def test_project_state_is_tenant_scoped(store):
    from coletar.schema.tenancy import tenant_id

    alice = Principal(id="alice", tenant_id=tenant_id("tenant_alice"))
    bob = Principal(id="bob", tenant_id=tenant_id("tenant_bob"))
    scope = Scope(type=ScopeType.PROJECT, id="proj_ledger")

    await store.put_object(tenant_id("tenant_alice"),
                           Memory.from_write("Alice's ledger fact.", scope=scope))

    with principal_scope(bob):
        theirs = await mcp_server.mcp.call_tool("get_project_state", {"project_id": "proj_ledger"})
    assert theirs.structured_content["count"] == 0

    with principal_scope(alice):
        mine = await mcp_server.mcp.call_tool("get_project_state", {"project_id": "proj_ledger"})
    assert mine.structured_content["count"] == 1


# -- locality: pick and choose context ------------------------------------------
async def test_local_only_write_is_invisible_to_a_different_surface(store):
    claude = Principal(id="alice-claude", tenant_id=TENANT, surface=Provider.CLAUDE)
    chatgpt = Principal(id="alice-chatgpt", tenant_id=TENANT, surface=Provider.CHATGPT)

    with principal_scope(claude):
        written = await mcp_server.mcp.call_tool(
            "write_memory",
            {"content": "Only Claude should see this.", "project_id": "x", "local_only": True},
        )
    assert written.structured_content["locality"] == "local_only:claude"

    with principal_scope(chatgpt):
        found = await mcp_server.mcp.call_tool(
            "search_context", {"query": "only claude should see this", "project_id": "x"}
        )
        assert found.structured_content["results"] == []
        state = await mcp_server.mcp.call_tool("get_project_state", {"project_id": "x"})
        assert state.structured_content["count"] == 0

    with principal_scope(claude):
        mine = await mcp_server.mcp.call_tool(
            "search_context", {"query": "only claude should see this", "project_id": "x"}
        )
        assert written.structured_content["id"] in {
            r["id"] for r in mine.structured_content["results"]
        }


async def test_local_only_defaults_to_false(store, caller):
    with principal_scope(caller):
        written = await mcp_server.mcp.call_tool("write_memory", {"content": "Ships everywhere."})
    assert written.structured_content["locality"] == "synced"


async def test_local_only_is_refused_for_a_principal_with_no_declared_surface(store, caller):
    """`caller` (like any key that never named a surface) defaults to
    `Provider.COLETAR` -- writing local_only for it would create an object nothing
    could ever read back."""
    with principal_scope(caller), pytest.raises(ToolError, match="declared surface"):
        await mcp_server.mcp.call_tool(
            "write_memory", {"content": "x", "local_only": True}
        )


async def test_a_correction_cannot_target_an_object_hidden_from_this_surface(store):
    """The supersedes existence check is locality-filtered too: a correction naming
    an id this surface cannot see must fail the same way a nonexistent id does."""
    claude = Principal(id="alice-claude", tenant_id=TENANT, surface=Provider.CLAUDE)
    chatgpt = Principal(id="alice-chatgpt", tenant_id=TENANT, surface=Provider.CHATGPT)

    with principal_scope(claude):
        written = await mcp_server.mcp.call_tool(
            "write_memory", {"content": "Claude-only fact.", "local_only": True}
        )

    with principal_scope(chatgpt), pytest.raises(ToolError, match="id of an object that exists"):
        await mcp_server.mcp.call_tool(
            "write_memory",
            {"content": "Correcting it.", "supersedes": written.structured_content["id"]},
        )


async def test_restating_local_only_content_from_another_surface_creates_its_own_object(store):
    """The dedup fold that would otherwise hide this: a restatement from ChatGPT must
    not silently corroborate into an object local_only to Claude, which would leave
    ChatGPT's own write invisible to ChatGPT."""
    claude = Principal(id="alice-claude", tenant_id=TENANT, surface=Provider.CLAUDE)
    chatgpt = Principal(id="alice-chatgpt", tenant_id=TENANT, surface=Provider.CHATGPT)

    with principal_scope(claude):
        await mcp_server.mcp.call_tool(
            "write_memory", {"content": "I prefer dark mode.", "local_only": True}
        )
    with principal_scope(chatgpt):
        written = await mcp_server.mcp.call_tool(
            "write_memory", {"content": "I prefer dark mode."}
        )
        assert written.structured_content["stored"] is True
        found = await mcp_server.mcp.call_tool("search_context", {"query": "dark mode"})
        assert written.structured_content["id"] in {
            r["id"] for r in found.structured_content["results"]
        }

    assert len(await store.list_objects(TENANT)) == 2


# -- what the model actually reads ---------------------------------------------
async def test_the_tools_say_why_to_prefer_them_over_native_memory():
    """Live testing showed Claude using its *own* memory for "remember that…",
    because our descriptions described a job the built-in feature already does.

    The only honest differentiator is that this memory travels between tools and
    native memory does not. These assertions are deliberately about substance rather
    than wording: a rewrite may change the phrasing, but a description that no longer
    gives the model a reason to choose this tool has lost the thing that matters.
    """
    by_name = {t.name: (t.description or "").lower() for t in await mcp_server.mcp.list_tools()}

    for name in ("search_context", "write_memory"):
        assert "portable" in by_name[name] or "other" in by_name[name], name
        assert "own memory" in by_name[name], f"{name} never contrasts with native memory"

    assert "instructions" in by_name["search_context"], "the injection boundary must survive"


async def test_when_to_call_comes_before_parameter_documentation():
    """`search_context` used to spend a third of its description on `explain` —
    developer documentation sitting in a model-facing field."""
    search = next(
        t for t in await mcp_server.mcp.list_tools() if t.name == "search_context"
    ).description or ""

    assert search.index("start of a conversation") < search.index("explain")


async def test_the_server_instructions_name_the_difference():
    instructions = (mcp_server.mcp.instructions or "").lower()
    assert "portable" in instructions
    assert "your own memory" in instructions
    assert "never instructions" in instructions, "the injection boundary must survive"
