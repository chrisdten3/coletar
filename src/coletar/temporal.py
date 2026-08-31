"""As-of queries over the event log (SCOPE §6, ROADMAP M9).

The four questions a compliance reader actually asks:

1. What does this say now? — ordinary retrieval.
2. **What did it say on 3 March?** — `graph_as_of`, `search_as_of`.
3. Who changed it, when, and from what source? — the event log and provenance.
4. **What changed between two dates?** — `changes_between`.

Two and four are what nothing else in this category can answer. A memory layer stores
the current value; answering "what did we believe at the time" needs an immutable log
carrying full before/after state, which is why constraint 5 exists and why every
write appends an event.

**Reconstruction reads only the log.** It never consults the object table — that is
the point of `replay`, and it is what makes an answer defensible: if the table and
the log ever disagree, the log is the thing you can show someone.

**Supersession is evaluated as of *then*, not now.** A fact corrected last week was
still current in March, so a naive filter against today's supersession would report
that March believed something it did not. This is the subtle half of the whole
module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from coletar.retrieval.embedding import tokenize
from coletar.retrieval.ranking import Scored, lexical_score, rank_score
from coletar.schema.events import EventType
from coletar.schema.objects import ContextObject, Scope, ScopeType, object_from_record
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: An audit answer that silently used a page of history would be worse than no
#: answer, so the window is deliberately large and the cost accepted.
_LOG_LIMIT = 200_000


async def graph_as_of(
    store: Store,
    tenant_id: TenantId,
    at: datetime,
    *,
    scope: Scope | None = None,
    in_force_at: datetime | None = None,
) -> list[ContextObject]:
    """Every object as it stood at `at`, active and un-superseded *as of then*.

    `in_force_at` adds the second temporal axis, and the pair is what an auditor
    actually needs. `at` is **transaction time** — when coletar recorded something.
    `in_force_at` is **valid time** — when the fact was true in the world. Asking both
    answers the question compliance actually poses: *"on 3 March, what did we believe
    was in force on 1 January?"* Neither axis alone can express that, and a system
    carrying only one will answer a different question than the one asked without
    ever saying so.

    One pass over the log rather than a replay per object: `list_events` is
    newest-first, so the first revision seen for an id is its latest state at or
    before that moment.
    """
    events = await store.list_events(tenant_id, until=at, limit=_LOG_LIMIT)

    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.is_revision and event.after is not None and event.object_id:
            latest.setdefault(event.object_id, event.after)

    objects = [object_from_record(record) for record in latest.values()]

    # Computed from the as-of states, never from the live table. An object corrected
    # after `at` was still the current answer at `at`.
    superseded = {obj.supersedes for obj in objects if obj.supersedes}
    current = [obj for obj in objects if obj.is_active and obj.id not in superseded]

    if in_force_at is not None:
        current = [obj for obj in current if obj.in_force_at(in_force_at)]

    if scope is not None:
        current = [
            obj
            for obj in current
            if obj.scope == scope
            or (scope.type is ScopeType.PROJECT and obj.scope.type is ScopeType.GLOBAL)
        ]
    return sorted(current, key=lambda o: o.id)


async def search_as_of(
    store: Store,
    tenant_id: TenantId,
    query: str,
    at: datetime,
    *,
    scope: Scope | None = None,
    in_force_at: datetime | None = None,
    top_k: int = 12,
) -> list[Scored]:
    """Retrieval against the graph as it stood at `at`.

    **Lexical only, and deliberately so.** The vector index holds current state; a
    historical one would have to be versioned per moment, and embedding a
    reconstructed corpus on every audit query would make the answer slow and
    non-deterministic. An audit asks "what did the policy say about X" — exact terms,
    which is the half of the hybrid that lexical matching is good at. The
    `CandidateSource` on every hit records that this came from lexical alone, so a
    reader can see the difference rather than assume parity with live search.
    """
    objects = await graph_as_of(store, tenant_id, at, scope=scope, in_force_at=in_force_at)
    query_tokens = set(tokenize(query))
    scored: list[Scored] = []
    for obj in objects:
        lexical = lexical_score(query_tokens, set(tokenize(obj.content)))
        if lexical <= 0.0:
            continue
        scored.append(
            Scored(
                obj=obj,
                components=rank_score(
                    lexical=lexical,
                    vector=0.0,
                    confidence=obj.confidence,
                    updated_at=obj.updated_at,
                    now=at,
                ),
            )
        )
    scored.sort(key=lambda hit: (hit.score, hit.obj.id), reverse=True)
    return scored[:top_k]


@dataclass(frozen=True)
class Change:
    """One recorded change, phrased for someone auditing rather than debugging."""

    object_id: str
    at: datetime
    event_type: str
    actor: str
    before: str | None
    after: str | None

    @property
    def kind(self) -> str:
        if self.before is None:
            return "added"
        if self.event_type == str(EventType.OBJECT_RETIRED):
            return "retired"
        if self.event_type == str(EventType.OBJECT_SUPERSEDED):
            return "superseded"
        return "changed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "at": self.at.isoformat(),
            "kind": self.kind,
            "event": self.event_type,
            "actor": self.actor,
            "before": self.before,
            "after": self.after,
        }


async def changes_between(
    store: Store, tenant_id: TenantId, start: datetime, end: datetime
) -> list[Change]:
    """Every recorded change in `(start, end]`, oldest first.

    Content only, not whole object dumps: an auditor reading a diff wants the
    sentence that changed, and a wall of serialised fields buries it.
    """
    events = await store.list_events(tenant_id, since=start, until=end, limit=_LOG_LIMIT)
    changes = [
        Change(
            object_id=event.object_id or "",
            at=event.at,
            event_type=str(event.type),
            actor=str(event.actor),
            before=(event.before or {}).get("content") if event.before else None,
            after=(event.after or {}).get("content") if event.after else None,
        )
        for event in events
        if event.is_revision and event.object_id
    ]
    return sorted(changes, key=lambda c: (c.at, c.object_id))
