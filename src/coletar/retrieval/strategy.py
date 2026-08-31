"""Retrieval strategies behind one interface (SCOPE §5.1, ROADMAP M4.3).

§5.1 names four boundaries — candidate generation, fusion, reranking, context
assembly — and until now three of them were the same function. Separating them is
not architecture for its own sake: M4.1 could only diagnose `scope_isolation` by
asking "did narrowing discard it, or did ranking rank it badly?", and that question
needs the two stages to be separable things.

**The published blend stays the default.** `rank_score` remains the deterministic
ranker and the backend-parity contract; everything here is optional and off unless a
caller asks. A strategy that changed results by existing would make every published
baseline ambiguous.

**No strategy may bypass policy.** That holds structurally rather than by review:
everything in this module reorders or drops what `Store.search` already returned,
and the store applies scope, locality, sensitivity, retirement and supersession
before any of it runs. A reranker cannot resurrect what narrowing refused, which is
the same property that made supersession a *candidate generation* fix in M4.1.
"""

from __future__ import annotations

from typing import Protocol

from coletar.retrieval.ranking import Scored

#: Bump when a strategy's behaviour changes. Traces record it beside the ranking
#: version, so a measured number stays attributable to what produced it.
STRATEGY_VERSION = "1.0"

#: Standard RRF constant. Large enough that the top few ranks are not winner-take-all,
#: small enough that rank still dominates.
RRF_K = 60.0


class Reranker(Protocol):
    """Reorders and may drop; never adds.

    The signature takes the already-policy-filtered hits for that reason. There is
    no store handle here on purpose — a reranker that could query would be a
    candidate generator, and would be able to reach past the filter.
    """

    name: str

    def rerank(self, hits: list[Scored], *, limit: int) -> list[Scored]: ...


class PublishedOrder:
    """The default: whatever `rank_score` decided, truncated to the limit."""

    name = "published"

    def rerank(self, hits: list[Scored], *, limit: int) -> list[Scored]:
        return hits[:limit]


class MaximalMarginalRelevance:
    """MMR: trade a little relevance for coverage.

    The failure it addresses is a context window spent restating one fact. Assembly
    already drops *near* duplicates at a 0.9 token overlap, but three memories about
    the same project at 0.5 overlap are each distinct and together say little more
    than the best one — and every one of them costs tokens.

    `lambda_` is the relevance/diversity balance: 1.0 is pure relevance and
    reproduces `PublishedOrder` exactly, which is the property that makes this safe
    to add. Lower trades rank for spread.
    """

    name = "mmr"

    def __init__(self, lambda_: float = 0.7) -> None:
        if not 0.0 <= lambda_ <= 1.0:
            raise ValueError("lambda_ must be between 0 and 1")
        self.lambda_ = lambda_

    def rerank(self, hits: list[Scored], *, limit: int) -> list[Scored]:
        if not hits:
            return []
        from coletar.retrieval.embedding import tokenize

        tokens = {hit.obj.id: set(tokenize(hit.obj.content)) for hit in hits}
        remaining = list(hits)
        selected: list[Scored] = [remaining.pop(0)]

        while remaining and len(selected) < limit:
            best_index, best_value = 0, float("-inf")
            for index, candidate in enumerate(remaining):
                overlap = max(
                    _jaccard(tokens[candidate.obj.id], tokens[chosen.obj.id])
                    for chosen in selected
                )
                value = self.lambda_ * candidate.score - (1.0 - self.lambda_) * overlap
                if value > best_value:
                    best_index, best_value = index, value
            selected.append(remaining.pop(best_index))
        return selected


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def reciprocal_rank_fusion(rankings: list[list[Scored]], *, limit: int) -> list[Scored]:
    """Fuse several orderings by rank rather than by score.

    Scores from different retrievers are not on a common scale — a cosine of 0.31
    and a BM25 of 4.7 cannot be added — so RRF uses position, which is the only
    thing they genuinely share. This is the fusion boundary §5.1 asks for, and it is
    what the Postgres sparse candidate path plugs into when that lands.

    The fused hit keeps its highest-ranked components, so `explain` still shows real
    arithmetic rather than a fusion score with no provenance.
    """
    fused: dict[str, float] = {}
    best: dict[str, Scored] = {}
    for ranking in rankings:
        for position, hit in enumerate(ranking):
            fused[hit.obj.id] = fused.get(hit.obj.id, 0.0) + 1.0 / (RRF_K + position + 1)
            current = best.get(hit.obj.id)
            if current is None or hit.score > current.score:
                best[hit.obj.id] = hit
    ordered = sorted(fused, key=lambda object_id: (fused[object_id], object_id), reverse=True)
    return [best[object_id] for object_id in ordered[:limit]]
