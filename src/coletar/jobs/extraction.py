"""Model-extract captured episodes without making the live request wait.

The live collect-then-batch mode writes no provisional regex memory. A fast false
positive is still a false positive, and the existing live fixture is too narrow to
justify exposing one before semantic extraction. This job is the authoritative
extraction pass for captured turns.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from coletar.capture import PENDING, is_pending
from coletar.episode_crypto import EpisodeKeyUnavailable, decrypt_episode
from coletar.extraction import extract_with_model
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
    unavailable: int = 0
    objects: int = 0
    edges: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "processed": self.processed,
            "unavailable": self.unavailable,
            "objects": self.objects,
            "edges": self.edges,
        }


async def extract_pending(
    store: Store,
    tenant_id: TenantId,
    *,
    provider: ExtractionProviderName | None = None,
    model: str | None = None,
    limit: int | None = None,
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
            objects, edges = await extract_with_model(
                transcript=transcript,
                scope=episode.scope,
                provider=episode.provenance.provider,
                extraction_provider=chosen_provider,
                model=chosen_model,
            )
        except (EpisodeKeyUnavailable, ExtractionUnavailable) as exc:
            report.unavailable += 1
            # The episode stays pending, so nothing is lost — but a retry that
            # leaves no trace makes a provider outage look exactly like an empty
            # queue. `coletar queue-health` reads these back.
            await store.append_event(
                tenant_id,
                Event(
                    type=EventType.EXTRACTION_UNAVAILABLE,
                    object_id=episode.id,
                    actor=Actor.JOB,
                    provider=episode.provenance.provider,
                    detail={
                        "reason": exc.__class__.__name__,
                        "extraction_provider": chosen_provider,
                        "extraction_model": chosen_model,
                    },
                ),
            )
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
            if isinstance(obj, Memory):
                result = await remember(store, tenant_id, obj, event=event)
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

        episode.payload = {
            **episode.payload,
            PENDING: False,
            "extraction_provider": chosen_provider,
            "extraction_model": chosen_model,
            "extracted_objects": len(objects),
        }
        await store.put_object(
            tenant_id,
            episode,
            event=Event(
                type=EventType.OBJECT_UPDATED,
                object_id=episode.id,
                actor=Actor.SYSTEM,
                provider=episode.provenance.provider,
                detail={"model_extraction_complete": True},
            ),
        )
        report.processed += 1

    return report
