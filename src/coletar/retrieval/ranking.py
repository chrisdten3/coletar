"""The one ranking formula (SCOPE §5).

Every backend re-ranks the same way, so switching Postgres in for the in-process
store changes performance and nothing about which memory a model sees. Keeping the
blend here rather than in each `search` is what makes that true.

Relevance is hybrid because neither half is sufficient on its own: the vector term
finds a memory the query paraphrases, and the lexical term keeps an exact identifier
(a project name, a library, a person) from being smeared into approximate neighbours.
"""

from __future__ import annotations

from datetime import UTC, datetime

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


def rank_score(
    *,
    lexical: float,
    vector: float,
    confidence: float,
    updated_at: datetime,
    now: datetime | None = None,
) -> float:
    now = now or datetime.now(UTC)
    # Signed hashing can produce a negative cosine between unrelated texts; a
    # negative similarity is no similarity, not anti-similarity.
    relevance = VECTOR_WEIGHT * max(0.0, vector) + LEXICAL_WEIGHT * lexical
    confidence_factor = CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * confidence
    return relevance * confidence_factor * _recency_factor(updated_at, now)
