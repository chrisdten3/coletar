"""Executing a `HistoryQuery` against the Event/Revision Log (SCOPE §6).

The log is read exactly once per query and replayed forward. That single pass
serves both metric families:

  * a **flow** metric counts matching events as they go past;
  * a **stock** metric reads the replayed object state at each bucket boundary.

Replaying rather than querying today's rows is the whole point. `list_objects`
tells you what a fact says now; only the log can tell you what it said in March,
and "what did my assistant know about me then" is the question this page exists
to answer. It is affordable because `events.REVISION_EVENTS` carry a full `after`
snapshot -- a design decision made for undo that turns out to make the graph a
time-series database.

Aggregation happens here, in the backend, and not in the browser. The page it
replaced shipped two thousand events to the client and counted them in JS, which
is fine at two thousand and wrong at two hundred thousand. Pushing the bucketing
further down -- into SQL, as a `date_trunc` and a `GROUP BY` behind a new `Store`
method -- is the next move when a tenant's log outgrows one pass; nothing above
this module would need to change, which is the reason the grammar is typed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from coletar.history.query import (
    Bucket,
    GroupBy,
    HistoryQuery,
    Metric,
    ObjectFilter,
    QueryResult,
    Series,
    SeriesPoint,
)
from coletar.schema.events import REVISION_EVENTS, Event, EventType
from coletar.schema.objects import ObjectType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Matches `temporal._LOG_LIMIT`. One number, two modules, same reason: a read of
#: "the whole log" has to have a bound, and the two must not disagree about it.
_SCAN_LIMIT = 200_000

#: Citations per bucket. Enough to open an investigation, not enough to make the
#: response a second copy of the log.
_MAX_CITATIONS = 12

_OTHER = "other"


def _utc(when: datetime) -> datetime:
    return when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC)


def _buckets(start: datetime, end: datetime, bucket: Bucket) -> list[datetime]:
    """Every bucket start in `(start, end]`, including empty ones.

    Empty buckets are emitted deliberately. A chart that omits them draws a
    straight line through a month of silence, which reads as "steady" when the
    truth is "nothing happened".
    """
    out: list[datetime] = []
    cursor = bucket.floor(start)
    guard = 0
    while cursor <= end and guard < 5000:
        out.append(cursor)
        if bucket is Bucket.MONTH:
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        else:
            cursor = cursor + bucket.delta
        guard += 1
    return out


@dataclass
class _ObjectState:
    """The replayed present-at-the-time view of one object."""

    snapshot: dict[str, Any]
    reviewed: bool = False
    last_read: datetime | None = None
    retired: bool = False
    superseded: bool = False

    @property
    def active(self) -> bool:
        return not self.retired and not self.superseded


def _dig(snapshot: dict[str, Any], *path: str) -> Any:
    node: Any = snapshot
    for step in path:
        if not isinstance(node, dict):
            return None
        node = node.get(step)
    return node


def _scope_of(snapshot: dict[str, Any]) -> str:
    scope = snapshot.get("scope") or {}
    if not isinstance(scope, dict) or scope.get("type") == "global":
        return "global"
    return f"project:{scope.get('id')}"


def _matches(snapshot: dict[str, Any] | None, event: Event, filt: ObjectFilter) -> bool:
    """Does this row pass the filter?

    Matched against the object *as it was* when the event fired, not as it is
    now. A chart of "writes where confidence < 0.5" must count the write that was
    low-confidence at the time even if a later corroboration raised it -- the
    alternative silently rewrites history to agree with the present, which is the
    one failure mode an audit surface cannot have.
    """
    if filt.actors and str(event.actor) not in {str(a) for a in filt.actors}:
        return False

    if filt.surfaces:
        surface = _dig(event.detail, "provider") or _dig(event.detail, "surface")
        if str(surface) not in {str(s) for s in filt.surfaces}:
            return False

    if snapshot is None:
        # A row with no object behind it (a retrieval trace, a migration) can only
        # be excluded by the event-level clauses above. Object clauses do not
        # apply rather than excluding it, for the same reason an empty filter
        # means "no opinion".
        return not _has_object_clauses(filt)

    if filt.object_ids and snapshot.get("id") not in set(filt.object_ids):
        return False
    if filt.types and str(snapshot.get("type")) not in {str(t) for t in filt.types}:
        return False
    if filt.kinds and str(snapshot.get("kind")) not in {str(k) for k in filt.kinds}:
        return False
    if filt.methods and str(snapshot.get("extraction_method")) not in {
        str(m) for m in filt.methods
    }:
        return False
    if filt.sensitivities and str(snapshot.get("sensitivity")) not in {
        str(s) for s in filt.sensitivities
    }:
        return False
    if filt.locality_modes and str(_dig(snapshot, "locality", "mode")) not in {
        str(m) for m in filt.locality_modes
    }:
        return False
    if filt.scopes and _scope_of(snapshot) not in set(filt.scopes):
        return False
    if filt.providers:
        provider = _dig(snapshot, "provenance", "provider")
        if str(provider) not in {str(p) for p in filt.providers}:
            return False

    confidence = snapshot.get("confidence")
    if filt.confidence_min is not None and (
        confidence is None or float(confidence) < filt.confidence_min
    ):
        return False
    if filt.confidence_max is not None and (
        confidence is None or float(confidence) > filt.confidence_max
    ):
        return False

    if filt.contains:
        content = str(snapshot.get("content") or "")
        if filt.contains.lower() not in content.lower():
            return False

    return True


def _has_object_clauses(filt: ObjectFilter) -> bool:
    return bool(
        filt.object_ids
        or filt.types
        or filt.kinds
        or filt.methods
        or filt.sensitivities
        or filt.locality_modes
        or filt.scopes
        or filt.providers
        or filt.contains
        or filt.confidence_min is not None
        or filt.confidence_max is not None
    )


def _group_key(group_by: GroupBy, event: Event, snapshot: dict[str, Any] | None) -> str:
    if group_by is GroupBy.NONE:
        return "all"
    if group_by is GroupBy.ACTOR:
        return str(event.actor)
    if group_by is GroupBy.SURFACE:
        return str(_dig(event.detail, "surface") or "unknown")
    if group_by is GroupBy.PROVIDER:
        # Who *asked* for a retrieval; who *wrote* anything else. The Event's own
        # `provider` is the store's, which answers neither.
        if event.type is EventType.RETRIEVAL_TRACE:
            return str(_dig(event.detail, "provider") or "unattributed")
        if snapshot is not None:
            return str(_dig(snapshot, "provenance", "provider") or event.provider)
        return str(event.provider)
    if snapshot is None:
        return "unknown"
    if group_by is GroupBy.OBJECT_TYPE:
        return str(snapshot.get("type") or "unknown")
    if group_by is GroupBy.MEMORY_KIND:
        return str(snapshot.get("kind") or "—")
    if group_by is GroupBy.EXTRACTION_METHOD:
        return str(snapshot.get("extraction_method") or "unknown")
    if group_by is GroupBy.SENSITIVITY:
        return str(snapshot.get("sensitivity") or "normal")
    if group_by is GroupBy.LOCALITY_MODE:
        return str(_dig(snapshot, "locality", "mode") or "synced")
    if group_by is GroupBy.SCOPE:
        return _scope_of(snapshot)
    return "all"


def _state_group_key(group_by: GroupBy, state: _ObjectState) -> str:
    """Grouping for stock metrics, where there is object state but no event."""
    if group_by is GroupBy.NONE:
        return "all"
    snapshot = state.snapshot
    if group_by is GroupBy.PROVIDER:
        return str(_dig(snapshot, "provenance", "provider") or "unknown")
    if group_by is GroupBy.OBJECT_TYPE:
        return str(snapshot.get("type") or "unknown")
    if group_by is GroupBy.MEMORY_KIND:
        return str(snapshot.get("kind") or "—")
    if group_by is GroupBy.EXTRACTION_METHOD:
        return str(snapshot.get("extraction_method") or "unknown")
    if group_by is GroupBy.SENSITIVITY:
        return str(snapshot.get("sensitivity") or "normal")
    if group_by is GroupBy.LOCALITY_MODE:
        return str(_dig(snapshot, "locality", "mode") or "synced")
    if group_by is GroupBy.SCOPE:
        return _scope_of(snapshot)
    # ACTOR and SURFACE are properties of an event, not of an object. Rather than
    # inventing an answer, a stock metric grouped that way collapses to one series.
    return "all"


def _apply(state: dict[str, _ObjectState], event: Event) -> None:
    """Fold one event into the replayed world."""
    if event.type is EventType.RETRIEVAL_TRACE:
        for object_id in _dig(event.detail, "returned_ids") or []:
            existing = state.get(str(object_id))
            if existing is not None:
                existing.last_read = event.at
        return

    if event.object_id is None:
        return

    if event.type is EventType.OBJECT_REVIEWED:
        existing = state.get(event.object_id)
        if existing is not None:
            existing.reviewed = True
        return

    if event.type not in REVISION_EVENTS or event.after is None:
        return

    entry = state.get(event.object_id)
    if entry is None:
        entry = _ObjectState(snapshot=dict(event.after))
        state[event.object_id] = entry
    else:
        entry.snapshot = dict(event.after)

    if event.type is EventType.OBJECT_RETIRED or event.after.get("retired_at"):
        entry.retired = True
    # A correction retires what it replaces: the superseded id is named by the
    # *new* object, so the flag lands on the old one.
    replaced = event.after.get("supersedes")
    if replaced and (prior := state.get(str(replaced))) is not None:
        prior.superseded = True


def _stock_value(metric: Metric, members: list[_ObjectState]) -> float:
    if metric is Metric.ACTIVE_OBJECTS:
        return float(sum(1 for m in members if m.active))
    if metric is Metric.NEVER_READ:
        return float(sum(1 for m in members if m.active and m.last_read is None))
    if metric is Metric.UNREVIEWED:
        return float(sum(1 for m in members if m.active and not m.reviewed))
    if metric is Metric.MEAN_CONFIDENCE:
        scores = [
            float(m.snapshot.get("confidence") or 0.0) for m in members if m.active
        ]
        return round(sum(scores) / len(scores), 4) if scores else 0.0
    return 0.0


def _counts_for(metric: Metric, event: Event) -> float:
    """How much one matching event contributes.

    Every flow metric is one-per-event except the two that measure how much
    context was actually handed over, which are counted in objects and in
    tokens rather than in searches. `tokens_served` is the one the cost view
    multiplies: it is the same figure the plan meter already reports, so a
    price drawn from it agrees with the number in the sidebar.
    """
    if metric is Metric.OBJECTS_SERVED:
        return float(len(_dig(event.detail, "returned_ids") or []))
    if metric is Metric.TOKENS_SERVED:
        return float(_dig(event.detail, "token_estimate") or 0)
    return 1.0


async def run_query(
    store: Store,
    tenant_id: TenantId,
    query: HistoryQuery,
    *,
    now: datetime | None = None,
) -> QueryResult:
    """Execute one typed question. No writes, ever -- reading history must not
    append to it, or the dashboard becomes its own biggest data source."""
    start, end = query.resolve_range(now=now)
    events = await store.list_events(tenant_id, until=end, limit=_SCAN_LIMIT)
    ordered = sorted(events, key=lambda e: e.at)
    truncated = len(events) >= _SCAN_LIMIT

    bucket_starts = _buckets(start, end, query.bucket)
    state: dict[str, _ObjectState] = {}

    # (series key, bucket start) -> running value and citations.
    totals: dict[tuple[str, datetime], float] = defaultdict(float)
    citations: dict[tuple[str, datetime], list[str]] = defaultdict(list)
    counts: dict[tuple[str, datetime], int] = defaultdict(int)
    scanned = 0

    if query.metric.is_stock:
        cursor = 0
        for boundary in bucket_starts:
            # Everything that had happened by the END of this bucket, so the point
            # reads "what the graph looked like on the last day of the week".
            edge = min(end, _bucket_end(boundary, query.bucket))
            while cursor < len(ordered) and _utc(ordered[cursor].at) <= edge:
                _apply(state, ordered[cursor])
                scanned += 1
                cursor += 1
            grouped: dict[str, list[_ObjectState]] = defaultdict(list)
            for entry in state.values():
                if not _matches(entry.snapshot, _synthetic_event(entry), query.filter):
                    continue
                grouped[_state_group_key(query.group_by, entry)].append(entry)
            for key, members in grouped.items():
                totals[(key, boundary)] = _stock_value(query.metric, members)
                counts[(key, boundary)] = len(members)
    else:
        wanted = query.metric.event_type
        for event in ordered:
            at = _utc(event.at)
            # Replay everything, count only the window: an event from before the
            # window still shapes the object state a later filter matches against.
            _apply(state, event)
            scanned += 1
            if at <= start or at > end:
                continue
            if query.metric.counts_corrections:
                # A correction is a create that names what it replaced. See
                # `Metric.counts_corrections` -- both spellings count, so this
                # keeps working if the dedicated event type ever starts firing.
                is_correction = (
                    event.type is EventType.OBJECT_SUPERSEDED
                    or (event.after or {}).get("supersedes") is not None
                )
                if not is_correction:
                    continue
            elif wanted is not None and event.type is not wanted:
                continue
            snapshot = event.after or event.before
            if snapshot is None and event.object_id:
                known = state.get(event.object_id)
                snapshot = known.snapshot if known else None
            if not _matches(snapshot, event, query.filter):
                continue
            slot = (
                _group_key(query.group_by, event, snapshot),
                query.bucket.floor(at),
            )
            totals[slot] += _counts_for(query.metric, event)
            counts[slot] += 1
            if len(citations[slot]) < _MAX_CITATIONS:
                citations[slot].append(event.id)

    series = _assemble(query, bucket_starts, totals, citations, counts)
    return QueryResult(
        query=query,
        explanation=query.describe(),
        start=start,
        end=end,
        series=series,
        events_scanned=scanned,
        truncated=truncated,
    )


def _bucket_end(boundary: datetime, bucket: Bucket) -> datetime:
    if bucket is Bucket.MONTH:
        return (boundary + timedelta(days=32)).replace(day=1) - timedelta(microseconds=1)
    return boundary + bucket.delta - timedelta(microseconds=1)


def _synthetic_event(entry: _ObjectState) -> Event:
    """A stand-in so stock filtering can reuse `_matches`.

    Stock metrics have object state but no event, and the alternative -- a second
    filter implementation for the state path -- is how two code paths start
    disagreeing about what "confidence below 0.5" means.
    """
    return Event(type=EventType.OBJECT_UPDATED, object_id=str(entry.snapshot.get("id") or ""))


def _assemble(
    query: HistoryQuery,
    bucket_starts: list[datetime],
    totals: dict[tuple[str, datetime], float],
    citations: dict[tuple[str, datetime], list[str]],
    counts: dict[tuple[str, datetime], int],
) -> list[Series]:
    """Dense series, biggest first, with the tail collapsed into `other`."""
    keys = {key for key, _ in totals}
    if not keys:
        keys = {"all"}

    weight = {
        key: sum(totals.get((key, b), 0.0) for b in bucket_starts) for key in keys
    }
    ranked = sorted(keys, key=lambda k: (-weight[k], k))
    kept, spilled = ranked[: query.max_series], ranked[query.max_series :]

    series: list[Series] = []
    for key in kept:
        series.append(
            Series(
                key=key,
                points=[
                    SeriesPoint(
                        at=b,
                        value=round(totals.get((key, b), 0.0), 4),
                        event_ids=citations.get((key, b), []),
                        event_count=counts.get((key, b), 0),
                    )
                    for b in bucket_starts
                ],
            )
        )
    if spilled:
        series.append(
            Series(
                key=_OTHER,
                points=[
                    SeriesPoint(
                        at=b,
                        value=round(sum(totals.get((k, b), 0.0) for k in spilled), 4),
                        event_ids=[],
                        event_count=sum(counts.get((k, b), 0) for k in spilled),
                    )
                    for b in bucket_starts
                ],
            )
        )
    return series


# ---------------------------------------------------------------------------
# A single fact, as a series.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifetimePoint:
    """One step in one object's life. `content` is what it said *after* the step,
    which is what makes the series readable as a story rather than a diff log."""

    at: datetime
    event_id: str
    object_id: str
    event_type: str
    actor: str
    provider: str
    confidence: float
    content: str
    previous: str | None
    reads_since: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "event_id": self.event_id,
            "object_id": self.object_id,
            "event": self.event_type,
            "actor": self.actor,
            "provider": self.provider,
            "confidence": self.confidence,
            "content": self.content,
            "previous": self.previous,
            "reads_since": self.reads_since,
            "changed": self.previous is not None and self.previous != self.content,
        }


@dataclass(frozen=True)
class Lifetime:
    """One fact's whole history, including the corrections that replaced it.

    Follows `supersedes` backwards and forwards, because a user asking "how has
    this changed" means the statement, not the row: "the launch is 10 October"
    becoming "the launch is 24 October" is one fact with two ids, and showing it
    as two unrelated objects is the failure the Context Inspector exists to avoid.
    """

    object_id: str
    chain: list[str]
    points: list[LifetimePoint] = field(default_factory=list)
    reads: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "chain": self.chain,
            "points": [p.as_dict() for p in self.points],
            "reads": self.reads,
        }


async def object_lifetime(
    store: Store, tenant_id: TenantId, object_id: str
) -> Lifetime:
    """Every recorded step of one fact, oldest first."""
    events = await store.list_events(tenant_id, limit=_SCAN_LIMIT)
    ordered = sorted(events, key=lambda e: e.at)

    # Build the supersession chain in both directions before selecting rows.
    replaces: dict[str, str] = {}
    for event in ordered:
        if event.after and event.after.get("supersedes"):
            replaces[str(event.after["id"])] = str(event.after["supersedes"])
    replaced_by = {old: new for new, old in replaces.items()}

    chain = [object_id]
    cursor = object_id
    while (older := replaces.get(cursor)) is not None and older not in chain:
        chain.insert(0, older)
        cursor = older
    cursor = object_id
    while (newer := replaced_by.get(cursor)) is not None and newer not in chain:
        chain.append(newer)
        cursor = newer
    members = set(chain)

    points: list[LifetimePoint] = []
    reads: list[dict[str, Any]] = []
    reads_since = 0
    for event in ordered:
        if event.type is EventType.RETRIEVAL_TRACE:
            served = {str(i) for i in (_dig(event.detail, "returned_ids") or [])}
            if served & members:
                reads_since += 1
                reads.append(
                    {
                        "at": event.at.isoformat(),
                        "event_id": event.id,
                        "surface": _dig(event.detail, "surface") or "unknown",
                        "provider": _dig(event.detail, "provider") or "unattributed",
                        "query_text": _dig(event.detail, "query_text"),
                        "query_digest": _dig(event.detail, "query_digest"),
                        "returned": len(served),
                    }
                )
            continue
        if event.object_id not in members or event.after is None:
            continue

        content = str(event.after.get("content") or "")
        # What this step replaced. On an in-place edit that is the event's own
        # `before`; on a correction there is no `before`, because the correction
        # is a *new* object -- so the previous wording is the last thing the
        # object it supersedes said. Without this, every correction renders as a
        # create with nothing to diff against, which is the one view a user
        # opening "how has this changed" came for.
        previous = (event.before or {}).get("content") if event.before else None
        replaced = event.after.get("supersedes")
        if previous is None and replaced:
            earlier = [p for p in points if p.object_id == str(replaced)]
            if earlier:
                previous = earlier[-1].content

        points.append(
            LifetimePoint(
                at=event.at,
                event_id=event.id,
                object_id=str(event.object_id),
                # A create that names what it replaced is a correction, and
                # labelling it "object created" in a history view is accurate
                # and useless.
                event_type="object.corrected"
                if replaced and event.type is EventType.OBJECT_CREATED
                else str(event.type),
                actor=str(event.actor),
                provider=str(
                    _dig(event.after, "provenance", "provider") or event.provider
                ),
                confidence=float(event.after.get("confidence") or 0.0),
                content=content,
                previous=previous,
                reads_since=reads_since,
            )
        )
        reads_since = 0

    reads.reverse()
    return Lifetime(object_id=object_id, chain=chain, points=points, reads=reads)


# ---------------------------------------------------------------------------
# Reach: which surfaces have actually been served which facts.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReachRow:
    object_id: str
    content: str
    locality_mode: str
    allowed: list[str]
    read_by: dict[str, int]
    last_read: datetime | None
    never_read: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "content": self.content,
            "locality_mode": self.locality_mode,
            "allowed": self.allowed,
            "read_by": self.read_by,
            "last_read": self.last_read.isoformat() if self.last_read else None,
            "never_read": self.never_read,
        }


async def reach_report(
    store: Store, tenant_id: TenantId, *, limit: int = 200
) -> dict[str, Any]:
    """"Which assistant has seen this fact", answered rather than inferred.

    Locality says which surfaces *may* read an object; this is what they *did*
    read. The gap between the two is the interesting part, and it runs both ways:
    a fact nobody has read is context that is not earning its place, and a fact
    read by a surface its reach no longer allows is a propagation bug with a date
    attached.
    """
    events = await store.list_events(tenant_id, limit=_SCAN_LIMIT)
    objects = await store.list_objects(tenant_id, limit=limit)

    read_by: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    last_read: dict[str, datetime] = {}
    by_surface: dict[str, int] = defaultdict(int)
    for event in events:
        if event.type is not EventType.RETRIEVAL_TRACE:
            continue
        who = str(_dig(event.detail, "provider") or "unattributed")
        by_surface[who] += 1
        for object_id in _dig(event.detail, "returned_ids") or []:
            key = str(object_id)
            read_by[key][who] += 1
            if key not in last_read or event.at > last_read[key]:
                last_read[key] = event.at

    rows = [
        ReachRow(
            object_id=obj.id,
            content=obj.content,
            locality_mode=str(obj.locality.mode),
            allowed=sorted(str(s) for s in obj.locality.surfaces),
            read_by=dict(read_by.get(obj.id, {})),
            last_read=last_read.get(obj.id),
            never_read=obj.id not in read_by,
        )
        for obj in objects
        if obj.type is not ObjectType.EPISODE
    ]
    rows.sort(key=lambda r: (r.never_read, -sum(r.read_by.values())))
    return {
        "rows": [r.as_dict() for r in rows],
        "by_surface": dict(sorted(by_surface.items(), key=lambda kv: -kv[1])),
        "never_read": sum(1 for r in rows if r.never_read),
        "total": len(rows),
    }
