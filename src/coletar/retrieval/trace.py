"""Retrieval traces (SCOPE §5.1, §6, §11).

One append-only trace per search, recording what narrowing kept, how the blend
scored it, what was packed, and how long each stage took -- enough to reproduce a
ranking decision by object id and component version, and to answer §6's dashboard.

**One** trace per search, not one event per hit. Before this, `search_context`
appended an `object.accessed` row for every returned object: twelve rows per search,
which floods the log the observability dashboard reads. The trade is that M4.2's
per-object "last access" becomes a query *over* traces -- a JSONB containment lookup
in Postgres, indexable -- rather than a direct row scan. That is the right side of
the trade, but it is a trade.

**Privacy is structural, not a default.** §11 names the real risk: retrieval
telemetry quietly becoming a second copy of the user's private history. So a trace
records a *hash* of the query and the *ids* of what was returned -- never the query
text, never the content. Content-level debugging is an explicit per-call argument,
never a global setting, because a global setting is how this gets switched on once
and left on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from coletar.retrieval.context import RetrievedContext
from coletar.retrieval.ranking import RANKING_VERSION
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import Scope

if TYPE_CHECKING:
    from coletar.store.base import Store

#: Truncated: enough to correlate repeat queries, far too little to invert.
_QUERY_DIGEST_CHARS = 16


def query_digest(query: str) -> str:
    """A stable, non-reversible handle for a query.

    Lets an operator see that the same question was asked twenty times, or correlate
    a trace with a user's bug report, without the log holding what was asked.
    """
    normalized = " ".join(query.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_QUERY_DIGEST_CHARS]


@dataclass(frozen=True)
class ComponentVersions:
    """What produced this result. A baseline you cannot attribute is not a baseline."""

    embedder: str
    ranking: str = RANKING_VERSION
    backend: str = "unknown"

    def as_dict(self) -> dict[str, str]:
        return {"embedder": self.embedder, "ranking": self.ranking, "backend": self.backend}


@dataclass
class RetrievalTrace:
    query_digest: str
    scope: str
    top_k: int
    token_budget: int
    versions: ComponentVersions
    returned_ids: list[str] = field(default_factory=list)
    component_scores: list[dict[str, float | str]] = field(default_factory=list)
    token_estimate: int = 0
    truncated: bool = False
    deduplicated: int = 0
    skipped_oversized: int = 0
    stage_ms: dict[str, float] = field(default_factory=dict)
    #: Only ever populated when a caller explicitly opts in per call.
    query_text: str | None = None

    def as_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "query_digest": self.query_digest,
            "scope": self.scope,
            "top_k": self.top_k,
            "token_budget": self.token_budget,
            "versions": self.versions.as_dict(),
            "returned_ids": self.returned_ids,
            "component_scores": self.component_scores,
            "token_estimate": self.token_estimate,
            "truncated": self.truncated,
            "deduplicated": self.deduplicated,
            "skipped_oversized": self.skipped_oversized,
            "stage_ms": self.stage_ms,
            "result_count": len(self.returned_ids),
        }
        if self.query_text is not None:
            detail["query_text"] = self.query_text
        return detail


def build_trace(
    *,
    query: str,
    scope: Scope | None,
    top_k: int,
    token_budget: int,
    context: RetrievedContext,
    embedder_model: str,
    backend: str = "unknown",
    record_query_text: bool = False,
) -> RetrievalTrace:
    return RetrievalTrace(
        query_digest=query_digest(query),
        scope=str(scope) if scope is not None else "any",
        top_k=top_k,
        token_budget=token_budget,
        versions=ComponentVersions(embedder=embedder_model, backend=backend),
        returned_ids=[obj.id for obj in context.objects],
        component_scores=[c.as_dict() for c in context.components],
        token_estimate=context.token_estimate,
        truncated=context.truncated,
        deduplicated=context.deduplicated,
        skipped_oversized=context.skipped_oversized,
        stage_ms=dict(context.stage_ms),
        # Explicit opt-in, per call. Never read from configuration.
        query_text=query if record_query_text else None,
    )


async def record_trace(
    store: Store, trace: RetrievalTrace, *, actor: Actor = Actor.CONNECTOR
) -> Event:
    event = Event(
        type=EventType.RETRIEVAL_TRACE,
        actor=actor,
        detail=trace.as_detail(),
    )
    await store.append_event(event)
    return event
