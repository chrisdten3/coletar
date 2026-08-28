"""The real embedder against a real Ollama (SCOPE §4, §11).

`OllamaEmbedder` is the embedder a deployment actually runs, and until this file
existed it had no test at all — every assertion about `/api/embed` was an assumption
of mine that nothing checked. That is the same class of gap as an SSE assembler
tested only against frames the test file writes itself.

Gated on a reachable Ollama with `nomic-embed-text` pulled, so the suite stays green
with nothing installed. A mock here would only re-assert the assumptions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coletar.retrieval.embedding import HashingEmbedder, OllamaEmbedder, cosine
from coletar.store.memory import InMemoryStore
from conftest import RelevanceSet, build_corpus_object, scope_from

EMBEDDING_DIM = 768


@pytest.fixture
def embedder(ollama_url: str) -> OllamaEmbedder:
    return OllamaEmbedder(ollama_url, "nomic-embed-text", EMBEDDING_DIM)


async def test_the_endpoint_and_response_shape_are_what_we_assume(embedder):
    vectors = await embedder.embed(["a first text", "a second text"])
    assert len(vectors) == 2, "batching must return one vector per input, in order"
    assert all(len(v) == EMBEDDING_DIM for v in vectors)


async def test_vectors_come_back_l2_normalized(embedder):
    """The ranking blend treats the dot product *as* the cosine, so normalization is
    not cosmetic — an unnormalized vector silently corrupts every score."""
    (vector,) = await embedder.embed(["Chris prefers fixed-point integers for money."])
    assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-5)


async def test_an_empty_batch_makes_no_request(embedder):
    assert await embedder.embed([]) == []


async def test_real_embeddings_separate_paraphrase_from_unrelated_text(embedder):
    """The property the hashing default cannot provide, and the reason this backend
    exists at all."""
    money, paraphrase, coffee = await embedder.embed(
        [
            "I prefer fixed-point integers over doubles for money.",
            "Monetary amounts should use fixed point integers.",
            "Chris drinks his coffee black.",
        ]
    )
    assert cosine(money, paraphrase) > 0.6
    assert cosine(money, coffee) < 0.5
    assert cosine(money, paraphrase) > cosine(money, coffee)


async def test_a_dimension_mismatch_is_a_clear_configuration_error(ollama_url):
    """Silently accepting the wrong width would mean terrible retrieval with nothing
    going red, and a pgvector column that rejects the write much later."""
    wrong = OllamaEmbedder(ollama_url, "nomic-embed-text", 1536)
    with pytest.raises(ValueError, match="dimensions"):
        await wrong.embed(["anything"])


async def test_real_embeddings_close_the_synonymy_gap(
    ollama_url: str, relevance_set: RelevanceSet
):
    """The one query the hashing default misses — "book a meeting at 9am" against
    "Do not schedule anything before 10am" — needs synonymy, and was left in the set
    on purpose as the canary for this backend."""
    gap = next(
        q for q in relevance_set.queries if "book a meeting" in str(q["query"])
    )
    store = InMemoryStore(embedder=OllamaEmbedder(ollama_url, "nomic-embed-text", EMBEDDING_DIM))
    keys: dict[str, str] = {}
    for item in relevance_set.corpus:
        stored = await store.put_object(build_corpus_object(item))
        keys[stored.id] = str(item["key"])

    results = await store.search(str(gap["query"]), scope=scope_from(gap.get("scope")), top_k=5)

    assert gap["expect"] in [keys[hit.obj.id] for hit in results]


async def test_the_published_numbers_still_hold(ollama_url: str, relevance_set: RelevanceSet):
    """docs/RETRIEVAL.md publishes a figure per backend. If either drifts, the
    documentation is wrong and this is what says so."""
    published = json.loads(
        (Path(__file__).parent / "fixtures" / "relevance_baselines.json").read_text()
    )
    for backend, embedder in (
        ("hashing", HashingEmbedder(EMBEDDING_DIM)),
        ("ollama", OllamaEmbedder(ollama_url, "nomic-embed-text", EMBEDDING_DIM)),
    ):
        store = InMemoryStore(embedder=embedder)
        keys: dict[str, str] = {}
        for item in relevance_set.corpus:
            stored = await store.put_object(build_corpus_object(item))
            keys[stored.id] = str(item["key"])

        hits = 0
        for query in relevance_set.queries:
            results = await store.search(
                str(query["query"]), scope=scope_from(query.get("scope")), top_k=5
            )
            hits += query["expect"] in [keys[hit.obj.id] for hit in results]

        rate = hits / len(relevance_set.queries)
        expected = published[backend]["top5_hit_rate"]
        assert rate == pytest.approx(expected, abs=0.05), (
            f"{backend}: measured {rate:.0%}, docs/RETRIEVAL.md publishes {expected:.0%}"
        )
