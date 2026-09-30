"""Model-extract captured episodes without making the live request wait.

The live collect-then-batch mode writes no provisional regex memory. A fast false
positive is still a false positive, and the existing live fixture is too narrow to
justify exposing one before semantic extraction. This job is the authoritative
extraction pass for captured turns.

**The gate.** Most turns hold nothing durable, and each one sent to the extractor
is a paid model call. So, by default, Jev is first asked whether the turn is worth
remembering at all (docs/DECISION_EVAL.md). The threshold is set for recall, because
a turn the gate rejects is never looked at again. For the same reason the gate
fails open: if Jev cannot answer, the turn goes to the extractor as if the gate did
not exist, and the episode records that it was not gated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from coletar.capture import PENDING, is_pending
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
from coletar.extraction import extract_with_model
from coletar.extraction.decisions import STAGE_VERSION, gate
from coletar.extraction.jev import JevConfigurationError, JevUnavailable
from coletar.extraction.providers import (
    ExtractionProviderName,
    ExtractionUnavailable,
    configured_model,
)
from coletar.ingest import remember
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import GLOBAL_LOCALITY, ContextObject, Edge, Memory, ObjectType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

SCAN_LIMIT = 10_000


def _stable_id(episode_id: str, obj: ContextObject) -> str:
    """Make a retry address the same object after a crash before queue acknowledgement."""
    digest = hashlib.sha256(
        f"{episode_id}\0{obj.type}\0{obj.content.casefold()}".encode()
    ).hexdigest()[:16]
    prefix = {
        ObjectType.MEMORY: "mem",
        ObjectType.ENTITY: "ent",
        ObjectType.FACT: "fact",
    }.get(obj.type, "obj")
    return f"{prefix}_{digest}"


@dataclass
class ExtractionBatchReport:
    scanned: int = 0
    processed: int = 0
    gated_out: int = 0
    unavailable: int = 0
    objects: int = 0
    edges: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "processed": self.processed,
            "gated_out": self.gated_out,
            "unavailable": self.unavailable,
            "objects": self.objects,
            "edges": self.edges,
        }


async def _gate(
    transcript: str, threshold: float, client: httpx.AsyncClient | None
) -> dict[str, Any]:
    """Jev's verdict on one turn, as recorded on the episode. Never raises for Jev."""
    try:
        decision = await gate(transcript, client=client)
    except (JevUnavailable, JevConfigurationError) as exc:
        return {"provider": "jev", "status": "unavailable", "reason": exc.__class__.__name__}
    return {
        "provider": "jev",
        "status": "passed" if decision.probability >= threshold else "rejected",
        "probability": decision.probability,
        "threshold": threshold,
        "stage": STAGE_VERSION,
        "model": decision.evaluation.model,
        "request_id": decision.evaluation.request_id,
    }


async def extract_pending(
    store: Store,
    tenant_id: TenantId,
    *,
    provider: ExtractionProviderName | None = None,
    model: str | None = None,
    limit: int | None = None,
    jev_client: httpx.AsyncClient | None = None,
) -> ExtractionBatchReport:
    """Process pending episodes once; unavailable turns remain pending for retry."""
    from coletar.config import get_settings

    settings = get_settings()
    chosen_provider = provider or settings.extraction_provider
    chosen_model = model or configured_model(chosen_provider)
    chosen_limit = limit or settings.extraction_batch_size
    # The Store protocol intentionally has no subtype-payload query. Scan the bounded
    # episode set, then apply the batch limit, or an old prefix of completed episodes
    # can permanently hide pending work behind it.
    episodes = await store.list_objects(tenant_id, type=ObjectType.EPISODE, limit=SCAN_LIMIT)
    pending = [episode for episode in episodes if is_pending(episode)][:chosen_limit]
    report = ExtractionBatchReport(scanned=len(episodes))

    for episode in pending:
        try:
            transcript = await decrypt_episode(store, tenant_id, episode)
        except EpisodeKeyUnavailable as exc:
            await _unavailable(store, tenant_id, episode, exc, chosen_provider, chosen_model)
            report.unavailable += 1
            continue

        verdict: dict[str, Any] | None = None
        if settings.gate_provider == "jev":
            verdict = await _gate(transcript, settings.gate_threshold, jev_client)
            if verdict["status"] == "rejected":
                await _complete(
                    store, tenant_id, episode,
                    {"gate": verdict, "extracted_objects": 0},
                    {"model_extraction_complete": True, "gated_out": True, "gate": verdict},
                )
                report.gated_out += 1
                continue

        try:
            objects, edges = await extract_with_model(
                transcript=transcript,
                scope=episode.scope,
                provider=episode.provenance.provider,
                extraction_provider=chosen_provider,
                model=chosen_model,
            )
        except ExtractionUnavailable as exc:
            report.unavailable += 1
            await _unavailable(store, tenant_id, episode, exc, chosen_provider, chosen_model)
            continue

        proposed_to_stored: dict[str, str] = {}
        for obj in objects:
            proposed_id = obj.id
            obj.id = _stable_id(episode.id, obj)
            # The raw evidence stays surface-local. The durable object is the output
            # intended for the canonical cross-surface graph.
            obj.locality = GLOBAL_LOCALITY
            obj.provenance.source_object_ids = [episode.id]
            existing_stable = await store.get_object(tenant_id, obj.id)
            if existing_stable is not None:
                proposed_to_stored[proposed_id] = existing_stable.id
                continue
            event = Event(
                type=EventType.OBJECT_CREATED,
                object_id=obj.id,
                actor=Actor.SYSTEM,
                provider=episode.provenance.provider,
                detail={
                    "episode": episode.id,
                    "extraction_provider": chosen_provider,
                    "extraction_model": chosen_model,
                },
            )
            # Memories and facts are both claims, so both are reconciled: a fact
            # written straight to the store could never retire the one it replaces.
            if isinstance(obj, Memory) or obj.type is ObjectType.FACT:
                result = await remember(
                    store, tenant_id, obj, event=event, jev_client=jev_client
                )
                stored_id = result.object_id
            elif obj.type is ObjectType.ENTITY:
                name = str(obj.payload.get("name", ""))
                existing = await store.find_entity(tenant_id, name) if name else None
                if existing is None:
                    stored_id = (await store.put_object(tenant_id, obj, event=event)).id
                else:
                    stored_id = existing.id
                    await store.append_event(
                        tenant_id,
                        Event(
                            type=EventType.OBJECT_CORROBORATED,
                            object_id=stored_id,
                            actor=Actor.SYSTEM,
                            provider=episode.provenance.provider,
                            detail={"episode": episode.id, "restated_as": obj.content},
                        ),
                    )
            else:
                stored_id = (await store.put_object(tenant_id, obj, event=event)).id
            proposed_to_stored[proposed_id] = stored_id
            report.objects += 1

        for edge in edges:
            mapped = Edge(
                src_id=proposed_to_stored.get(edge.src_id, edge.src_id),
                dst_id=proposed_to_stored.get(edge.dst_id, edge.dst_id),
                type=edge.type,
                confidence=edge.confidence,
                created_at=edge.created_at,
            )
            await store.add_edge(tenant_id, mapped)
            report.edges += 1

        await _complete(
            store, tenant_id, episode,
            {
                "extraction_provider": chosen_provider,
                "extraction_model": chosen_model,
                "extracted_objects": len(objects),
                **({"gate": verdict} if verdict else {}),
            },
            {"model_extraction_complete": True, **({"gate": verdict} if verdict else {})},
        )
        report.processed += 1

    return report


async def _unavailable(
    store: Store,
    tenant_id: TenantId,
    episode: ContextObject,
    exc: Exception,
    extraction_provider: str,
    extraction_model: str,
) -> None:
    # The episode stays pending, so nothing is lost — but a retry that leaves no
    # trace makes a provider outage look exactly like an empty queue. `coletar
    # queue-health` reads these back.
    await store.append_event(
        tenant_id,
        Event(
            type=EventType.EXTRACTION_UNAVAILABLE,
            object_id=episode.id,
            actor=Actor.JOB,
            provider=episode.provenance.provider,
            detail={
                "reason": exc.__class__.__name__,
                "extraction_provider": extraction_provider,
                "extraction_model": extraction_model,
            },
        ),
    )


async def _complete(
    store: Store,
    tenant_id: TenantId,
    episode: ContextObject,
    payload: dict[str, Any],
    detail: dict[str, Any],
) -> None:
    """Take the episode off the queue, recording why, in one evented write."""
    episode.payload = {**episode.payload, PENDING: False, **payload}
    await store.put_object(
        tenant_id,
        episode,
        event=Event(
            type=EventType.OBJECT_UPDATED,
            object_id=episode.id,
            actor=Actor.SYSTEM,
            provider=episode.provenance.provider,
            detail=detail,
        ),
    )
