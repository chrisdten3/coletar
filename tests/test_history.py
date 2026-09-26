"""History as an observability surface (SCOPE §6).

Three things have to hold or the page is decorative:

  * a number on a chart is derived from named events and can be traced back;
  * a metric about the past reads the past, not today's rows projected onto it;
  * an English question turns into a query the user can see, and a question the
    compiler did not understand says so rather than charting a default.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coletar.history.clusters import cluster_mass, movers
from coletar.history.nl import EXAMPLE_QUESTIONS, compile_question
from coletar.history.query import (
    Bucket,
    GroupBy,
    HistoryQuery,
    Metric,
    ObjectFilter,
)
from coletar.history.rollup import object_lifetime, reach_report, run_query
from coletar.retrieval.trace import ComponentVersions, RetrievalTrace
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    ExtractionMethod,
    Memory,
    MemoryKind,
    Provider,
)
from coletar.store.memory import InMemoryStore
from conftest import TENANT

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


async def _write(
    store: InMemoryStore,
    content: str,
    *,
    days_ago: float,
    actor: Actor = Actor.USER,
    provider: Provider = Provider.COLETAR,
    method: ExtractionMethod = ExtractionMethod.EXPLICIT_STATEMENT,
    confidence: float = 0.9,
    kind: MemoryKind = MemoryKind.FACT,
    supersedes: str | None = None,
):
    memory = Memory.from_write(
        content,
        kind=kind,
        extraction_method=method,
        provider=provider,
        confidence=confidence,
        supersedes=supersedes,
    )
    when = _days_ago(days_ago)
    memory.created_at = when
    memory.updated_at = when
    return await store.put_object(
        TENANT,
        memory,
        event=Event(
            type=EventType.OBJECT_CREATED,
            object_id=memory.id,
            actor=actor,
            provider=provider,
            at=when,
        ),
    )


async def _trace(
    store: InMemoryStore,
    *,
    days_ago: float,
    provider: Provider,
    returned: list[str],
    surface: str = "mcp",
) -> Event:
    event = Event(
        type=EventType.RETRIEVAL_TRACE,
        actor=Actor.CONNECTOR,
        provider=provider,
        at=_days_ago(days_ago),
        detail=RetrievalTrace(
            query_digest="abc123",
            scope="any",
            surface=surface,
            provider=str(provider),
            principal=None,
            top_k=12,
            token_budget=2000,
            versions=ComponentVersions(embedder="test"),
            returned_ids=returned,
        ).as_detail(),
    )
    await store.append_event(TENANT, event)
    return event


# ---------------------------------------------------------------------------
# Buckets and ranges.
# ---------------------------------------------------------------------------


def test_week_buckets_start_on_monday():
    # Friday 25 September 2026 floors to Monday the 21st.
    friday = datetime(2026, 9, 25, 17, 30, tzinfo=UTC)
    assert Bucket.WEEK.floor(friday) == datetime(2026, 9, 21, tzinfo=UTC)


def test_month_buckets_floor_to_the_first():
    assert Bucket.MONTH.floor(datetime(2026, 9, 25, tzinfo=UTC)) == datetime(
        2026, 9, 1, tzinfo=UTC
    )


def test_window_is_clamped_to_the_scan_ceiling():
    """A query cannot quietly ask for the whole log. The bound exists because
    the dashboard walks what it asks for."""
    query = HistoryQuery(since=datetime(2015, 1, 1, tzinfo=UTC))
    start, end = query.resolve_range(now=NOW)
    assert (end - start).days <= 730


# ---------------------------------------------------------------------------
# Flow metrics.
# ---------------------------------------------------------------------------


async def test_writes_are_counted_into_the_bucket_they_happened_in():
    store = InMemoryStore()
    await _write(store, "Written eight days ago.", days_ago=8)
    await _write(store, "Written two days ago.", days_ago=2)
    await _write(store, "Also two days ago.", days_ago=2)

    result = await run_query(
        store, TENANT, HistoryQuery(metric=Metric.WRITES, bucket=Bucket.DAY), now=NOW
    )
    by_day = {p.at.date(): p.value for p in result.series[0].points}
    assert by_day[_days_ago(2).date()] == 2
    assert by_day[_days_ago(8).date()] == 1
    assert result.headline == 3


async def test_empty_buckets_are_emitted_as_zero():
    """A gap in the log has to draw as a gap. A chart that omits empty buckets
    draws a straight line through a month of silence and reads as steady."""
    store = InMemoryStore()
    await _write(store, "One lonely fact.", days_ago=20)
    result = await run_query(
        store, TENANT, HistoryQuery(metric=Metric.WRITES, bucket=Bucket.DAY, window_days=30),
        now=NOW,
    )
    points = result.series[0].points
    assert len(points) >= 30
    assert sum(1 for p in points if p.value == 0) >= 25


async def test_grouping_splits_one_series_per_value():
    store = InMemoryStore()
    await _write(store, "By me.", days_ago=3, actor=Actor.USER)
    await _write(store, "By me again.", days_ago=4, actor=Actor.USER)
    await _write(store, "By the model.", days_ago=5, actor=Actor.MODEL)

    result = await run_query(
        store, TENANT, HistoryQuery(metric=Metric.WRITES, group_by=GroupBy.ACTOR), now=NOW
    )
    totals = {s.key: s.total for s in result.series}
    assert totals == {"user": 2.0, "model": 1.0}


async def test_retrievals_group_by_who_asked_not_by_the_store():
    """A trace's Event.provider is the store's. Who asked lives in the detail,
    and grouping reads must not silently answer a different question."""
    store = InMemoryStore()
    obj = await _write(store, "A fact worth reading.", days_ago=10)
    await _trace(store, days_ago=3, provider=Provider.CLAUDE, returned=[obj.id])
    await _trace(store, days_ago=2, provider=Provider.CLAUDE, returned=[obj.id])
    await _trace(store, days_ago=1, provider=Provider.CHATGPT, returned=[obj.id])

    result = await run_query(
        store,
        TENANT,
        HistoryQuery(metric=Metric.RETRIEVALS, group_by=GroupBy.PROVIDER),
        now=NOW,
    )
    assert {s.key: s.total for s in result.series} == {"claude": 2.0, "chatgpt": 1.0}


async def test_objects_served_counts_objects_not_searches():
    store = InMemoryStore()
    a = await _write(store, "First.", days_ago=10)
    b = await _write(store, "Second.", days_ago=10)
    await _trace(store, days_ago=2, provider=Provider.CLAUDE, returned=[a.id, b.id])

    searches = await run_query(store, TENANT, HistoryQuery(metric=Metric.RETRIEVALS), now=NOW)
    served = await run_query(
        store, TENANT, HistoryQuery(metric=Metric.OBJECTS_SERVED), now=NOW
    )
    assert searches.headline == 1
    assert served.headline == 2


async def test_a_correction_counts_as_a_supersession():
    """Nothing in the codebase emits `object.superseded`: a correction is a
    create that names what it replaced. The metric has to read the log that
    exists, not the one its name implies."""
    store = InMemoryStore()
    original = await _write(store, "The launch is 10 October.", days_ago=20)
    await _write(store, "The launch is 24 October.", days_ago=5, supersedes=original.id)

    result = await run_query(
        store, TENANT, HistoryQuery(metric=Metric.SUPERSESSIONS, bucket=Bucket.DAY), now=NOW
    )
    assert result.headline == 1
    # And it is not double-counted as a plain write of its own.
    writes = await run_query(store, TENANT, HistoryQuery(metric=Metric.WRITES), now=NOW)
    assert writes.headline == 2


# ---------------------------------------------------------------------------
# Stock metrics. These are the ones that have to read the past.
# ---------------------------------------------------------------------------


async def test_active_objects_is_replayed_not_projected_from_today():
    store = InMemoryStore()
    await _write(store, "Oldest.", days_ago=60)
    await _write(store, "Middle.", days_ago=30)
    await _write(store, "Newest.", days_ago=2)

    result = await run_query(
        store,
        TENANT,
        HistoryQuery(metric=Metric.ACTIVE_OBJECTS, bucket=Bucket.MONTH, window_days=90),
        now=NOW,
    )
    values = [p.value for p in result.series[0].points]
    # Strictly growing: the graph had fewer objects in the past, and the chart
    # must say so rather than drawing today's count across every bucket.
    assert values == sorted(values)
    assert values[0] < values[-1]
    assert values[-1] == 3


async def test_stock_headline_is_the_latest_bucket_not_a_sum():
    """Adding "214 active objects in March" to "230 in April" produces 444 of
    nothing. A stock metric reports its most recent bucket."""
    store = InMemoryStore()
    for day in (40, 30, 20, 10):
        await _write(store, f"Fact from day {day}.", days_ago=day)

    result = await run_query(
        store,
        TENANT,
        HistoryQuery(metric=Metric.ACTIVE_OBJECTS, bucket=Bucket.WEEK, window_days=60),
        now=NOW,
    )
    assert result.headline == 4
    assert result.headline_label == "now"
    assert result.total > result.headline


async def test_a_retired_object_leaves_the_active_count_at_its_retirement():
    store = InMemoryStore()
    obj = await _write(store, "True until it was not.", days_ago=40)
    await _write(store, "Still true.", days_ago=40)

    obj.retired_at = _days_ago(10)
    await store.put_object(
        TENANT,
        obj,
        event=Event(
            type=EventType.OBJECT_RETIRED,
            object_id=obj.id,
            actor=Actor.USER,
            at=_days_ago(10),
        ),
    )

    result = await run_query(
        store,
        TENANT,
        HistoryQuery(metric=Metric.ACTIVE_OBJECTS, bucket=Bucket.DAY, window_days=45),
        now=NOW,
    )
    by_day = {p.at.date(): p.value for p in result.series[0].points}
    assert by_day[_days_ago(20).date()] == 2
    assert by_day[_days_ago(5).date()] == 1


async def test_never_read_counts_only_what_no_trace_returned():
    store = InMemoryStore()
    read = await _write(store, "Something an assistant asked for.", days_ago=30)
    await _write(store, "Something nothing ever wanted.", days_ago=30)
    await _trace(store, days_ago=5, provider=Provider.CLAUDE, returned=[read.id])

    result = await run_query(
        store, TENANT, HistoryQuery(metric=Metric.NEVER_READ, bucket=Bucket.DAY), now=NOW
    )
    assert result.headline == 1


# ---------------------------------------------------------------------------
# Filters.
# ---------------------------------------------------------------------------


async def test_filters_match_the_object_as_it_was_at_the_event():
    """A chart of "writes where confidence was low" must count the write that
    was low-confidence at the time, even if the object was later corroborated
    upwards. Matching against today's row silently rewrites history to agree
    with the present."""
    store = InMemoryStore()
    obj = await _write(store, "A shaky claim.", days_ago=30, confidence=0.4)

    # Later, the same object becomes confident.
    obj.confidence = 0.95
    await store.put_object(
        TENANT,
        obj,
        event=Event(
            type=EventType.OBJECT_UPDATED,
            object_id=obj.id,
            actor=Actor.USER,
            at=_days_ago(2),
        ),
    )

    result = await run_query(
        store,
        TENANT,
        HistoryQuery(
            metric=Metric.WRITES,
            window_days=60,
            filter=ObjectFilter(confidence_max=0.5),
        ),
        now=NOW,
    )
    assert result.headline == 1


async def test_an_empty_filter_means_no_opinion_not_nothing():
    store = InMemoryStore()
    await _write(store, "Anything at all.", days_ago=5)
    result = await run_query(
        store, TENANT, HistoryQuery(metric=Metric.WRITES, filter=ObjectFilter()), now=NOW
    )
    assert result.headline == 1


async def test_contains_filter_is_case_insensitive():
    store = InMemoryStore()
    await _write(store, "The Ledger rewrite replaces the tables.", days_ago=5)
    await _write(store, "Unrelated to anything.", days_ago=5)
    result = await run_query(
        store,
        TENANT,
        HistoryQuery(metric=Metric.WRITES, filter=ObjectFilter(contains="ledger")),
        now=NOW,
    )
    assert result.headline == 1


# ---------------------------------------------------------------------------
# Citations. A number nobody can open is an assertion, not a measurement.
# ---------------------------------------------------------------------------


async def test_every_non_empty_point_carries_the_events_it_counted():
    store = InMemoryStore()
    await _write(store, "One.", days_ago=3)
    await _write(store, "Two.", days_ago=3)

    result = await run_query(
        store, TENANT, HistoryQuery(metric=Metric.WRITES, bucket=Bucket.DAY), now=NOW
    )
    point = next(p for p in result.series[0].points if p.value)
    assert len(point.event_ids) == 2
    assert point.event_count == 2

    events = await store.list_events(TENANT, limit=100)
    known = {e.id for e in events}
    assert set(point.event_ids) <= known


# ---------------------------------------------------------------------------
# One fact, as a series.
# ---------------------------------------------------------------------------


async def test_lifetime_follows_the_supersession_chain_in_both_directions():
    """A user asking "how has this changed" means the statement, not the row.
    Two ids for one fact must read as one history."""
    store = InMemoryStore()
    first = await _write(store, "The launch is 10 October.", days_ago=40)
    second = await _write(
        store, "The launch is 24 October.", days_ago=20, supersedes=first.id
    )
    third = await _write(
        store, "The launch is 7 November.", days_ago=5, supersedes=second.id
    )

    from_middle = await object_lifetime(store, TENANT, second.id)
    assert from_middle.chain == [first.id, second.id, third.id]
    assert [p.content for p in from_middle.points] == [
        "The launch is 10 October.",
        "The launch is 24 October.",
        "The launch is 7 November.",
    ]

    # Entering from either end tells the same story.
    from_start = await object_lifetime(store, TENANT, first.id)
    assert from_start.chain == from_middle.chain


async def test_lifetime_records_reads_against_the_whole_chain():
    store = InMemoryStore()
    first = await _write(store, "Original.", days_ago=30)
    second = await _write(store, "Corrected.", days_ago=10, supersedes=first.id)
    await _trace(store, days_ago=20, provider=Provider.CLAUDE, returned=[first.id])
    await _trace(store, days_ago=2, provider=Provider.CHATGPT, returned=[second.id])

    lifetime = await object_lifetime(store, TENANT, second.id)
    assert len(lifetime.reads) == 2
    assert {r["provider"] for r in lifetime.reads} == {"claude", "chatgpt"}


async def test_lifetime_does_not_leak_query_text_that_was_not_opted_into():
    """§11: a trace records a digest unless the caller opted in per call. The
    history view must not be the place that quietly surfaces the text."""
    store = InMemoryStore()
    obj = await _write(store, "A fact.", days_ago=10)
    await _trace(store, days_ago=2, provider=Provider.CLAUDE, returned=[obj.id])

    lifetime = await object_lifetime(store, TENANT, obj.id)
    assert lifetime.reads[0]["query_text"] is None
    assert lifetime.reads[0]["query_digest"] == "abc123"


# ---------------------------------------------------------------------------
# Reach.
# ---------------------------------------------------------------------------


async def test_reach_report_separates_never_read_from_read_by_nobody_recently():
    store = InMemoryStore()
    read = await _write(store, "Asked for twice.", days_ago=30)
    await _write(store, "Never asked for.", days_ago=30)
    await _trace(store, days_ago=9, provider=Provider.CLAUDE, returned=[read.id])
    await _trace(store, days_ago=1, provider=Provider.CLAUDE, returned=[read.id])

    report = await reach_report(store, TENANT)
    assert report["never_read"] == 1
    assert report["by_surface"]["claude"] == 2
    row = next(r for r in report["rows"] if r["object_id"] == read.id)
    assert row["read_by"] == {"claude": 2}
    assert row["never_read"] is False


# ---------------------------------------------------------------------------
# The natural-language compiler.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", EXAMPLE_QUESTIONS)
def test_every_offered_example_compiles_cleanly(question: str):
    """The examples are offered in the UI, so a user's first click must not
    produce "I did not understand that"."""
    compiled = compile_question(question, now=NOW)
    assert compiled.understood
    assert not compiled.unmatched, f"{question!r} left {compiled.unmatched}"


def test_a_question_with_no_vocabulary_says_so():
    compiled = compile_question("aubergine flugelhorn", now=NOW)
    assert not compiled.understood
    assert "aubergine" in compiled.unmatched


def test_matched_phrases_are_reported_so_the_reading_can_be_checked():
    compiled = compile_question("retrievals by provider last 30 days", now=NOW)
    meanings = {m for _, m in compiled.matched}
    assert "metric = retrievals" in meanings
    assert "grouped by provider" in meanings
    assert compiled.query.window_days == 30


def test_a_short_word_does_not_match_inside_a_longer_one():
    """Regression: a left-only word boundary let "me" match the middle of
    "mean confidence" and silently add an actor filter to a query about
    averages -- the exact class of misreading the compiler exists to prevent."""
    compiled = compile_question("mean confidence per week by type", now=NOW)
    assert compiled.query.metric is Metric.MEAN_CONFIDENCE
    assert compiled.query.filter.actors == []


def test_show_me_is_an_idiom_not_an_actor():
    compiled = compile_question("show me writes by extraction method", now=NOW)
    assert compiled.query.filter.actors == []
    assert compiled.query.group_by is GroupBy.EXTRACTION_METHOD


def test_never_read_does_not_parse_as_all_time():
    """Regression: "ever" matched inside "never" and widened a question that
    named no window at all to two years."""
    compiled = compile_question("which facts have never been read", now=NOW)
    assert compiled.query.metric is Metric.NEVER_READ
    assert compiled.query.window_days == 90


def test_low_confidence_is_a_filter_not_the_metric():
    compiled = compile_question("low confidence facts written by the model", now=NOW)
    assert compiled.query.metric is Metric.WRITES
    assert compiled.query.filter.confidence_max == 0.6
    assert Actor.MODEL in compiled.query.filter.actors


def test_a_provider_next_to_a_retrieval_metric_means_who_asked():
    compiled = compile_question("what has Claude been reading this month", now=NOW)
    assert compiled.query.metric is Metric.RETRIEVALS
    assert compiled.query.filter.surfaces == ["claude"]
    assert compiled.query.filter.providers == []


def test_duplicate_phrases_for_one_value_do_not_repeat_the_filter():
    compiled = compile_question("what the model and the assistant wrote", now=NOW)
    assert compiled.query.filter.actors == [Actor.MODEL]


def test_a_quoted_subject_wins_over_the_about_clause():
    compiled = compile_question('corrections about "the cutover date" per month', now=NOW)
    assert compiled.query.filter.contains == "the cutover date"
    assert compiled.query.bucket is Bucket.MONTH


async def test_compiled_query_is_executable_against_a_real_store():
    """The compiler and the engine have to agree about the schema. They are
    joined only by `HistoryQuery`, so this is the seam worth asserting."""
    store = InMemoryStore()
    await _write(store, "A fact about the ledger.", days_ago=5)
    compiled = compile_question("writes per week last 30 days", now=NOW)
    result = await run_query(store, TENANT, compiled.query, now=NOW)
    assert result.headline == 1
    assert result.query == compiled.query


# ---------------------------------------------------------------------------
# Ideas over time.
# ---------------------------------------------------------------------------


async def test_cluster_threshold_is_derived_from_the_graphs_own_distribution():
    """The absolute cosine is a property of the embedder, not of the graph, and
    `embedding_backend` is a config value that can change under this module."""
    store = InMemoryStore()
    for index in range(12):
        await _write(store, f"The ledger rewrite replaces table {index}.", days_ago=30 + index)
    for index in range(12):
        await _write(store, f"The hiring loop interview {index} is scheduled.", days_ago=20 + index)

    payload = await cluster_mass(store, TENANT, window_days=90, now=NOW)
    assert payload["threshold_source"] == "auto"
    assert 0.0 < payload["threshold"] <= 1.0
    assert payload["clusters"], "two distinct vocabularies should produce clusters"


async def test_cluster_mass_counts_active_members_per_bucket():
    store = InMemoryStore()
    for index in range(8):
        # One per week going back, so mass has to rise across the window.
        await _write(
            store,
            f"The ledger rewrite decision number {index} was recorded.",
            days_ago=7 * (8 - index),
        )

    payload = await cluster_mass(
        store, TENANT, window_days=70, min_size=3, now=NOW
    )
    assert payload["series"]
    values = [p["value"] for p in payload["series"][0]["points"]]
    assert values == sorted(values)
    assert values[-1] > values[0]


async def test_an_empty_graph_reports_no_topics_rather_than_inventing_some():
    store = InMemoryStore()
    payload = await cluster_mass(store, TENANT, now=NOW)
    assert payload["clusters"] == []
    assert payload["series"] == []
    assert movers(payload) == []


async def test_cluster_payload_names_the_embedder_that_produced_it():
    """Cluster quality tracks embedder quality. A chart whose meaning depends
    on a config value has to disclose it."""
    store = InMemoryStore()
    await _write(store, "Something to embed.", days_ago=5)
    payload = await cluster_mass(store, TENANT, now=NOW)
    assert payload["embedder"] == store.embedder_model


# ---------------------------------------------------------------------------
# The surface as a whole must not write to the log it reads.
# ---------------------------------------------------------------------------


async def test_reading_history_appends_nothing():
    """A dashboard that writes to its own data source becomes the largest thing
    on every chart within a week."""
    store = InMemoryStore()
    obj = await _write(store, "A fact.", days_ago=10)
    await _trace(store, days_ago=2, provider=Provider.CLAUDE, returned=[obj.id])
    before = len(await store.list_events(TENANT, limit=1000))

    await run_query(store, TENANT, HistoryQuery(metric=Metric.WRITES), now=NOW)
    await run_query(store, TENANT, HistoryQuery(metric=Metric.ACTIVE_OBJECTS), now=NOW)
    await object_lifetime(store, TENANT, obj.id)
    await reach_report(store, TENANT)
    await cluster_mass(store, TENANT, now=NOW)

    assert len(await store.list_events(TENANT, limit=1000)) == before


async def test_a_correction_carries_the_wording_it_replaced():
    """A correction is a new object, so its event has no `before` and nothing to
    diff against. The previous wording has to come from the object it
    supersedes, or the one view a user opened "how has this changed" for shows
    a create with no change in it."""
    store = InMemoryStore()
    first = await _write(store, "The launch is 10 October.", days_ago=30)
    second = await _write(
        store, "The launch is 24 October.", days_ago=5, supersedes=first.id
    )

    lifetime = await object_lifetime(store, TENANT, second.id)
    correction = lifetime.points[-1]
    assert correction.previous == "The launch is 10 October."
    assert correction.content == "The launch is 24 October."
    assert correction.as_dict()["changed"] is True
    # And it is labelled as what it is, not as a bare create.
    assert correction.event_type == "object.corrected"


async def test_an_in_place_edit_diffs_against_its_own_before():
    store = InMemoryStore()
    obj = await _write(store, "Postgres 15 is the target.", days_ago=20)
    obj.content = "Postgres 16 is the target."
    await store.put_object(
        TENANT,
        obj,
        event=Event(
            type=EventType.OBJECT_UPDATED,
            object_id=obj.id,
            actor=Actor.USER,
            at=_days_ago(3),
        ),
    )

    lifetime = await object_lifetime(store, TENANT, obj.id)
    edit = lifetime.points[-1]
    assert edit.previous == "Postgres 15 is the target."
    assert edit.event_type == "object.updated"
