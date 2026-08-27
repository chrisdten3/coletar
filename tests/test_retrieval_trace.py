"""M2.3: retrieval traces (SCOPE §5.1, §11).

The privacy properties are the ones worth testing hardest. §11 names the risk
plainly — retrieval telemetry becoming a second copy of the user's private history —
and the defence is structural rather than a default that can be flipped.
"""

from __future__ import annotations

from coletar.retrieval import retrieve
from coletar.retrieval.ranking import RANKING_VERSION
from coletar.retrieval.trace import (
    ComponentVersions,
    build_trace,
    query_digest,
    record_trace,
)
from coletar.schema.events import Actor, EventType
from coletar.schema.objects import Memory, MemoryKind, Scope, ScopeType
from coletar.store.memory import InMemoryStore

SECRET = "Chris banks with Ficticious Trust, account 12345."


async def _traced(store: InMemoryStore, query: str, **kwargs):
    context = await retrieve(store, query, top_k=5, token_budget=1500)
    trace = build_trace(
        query=query,
        scope=None,
        top_k=5,
        token_budget=1500,
        context=context,
        embedder_model="hashing-768",
        backend="memory",
        **kwargs,
    )
    return await record_trace(store, trace), context


# -- privacy ------------------------------------------------------------------
async def test_a_trace_holds_neither_the_query_nor_the_content():
    store = InMemoryStore()
    await store.put_object(Memory.from_write(SECRET))

    event, _ = await _traced(store, "which bank does chris use")

    serialized = event.model_dump_json()
    assert "which bank does chris use" not in serialized
    assert "Ficticious Trust" not in serialized
    assert "12345" not in serialized


async def test_the_digest_is_stable_and_normalizing():
    """Stable enough to correlate repeat questions; far too little to invert."""
    assert query_digest("Where does Ledger deploy?") == query_digest(
        "  where does   LEDGER deploy?  "
    )
    assert query_digest("a") != query_digest("b")
    assert len(query_digest("anything")) == 16


async def test_recording_the_query_text_is_a_per_call_opt_in():
    """Never a global setting: a global setting is how this gets switched on once
    and left on."""
    store = InMemoryStore()
    await store.put_object(Memory.from_write("Ledger deploys to Fly.io."))

    default_event, _ = await _traced(store, "where does ledger deploy")
    opted_in, _ = await _traced(store, "where does ledger deploy", record_query_text=True)

    assert "query_text" not in default_event.detail
    assert opted_in.detail["query_text"] == "where does ledger deploy"


# -- content ------------------------------------------------------------------
async def test_a_trace_records_what_is_needed_to_reproduce_the_decision():
    store = InMemoryStore()
    stored = await store.put_object(
        Memory.from_write("Ledger deploys to Fly.io.", kind=MemoryKind.FACT)
    )

    event, _ = await _traced(store, "where does ledger deploy")

    detail = event.detail
    assert event.type is EventType.RETRIEVAL_TRACE
    assert event.actor is Actor.CONNECTOR
    assert stored.id in detail["returned_ids"]
    assert detail["versions"] == {
        "embedder": "hashing-768", "ranking": RANKING_VERSION, "backend": "memory"
    }
    assert set(detail["stage_ms"]) == {"candidates", "assembly", "total"}
    assert detail["result_count"] == len(detail["returned_ids"])


async def test_component_scores_carry_the_candidate_source():
    """Which retriever found something is what tells you where to fix a miss."""
    store = InMemoryStore()
    await store.put_object(Memory.from_write("Ledger deploys to Fly.io."))

    event, _ = await _traced(store, "where does ledger deploy")

    for score in event.detail["component_scores"]:
        assert set(score) == {
            "vector", "lexical", "confidence_factor", "recency_factor",
            "relevance", "total", "source",
        }
        assert score["source"] in {"vector", "lexical", "both"}


async def test_the_trace_is_one_event_regardless_of_result_count():
    store = InMemoryStore()
    for i in range(8):
        await store.put_object(Memory.from_write(f"Ledger fact number {i}."))

    await _traced(store, "ledger fact")

    traces = [e for e in await store.list_events() if e.type is EventType.RETRIEVAL_TRACE]
    assert len(traces) == 1
    assert len(traces[0].detail["returned_ids"]) > 1


async def test_component_versions_default_to_the_current_ranking_formula():
    assert ComponentVersions(embedder="x").ranking == RANKING_VERSION


# -- assembly stage -----------------------------------------------------------
async def test_near_duplicates_are_dropped_before_packing():
    """Spending a token budget on the same fact phrased twice is the most expensive
    way to say nothing."""
    store = InMemoryStore()
    await store.put_object(Memory.from_write("Ledger deploys to Fly.io on every merge."))
    await store.put_object(Memory.from_write("Ledger deploys to Fly.io on every merge!"))

    event, context = await _traced(store, "where does ledger deploy")

    assert len(context.objects) == 1
    assert event.detail["deduplicated"] == 1


async def test_packing_skips_an_oversized_hit_instead_of_stopping():
    """§5.1: terminating on the first thing that does not fit throws away every
    smaller useful result behind it."""
    store = InMemoryStore()
    await store.put_object(Memory.from_write("ledger " + "verbose padding " * 200))
    small = await store.put_object(Memory.from_write("ledger runs on Fly.io"))

    context = await retrieve(store, "ledger", top_k=5, token_budget=40)

    assert small.id in {o.id for o in context.objects}
    assert context.skipped_oversized >= 1
    assert context.truncated


async def test_scope_is_recorded_even_when_unconstrained():
    store = InMemoryStore()
    await store.put_object(
        Memory.from_write("A project fact.", scope=Scope(type=ScopeType.PROJECT, id="p"))
    )
    event, _ = await _traced(store, "project fact")
    assert event.detail["scope"] == "any"
