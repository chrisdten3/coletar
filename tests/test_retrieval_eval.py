"""M2.3: the retrieval evaluation harness and its published baselines.

§5.1 asks for the harness and the baseline to be published together, and for
measurement at two boundaries — candidate recall and final ranking — because a
reranker cannot repair an object that narrowing discarded.

The heavy backend-comparison runs are in `test_embedding_live.py`, gated on Ollama.
What lives here runs with no infrastructure at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coletar.retrieval.embedding import HashingEmbedder
from coletar.retrieval.evaluation import (
    CANDIDATE_DEPTH,
    EvaluationResult,
    evaluate,
    load_eval_set,
    seed_corpus,
)
from coletar.store.memory import InMemoryStore

FIXTURES = Path(__file__).parent / "fixtures"
EVAL_SET = FIXTURES / "retrieval_eval.json"
BASELINES = FIXTURES / "retrieval_eval_baselines.json"

REQUIRED_CATEGORIES = {
    "exact_id", "paraphrase", "temporal", "correction",
    "negation", "scope_isolation", "multi_hop", "near_miss",
}
MINIMUM_QUERIES = 100
#: Numbers move a little with tie-breaking; a drift larger than this is a change,
#: not noise, and should be re-measured and re-published rather than absorbed.
TOLERANCE = 0.03


@pytest.fixture(scope="module")
def eval_set() -> dict:
    return load_eval_set(EVAL_SET)


@pytest.fixture(scope="module")
def baselines() -> dict:
    return json.loads(BASELINES.read_text())


@pytest.fixture
async def measured(eval_set) -> EvaluationResult:
    store = InMemoryStore(embedder=HashingEmbedder(768))
    ids = await seed_corpus(store, eval_set["corpus"])
    return await evaluate(store, eval_set, ids)


# -- the fixture itself -------------------------------------------------------
def test_the_set_is_at_least_a_hundred_labelled_queries(eval_set):
    assert len(eval_set["queries"]) >= MINIMUM_QUERIES


def test_every_required_category_is_covered(eval_set):
    covered = {q["category"] for q in eval_set["queries"]}
    assert covered >= REQUIRED_CATEGORIES, REQUIRED_CATEGORIES - covered


def test_every_category_has_enough_queries_to_mean_something(eval_set):
    counts: dict[str, int] = {}
    for query in eval_set["queries"]:
        counts[query["category"]] = counts.get(query["category"], 0) + 1
    thin = {c: n for c, n in counts.items() if n < 5}
    assert not thin, f"too few queries to be a rate: {thin}"


def test_no_query_references_a_corpus_key_that_does_not_exist(eval_set):
    keys = {c["key"] for c in eval_set["corpus"]}
    dangling = [
        q["query"]
        for q in eval_set["queries"]
        if q["expect"] not in keys
        or (q.get("expect_absent") and q["expect_absent"] not in keys)
    ]
    assert dangling == []


def test_the_m1_2_subset_is_preserved(eval_set):
    """The original 20-query set is carried verbatim so the headline number stays
    comparable across the expansion."""
    original = json.loads((FIXTURES / "relevance_set.json").read_text())["queries"]
    tagged = [q for q in eval_set["queries"] if q.get("m1_2")]
    assert len(tagged) == len(original) == 20
    assert {q["query"] for q in tagged} == {q["query"] for q in original}


# -- the two boundaries -------------------------------------------------------
async def test_candidate_recall_is_measured_separately_from_ranking(measured):
    """A reranker cannot repair an object narrowing discarded, so the two are not
    interchangeable — and candidate recall must be the higher of the pair."""
    assert measured.candidate_recall >= measured.hit5


async def test_nothing_superseded_or_out_of_scope_ever_surfaces(measured):
    """A hard zero, not a target. Hit rate bought by also returning the stale answer
    is not a retrieval win (§5.1)."""
    assert measured.leaks == [], measured.leaks


async def test_injected_tokens_are_reported_alongside_accuracy(measured):
    """So a future change cannot buy hit rate by flooding the context."""
    assert measured.mean_tokens > 0
    assert measured.mean_tokens < 400, "context is being flooded"


# -- the published baseline ---------------------------------------------------
async def test_the_published_hashing_baseline_still_holds(measured, baselines):
    published = baselines["hashing"]
    for metric, actual in (
        ("candidate_recall_at_50", measured.candidate_recall),
        ("hit_at_1", measured.hit1),
        ("hit_at_5", measured.hit5),
        ("mrr_at_5", measured.mrr5),
    ):
        assert actual == pytest.approx(published[metric], abs=TOLERANCE), (
            f"{metric}: measured {actual:.3f}, published {published[metric]:.3f}\n"
            f"{measured.report()}"
        )


async def test_the_published_per_category_rates_still_hold(measured, baselines):
    published = baselines["hashing"]["by_category"]
    for category, rate in measured.category_rates().items():
        assert rate == pytest.approx(published[category], abs=TOLERANCE), (
            f"{category}: measured {rate:.3f}, published {published[category]:.3f}"
        )


async def test_the_baseline_records_the_ranking_version(baselines):
    """A number you cannot attribute to a formula is not a baseline."""
    from coletar.retrieval.ranking import RANKING_VERSION

    assert baselines["ranking_version"] == RANKING_VERSION


# -- the harness itself -------------------------------------------------------
async def test_corrections_resolve_to_the_superseding_object(eval_set):
    """The corpus builds real supersedes chains, so the eval exercises the same
    retirement semantics production does rather than a flat corpus."""
    store = InMemoryStore(embedder=HashingEmbedder(768))
    ids = await seed_corpus(store, eval_set["corpus"])

    stale = await store.get_object(ids["employer_v1"])
    current = await store.get_object(ids["employer_v2"])

    assert current is not None and current.supersedes == ids["employer_v1"]
    assert stale is not None, "never hard-delete"
    assert ids["employer_v1"] not in {
        hit.obj.id for hit in await store.search("who does chris work for", top_k=CANDIDATE_DEPTH)
    }
