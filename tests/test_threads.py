"""Following one subject through the graph (SCOPE §6; AGENTS.md §1, 2026-09-26).

The claim this view makes is stronger than the rest of History's: not "you wrote
nine things in March" but "you moved off Postgres 15 in June". A user will act on
that, so the evidence has to be a recorded correction rather than an inference,
and these are about keeping it that way.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from coletar.history.query import Bucket, GroupBy, HistoryQuery, Metric
from coletar.history.rollup import run_query
from coletar.history.threads import (
    candidate_names,
    dedupe_names,
    detect_switches,
    gather,
    run_thread,
    suggest_threads,
)
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import ExtractionMethod, Memory, MemoryKind, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


async def _write(
    store: InMemoryStore,
    content: str,
    *,
    days_ago: float,
    supersedes: str | None = None,
    kind: MemoryKind = MemoryKind.FACT,
    retired: bool = False,
):
    memory = Memory.from_write(
        content,
        kind=kind,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        provider=Provider.CLAUDE,
        supersedes=supersedes,
    )
    when = NOW - timedelta(days=days_ago)
    memory.created_at = when
    memory.updated_at = when
    if retired:
        memory.retired_at = when + timedelta(days=1)
    return await store.put_object(
        TENANT,
        memory,
        event=Event(
            type=EventType.OBJECT_CREATED,
            object_id=memory.id,
            actor=Actor.USER,
            at=when,
        ),
    )


async def _database_chain(store: InMemoryStore):
    first = await _write(store, "The ledger runs on Postgres 15 on RDS.", days_ago=180)
    second = await _write(
        store,
        "The ledger targets Postgres 16 after the partitioning work.",
        days_ago=90,
        supersedes=first.id,
        kind=MemoryKind.CORRECTION,
    )
    third = await _write(
        store,
        "Ledger reads move to a Neon read replica; writes stay on the primary.",
        days_ago=20,
        supersedes=second.id,
        kind=MemoryKind.CORRECTION,
    )
    return first, second, third


# ---------------------------------------------------------------------------
# Naming the alternatives.
# ---------------------------------------------------------------------------


def test_product_names_are_recognised_by_shape_not_by_a_dictionary():
    assert candidate_names("The ledger runs on Postgres 15 on RDS.") == {
        "Postgres 15",
        "RDS",
    }
    assert "Fly.io" in candidate_names("Settlement deploys to Fly.io behind the proxy.")
    assert "qwen2.5" in candidate_names("Extraction runs on local qwen2.5 for that.")
    assert "CapIQ" in candidate_names("Comps come from CapIQ.")


def test_a_lowercase_compound_is_not_a_product():
    """Regression: the dot/hyphen rule had no case requirement, so "per-service"
    and "two-week" were offered as competing products a user had switched
    between."""
    assert candidate_names("Events go through a per-service exchange.") == set()
    assert candidate_names("The dual-write window is two-week by default.") == set()


def test_two_capitalised_words_are_one_alternative():
    """"Claude Sonnet" is the choice; "Claude" and "Sonnet" are not two of them."""
    names = candidate_names("Extraction moved to Claude Sonnet last quarter.")
    assert "Claude Sonnet" in names
    assert "Claude" not in names and "Sonnet" not in names


def test_a_version_subsumes_its_bare_name():
    """A series that collapsed "Postgres 15" and "Postgres 16" into "postgres"
    would chart a migration as a flat line; keeping the bare form beside them
    charts it twice."""
    assert dedupe_names({"Postgres", "Postgres 15", "Postgres 16"}) == {
        "Postgres 15",
        "Postgres 16",
    }
    assert dedupe_names({"Fly", "Fly.io"}) == {"Fly.io"}
    assert dedupe_names({"GPT", "GPT-4"}) == {"GPT-4"}


# ---------------------------------------------------------------------------
# Switches.
# ---------------------------------------------------------------------------


async def test_a_switch_names_what_was_dropped_and_what_was_adopted():
    store = InMemoryStore()
    await _database_chain(store)
    objects, _ = await gather(store, TENANT, subject="ledger database")
    switches = detect_switches(objects)

    assert [s.adopted for s in switches] == [["Postgres 16"], ["Neon"]]
    assert switches[0].dropped == ["Postgres 15", "RDS"]
    assert switches[0].at < switches[1].at


async def test_a_reword_is_not_a_switch():
    """A correction that changes no named alternative is a revision. Listing it
    would bury the switches that matter under the ones that do not."""
    store = InMemoryStore()
    first = await _write(store, "Reviews happen within two days.", days_ago=60)
    await _write(
        store,
        "Reviews happen within two days, as of the most recent check.",
        days_ago=10,
        supersedes=first.id,
        kind=MemoryKind.CORRECTION,
    )
    objects, _ = await gather(store, TENANT, subject="reviews")
    assert not any(s.is_substantive for s in detect_switches(objects))


async def test_a_switch_cites_a_resolvable_object():
    store = InMemoryStore()
    _, second, _ = await _database_chain(store)
    report = await run_thread(store, TENANT, subject="ledger database", now=NOW)
    ids = {s.to_object_id for s in report.switches}
    assert second.id in ids
    for object_id in ids:
        assert await store.get_object(TENANT, object_id) is not None


# ---------------------------------------------------------------------------
# Gathering.
# ---------------------------------------------------------------------------


async def test_the_gather_keeps_superseded_and_retired_objects():
    """The opposite of every other read path, and the entire point: a thread
    whose gather dropped the position that was replaced would be a history of
    the present."""
    store = InMemoryStore()
    first, second, third = await _database_chain(store)
    objects, _ = await gather(store, TENANT, anchor_ids=[third.id])
    found = {o.id for o in objects}
    assert {first.id, second.id, third.id} <= found


async def test_anchors_pull_in_the_whole_chain_from_any_point():
    store = InMemoryStore()
    first, second, third = await _database_chain(store)
    for entry in (first, second, third):
        objects, _ = await gather(store, TENANT, anchor_ids=[entry.id])
        assert {first.id, second.id, third.id} <= {o.id for o in objects}


async def test_the_gather_is_bounded():
    """AGENTS.md §1 says one subject's objects, never the whole graph. The
    ceiling is what makes that true rather than aspirational, because this set
    is what a model backend would be handed."""
    store = InMemoryStore()
    for index in range(40):
        await _write(store, f"The ledger runs on Postgres and shard {index}.", days_ago=30)
    objects, truncated = await gather(store, TENANT, subject="ledger", max_objects=10)
    assert len(objects) == 10
    assert truncated


# ---------------------------------------------------------------------------
# Alternative mass.
# ---------------------------------------------------------------------------


async def test_a_replaced_option_stops_counting_when_it_was_replaced():
    store = InMemoryStore()
    await _database_chain(store)
    report = await run_thread(
        store, TENANT, subject="ledger database", bucket=Bucket.MONTH, now=NOW
    )
    by_name = {a.name: [p[1] for p in a.points] for a in report.alternatives}

    assert "Postgres 15" in by_name and "Neon" in by_name
    # The old position is live early and gone late; the new one is the reverse.
    assert by_name["Postgres 15"][-1] == 0
    assert by_name["Neon"][-1] == 1
    assert max(by_name["Postgres 15"]) == 1


async def test_a_name_from_a_switch_is_charted_even_if_mentioned_once():
    """Dropping a rare name for being rare would hide the very change the thread
    was opened to see."""
    store = InMemoryStore()
    await _database_chain(store)
    report = await run_thread(store, TENANT, subject="ledger database", now=NOW)
    assert any(a.name == "Neon" and a.total == 1 for a in report.alternatives)


# ---------------------------------------------------------------------------
# Suggestions.
# ---------------------------------------------------------------------------


async def test_suggestions_are_derived_and_never_open_an_empty_chart():
    store = InMemoryStore()
    await _database_chain(store)
    suggestions = await suggest_threads(store, TENANT)
    assert suggestions
    for suggestion in suggestions:
        assert suggestion.switches >= 1
        report = await run_thread(
            store, TENANT, subject=suggestion.subject, anchor_ids=suggestion.anchor_ids, now=NOW
        )
        assert report.switches


async def test_a_chain_is_one_subject_not_one_per_switch():
    store = InMemoryStore()
    await _database_chain(store)
    suggestions = await suggest_threads(store, TENANT)
    assert len(suggestions) == 1
    assert suggestions[0].switches == 2
    assert suggestions[0].subject == "Postgres 15 → Neon"


async def test_a_one_sided_switch_is_labelled_from_the_opening_statement():
    """Regression: "Events go through Kafka; RabbitMQ is retired" adopts a name
    without the old one disappearing, and the thread was labelled "How has Kafka
    changed over time" -- a question nobody would ask."""
    store = InMemoryStore()
    first = await _write(store, "Events go through RabbitMQ per service.", days_ago=120)
    await _write(
        store,
        "Events go through Kafka; RabbitMQ is retired once the cutover completes.",
        days_ago=30,
        supersedes=first.id,
        kind=MemoryKind.CORRECTION,
    )
    suggestions = await suggest_threads(store, TENANT)
    assert suggestions[0].subject == "RabbitMQ → Kafka"


async def test_swaps_rank_above_one_sided_changes():
    """A chain where one name left and another arrived is a choice changing. A
    correction to a person's name is a real recorded change and not a question
    anyone would ask, so it must not lead."""
    store = InMemoryStore()
    person = await _write(store, "The witness is Gerald Roe of Harborline.", days_ago=100)
    await _write(
        store,
        "The witness is Gerald Roe of Harborline and Whitcombe.",
        days_ago=40,
        supersedes=person.id,
        kind=MemoryKind.CORRECTION,
    )
    await _database_chain(store)

    suggestions = await suggest_threads(store, TENANT)
    assert "→" in suggestions[0].subject


async def test_a_graph_with_no_recorded_changes_offers_nothing():
    """Better an empty list than four questions the evidence cannot answer."""
    store = InMemoryStore()
    await _write(store, "The ledger runs on Postgres 15.", days_ago=30)
    await _write(store, "Deploys go to Fly.io.", days_ago=20)
    assert await suggest_threads(store, TENANT) == []


# ---------------------------------------------------------------------------
# Cost, which is the one number here that is measured rather than read.
# ---------------------------------------------------------------------------


async def test_tokens_served_sums_the_trace_estimates():
    """The cost view multiplies this series by the user's own rates, so it has
    to agree with the figure the plan meter already reports."""
    store = InMemoryStore()
    for index, (provider, tokens) in enumerate(
        [(Provider.CLAUDE, 400), (Provider.CLAUDE, 600), (Provider.CHATGPT, 250)]
    ):
        await store.append_event(
            TENANT,
            Event(
                type=EventType.RETRIEVAL_TRACE,
                actor=Actor.CONNECTOR,
                at=NOW - timedelta(days=index + 1),
                detail={
                    "provider": str(provider),
                    "surface": "mcp",
                    "returned_ids": [],
                    "token_estimate": tokens,
                },
            ),
        )

    result = await run_query(
        store,
        TENANT,
        HistoryQuery(metric=Metric.TOKENS_SERVED, group_by=GroupBy.PROVIDER, bucket=Bucket.DAY),
        now=NOW,
    )
    assert {s.key: s.total for s in result.series} == {"claude": 1000.0, "chatgpt": 250.0}


# ---------------------------------------------------------------------------
# Still read-only.
# ---------------------------------------------------------------------------


async def test_following_a_thread_appends_nothing():
    store = InMemoryStore()
    _, _, third = await _database_chain(store)
    before = len(await store.list_events(TENANT, limit=1000))

    await suggest_threads(store, TENANT)
    await run_thread(store, TENANT, subject="ledger database", anchor_ids=[third.id], now=NOW)

    assert len(await store.list_events(TENANT, limit=1000)) == before


async def test_the_names_shown_match_the_names_in_the_question():
    """Regression: the label and the chips beneath it were computed separately,
    so a one-sided switch produced "How has ChatGPT → Mistral changed over
    time?" above a single chip reading "Mistral"."""
    store = InMemoryStore()
    first = await _write(store, "Model work runs through ChatGPT on the team plan.", days_ago=120)
    await _write(
        store,
        "Live deals run on the local Mistral instance only; ChatGPT is for public comps.",
        days_ago=30,
        supersedes=first.id,
        kind=MemoryKind.CORRECTION,
    )
    suggestion = (await suggest_threads(store, TENANT))[0]
    assert suggestion.subject == "ChatGPT → Mistral"
    for endpoint in suggestion.subject.split(" → "):
        assert endpoint in suggestion.names
