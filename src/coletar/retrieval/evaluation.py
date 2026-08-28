"""Retrieval evaluation harness (SCOPE §5.1).

§5.1 requires measurement at **two** boundaries, and the distinction is the point:

  * **Candidate recall** — did narrowing keep the relevant object at all? A reranker
    cannot repair an object that candidate generation discarded, so a ranking metric
    alone cannot tell you whether the fix belongs in the retriever or the ranker.
  * **Final ranking** — did it land in the context actually shown to the model?

It also names two ways to score well while retrieving badly, so both are measured:
returning a superseded or out-of-scope object alongside the right one is a **leak**,
and buying hit rate by flooding the context is why **injected tokens** is reported
next to accuracy rather than underneath it.

This lives in the package rather than in `tests/` because §5.1 asks for the harness
and the baseline to be published together, and because the Context Inspector and the
dashboard will want to run it on a user's own corpus.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coletar.retrieval.context import estimate_tokens
from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    ExtractionMethod,
    Memory,
    MemoryKind,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
    Scope,
    ScopeType,
)
from coletar.store.base import Store

#: §5.1's candidate-recall boundary is measured at this depth.
CANDIDATE_DEPTH = 50


def scope_from(raw: str | None) -> Scope:
    return Scope(type=ScopeType.PROJECT, id=raw) if raw else GLOBAL_SCOPE


def build_object(item: dict[str, Any]) -> ContextObject:
    """One corpus entry as a canonical object."""
    scope = scope_from(item.get("scope"))
    object_type = ObjectType(item.get("type", "memory"))
    content = str(item["content"])
    if object_type is ObjectType.MEMORY:
        return Memory.from_write(
            content,
            kind=MemoryKind(item.get("kind", "fact")),
            scope=scope,
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            origin_type=OriginType.USER,
        )
    return ContextObject(
        type=object_type,
        content=content,
        scope=scope,
        confidence=0.9,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        provenance=Provenance(origin_type=OriginType.USER, provider=Provider.COLETAR),
    )


@dataclass
class EvaluationResult:
    total: int = 0
    candidate_hits: int = 0
    hit_at_1: int = 0
    hit_at_5: int = 0
    reciprocal_ranks: list[float] = field(default_factory=list)
    precision_at_5: list[float] = field(default_factory=list)
    injected_tokens: list[int] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    #: A superseded or out-of-scope object that surfaced anyway. Never acceptable.
    leaks: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    by_category: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))

    def _rate(self, count: int) -> float:
        return count / self.total if self.total else 0.0

    @property
    def candidate_recall(self) -> float:
        return self._rate(self.candidate_hits)

    @property
    def hit1(self) -> float:
        return self._rate(self.hit_at_1)

    @property
    def hit5(self) -> float:
        return self._rate(self.hit_at_5)

    @property
    def mrr5(self) -> float:
        return sum(self.reciprocal_ranks) / self.total if self.total else 0.0

    @property
    def precision5(self) -> float:
        return sum(self.precision_at_5) / self.total if self.total else 0.0

    @property
    def mean_tokens(self) -> float:
        if not self.injected_tokens:
            return 0.0
        return sum(self.injected_tokens) / len(self.injected_tokens)

    def percentile_ms(self, fraction: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        return ordered[max(0, int(fraction * len(ordered)) - 1)]

    def category_rates(self) -> dict[str, float]:
        return {
            category: sum(results) / len(results)
            for category, results in sorted(self.by_category.items())
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": self.total,
            "candidate_recall_at_50": round(self.candidate_recall, 4),
            "hit_at_1": round(self.hit1, 4),
            "hit_at_5": round(self.hit5, 4),
            "mrr_at_5": round(self.mrr5, 4),
            "precision_at_5": round(self.precision5, 4),
            "mean_injected_tokens": round(self.mean_tokens, 1),
            "p50_ms": round(self.percentile_ms(0.50), 2),
            "p95_ms": round(self.percentile_ms(0.95), 2),
            "leaks": len(self.leaks),
            "by_category": {k: round(v, 4) for k, v in self.category_rates().items()},
        }

    def report(self) -> str:
        d = self.as_dict()
        lines = [
            f"  queries              {d['queries']}",
            f"  candidate recall@50  {d['candidate_recall_at_50']:.1%}",
            f"  hit@1                {d['hit_at_1']:.1%}",
            f"  hit@5                {d['hit_at_5']:.1%}",
            f"  MRR@5                {d['mrr_at_5']:.3f}",
            f"  precision@5          {d['precision_at_5']:.3f}",
            f"  mean tokens injected {d['mean_injected_tokens']:.1f}",
            f"  latency p50/p95      {d['p50_ms']:.1f}ms / {d['p95_ms']:.1f}ms",
            f"  leaks                {d['leaks']}",
            "  by category:",
        ]
        lines += [f"    {k:<16} {v:.1%}" for k, v in self.category_rates().items()]
        return "\n".join(lines)


def load_eval_set(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


async def seed_corpus(store: Store, corpus: list[dict[str, Any]]) -> dict[str, str]:
    """Load the corpus, resolving `supersedes` by key. Returns key -> object id."""
    ids: dict[str, str] = {}
    # Two passes: a correction can only point at an object that already exists.
    for item in [c for c in corpus if not c.get("supersedes")]:
        ids[str(item["key"])] = (await store.put_object(build_object(item))).id
    for item in [c for c in corpus if c.get("supersedes")]:
        obj = build_object(item)
        obj.supersedes = ids[str(item["supersedes"])]
        ids[str(item["key"])] = (await store.put_object(obj)).id
    return ids


async def evaluate(
    store: Store, eval_set: dict[str, Any], ids: dict[str, str], *, top_k: int = 5
) -> EvaluationResult:
    by_id = {object_id: key for key, object_id in ids.items()}
    result = EvaluationResult()

    for query in eval_set["queries"]:
        expected = str(query["expect"])
        scope = scope_from(query.get("scope"))
        forbidden = query.get("expect_absent")

        started = time.perf_counter()
        candidates = await store.search(str(query["query"]), scope=scope, top_k=CANDIDATE_DEPTH)
        result.latencies_ms.append((time.perf_counter() - started) * 1000.0)

        candidate_keys = [by_id.get(hit.obj.id, "?") for hit in candidates]
        ranked = candidate_keys[:top_k]

        result.total += 1
        result.by_category[str(query["category"])].append(expected in ranked)

        if expected in candidate_keys:
            result.candidate_hits += 1
        if expected in ranked:
            rank = ranked.index(expected) + 1
            result.hit_at_5 += 1
            result.hit_at_1 += rank == 1
            result.reciprocal_ranks.append(1.0 / rank)
        else:
            result.misses.append(f"[{query['category']}] {query['query']}")

        # precision@5 against a single labelled answer: 1/k when found, 0 otherwise.
        result.precision_at_5.append((1.0 / top_k) if expected in ranked else 0.0)
        result.injected_tokens.append(
            sum(estimate_tokens(hit.obj.content) for hit in candidates[:top_k])
        )

        # A stale or cross-scope object surfacing at all is a leak, not a ranking
        # nuance: hit rate bought by also returning the superseded answer is not a
        # retrieval win (§5.1).
        if forbidden and str(forbidden) in ranked:
            result.leaks.append(f"[{query['category']}] {query['query']} -> {forbidden}")

    return result
