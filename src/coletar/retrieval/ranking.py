"""The one ranking formula (SCOPE §5, §5.1).

Every backend re-ranks the same way, so switching Postgres in for the in-process
store changes performance and nothing about which memory a model sees. Keeping the
blend here rather than in each `search` is what makes that true.

Relevance is hybrid because neither half is sufficient on its own: the vector term
finds a memory the query paraphrases, and the lexical term keeps an exact identifier
(a project name, a library, a person) from being smeared into approximate neighbours.

§5.1 makes this the *deterministic default* reranker and the parity contract between
backends. Optional strategies -- reciprocal-rank fusion, MMR, a bounded local
cross-encoder -- arrive behind the same interface at M4, and none of them may bypass
scope, sensitivity, retirement or supersession.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from coletar.schema.objects import ContextObject

#: Bump when the blend below changes. Traces and published baselines record it, so a
#: measured number can always be attributed to the formula that produced it -- a
#: baseline you cannot attribute is not a baseline.
RANKING_VERSION = "1.0"

#: Vector leads, lexical corrects. Tuned against tests/fixtures/relevance_set.json.
VECTOR_WEIGHT = 0.55
LEXICAL_WEIGHT = 0.45

#: Confidence scales relevance rather than gating it -- a low-confidence memory is
#: still worth showing if it is the only thing that matches, just below a
#: high-confidence one that matches as well (§2, §3.1).
CONFIDENCE_FLOOR = 0.5

#: Recency is a tiebreaker, not a ranking signal. A fact from a year ago that
#: answers the question outranks a fresh one that does not.
RECENCY_FLOOR = 0.85
RECENCY_HALF_LIFE_DAYS = 90.0


class CandidateSource(StrEnum):
    """Which half of the hybrid surfaced this object.

    Recorded per hit because §5.1 measures candidate generation and final ranking as
    two separate boundaries: a reranker cannot repair an object that narrowing
    discarded, so knowing *which* retriever found something is what tells you where
    to fix a miss.
    """

    VECTOR = "vector"
    LEXICAL = "lexical"
    BOTH = "both"


@dataclass(frozen=True)
class ScoreComponents:
    """The arithmetic behind one hit, carried rather than recomputed.

    `explain` mode surfaces this. It is computed on the ranking path itself so the
    explanation cannot drift from the score it explains -- recomputing the blend
    separately for display is how those two quietly diverge.
    """

    vector: float
    lexical: float
    confidence_factor: float
    recency_factor: float
    relevance: float
    total: float
    source: CandidateSource

    def as_dict(self) -> dict[str, float | str]:
        return {
            "vector": round(self.vector, 4),
            "lexical": round(self.lexical, 4),
            "confidence_factor": round(self.confidence_factor, 4),
            "recency_factor": round(self.recency_factor, 4),
            "relevance": round(self.relevance, 4),
            "total": round(self.total, 4),
            "source": self.source.value,
        }


def lexical_score(query_tokens: set[str], content_tokens: set[str]) -> float:
    """Fraction of the query the object covers. Recall-shaped on purpose: a short
    query matched exactly should not lose to a long object that matched loosely."""
    if not query_tokens:
        return 0.0
    return len(query_tokens & content_tokens) / len(query_tokens)


def _recency_factor(updated_at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - updated_at).total_seconds() / 86_400.0)
    decay = float(0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS))
    return RECENCY_FLOOR + (1.0 - RECENCY_FLOOR) * decay


def candidate_source(*, lexical: float, vector: float) -> CandidateSource:
    if lexical > 0.0 and vector > 0.0:
        return CandidateSource.BOTH
    return CandidateSource.LEXICAL if lexical > 0.0 else CandidateSource.VECTOR


def rank_score(
    *,
    lexical: float,
    vector: float,
    confidence: float,
    updated_at: datetime,
    now: datetime | None = None,
) -> ScoreComponents:
    now = now or datetime.now(UTC)
    # Signed hashing can produce a negative cosine between unrelated texts; a
    # negative similarity is no similarity, not anti-similarity.
    clamped_vector = max(0.0, vector)
    relevance = VECTOR_WEIGHT * clamped_vector + LEXICAL_WEIGHT * lexical
    confidence_factor = CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * confidence
    recency = _recency_factor(updated_at, now)
    return ScoreComponents(
        vector=clamped_vector,
        lexical=lexical,
        confidence_factor=confidence_factor,
        recency_factor=recency,
        relevance=relevance,
        total=relevance * confidence_factor * recency,
        source=candidate_source(lexical=lexical, vector=clamped_vector),
    )


@dataclass(frozen=True)
class Scored:
    """One ranked hit: the object, and the arithmetic that ranked it.

    `search` returns these rather than bare (object, float) pairs so the components
    survive out of the backend. Recomputing them upstream for `explain` would mean
    two implementations of one formula.
    """

    obj: ContextObject
    components: ScoreComponents

    @property
    def score(self) -> float:
        return self.components.total
