"""The capture queue.

Three things here are worth more than the markup.

Erasure has to leave a mark. A row that simply disappeared would make the product's
strongest guarantee invisible at the exact moment a user exercised it — and would
be indistinguishable, on screen, from the turn having been deleted outright, which
is the thing constraint 6 says never happens.

The page's numbers have to be the same numbers the cron line exits non-zero on.
"Is the queue draining?" answered twice in two places is how two answers start
disagreeing, so the view calls `queue_health` rather than counting for itself.

And captured text has to be readable. Hiding it would make what was kept
unverifiable by the only person entitled to check.
"""

from __future__ import annotations

import pytest

from coletar.capture import capture_turn
from coletar.inspector.capture_view import render_capture
from coletar.inspector.review import erase_episode
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import Memory, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT

TURN = "I decided against a second table for the queue."


@pytest.fixture
async def store() -> InMemoryStore:
    return InMemoryStore()


# -- what a captured turn looks like ------------------------------------------
async def test_a_captured_turn_is_readable_by_its_owner(store: InMemoryStore):
    """Encryption at rest protects the turn from everyone who is not this user. It
    was never meant to protect it from them, and hiding it would make what was kept
    impossible to check."""
    await capture_turn(store, TENANT, TURN, surface=Provider.CLAUDE)
    html = await render_capture(store, TENANT)

    assert TURN in html
    assert "coletar:episode:aesgcm" not in html, "ciphertext reached the page"


async def test_a_pending_turn_says_nothing_has_judged_it_yet(store: InMemoryStore):
    await capture_turn(store, TENANT, TURN, surface=Provider.CLAUDE)
    html = await render_capture(store, TENANT)

    assert "awaiting extraction" in html
    assert "1</b>awaiting extraction" in html


async def test_the_retention_promise_is_stated_where_it_applies(store: InMemoryStore):
    await capture_turn(store, TENANT, TURN, surface=Provider.CLAUDE)
    html = await render_capture(store, TENANT)

    assert "key destroyed" in html


async def test_an_extracted_turn_links_to_what_it_produced(store: InMemoryStore):
    episode = await capture_turn(store, TENANT, TURN, surface=Provider.CLAUDE)
    memory = Memory.from_write("A durable fact.", source_object_ids=[episode.id])
    await store.put_object(TENANT, memory)
    episode.payload = {**episode.payload, "needs_model_extraction": False}
    await store.put_object(
        TENANT,
        episode,
        event=Event(type=EventType.OBJECT_UPDATED, object_id=episode.id, actor=Actor.JOB),
    )

    html = await render_capture(store, TENANT)
    assert "Produced" in html
    assert f'href="/object/{memory.id}"' in html


async def test_a_judged_turn_that_produced_nothing_says_so(store: InMemoryStore):
    """Precision over recall means most turns hold nothing durable. A queue that
    showed only the productive ones would misrepresent how often that is true."""
    episode = await capture_turn(store, TENANT, "Just chatting.", surface=Provider.CLAUDE)
    episode.payload = {**episode.payload, "needs_model_extraction": False}
    await store.put_object(
        TENANT,
        episode,
        event=Event(type=EventType.OBJECT_UPDATED, object_id=episode.id, actor=Actor.JOB),
    )

    html = await render_capture(store, TENANT)
    assert "nothing durable in it" in html


async def test_content_is_escaped(store: InMemoryStore):
    await capture_turn(store, TENANT, "<script>alert(1)</script>", surface=Provider.CLAUDE)
    html = await render_capture(store, TENANT)

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


async def test_an_empty_queue_says_where_turns_come_from(store: InMemoryStore):
    html = await render_capture(store, TENANT)
    assert "No turns captured yet" in html


# -- erasure ------------------------------------------------------------------
async def test_erasing_a_turn_removes_the_text_and_keeps_the_record(store: InMemoryStore):
    """The demo's strongest moment: the content is gone, the fact that it existed
    and was erased is not."""
    episode = await capture_turn(store, TENANT, TURN, surface=Provider.CLAUDE)
    await erase_episode(store, TENANT, episode.id)

    html = await render_capture(store, TENANT)
    assert TURN not in html, "erased text is still on the page"
    assert episode.id in html, "the record of the turn vanished with its content"
    assert "the key that could read this was destroyed" in html
    assert "1 erased" in html


async def test_an_erased_turn_offers_no_second_erase(store: InMemoryStore):
    episode = await capture_turn(store, TENANT, TURN, surface=Provider.CLAUDE)
    await erase_episode(store, TENANT, episode.id)

    html = await render_capture(store, TENANT)
    assert "Erase this turn" not in html
    assert "Key destroyed" in html


async def test_an_erased_turn_no_longer_counts_as_pending(store: InMemoryStore):
    """The header count and the health strip read the same graph; if erasure moved
    one and not the other the page would contradict itself."""
    first = await capture_turn(store, TENANT, TURN, surface=Provider.CLAUDE)
    await capture_turn(store, TENANT, "Another turn.", surface=Provider.CHATGPT)
    await erase_episode(store, TENANT, first.id)

    html = await render_capture(store, TENANT)
    assert "1</b>awaiting extraction" in html
    assert "1 live, 1 erased" in html


# -- operational state --------------------------------------------------------
async def test_a_healthy_queue_says_so(store: InMemoryStore):
    await capture_turn(store, TENANT, TURN, surface=Provider.CLAUDE)
    html = await render_capture(store, TENANT)

    assert "draining normally" in html
    assert "gate open" in html


async def test_repeated_failures_are_surfaced_not_only_counted(store: InMemoryStore):
    """A provider outage and a quiet user look identical without this."""
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

    html = await render_capture(store, TENANT)
    assert "gate blocked" in html
    assert "check the extraction provider" in html
    assert "5</b>failures, 24h" in html


async def test_the_worker_is_named_when_one_holds_the_lease(store: InMemoryStore):
    from coletar.jobs.worker import BATCH_LEASE

    await store.acquire_lease(TENANT, BATCH_LEASE, owner="host/123/abc", ttl_seconds=60)
    html = await render_capture(store, TENANT)

    assert "host/123/abc" in html


async def test_an_abandoned_lease_is_reported(store: InMemoryStore):
    import asyncio

    from coletar.jobs.worker import BATCH_LEASE

    await store.acquire_lease(TENANT, BATCH_LEASE, owner="crashed", ttl_seconds=0.01)
    await asyncio.sleep(0.05)

    html = await render_capture(store, TENANT)
    assert "(expired)" in html
    assert "without releasing" in html


# -- the route ----------------------------------------------------------------
@pytest.fixture
def live(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from coletar.config import get_settings
    from coletar.store import build_store, reset_store

    monkeypatch.setenv("COLETAR_STORE_PATH", str(tmp_path / "store.json"))
    monkeypatch.setenv("COLETAR_STORE_BACKEND", "memory")
    monkeypatch.setenv("COLETAR_DEFAULT_TENANT_ID", str(TENANT))
    get_settings.cache_clear()
    reset_store()
    yield build_store()
    get_settings.cache_clear()
    reset_store()


def _client():  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from coletar.inspector.app import app

    return TestClient(app)


async def test_the_capture_page_serves(live):  # type: ignore[no-untyped-def]
    await capture_turn(live, TENANT, TURN, surface=Provider.CLAUDE)
    response = _client().get("/capture")

    assert response.status_code == 200
    assert TURN in response.text


async def test_erasing_returns_to_the_page_it_was_done_from(live):  # type: ignore[no-untyped-def]
    episode = await capture_turn(live, TENANT, TURN, surface=Provider.CLAUDE)
    response = _client().post(
        "/erase-episode", data={"object_id": episode.id}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/capture"


async def test_erasing_something_that_is_not_a_turn_is_refused(live):  # type: ignore[no-untyped-def]
    memory = Memory.from_write("Not an episode.")
    await live.put_object(TENANT, memory)

    response = _client().post(
        "/erase-episode", data={"object_id": memory.id}, follow_redirects=True
    )

    assert "only a raw episode" in response.text
    still = await live.get_object(TENANT, memory.id)
    assert still is not None and still.is_active
