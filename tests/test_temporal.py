"""M9 — as-of queries.

Two of the four questions a compliance reader asks cannot be answered by any memory
layer that stores only the current value: *what did this say on 3 March*, and *what
changed between these dates*. They need an immutable log carrying full before/after
state, which is why constraint 5 exists.

The subtle test here is `test_supersession_is_evaluated_as_of_then`. Getting that
wrong makes the whole feature confidently wrong rather than visibly broken.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from coletar.schema.objects import (
    ExtractionMethod,
    Memory,
    MemoryKind,
    OriginType,
    Provenance,
    Provider,
    Scope,
    ScopeType,
)
from coletar.store.memory import InMemoryStore
from coletar.temporal import changes_between, graph_as_of, search_as_of
from conftest import TENANT

PROJECT = Scope(type=ScopeType.PROJECT, id="proj_policy")


async def mark() -> datetime:
    """A timestamp strictly between two writes."""
    await asyncio.sleep(0.01)
    now = datetime.now(UTC)
    await asyncio.sleep(0.01)
    return now


# --- the question nobody else can answer -----------------------------------------


@pytest.mark.asyncio
async def test_what_a_policy_said_at_a_past_moment() -> None:
    store = InMemoryStore()
    policy = Memory.from_write(
        "Expense approvals over $500 need a director.", kind=MemoryKind.FACT
    )
    await store.put_object(TENANT, policy)
    march = await mark()

    stored = await store.get_object(TENANT, policy.id)
    assert stored is not None
    stored.content = "Expense approvals over $2000 need a director."
    await store.put_object(TENANT, stored)

    then = await graph_as_of(store, TENANT, march)
    assert [o.content for o in then] == ["Expense approvals over $500 need a director."]

    now = await graph_as_of(store, TENANT, datetime.now(UTC))
    assert [o.content for o in now] == ["Expense approvals over $2000 need a director."]


@pytest.mark.asyncio
async def test_supersession_is_evaluated_as_of_then_not_now() -> None:
    """The subtle half, and the one that makes this feature trustworthy.

    A fact corrected last week was still the current answer in March. Filtering the
    reconstructed graph against *today's* supersession would report that March
    believed something it did not — confidently wrong rather than visibly broken,
    which is the worst kind of answer for an audit.
    """
    store = InMemoryStore()
    original = Memory.from_write("Retention is 3 years.")
    await store.put_object(TENANT, original)
    march = await mark()

    correction = Memory.from_write("Retention is 7 years.", supersedes=original.id)
    await store.put_object(TENANT, correction)

    then = [o.content for o in await graph_as_of(store, TENANT, march)]
    assert then == ["Retention is 3 years."]

    now = [o.content for o in await graph_as_of(store, TENANT, datetime.now(UTC))]
    assert now == ["Retention is 7 years."]


@pytest.mark.asyncio
async def test_a_retired_object_was_still_live_before_retirement() -> None:
    store = InMemoryStore()
    rule = Memory.from_write("Contractors need a signed NDA.")
    await store.put_object(TENANT, rule)
    before = await mark()
    await store.retire_object(TENANT, rule.id, reason="policy withdrawn")

    assert [o.content for o in await graph_as_of(store, TENANT, before)] == [
        "Contractors need a signed NDA."
    ]
    assert await graph_as_of(store, TENANT, datetime.now(UTC)) == []


@pytest.mark.asyncio
async def test_before_anything_existed_the_answer_is_nothing() -> None:
    store = InMemoryStore()
    dawn = datetime.now(UTC) - timedelta(days=365)
    await store.put_object(TENANT, Memory.from_write("Anything."))
    assert await graph_as_of(store, TENANT, dawn) == []


# --- reconstruction reads the log, not the table ----------------------------------


@pytest.mark.asyncio
async def test_reconstruction_uses_the_log_so_the_answer_is_defensible() -> None:
    """If the table and the log ever disagree, the log is the thing you can show
    someone. The reconstruction is built from `after` snapshots alone."""
    store = InMemoryStore()
    fact = Memory.from_write("Original text.")
    await store.put_object(TENANT, fact)

    events = await store.list_events(TENANT, object_id=fact.id, limit=10)
    assert any(e.after and e.after["content"] == "Original text." for e in events)

    rebuilt = await graph_as_of(store, TENANT, datetime.now(UTC))
    assert [o.content for o in rebuilt] == ["Original text."]


# --- searching the past -----------------------------------------------------------


@pytest.mark.asyncio
async def test_search_finds_what_the_policy_said_then() -> None:
    store = InMemoryStore()
    policy = Memory.from_write("Expense approvals over $500 need a director.")
    await store.put_object(TENANT, policy)
    await store.put_object(TENANT, Memory.from_write("Coffee is reimbursable."))
    march = await mark()

    stored = await store.get_object(TENANT, policy.id)
    assert stored is not None
    stored.content = "Expense approvals need no director."
    await store.put_object(TENANT, stored)

    hits = await search_as_of(store, TENANT, "expense approvals director", march)
    assert hits
    assert "$500" in hits[0].obj.content


@pytest.mark.asyncio
async def test_as_of_search_is_lexical_and_says_so() -> None:
    """Documented rather than quietly different: the vector index holds current
    state, so a reader should be able to see that this is not the live hybrid."""
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("Retention is seven years."))
    hits = await search_as_of(store, TENANT, "retention seven", datetime.now(UTC))
    assert hits
    assert hits[0].components.vector == 0.0
    assert str(hits[0].components.source) == "lexical"


@pytest.mark.asyncio
async def test_as_of_search_respects_scope() -> None:
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("Global retention is 3 years."))
    await store.put_object(
        TENANT, Memory.from_write("Policy project retention is 7 years.", scope=PROJECT)
    )
    now = datetime.now(UTC)

    scoped = await search_as_of(store, TENANT, "retention years", now, scope=PROJECT)
    contents = {hit.obj.content for hit in scoped}
    # A project sees its own and the global; never another project's.
    assert any("Policy project" in c for c in contents)
    assert any("Global retention" in c for c in contents)


# --- what changed between two dates ------------------------------------------------


@pytest.mark.asyncio
async def test_changes_between_two_dates_reads_as_a_diff() -> None:
    store = InMemoryStore()
    start = await mark()

    policy = Memory.from_write("Retention is 3 years.")
    await store.put_object(TENANT, policy)
    stored = await store.get_object(TENANT, policy.id)
    assert stored is not None
    stored.content = "Retention is 7 years."
    await store.put_object(TENANT, stored)

    end = datetime.now(UTC)
    changes = await changes_between(store, TENANT, start, end)

    assert [c.kind for c in changes] == ["added", "changed"]
    assert changes[1].before == "Retention is 3 years."
    assert changes[1].after == "Retention is 7 years."
    assert changes[1].actor


@pytest.mark.asyncio
async def test_a_window_with_no_changes_is_empty_not_an_error() -> None:
    store = InMemoryStore()
    await store.put_object(TENANT, Memory.from_write("Something."))
    quiet_start = datetime.now(UTC) + timedelta(days=1)
    quiet_end = quiet_start + timedelta(days=1)
    assert await changes_between(store, TENANT, quiet_start, quiet_end) == []


@pytest.mark.asyncio
async def test_a_retirement_is_reported_as_one() -> None:
    store = InMemoryStore()
    start = await mark()
    rule = Memory.from_write("An old rule.")
    await store.put_object(TENANT, rule)
    await store.retire_object(TENANT, rule.id, reason="withdrawn")

    changes = await changes_between(store, TENANT, start, datetime.now(UTC))
    assert "retired" in {c.kind for c in changes}


# --- the second axis: what was in force, as against what we knew ------------------


def test_validity_is_half_open() -> None:
    """`valid_until` is when a fact stopped being true, so a policy superseded at
    midnight was not in force at midnight. Closing the interval would make two
    successive policies both apply for one instant — exactly the ambiguity an auditor
    is trying to resolve."""
    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = datetime(2026, 7, 1, tzinfo=UTC)
    policy = Memory.from_write("Q2 rate is 4%.")
    policy.valid_from, policy.valid_until = start, end

    assert not policy.in_force_at(start - timedelta(seconds=1))
    assert policy.in_force_at(start)
    assert policy.in_force_at(end - timedelta(seconds=1))
    assert not policy.in_force_at(end)


def test_an_undated_fact_is_always_in_force() -> None:
    """The honest reading of a preference stated without a date, and what keeps every
    object written before this existed behaving as it did."""
    preference = Memory.from_write("Chris prefers tabs.")
    assert preference.in_force_at(datetime(1999, 1, 1, tzinfo=UTC))
    assert preference.in_force_at(datetime(2099, 1, 1, tzinfo=UTC))


def test_an_interval_that_ends_before_it_starts_is_refused() -> None:
    """A data-entry error, not a fact with a strange shape. Postgres refuses it with
    a CHECK; the model refuses it too, so the backends tell the same story."""
    with pytest.raises(ValueError, match="valid_from must be before valid_until"):
        Memory(
            content="Backwards.",
            extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
            provenance=Provenance(origin_type=OriginType.USER, provider=Provider.LOCAL),
            valid_from=datetime(2026, 7, 1, tzinfo=UTC),
            valid_until=datetime(2026, 4, 1, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_the_two_axes_answer_different_questions() -> None:
    """The query compliance actually poses, which neither axis alone can express:
    *on 3 March, what did we believe was in force on 1 January?*"""
    store = InMemoryStore()

    q1 = Memory.from_write("Rate is 3%.")
    q1.valid_from = datetime(2026, 1, 1, tzinfo=UTC)
    q1.valid_until = datetime(2026, 4, 1, tzinfo=UTC)
    await store.put_object(TENANT, q1)

    q2 = Memory.from_write("Rate is 5%.")
    q2.valid_from = datetime(2026, 4, 1, tzinfo=UTC)
    await store.put_object(TENANT, q2)
    recorded = datetime.now(UTC)

    january = await graph_as_of(
        store, TENANT, recorded, in_force_at=datetime(2026, 2, 1, tzinfo=UTC)
    )
    assert [o.content for o in january] == ["Rate is 3%."]

    may = await graph_as_of(
        store, TENANT, recorded, in_force_at=datetime(2026, 5, 1, tzinfo=UTC)
    )
    assert [o.content for o in may] == ["Rate is 5%."]

    # Transaction time alone sees both, because both are recorded and neither
    # supersedes the other — which is precisely why the second axis is needed.
    assert len(await graph_as_of(store, TENANT, recorded)) == 2


@pytest.mark.asyncio
async def test_a_fact_recorded_before_it_takes_effect() -> None:
    """A policy announced in March, effective in April. Transaction time says we knew
    in March; valid time says nothing changed until April."""
    store = InMemoryStore()
    future = Memory.from_write("From April, approvals need two signatures.")
    future.valid_from = datetime(2026, 4, 1, tzinfo=UTC)
    await store.put_object(TENANT, future)
    recorded = datetime.now(UTC)

    assert await graph_as_of(
        store, TENANT, recorded, in_force_at=datetime(2026, 3, 15, tzinfo=UTC)
    ) == []
    assert len(
        await graph_as_of(
            store, TENANT, recorded, in_force_at=datetime(2026, 4, 2, tzinfo=UTC)
        )
    ) == 1


@pytest.mark.asyncio
async def test_search_can_be_asked_on_both_axes() -> None:
    store = InMemoryStore()
    old = Memory.from_write("Retention policy is 3 years.")
    old.valid_until = datetime(2026, 4, 1, tzinfo=UTC)
    await store.put_object(TENANT, old)
    new = Memory.from_write("Retention policy is 7 years.")
    new.valid_from = datetime(2026, 4, 1, tzinfo=UTC)
    await store.put_object(TENANT, new)

    hits = await search_as_of(
        store,
        TENANT,
        "retention policy",
        datetime.now(UTC),
        in_force_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    assert [h.obj.content for h in hits] == ["Retention policy is 3 years."]
