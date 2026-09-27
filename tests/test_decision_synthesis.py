"""M11.2: distilling captured traces, and destroying the raw content behind them.

The property worth testing here is not that synthesis produces a sample -- it is what
happens to the raw trace afterwards. A pass that produced samples and left a day of
real business data lying around would pass a naive test and defeat the design.
"""

from __future__ import annotations

import json

import pytest

from coletar.decisions.raw_capture import (
    DECISION_RAW,
    DECISION_SAMPLE,
    EPISODE_KIND,
    NEEDS_DECISION_SYNTHESIS,
    capture_trace,
    is_pending_synthesis,
)
from coletar.decisions.schema import SynthesizedDecisionSample
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
from coletar.jobs.decision_synthesis import synthesize_pending
from coletar.jobs.extraction import extract_pending
from coletar.schema.events import EventType
from coletar.schema.objects import GLOBAL_SCOPE, ObjectType, Provider
from coletar.store.memory import InMemoryStore
from conftest import TENANT

TOOL = "route_ticket"


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


async def _capture(store, *, tool=TOOL, intent="duplicate charge dispute"):
    return await capture_trace(
        store,
        TENANT,
        tool_name=tool,
        intent=intent,
        environmental_state={"tier": "enterprise"},
        tool_call={"name": tool, "arguments": {"queue": "billing"}},
        tool_response={"team": "priority-billing"},
        surface=Provider.COLETAR,
        scope=GLOBAL_SCOPE,
        principal_id="integration",
    )


def _of_kind(episodes, kind):
    return [e for e in episodes if e.payload.get(EPISODE_KIND) == kind]


async def test_a_pending_trace_becomes_a_sample(store):
    trace = await _capture(store)
    assert is_pending_synthesis(trace)

    report = await synthesize_pending(store, TENANT)
    assert report.processed == 1
    assert report.unavailable == 0

    samples = _of_kind(await store.list_objects(TENANT, type=ObjectType.EPISODE), DECISION_SAMPLE)
    assert len(samples) == 1
    sample = SynthesizedDecisionSample.model_validate_json(
        await decrypt_episode(store, TENANT, samples[0])
    )
    assert sample.tool_name == TOOL
    assert sample.source_episode_id == trace.id
    assert sample.tool_response == {"team": "priority-billing"}
    assert sample.schema_version == 1


async def test_the_raw_trace_is_shredded_once_distilled(store):
    """Raw content is liability the moment the sample exists, not a day later."""
    trace = await _capture(store)
    report = await synthesize_pending(store, TENANT)
    assert report.shredded == 1

    stored = await store.get_object(TENANT, trace.id)
    assert stored is not None, "the object survives for provenance"
    with pytest.raises(EpisodeKeyUnavailable):
        await decrypt_episode(store, TENANT, stored)


async def test_the_sample_keeps_its_own_key(store):
    """Shredding the trace must not take the sample with it."""
    await _capture(store)
    await synthesize_pending(store, TENANT)
    samples = _of_kind(await store.list_objects(TENANT, type=ObjectType.EPISODE), DECISION_SAMPLE)
    assert json.loads(await decrypt_episode(store, TENANT, samples[0]))["intent"]


async def test_a_second_pass_does_nothing(store):
    await _capture(store)
    assert (await synthesize_pending(store, TENANT)).processed == 1
    assert (await synthesize_pending(store, TENANT)).processed == 0


async def test_a_shredded_trace_is_reported_not_silently_skipped(store):
    """"Nothing to do" and "could not do it" must stay different answers."""
    trace = await _capture(store)
    await store.shred_object_key(TENANT, trace.id, reason="consent_revoked")

    report = await synthesize_pending(store, TENANT)
    assert report.unavailable == 1 and report.processed == 0
    assert [
        e
        for e in await store.list_events(TENANT)
        if e.type is EventType.DECISION_SYNTHESIS_UNAVAILABLE
    ]


async def test_the_batch_limit_is_respected(store):
    for _ in range(4):
        await _capture(store)
    assert (await synthesize_pending(store, TENANT, limit=2)).processed == 2
    assert (await synthesize_pending(store, TENANT, limit=2)).processed == 2


async def test_memory_extraction_ignores_decision_traces(store):
    """Two queues share one object type; neither may pick up the other's work."""
    await _capture(store)
    report = await extract_pending(store, TENANT)
    assert report.processed == 0
    assert report.unavailable == 0

    traces = _of_kind(await store.list_objects(TENANT, type=ObjectType.EPISODE), DECISION_RAW)
    assert traces[0].payload[NEEDS_DECISION_SYNTHESIS] is True, "still ours to do"
