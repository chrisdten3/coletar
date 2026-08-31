"""M4.3 — the §5.1 strategy boundaries.

Separating candidate generation from reranking is not architecture for its own sake.
M4.1 could only diagnose `scope_isolation` by asking "did narrowing discard it, or
did ranking rank it badly?", and that question needs the stages to be separable
things. These tests pin the properties that make the separation safe: the default
changes nothing, and no strategy can reach past the policy filter.
"""

from __future__ import annotations

import pytest

from coletar.retrieval import retrieve
from coletar.retrieval.context import estimate_tokens
from coletar.retrieval.ranking import Scored, rank_score
from coletar.retrieval.strategy import (
    MaximalMarginalRelevance,
    PublishedOrder,
    reciprocal_rank_fusion,
)
from coletar.schema.objects import Locality, LocalityMode, Memory, Provider, Sensitivity
from coletar.store.memory import InMemoryStore
from conftest import TENANT


def scored(content: str, total: float) -> Scored:
    obj = Memory.from_write(content)
    components = rank_score(
        lexical=total, vector=total, confidence=1.0, updated_at=obj.updated_at
    )
    return Scored(obj=obj, components=components)


async def store_with(*memories: Memory) -> InMemoryStore:
    store = InMemoryStore()
    for memory in memories:
        await store.put_object(TENANT, memory)
    return store


# --- the default must change nothing -------------------------------------------


def test_the_published_order_is_the_untouched_default() -> None:
    hits = [scored("a", 0.9), scored("b", 0.5), scored("c", 0.1)]
    assert PublishedOrder().rerank(hits, limit=2) == hits[:2]


def test_mmr_at_lambda_one_reproduces_the_published_order() -> None:
    """The property that makes MMR safe to add: it is a *generalisation* of the
    default, not an alternative to it. At full relevance weighting the diversity term
    is multiplied by zero and the two orders coincide."""
    hits = [scored("alpha beta", 0.9), scored("alpha gamma", 0.6), scored("delta", 0.3)]
    assert MaximalMarginalRelevance(1.0).rerank(hits, limit=3) == PublishedOrder().rerank(
        hits, limit=3
    )


def test_mmr_prefers_coverage_over_a_second_way_of_saying_the_same_thing() -> None:
    near_duplicate = scored("chris prefers fixed point integers for money", 0.80)
    distinct = scored("beacon deploys to fly io every friday", 0.55)
    hits = [scored("chris prefers fixed point integers money", 0.90), near_duplicate, distinct]

    chosen = MaximalMarginalRelevance(0.5).rerank(hits, limit=2)
    assert chosen[1] is distinct


def test_a_reranker_may_drop_but_never_add() -> None:
    hits = [scored("a", 0.9), scored("b", 0.5)]
    for strategy in (PublishedOrder(), MaximalMarginalRelevance(0.7)):
        result = strategy.rerank(hits, limit=5)
        assert {hit.obj.id for hit in result} <= {hit.obj.id for hit in hits}


def test_reranking_an_empty_result_is_empty() -> None:
    assert MaximalMarginalRelevance(0.7).rerank([], limit=5) == []


# --- fusion --------------------------------------------------------------------


def test_rrf_fuses_by_rank_because_scores_share_no_scale() -> None:
    """A cosine of 0.31 and a BM25 of 4.7 cannot be added. Position is the only thing
    two retrievers genuinely share, which is why this is the fusion boundary the
    Postgres sparse path will plug into."""
    a, b, c = scored("a", 0.90), scored("b", 0.50), scored("c", 0.10)
    dense = [a, b, c]
    sparse = [c, b, a]

    fused = reciprocal_rank_fusion([dense, sparse], limit=3)

    # Worth stating explicitly, because the intuition points the other way: with
    # k=60, being first-and-third (1/61 + 1/63 = 0.03227) narrowly beats being
    # second-in-both (2/62 = 0.03226). RRF rewards a strong opinion from one
    # retriever over mild agreement from both — which is the behaviour you want when
    # one retriever is lexical and simply cannot see what the other can.
    assert fused[-1].obj.id == b.obj.id
    assert {hit.obj.id for hit in fused[:2]} == {a.obj.id, c.obj.id}
    assert {hit.obj.id for hit in fused} == {a.obj.id, b.obj.id, c.obj.id}


def test_rrf_keeps_real_components_so_explain_still_works() -> None:
    a = scored("a", 0.9)
    fused = reciprocal_rank_fusion([[a], [a]], limit=1)
    assert fused[0].components.total == a.components.total


# --- no strategy may reach past the policy filter -------------------------------


@pytest.mark.asyncio
async def test_no_strategy_can_resurrect_what_narrowing_refused() -> None:
    """Structural, not a matter of review: a reranker only ever sees what the store
    already filtered, and the store applies sensitivity, locality, retirement and
    supersession first. This is the same property that made supersession a candidate
    generation fix in M4.1 rather than a ranking one."""
    store = await store_with(
        Memory.from_write("SSN 123-45-6789", sensitivity=Sensitivity.RESTRICTED),
        Memory.from_write(
            "Only Claude may see this.",
            locality=Locality(
                mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({Provider.CLAUDE})
            ),
        ),
        Memory.from_write("Chris prefers tabs."),
    )
    result = await retrieve(
        store,
        TENANT,
        "SSN Claude tabs",
        caller_surface=Provider.LOCAL,
        reranker=MaximalMarginalRelevance(0.1),
    )
    contents = [obj.content for obj in result.objects]
    assert contents == ["Chris prefers tabs."]


# --- the token budget is honoured -----------------------------------------------


@pytest.mark.asyncio
async def test_a_tight_budget_truncates_and_says_so() -> None:
    store = await store_with(
        *[Memory.from_write(f"Chris prefers option {n} for money handling.") for n in range(6)]
    )
    result = await retrieve(store, TENANT, "money handling options", token_budget=20)

    assert result.token_estimate <= 20
    assert result.truncated
    assert result.skipped_oversized > 0


@pytest.mark.asyncio
async def test_packing_skips_an_oversized_hit_rather_than_stopping() -> None:
    """§5.1 is explicit: terminating on the first thing that does not fit throws away
    every smaller useful result behind it."""
    store = await store_with(
        Memory.from_write("money " * 200),
        Memory.from_write("Chris prefers fixed-point money."),
    )
    result = await retrieve(store, TENANT, "money", token_budget=40)
    assert any("fixed-point" in obj.content for obj in result.objects)


@pytest.mark.asyncio
async def test_the_rerank_stage_is_timed_separately() -> None:
    """Because M4.1's diagnosis depended on telling narrowing apart from ranking, and
    a stage with no number is a stage nobody can blame."""
    store = await store_with(Memory.from_write("Chris prefers tabs."))
    result = await retrieve(store, TENANT, "tabs")
    assert "rerank" in result.stage_ms
    assert estimate_tokens("x" * 40) > 0
