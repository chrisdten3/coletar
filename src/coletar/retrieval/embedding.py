"""Embeddings for the Search/Retrieval index (SCOPE §5).

Two implementations, and the split is deliberate:

  * `OllamaEmbedder` is the real one. It runs against the user's *own* local model,
    which is the point made in §4 and §11 -- typed extraction and embedding at
    consumer scale is real inference spend, and on the local leg that spend is
    zero. This is what a deployment uses.

  * `HashingEmbedder` is the default, and it is not a mock. It is a signed-hashing
    bag of word unigrams and character 4-grams projected into the same vector space
    the real embedder targets. Character n-grams are what buy it morphological
    reach ("money" ~ "monetary", "prefer" ~ "prefers"); what it cannot do is
    synonymy, because it has no model behind it. It exists so a fresh clone has a
    working vector index with nothing installed -- the in-process store must keep
    working with no infrastructure, and an embedding pipeline that requires a model
    server would break that.

Retrieval blends this with a lexical term (see `coletar.retrieval.ranking`), so the
hashing default degrades to something close to lexical search rather than to
nothing.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

import httpx

from coletar.config import get_settings

_WORD = re.compile(r"[a-z0-9']+")
_CHAR_NGRAM = 4

#: Closed-class words carry no retrieval signal but do carry rank. Left in, a query
#: like "what should I do when a function fails" scores an object that merely shares
#: "what/should/does" above the one that actually answers it -- which is exactly the
#: failure mode this list exists to remove.
_STOPWORD_TEXT = """
a an and any are as at be been before being but by can could did do does doing done
for from had has have having he her him his how i if in into is it its me my no nor
not of on or our should so than that the their them then there these they this those
to too us was we were what when where which while who whom why will with would you
your
"""
_STOPWORDS: frozenset[str] = frozenset(_STOPWORD_TEXT.split())

#: Deliberately light. A full stemmer over-conflates ("universe"/"university"), and
#: the character n-grams below already carry most of the morphological load; this
#: only has to close the gaps that break an exact-token match outright, like the
#: possessive in "Ledger's test suite" against a query saying "Ledger".
_SUFFIXES = ("ies", "ing", "ed", "es", "s")


def stem(word: str) -> str:
    word = word.removesuffix("'s").removesuffix("'")
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return f"{word[:-3]}y"
    if word.endswith("ss"):
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word

#: Word features carry more signal than character n-grams; the n-grams are there for
#: morphological tolerance, not to dominate the vector.
_WORD_WEIGHT = 1.0
_NGRAM_WEIGHT = 0.35


@runtime_checkable
class Embedder(Protocol):
    """Embedding is on the write path, so this stays batch-shaped: a store that
    ingests 500 objects from an export should make one call, not 500."""

    model: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    """Content tokens: stopwords dropped, lightly stemmed.

    Both the lexical term and the embedder's word features run through this, so a
    query and an object are always compared in the same vocabulary.
    """
    return [
        stemmed
        for word in _WORD.findall(text.lower())
        if word not in _STOPWORDS and (stemmed := stem(word)) not in _STOPWORDS
    ]


def _features(text: str) -> dict[str, float]:
    feats: dict[str, float] = {}
    words = tokenize(text)
    for word in words:
        feats[f"w:{word}"] = feats.get(f"w:{word}", 0.0) + _WORD_WEIGHT
    # n-grams over the stemmed content words only, for the same reason the lexical
    # term drops stopwords: " the " is a strong n-gram and a meaningless one.
    padded = f" {' '.join(words)} "
    for i in range(len(padded) - _CHAR_NGRAM + 1):
        gram = f"c:{padded[i : i + _CHAR_NGRAM]}"
        feats[gram] = feats.get(gram, 0.0) + _NGRAM_WEIGHT
    return feats


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    """Signed hashing: the sign bit keeps unrelated features from piling up
    constructively in the same bucket, which is what makes collisions tolerable."""
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if (value >> 63) & 1 else -1.0


def l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """Both sides are stored L2-normalized, so this is a plain dot product."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


class HashingEmbedder:
    """Deterministic, offline, dependency-free. See the module docstring for what
    it can and cannot do -- it is honest about being lexical, not semantic."""

    def __init__(self, dim: int = 768) -> None:
        self.model = f"hashing-{dim}"
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]

    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for feature, weight in _features(text).items():
            index, sign = _bucket(feature, self.dim)
            vector[index] += sign * weight
        return l2_normalize(vector)


class OllamaEmbedder:
    """The real embedder, against the user's own model server. Free inference on the
    local leg, which is what makes embedding-on-every-write affordable (§11)."""

    def __init__(self, base_url: str, model: str, dim: int, *, timeout: float = 30.0) -> None:
        # The embeddings endpoint is on Ollama's native API, not its OpenAI-compatible
        # /v1 surface, so strip that suffix if the configured upstream carries it.
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.model = model
        self.dim = dim
        self._timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/embed", json={"model": self.model, "input": texts}
            )
            response.raise_for_status()
            payload = response.json()
        vectors = [l2_normalize([float(v) for v in row]) for row in payload["embeddings"]]
        for vector in vectors:
            if len(vector) != self.dim:
                raise ValueError(
                    f"{self.model} returned {len(vector)} dimensions, but the store and "
                    f"the pgvector column are built for {self.dim}. Set "
                    f"COLETAR_EMBEDDING_DIM to match and re-run migrations."
                )
        return vectors


def build_embedder() -> Embedder:
    settings = get_settings()
    if settings.embedding_backend == "ollama":
        return OllamaEmbedder(
            settings.upstream_base_url, settings.embedding_model, settings.embedding_dim
        )
    return HashingEmbedder(settings.embedding_dim)
