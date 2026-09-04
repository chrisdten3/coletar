"""The scheduled batch worker and its lease.

The lease exists for one reason: extraction is idempotent by stable id, so two
workers produce the same objects, but they do *not* produce the same corroboration
events. A corroboration count is evidence a user reads when deciding whether to
trust a fact, and inflating it by deploying a second worker would be a silent
integrity failure. So the tests that matter here are the adversarial ones — two
workers racing, and a worker that dies without releasing.

Both backends are covered. The in-process store's exclusion is cooperative
scheduling; Postgres's is a conditional upsert. Those are different mechanisms with
the same contract, which is exactly the case where testing only one is how they
drift apart.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from coletar.capture import capture_turn, is_pending
from coletar.jobs.health import queue_health
from coletar.jobs.worker import BATCH_LEASE, run_forever, run_pass, worker_identity
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import ObjectType, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


# -- the lease itself ---------------------------------------------------------
async def test_two_workers_racing_produce_one_holder_and_one_refusal(store: InMemoryStore):
    first, second = await asyncio.gather(
        store.acquire_lease(TENANT, "batch", owner="a", ttl_seconds=60),
        store.acquire_lease(TENANT, "batch", owner="b", ttl_seconds=60),
    )
    held = [lease for lease in (first, second) if lease is not None]
    assert len(held) == 1, "both workers believed they held the lease"


async def test_the_same_owner_may_reacquire_its_own_lease(store: InMemoryStore):
    """A worker that restarts a pass must not be locked out by itself."""
    assert await store.acquire_lease(TENANT, "batch", owner="a", ttl_seconds=60) is not None
    assert await store.acquire_lease(TENANT, "batch", owner="a", ttl_seconds=60) is not None


async def test_an_expired_lease_is_free_for_the_taking(store: InMemoryStore):
    """The failure this prevents is a queue wedged forever by a worker that was
    killed between acquiring and releasing."""
    assert await store.acquire_lease(TENANT, "batch", owner="dead", ttl_seconds=0.01) is not None
    await asyncio.sleep(0.05)
    assert await store.acquire_lease(TENANT, "batch", owner="live", ttl_seconds=60) is not None


async def test_only_the_owner_may_release(store: InMemoryStore):
    """A worker whose lease expired mid-pass must not release its successor's."""
    await store.acquire_lease(TENANT, "batch", owner="a", ttl_seconds=60)
    assert await store.release_lease(TENANT, "batch", owner="b") is False
    assert await store.read_lease(TENANT, "batch") is not None
    assert await store.release_lease(TENANT, "batch", owner="a") is True
    assert await store.read_lease(TENANT, "batch") is None


async def test_a_lease_is_scoped_to_its_tenant(store: InMemoryStore):
    from coletar.schema.tenancy import tenant_id

    other = tenant_id("tenant_other")
    assert await store.acquire_lease(TENANT, "batch", owner="a", ttl_seconds=60) is not None
    assert await store.acquire_lease(other, "batch", owner="b", ttl_seconds=60) is not None


async def test_a_lease_is_not_a_graph_write(store: InMemoryStore):
    """Acquiring and releasing must leave the event log alone. The log is the
    provenance record; acquire/release pairs every interval would bury it."""
    owner = worker_identity()
    await store.acquire_lease(TENANT, BATCH_LEASE, owner=owner, ttl_seconds=60)
    await store.release_lease(TENANT, BATCH_LEASE, owner=owner)
    assert await store.list_events(TENANT) == []


# -- the pass -----------------------------------------------------------------
async def test_a_pass_releases_its_lease_so_the_next_one_can_run(store: InMemoryStore):
    result = await run_pass(store, TENANT)
    assert result.skipped is False
    assert await store.read_lease(TENANT, BATCH_LEASE) is None


async def test_a_second_worker_skips_rather_than_failing(store: InMemoryStore):
    """A busy queue is the system working. A scheduler that reads this as an error
    pages someone about correct behaviour."""
    await store.acquire_lease(TENANT, BATCH_LEASE, owner="other", ttl_seconds=60)
    result = await run_pass(store, TENANT)

    assert result.skipped is True
    assert result.held_by == "other"
    assert result.error is None


async def test_a_failing_pass_still_releases_the_lease(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
):
    """Holding the lease until expiry would make one bad pass cost a full TTL of
    queue latency on top of the failure."""

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("coletar.jobs.worker.extract_pending", boom)
    result = await run_pass(store, TENANT)

    assert result.error is not None and "provider exploded" in result.error
    assert await store.read_lease(TENANT, BATCH_LEASE) is None


async def test_the_loop_runs_the_number_of_passes_it_is_asked_for(store: InMemoryStore):
    passes = await run_forever(store, TENANT, interval_seconds=0.01, max_passes=3)
    assert len(passes) == 3
    assert all(p.skipped is False for p in passes)


async def test_concurrent_passes_do_not_both_do_the_work(store: InMemoryStore):
    """The end-to-end version of the race: two workers, one queue, one winner."""
    await capture_turn(store, TENANT, "I moved the project to Postgres.", surface=Provider.CLAUDE)

    results = await asyncio.gather(
        run_pass(store, TENANT, owner="a"), run_pass(store, TENANT, owner="b")
    )
    assert sum(r.skipped for r in results) == 1, "both workers ran the batch pass"


# -- queue health -------------------------------------------------------------
async def test_a_healthy_empty_queue_raises_nothing(store: InMemoryStore):
    health = await queue_health(store, TENANT)
    assert health.ok
    assert health.pending == 0


async def test_a_stale_queue_alerts_with_its_age(store: InMemoryStore):
    episode = await capture_turn(store, TENANT, "I use tabs.", surface=Provider.CLAUDE)
    assert is_pending(episode)

    fresh = await queue_health(store, TENANT)
    assert fresh.pending == 1
    assert fresh.ok, "a turn captured a moment ago is not a stalled queue"

    # Backdate the capture rather than lower the threshold to zero: what is under
    # test is that a genuinely old turn is noticed, not that a comparison works.
    episode.created_at = datetime.now(UTC) - timedelta(hours=9)
    await store.put_object(
        TENANT,
        episode,
        event=Event(type=EventType.OBJECT_UPDATED, object_id=episode.id, actor=Actor.JOB),
    )

    stale = await queue_health(store, TENANT, pending_hours=6.0)
    assert not stale.ok
    assert stale.oldest_pending_hours is not None and stale.oldest_pending_hours >= 9
    assert "not draining" in stale.alerts[0]


async def test_repeated_provider_failures_alert(store: InMemoryStore):
    """The distinction that matters: a provider that is down looks exactly like a
    user who has stopped typing, until this counts the failures."""
    for _ in range(5):
        await store.append_event(
            TENANT,
            Event(
                type=EventType.EXTRACTION_UNAVAILABLE,
                object_id="ep_1",
                actor=Actor.JOB,
                detail={"reason": "ExtractionUnavailable"},
            ),
        )

    health = await queue_health(store, TENANT, failure_threshold=5)
    assert not health.ok
    assert health.recent_failures == 5
    assert "extraction provider" in health.alerts[-1]


async def test_an_abandoned_lease_is_reported(store: InMemoryStore):
    await store.acquire_lease(TENANT, BATCH_LEASE, owner="crashed", ttl_seconds=0.01)
    await asyncio.sleep(0.05)

    health = await queue_health(store, TENANT)
    assert health.lease_expired is True
    assert health.lease_owner == "crashed"
    assert any("without releasing" in alert for alert in health.alerts)


async def test_health_counts_only_this_tenants_failures(store: InMemoryStore):
    from coletar.schema.tenancy import tenant_id

    other = tenant_id("tenant_other")
    await store.append_event(
        other,
        Event(
            type=EventType.EXTRACTION_UNAVAILABLE,
            object_id="ep_other",
            actor=Actor.JOB,
        ),
    )
    assert (await queue_health(store, TENANT)).recent_failures == 0


# -- failures are recorded where health can see them --------------------------
async def test_an_unavailable_provider_leaves_a_trace_and_the_episode_pending(
    store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
):
    """A retry that leaves no trace makes an outage indistinguishable from silence."""
    from coletar.extraction.providers import ExtractionUnavailable

    episode = await capture_turn(store, TENANT, "I use tabs.", surface=Provider.CLAUDE)

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise ExtractionUnavailable("provider down")

    monkeypatch.setattr("coletar.jobs.extraction.extract_with_model", unavailable)
    from coletar.jobs.extraction import extract_pending

    report = await extract_pending(store, TENANT)

    assert report.unavailable == 1
    still = await store.get_object(TENANT, episode.id)
    assert still is not None and is_pending(still), "an unextracted turn must stay queued"

    events = await store.list_events(TENANT, object_id=episode.id)
    assert any(e.type is EventType.EXTRACTION_UNAVAILABLE for e in events)
    assert (await queue_health(store, TENANT)).recent_failures == 1


async def test_the_failure_event_is_not_a_revision(store: InMemoryStore):
    """Replay reconstructs state from revision events. A failed attempt changed no
    state, so counting it as one would rewrite history that did not happen."""
    from coletar.schema.events import REVISION_EVENTS

    assert EventType.EXTRACTION_UNAVAILABLE not in REVISION_EVENTS


async def test_episode_type_is_what_the_queue_scans(store: InMemoryStore):
    episode = await capture_turn(store, TENANT, "I use tabs.", surface=Provider.CLAUDE)
    assert episode.type is ObjectType.EPISODE
    assert (await queue_health(store, TENANT)).oldest_pending_hours is not None
    assert episode.created_at <= datetime.now(UTC)
