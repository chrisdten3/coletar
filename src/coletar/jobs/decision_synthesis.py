"""Distil captured decision traces, then destroy the raw content (M11.2).

Same shape as `jobs.extraction.extract_pending`, for the same reason: capture has to
be cheap and synchronous, and judging what was captured has to happen somewhere that
nobody is waiting on.

The difference is what happens afterwards. Extraction leaves its episode in place
until its retention runs out, because the user may want to see the sentence a memory
came from. A decision trace has no such claim on anyone's attention: once the sample
exists, the raw inputs and outputs are pure liability, so this pass shreds their key
the moment synthesis succeeds rather than waiting for the one-day TTL. The TTL stays
as the ceiling for the case this pass never manages to read them at all -- a
synthesizer that has been broken for a week must not become a reason raw business
data is still sitting there.

Failure is recorded, not swallowed: a trace whose key is already gone leaves a
`DECISION_SYNTHESIS_UNAVAILABLE` event, so "nothing to synthesize" and "unable to
synthesize" stay different answers -- the distinction `EXTRACTION_UNAVAILABLE`
exists to preserve on the memory queue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from coletar.decisions.raw_capture import (
    DECISION_SAMPLE,
    EPISODE_KIND,
    NEEDS_DECISION_SYNTHESIS,
    RAW_TTL_DAYS,
    is_pending_synthesis,
)
from coletar.decisions.schema import SynthesizedDecisionSample
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode, encrypt_episode
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    ContextObject,
    ExtractionMethod,
    ObjectType,
    OriginType,
    Provenance,
    new_id,
)
from coletar.schema.tenancy import TenantId

if TYPE_CHECKING:
    from coletar.store.base import Store

#: A ceiling on one pass, as everywhere else that scans the graph.
SCAN_LIMIT = 10_000

SHRED_REASON = "decision_trace_synthesized"


@dataclass
class SynthesisBatchReport:
    scanned: int = 0
    processed: int = 0
    unavailable: int = 0
    shredded: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "processed": self.processed,
            "unavailable": self.unavailable,
            "shredded": self.shredded,
        }


async def synthesize_pending(
    store: Store, tenant_id: TenantId, *, limit: int | None = None
) -> SynthesisBatchReport:
    """Turn pending raw traces into samples once, shredding each raw trace behind it.

    Consent is not re-checked here. A `decision_raw` episode only exists because the
    capture path found consent for that tool when it arrived, so re-deriving that
    judgement from settings that may have changed since would answer a different
    question than the one that authorised the data.
    """
    from coletar.config import get_settings

    chosen_limit = limit or get_settings().decision_synthesis_batch_size
    episodes = await store.list_objects(tenant_id, type=ObjectType.EPISODE, limit=SCAN_LIMIT)
    pending = [episode for episode in episodes if is_pending_synthesis(episode)][:chosen_limit]
    report = SynthesisBatchReport(scanned=len(episodes))

    for episode in pending:
        try:
            plaintext = await decrypt_episode(store, tenant_id, episode)
        except EpisodeKeyUnavailable as exc:
            report.unavailable += 1
            await store.append_event(
                tenant_id,
                Event(
                    type=EventType.DECISION_SYNTHESIS_UNAVAILABLE,
                    object_id=episode.id,
                    actor=Actor.JOB,
                    provider=episode.provenance.provider,
                    detail={"reason": exc.__class__.__name__},
                ),
            )
            continue

        captured: dict[str, Any] = json.loads(plaintext)
        sample = SynthesizedDecisionSample(
            sample_id=new_id(ObjectType.EPISODE),
            source_episode_id=episode.id,
            tool_name=str(episode.payload.get("tool_name", "")),
            intent=str(captured.get("intent", "")),
            environmental_state=captured.get("environmental_state") or {},
            tool_call=captured.get("tool_call") or {},
            tool_response=captured.get("tool_response") or {},
        )
        await _write_sample(store, tenant_id, episode, sample)

        # Raw content goes as soon as the sample exists. `shred_object_key` appends
        # its own `object.shredded` event, so the erasure is audited without this
        # job describing it twice.
        if await store.shred_object_key(tenant_id, episode.id, reason=SHRED_REASON):
            report.shredded += 1

        episode.payload = {**episode.payload, NEEDS_DECISION_SYNTHESIS: False}
        await store.put_object(
            tenant_id,
            episode,
            event=Event(
                type=EventType.OBJECT_UPDATED,
                object_id=episode.id,
                actor=Actor.JOB,
                provider=episode.provenance.provider,
                detail={"decision_synthesis_complete": True, "sample": sample.sample_id},
            ),
        )
        report.processed += 1

    return report


async def _write_sample(
    store: Store,
    tenant_id: TenantId,
    source: ContextObject,
    sample: SynthesizedDecisionSample,
) -> ContextObject:
    """Store the sample encrypted under its own key.

    Its own key, not the source's: a sample may still carry near-verbatim fragments
    of what the tool was handed, so it earns an erasure boundary of its own rather
    than inheriting one that is about to be destroyed.
    """
    ciphertext, key = encrypt_episode(
        tenant_id, sample.sample_id, sample.model_dump_json()
    )
    stored = ContextObject(
        id=sample.sample_id,
        type=ObjectType.EPISODE,
        content=ciphertext,
        scope=source.scope,
        locality=source.locality,
        confidence=1.0,
        extraction_method=ExtractionMethod.DERIVED_SUMMARY,
        ttl_days=RAW_TTL_DAYS,
        provenance=Provenance(
            origin_type=OriginType.SYSTEM,
            provider=source.provenance.provider,
            source_object_ids=[source.id],
            confidence=1.0,
        ),
        payload={
            EPISODE_KIND: DECISION_SAMPLE,
            "content_encryption": "aesgcm-v1",
            "tool_name": sample.tool_name,
            "schema_version": sample.schema_version,
        },
    )
    await store.put_object_key(tenant_id, stored.id, key)
    await store.put_object(
        tenant_id,
        stored,
        event=Event(
            type=EventType.OBJECT_CREATED,
            object_id=stored.id,
            actor=Actor.JOB,
            provider=source.provenance.provider,
            detail={"source_episode": source.id, EPISODE_KIND: DECISION_SAMPLE},
        ),
    )
    return stored
