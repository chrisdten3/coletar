"""Observability over the event log (SCOPE §6, ROADMAP M4.4).

A **view**, not a subsystem. Every number here is derived from objects and events
that already exist — nothing is counted into a new table, and nothing new is written
to produce a reading. That is the §6 discipline: compression, observability and the
agentic graph are views over the substrate, which is the whole reason the graph
carries `supersedes`, `confidence` and an append-only log in the first place.

**Last access is derived from retrieval traces, not from an access event.**
`OBJECT_ACCESSED` exists in the enum and nothing emits it, which turns out to be the
right outcome: writing an event per object per search would multiply the log by the
width of every result set, to record something the traces already imply. An object
was accessed when it appeared in a trace's `returned_ids`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from coletar.capture import is_pending
from coletar.episode_crypto import PREFIX, EpisodeKeyUnavailable, decrypt_episode
from coletar.schema.events import Event, EventType
from coletar.schema.objects import ContextObject, EdgeType, ObjectType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Bounded so a long-lived graph does not turn the dashboard into a full scan.
EVENT_WINDOW = 2_000
OBJECT_WINDOW = 5_000
FEED_LENGTH = 40


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


@dataclass(frozen=True)
class SurfaceUsage:
    """§6 groups by surface, because "the local bridge injected this" and "Claude
    asked" are different facts about the same graph."""

    surface: str
    searches: int
    mean_tokens: float
    p50_ms: float
    p95_ms: float
    truncated: int
    deduplicated: int


@dataclass(frozen=True)
class ObjectHealth:
    object_id: str
    type: str
    scope: str
    confidence: float
    size_bytes: int
    last_access: datetime | None
    expires_at: datetime | None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at < datetime.now(UTC)

    @property
    def never_read(self) -> bool:
        return self.last_access is None


@dataclass(frozen=True)
class Dashboard:
    tenant: TenantId
    generated_at: datetime
    total_objects: int
    retired: int
    superseded: int
    total_bytes: int
    by_type: dict[str, int]
    by_actor: dict[str, int]
    usage: list[SurfaceUsage]
    health: list[ObjectHealth]
    feed: list[Event]
    #: The most recent trace's component scores, so "why did I get this?" is
    #: answerable from the same page rather than from a log grep.
    last_explanation: list[dict[str, Any]] = field(default_factory=list)

    @property
    def never_read(self) -> int:
        return sum(1 for row in self.health if row.never_read)

    @property
    def expired(self) -> int:
        return sum(1 for row in self.health if row.expired)


def _expires_at(obj: ContextObject) -> datetime | None:
    if obj.ttl_days is None:
        return None
    return obj.created_at + timedelta(days=obj.ttl_days)


async def build_dashboard(store: Store, tenant_id: TenantId) -> Dashboard:
    """One pass over the log and one over the objects. No writes."""
    objects = await store.list_objects(
        tenant_id, include_retired=True, include_superseded=True, limit=OBJECT_WINDOW
    )
    events = await store.list_events(tenant_id, limit=EVENT_WINDOW)

    superseded_ids = {o.supersedes for o in objects if o.supersedes}

    last_access: dict[str, datetime] = {}
    per_surface: dict[str, list[dict[str, Any]]] = {}
    last_explanation: list[dict[str, Any]] = []
    for event in events:
        if event.type is not EventType.RETRIEVAL_TRACE:
            continue
        detail = event.detail
        for object_id in detail.get("returned_ids") or []:
            current = last_access.get(str(object_id))
            if current is None or event.at > current:
                last_access[str(object_id)] = event.at
        per_surface.setdefault(str(detail.get("surface", "unknown")), []).append(detail)
        if not last_explanation:
            # `list_events` is newest first, so the first trace seen is the latest.
            last_explanation = list(detail.get("component_scores") or [])

    usage: list[SurfaceUsage] = []
    for surface, traces in sorted(per_surface.items()):
        latencies = [
            float(t.get("stage_ms", {}).get("total", 0.0)) for t in traces
        ]
        tokens = [float(t.get("token_estimate", 0)) for t in traces]
        usage.append(
            SurfaceUsage(
                surface=surface,
                searches=len(traces),
                mean_tokens=sum(tokens) / len(tokens) if tokens else 0.0,
                p50_ms=_percentile(latencies, 0.50),
                p95_ms=_percentile(latencies, 0.95),
                truncated=sum(1 for t in traces if t.get("truncated")),
                deduplicated=sum(int(t.get("deduplicated", 0) or 0) for t in traces),
            )
        )

    health = [
        ObjectHealth(
            object_id=obj.id,
            type=str(obj.type),
            scope=str(obj.scope),
            confidence=obj.confidence,
            size_bytes=len(obj.content.encode("utf-8")),
            last_access=last_access.get(obj.id),
            expires_at=_expires_at(obj),
        )
        for obj in objects
    ]

    return Dashboard(
        tenant=tenant_id,
        generated_at=datetime.now(UTC),
        total_objects=len(objects),
        retired=sum(1 for o in objects if not o.is_active),
        superseded=sum(1 for o in objects if o.id in superseded_ids),
        total_bytes=sum(row.size_bytes for row in health),
        by_type=dict(Counter(str(o.type) for o in objects).most_common()),
        by_actor=dict(Counter(str(e.actor) for e in events).most_common()),
        usage=usage,
        health=sorted(health, key=lambda r: (r.last_access is not None, -r.size_bytes)),
        feed=events[:FEED_LENGTH],
        last_explanation=last_explanation,
    )


# --- the agentic view ------------------------------------------------------------

#: §6's entity / fact / episode model is *three object types*, not a parallel store.
#: Rendering them together is a filter, and keeping it a filter is what stops a
#: second graph appearing behind the first.
AGENTIC_TYPES: tuple[ObjectType, ...] = (
    ObjectType.ENTITY,
    ObjectType.FACT,
    ObjectType.EPISODE,
)


@dataclass(frozen=True)
class AgenticView:
    """Entity / fact / episode, with the lineage from an episode to what it produced."""

    by_type: dict[str, list[ContextObject]]
    #: episode id -> objects whose provenance names it. Preserved because §6 requires
    #: episode-to-derived-object lineage to survive; losing it would make the view
    #: pretty and unfalsifiable.
    derived_from: dict[str, list[ContextObject]]
    #: entity id -> the facts that mention it, via MENTIONS edges. Constraint 4 says
    #: an object the Inspector cannot explain should not exist, and an entity alone
    #: explains nothing: "Amanda, Walleye Business Development" is a name with no
    #: answer to "why is she in my graph?". The facts mentioning her are the answer.
    mentioned_by: dict[str, list[ContextObject]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(len(rows) for rows in self.by_type.values())

    @property
    def pending_episodes(self) -> int:
        return sum(
            1 for episode in self.by_type[str(ObjectType.EPISODE)] if is_pending(episode)
        )


async def build_agentic_view(store: Store, tenant_id: TenantId) -> AgenticView:
    objects = await store.list_objects(tenant_id, limit=OBJECT_WINDOW)
    by_type: dict[str, list[ContextObject]] = {str(t): [] for t in AGENTIC_TYPES}
    for obj in objects:
        if obj.type in AGENTIC_TYPES:
            visible = obj
            if obj.type is ObjectType.EPISODE and obj.content.startswith(PREFIX):
                visible = obj.model_copy(deep=True)
                try:
                    visible.content = await decrypt_episode(store, tenant_id, obj)
                except EpisodeKeyUnavailable:
                    visible.content = "[episode content unavailable: key shredded]"
            by_type[str(obj.type)].append(visible)

    episode_ids = {o.id for o in by_type[str(ObjectType.EPISODE)]}
    derived_from: dict[str, list[ContextObject]] = {}
    for obj in objects:
        for source in obj.provenance.source_object_ids:
            if source in episode_ids:
                derived_from.setdefault(source, []).append(obj)
    # Read the MENTIONS edges back out. Model-assisted extraction writes them and,
    # until now, nothing ever read them again — an edge nothing renders is a link
    # the user cannot see.
    by_id = {o.id: o for o in objects}
    mentioned_by: dict[str, list[ContextObject]] = {}
    for fact in by_type[str(ObjectType.FACT)]:
        for edge in await store.edges_from(tenant_id, fact.id):
            if edge.type is EdgeType.MENTIONS and edge.dst_id in by_id:
                mentioned_by.setdefault(edge.dst_id, []).append(fact)

    return AgenticView(
        by_type=by_type, derived_from=derived_from, mentioned_by=mentioned_by
    )
