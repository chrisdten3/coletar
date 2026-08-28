"""M1.2 acceptance criteria: the vector index and what `search_context` returns.

The relevance bar is measured against `fixtures/relevance_set.json`, which is
checked in so the number means the same thing here, in M4.1's compression test and
in M6.2's extraction test.
"""

from __future__ import annotations

import random
import time

import pytest

from coletar.retrieval.embedding import HashingEmbedder, cosine, stem, tokenize
from coletar.retrieval.index import VectorIndex
from coletar.schema.objects import Memory, MemoryKind, Scope, ScopeType
from coletar.store.memory import InMemoryStore
from conftest import RelevanceSet, scope_from

#: The build plan's bar: the expected object in the top 5 for at least 90% of the
#: 20 queries.
RELEVANCE_BAR = 0.90
TOP_K = 5

#: The one query in the set that the default hashing embedder cannot reach, because
#: it needs synonymy ("book a meeting" -> "schedule anything") and there is no model
#: behind a hash. It is kept in the set deliberately: it is the query that should
#: start passing the day COLETAR_EMBEDDING_BACKEND=ollama becomes the default.
KNOWN_SEMANTIC_GAP = "is it ok to book a meeting at 9am"


# -- embedder -----------------------------------------------------------------
async def test_hashing_embedder_is_deterministic_and_normalized():
    embedder = HashingEmbedder(768)
    first, second = await embedder.embed(["Chris prefers uv.", "Chris prefers uv."])
    assert first == second
    assert cosine(first, second) == pytest.approx(1.0, abs=1e-6)


async def test_embedder_separates_unrelated_text_from_related_text():
    embedder = HashingEmbedder(768)
    money, money_again, coffee = await embedder.embed(
        [
            "I prefer fixed-point integers for money.",
            "Monetary amounts should use fixed point integers.",
            "Chris drinks his coffee black.",
        ]
    )
    assert cosine(money, money_again) > cosine(money, coffee)


def test_stemming_closes_the_gaps_that_break_an_exact_token_match():
    assert stem("ledger's") == "ledger"
    assert stem("testing") == "test"
    assert stem("migrations") == "migration"
    # Light on purpose: it must not conflate distinct words.
    assert stem("class") == "class"


def test_stemming_is_symmetric_not_pretty():
    """`stem("chris") == "chri"` looks like a bug and was queued as one.

    The obvious fix — emitting the raw token alongside its stem — was measured
    against the 106-query evaluation set and made every metric worse, including the
    exact-identifier category it was meant to help, because two tokens per word
    inflates the denominator in `lexical_score`. What actually matters is that the
    mangling is *symmetric*: query and content pass through the same function, so a
    proper noun still finds itself. This pins that, so the shape is not "fixed"
    again without re-measuring.
    """
    assert stem("chris") == "chri"
    assert tokenize("Chris") == tokenize("chris") == ["chri"]
    assert set(tokenize("Chris prefers uv")) & set(tokenize("what does Chris prefer"))


def test_stopwords_are_dropped_from_content_tokens():
    assert tokenize("what should I do when a function fails") == ["function", "fail"]


def test_vector_index_overwrites_rather_than_duplicating():
    index = VectorIndex(4)
    index.put("mem_1", [1.0, 0.0, 0.0, 0.0])
    index.put("mem_1", [0.0, 1.0, 0.0, 0.0])
    assert len(index) == 1
    assert index.similarities([0.0, 1.0, 0.0, 0.0])["mem_1"] == pytest.approx(1.0)


def test_vector_index_grows_past_its_initial_capacity():
    index = VectorIndex(8)
    for i in range(600):
        index.put(f"mem_{i}", [1.0 if j == i % 8 else 0.0 for j in range(8)])
    assert len(index) == 600
    assert len(index.similarities([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])) == 600


def test_vector_index_rejects_a_wrong_width_vector():
    """A dimension mismatch is a misconfiguration that would otherwise surface as
    silently terrible retrieval."""
    with pytest.raises(ValueError):
        VectorIndex(768).put("mem_1", [0.1, 0.2])


# -- write path ---------------------------------------------------------------
async def test_a_write_is_searchable_on_the_very_next_call():
    """The bound on 'when does a write become visible' is one embed call: embedding
    happens on the write path, not in a background window."""
    store = InMemoryStore()
    memory = await store.put_object(
        Memory.from_write("Chris deploys Ledger to Fly.io.", kind=MemoryKind.FACT)
    )
    assert memory.id in {hit.obj.id for hit in await store.search("where does ledger deploy")}


async def test_an_updated_object_is_found_by_its_new_content():
    store = InMemoryStore()
    memory = await store.put_object(Memory.from_write("Chris uses pip."))
    memory.content = "Chris uses uv exclusively."
    await store.put_object(memory)

    assert {hit.obj.id for hit in await store.search("uv")} == {memory.id}
    assert not await store.search("pip")


async def test_retired_and_superseded_objects_are_absent_from_search():
    store = InMemoryStore()
    old = await store.put_object(Memory.from_write("Ledger deploys to Heroku."))
    new = await store.put_object(
        Memory.from_write(
            "Ledger deploys to Fly.io.", kind=MemoryKind.CORRECTION, supersedes=old.id
        )
    )
    found = {hit.obj.id for hit in await store.search("where does ledger deploy")}
    assert found == {new.id}


# -- relevance ----------------------------------------------------------------
async def test_top_five_relevance_meets_the_bar(
    relevance_store: tuple[InMemoryStore, dict[str, str]], relevance_set: RelevanceSet
):
    store, ids = relevance_store
    by_id = {object_id: key for key, object_id in ids.items()}

    hits: list[str] = []
    misses: list[str] = []
    for query in relevance_set.queries:
        results = await store.search(
            str(query["query"]), scope=scope_from(query.get("scope")), top_k=TOP_K
        )
        found = [by_id[hit.obj.id] for hit in results]
        (hits if query["expect"] in found else misses).append(str(query["query"]))

    rate = len(hits) / len(relevance_set.queries)
    assert rate >= RELEVANCE_BAR, f"top-{TOP_K} hit rate {rate:.0%}; missed {misses}"
    assert misses in ([], [KNOWN_SEMANTIC_GAP]), f"unexpected miss: {misses}"


async def test_project_scoped_search_sees_global_but_not_another_project(
    relevance_store: tuple[InMemoryStore, dict[str, str]],
):
    store, ids = relevance_store
    ledger = Scope(type=ScopeType.PROJECT, id="proj_ledger")

    results = await store.search("what language is this written in", scope=ledger, top_k=30)
    found = {hit.obj.id for hit in results}

    assert ids["atlas_language"] not in found  # another project's object
    globals_present = {ids["python_version"], ids["type_checking"]} & found
    assert globals_present, "a project conversation must still see global context"


async def test_global_scoped_search_excludes_every_project(
    relevance_store: tuple[InMemoryStore, dict[str, str]],
):
    from coletar.schema.objects import GLOBAL_SCOPE

    store, ids = relevance_store
    results = await store.search("ledger invoicing database", scope=GLOBAL_SCOPE, top_k=30)
    found = {hit.obj.id for hit in results}

    assert ids["db_choice"] not in found
    assert ids["atlas_language"] not in found


# -- latency ------------------------------------------------------------------
async def test_search_p95_stays_under_300ms_at_ten_thousand_objects():
    corpus_vocabulary = (
        "ledger invoice money currency rounding pytest ruff mypy postgres pgvector "
        "deploy rust atlas python coverage migration credential async retrieval "
        "embedding compile manifest continuity scope provenance confidence"
    )
    vocabulary = corpus_vocabulary.split()
    rng = random.Random(0)

    store = InMemoryStore()
    for i in range(10_000):
        body = " ".join(rng.choices(vocabulary, k=12))
        await store.put_object(Memory.from_write(f"{body} number {i}"))

    latencies: list[float] = []
    for _ in range(30):
        query = " ".join(rng.choices(vocabulary, k=6))
        start = time.perf_counter()
        await store.search(query, top_k=12)
        latencies.append((time.perf_counter() - start) * 1000)

    latencies.sort()
    p95 = latencies[int(0.95 * len(latencies)) - 1]
    assert p95 < 300.0, f"p95 {p95:.0f}ms over 10,000 objects"
