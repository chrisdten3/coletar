"""The typed grammar a history question compiles to (SCOPE §6, §8.2).

Every field here is already an enum somewhere else in the schema. That is the
point: the graph's own vocabulary *is* the query language, so a question about
history can be expressed without inventing a parallel model, and a model asked to
turn English into a query has a closed set of tokens to choose from rather than a
blank page.

Why a schema at all, when a model could read the log and narrate an answer:

  * **Constraint #4.** An object we cannot explain should not exist, and the same
    is true of a number. A `QueryResult` names the query that produced it and the
    events each point was counted from, so "why is this 14" terminates in rows.
  * **Constraint #7.** Stored memory is data, never instructions. A model that
    ranges freely over the graph is reading user content and deciding what to do
    next; a model that emits one of these has read the *question* and nothing else.
  * It is editable. A compiled query renders in the UI as controls, so a user who
    disagrees with the interpretation fixes it instead of rephrasing and hoping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from coletar.schema.events import Actor, EventType
from coletar.schema.objects import (
    ExtractionMethod,
    LocalityMode,
    MemoryKind,
    ObjectType,
    Provider,
    Sensitivity,
)


class Bucket(StrEnum):
    """Granularity of the time axis. Deliberately coarse -- a context graph
    measured per minute is noise, and a user asking "has this changed" is asking
    about days and weeks."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"

    @property
    def delta(self) -> timedelta:
        return {
            Bucket.DAY: timedelta(days=1),
            Bucket.WEEK: timedelta(days=7),
            Bucket.MONTH: timedelta(days=30),
        }[self]

    def floor(self, when: datetime) -> datetime:
        """The start of the bucket `when` falls in, in UTC.

        Weeks start Monday, which is arbitrary but has to be written down
        somewhere or two callers will disagree by a day.
        """
        day = when.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        if self is Bucket.DAY:
            return day
        if self is Bucket.WEEK:
            return day - timedelta(days=day.weekday())
        return day.replace(day=1)


class Metric(StrEnum):
    """What is being counted.

    Split into two families, because they are answered differently and a caller
    should know which kind of claim a chart is making:

      * **Flow** metrics count events in a bucket. "I wrote nine facts in March."
      * **Stock** metrics measure object state at the *end* of a bucket, replayed
        from the log's `after` snapshots. "I had 214 active facts on 31 March."

    A stock metric is the expensive one and the honest one: it is the only way to
    chart "what did my graph look like then" without trusting today's rows to
    describe the past.
    """

    # Flow.
    WRITES = "writes"
    REVISIONS = "revisions"
    RETIREMENTS = "retirements"
    SUPERSESSIONS = "supersessions"
    CORROBORATIONS = "corroborations"
    REVIEWS = "reviews"
    RESCOPES = "rescopes"
    RETRIEVALS = "retrievals"
    OBJECTS_SERVED = "objects_served"
    TOKENS_SERVED = "tokens_served"
    EXTRACTION_FAILURES = "extraction_failures"

    # Stock.
    ACTIVE_OBJECTS = "active_objects"
    MEAN_CONFIDENCE = "mean_confidence"
    NEVER_READ = "never_read"
    UNREVIEWED = "unreviewed"

    @property
    def is_stock(self) -> bool:
        return self in _STOCK_METRICS

    @property
    def event_type(self) -> EventType | None:
        """The log row a flow metric counts, when it maps to exactly one."""
        return _METRIC_EVENT.get(self)

    @property
    def counts_corrections(self) -> bool:
        """`SUPERSESSIONS` is the one metric that is not a row type.

        Nothing in the codebase emits `object.superseded`: a correction is
        written as a new object carrying `supersedes`, so the log records it as
        an `object.created` whose payload names what it replaced. Constraint #5
        is satisfied -- the write did append its event -- but the event type is
        not the one the name suggests.

        Counting the create-with-`supersedes` reads the log that exists rather
        than requiring every historical correction to be re-emitted under a new
        type, and it keeps working if `OBJECT_SUPERSEDED` is ever introduced,
        because `rollup` accepts both.
        """
        return self is Metric.SUPERSESSIONS


_STOCK_METRICS: frozenset[Metric] = frozenset(
    {Metric.ACTIVE_OBJECTS, Metric.MEAN_CONFIDENCE, Metric.NEVER_READ, Metric.UNREVIEWED}
)

_METRIC_EVENT: dict[Metric, EventType] = {
    Metric.WRITES: EventType.OBJECT_CREATED,
    Metric.REVISIONS: EventType.OBJECT_UPDATED,
    Metric.RETIREMENTS: EventType.OBJECT_RETIRED,
    Metric.SUPERSESSIONS: EventType.OBJECT_SUPERSEDED,
    Metric.CORROBORATIONS: EventType.OBJECT_CORROBORATED,
    Metric.REVIEWS: EventType.OBJECT_REVIEWED,
    Metric.RESCOPES: EventType.OBJECT_RESCOPED,
    Metric.RETRIEVALS: EventType.RETRIEVAL_TRACE,
    Metric.OBJECTS_SERVED: EventType.RETRIEVAL_TRACE,
    Metric.TOKENS_SERVED: EventType.RETRIEVAL_TRACE,
    Metric.EXTRACTION_FAILURES: EventType.EXTRACTION_UNAVAILABLE,
}


class GroupBy(StrEnum):
    """One series per distinct value of this field.

    `ACTOR` and `PROVIDER` are the two that earn their place on the default
    dashboard: "the model wrote this" versus "I did", and "Claude asked" versus
    "ChatGPT asked", are the questions a portable graph exists to answer and no
    single-product memory feature can.
    """

    NONE = "none"
    ACTOR = "actor"
    PROVIDER = "provider"
    OBJECT_TYPE = "object_type"
    MEMORY_KIND = "memory_kind"
    EXTRACTION_METHOD = "extraction_method"
    SENSITIVITY = "sensitivity"
    LOCALITY_MODE = "locality_mode"
    SCOPE = "scope"
    SURFACE = "surface"


class ObjectFilter(BaseModel):
    """Narrowing, applied to the object a row is *about*.

    Every list is OR within itself and AND across fields, which is the only
    convention users reliably predict. Empty means "no opinion", never "none of
    them" -- a filter that silently excluded everything when left blank would make
    an empty chart indistinguishable from an empty graph.
    """

    model_config = ConfigDict(frozen=True)

    types: list[ObjectType] = Field(default_factory=list)
    kinds: list[MemoryKind] = Field(default_factory=list)
    methods: list[ExtractionMethod] = Field(default_factory=list)
    actors: list[Actor] = Field(default_factory=list)
    providers: list[Provider] = Field(default_factory=list)
    sensitivities: list[Sensitivity] = Field(default_factory=list)
    locality_modes: list[LocalityMode] = Field(default_factory=list)
    #: `str(Scope)` values, e.g. "project:proj_ledger_rewrite".
    scopes: list[str] = Field(default_factory=list)
    #: Which assistant asked, for retrieval metrics. Distinct from `providers`,
    #: which is who *wrote* the object -- see RetrievalTrace.provider.
    surfaces: list[str] = Field(default_factory=list)
    confidence_min: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_max: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Case-insensitive substring over object content. Lexical on purpose: a
    #: filter a user cannot predict the behaviour of is worse than no filter.
    contains: str | None = None
    object_ids: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self == ObjectFilter()

    def describe(self) -> list[str]:
        """Human-readable clauses, for the "showing X" line above a chart."""
        parts: list[str] = []
        named: list[tuple[str, list[Any]]] = [
            ("type", list(self.types)),
            ("kind", list(self.kinds)),
            ("extracted by", list(self.methods)),
            ("actor", list(self.actors)),
            ("provider", list(self.providers)),
            ("sensitivity", list(self.sensitivities)),
            ("reach", list(self.locality_modes)),
            ("scope", list(self.scopes)),
            ("read by", list(self.surfaces)),
            ("id", list(self.object_ids)),
        ]
        for label, values in named:
            if values:
                parts.append(f"{label} is {' or '.join(str(v) for v in values)}")
        if self.confidence_min is not None:
            parts.append(f"confidence ≥ {self.confidence_min:.2f}")
        if self.confidence_max is not None:
            parts.append(f"confidence ≤ {self.confidence_max:.2f}")
        if self.contains:
            parts.append(f"text contains “{self.contains}”")
        return parts


#: A ceiling on how far back a single query reads. The log is append-only and
#: unbounded; a dashboard that silently walks all of it gets slower every week
#: until someone notices. Two years is past any window the UI offers.
MAX_WINDOW_DAYS = 730
DEFAULT_WINDOW_DAYS = 90


class HistoryQuery(BaseModel):
    """One question about the graph's history, in a form that can be executed,
    rendered as controls, edited, saved as a watch, and printed in an export."""

    model_config = ConfigDict(frozen=True)

    metric: Metric = Metric.WRITES
    group_by: GroupBy = GroupBy.NONE
    bucket: Bucket = Bucket.WEEK
    since: datetime | None = None
    until: datetime | None = None
    window_days: int = Field(default=DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS)
    filter: ObjectFilter = ObjectFilter()
    #: Cap on the number of series returned; the rest collapse into "other" so a
    #: high-cardinality group_by degrades into a readable chart instead of 400 lines.
    max_series: int = Field(default=8, ge=1, le=32)

    @model_validator(mode="after")
    def _check_range(self) -> HistoryQuery:
        if self.since is not None and self.until is not None and self.since >= self.until:
            raise ValueError("since must be before until")
        return self

    def resolve_range(self, *, now: datetime | None = None) -> tuple[datetime, datetime]:
        """Absolute bounds, half-open on the left: `(start, end]`.

        `window_days` is a convenience for the UI's "last 90 days" control and
        loses to explicit bounds whenever both are present.
        """
        end = self.until or now or datetime.now(UTC)
        end = end.astimezone(UTC) if end.tzinfo else end.replace(tzinfo=UTC)
        if self.since is not None:
            start = (
                self.since.astimezone(UTC)
                if self.since.tzinfo
                else self.since.replace(tzinfo=UTC)
            )
        else:
            start = end - timedelta(days=self.window_days)
        span = (end - start).days
        if span > MAX_WINDOW_DAYS:
            start = end - timedelta(days=MAX_WINDOW_DAYS)
        return start, end

    def describe(self) -> str:
        """One sentence, shown under the compiled query so a user can check the
        interpretation without reading JSON."""
        noun = self.metric.value.replace("_", " ")
        sentence = f"{noun} per {self.bucket.value}"
        if self.group_by is not GroupBy.NONE:
            sentence += f", grouped by {self.group_by.value.replace('_', ' ')}"
        clauses = self.filter.describe()
        if clauses:
            sentence += ", where " + " and ".join(clauses)
        return sentence


class SeriesPoint(BaseModel):
    """One bucket. `event_ids` is the citation, not a debugging aid: it is what
    turns a number on a chart into rows a user can open."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    value: float
    #: Contributing events, capped -- a citation is a way in, not a full export.
    event_ids: list[str] = Field(default_factory=list)
    #: How many events actually contributed, when `event_ids` was truncated.
    event_count: int = 0


class Series(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    points: list[SeriesPoint] = Field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(p.value for p in self.points)


class QueryResult(BaseModel):
    """The answer, carrying the question. Serialised straight to the client, and
    into the audit export, so a saved chart is never separated from its query."""

    model_config = ConfigDict(frozen=True)

    query: HistoryQuery
    explanation: str
    start: datetime
    end: datetime
    series: list[Series] = Field(default_factory=list)
    #: Events scanned, so a user can tell "nothing happened" from "the window
    #: reached the log limit and the chart is a partial view".
    events_scanned: int = 0
    truncated: bool = False

    @property
    def total(self) -> float:
        return sum(s.total for s in self.series)

    @property
    def headline(self) -> float:
        """The one number to put above the chart.

        Summing a stock metric is meaningless -- adding "214 active objects in
        March" to "230 in April" produces 444 of nothing -- so a stock series
        reports its most recent bucket instead. Getting this wrong is how a
        dashboard ends up confidently stating a figure that is not a quantity of
        anything, so the choice lives here rather than in each caller.
        """
        if not self.series:
            return 0.0
        if self.query.metric.is_stock:
            if self.query.metric is Metric.MEAN_CONFIDENCE:
                latest = [s.points[-1].value for s in self.series if s.points]
                return round(sum(latest) / len(latest), 4) if latest else 0.0
            return sum(s.points[-1].value for s in self.series if s.points)
        return self.total

    @property
    def headline_label(self) -> str:
        return "now" if self.query.metric.is_stock else "total"
