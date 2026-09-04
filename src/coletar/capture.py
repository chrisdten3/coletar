"""Keep the turn, then decide about it later (docs/CAPTURE_AND_BATCH.md).

Extraction has two paths with different quality, and they drift apart: backfill can
name a third party, live sync cannot, because the pattern extractor only emits
first-person memories. A user imports their history and coletar knows who Amanda is;
they say the same thing in the composer tomorrow and it does not.

Putting a model in front of the composer response is not the fix: at 1.5–5s per turn
it is felt by the person waiting. Nor is a provisional regex write justified by a
narrow fixture on which its rules were tuned; on transient task context the same
extractor measures 42.1% precision.

So split capture from judgement. Store an encrypted, lossless copy of the turn as an
`EPISODE`, synchronously and cheaply, and let a background model pass decide what is
durable.
The legacy heuristic path remains available to installations that decline raw-turn
retention, but `collect_then_batch` does not write a preliminary regex memory.

**The turn is captured whether or not extraction found anything.** A turn the
heuristic missed is precisely what the batch pass is for, so capturing only the
successes would defeat the design.

`EPISODE` is §6's own object type, created until now only by `seed.py`. A captured
turn is what it is for.
"""

from __future__ import annotations

from typing import Any

from coletar.episode_crypto import encrypt_episode
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    ExtractionMethod,
    Locality,
    LocalityMode,
    ObjectType,
    OriginType,
    Provenance,
    Provider,
    Scope,
    new_id,
)
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Marks an episode the model pass has not yet seen. The queue is a payload flag and
#: not a table: §2 says a subtype's extras live in `payload`, and a second table
#: would be a second place for "what still needs doing" to be wrong.
PENDING = "needs_model_extraction"


def is_pending(episode: ContextObject) -> bool:
    """Whether the batch pass still owes this turn a look."""
    return bool(episode.payload.get(PENDING))


async def capture_turn(
    store: Store,
    tenant_id: TenantId,
    text: str,
    *,
    surface: Provider,
    scope: Scope = GLOBAL_SCOPE,
    locality: Locality | None = None,
    principal_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> ContextObject:
    """Store one turn losslessly under a disposable key, before judging it.

    Locality defaults to the *calling surface only*. A raw turn is not a memory the
    user chose to keep and share; it is working material, and defaulting it to every
    surface would put text typed into one assistant in front of another before a
    human ever reviewed it. The batch pass sets locality on what it derives.

    Carries a TTL so `coletar expire` reaches it. An episode without one would
    outlive every retention promise the product makes.
    """
    from coletar.config import get_settings

    settings = get_settings()
    episode_id = new_id(ObjectType.EPISODE)
    ciphertext, key = encrypt_episode(tenant_id, episode_id, text)
    episode = ContextObject(
        id=episode_id,
        type=ObjectType.EPISODE,
        content=ciphertext,
        scope=scope,
        locality=locality
        or Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({surface})),
        # A verbatim turn is not a claim. Confidence describes how much we believe an
        # assertion, and there is no assertion here yet — the episode is evidence,
        # and the objects derived from it carry the confidence.
        confidence=1.0,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        ttl_days=settings.capture_ttl_days,
        provenance=Provenance(
            origin_type=OriginType.USER,
            provider=surface,
            source_object_ids=[],
            confidence=1.0,
        ),
        payload={PENDING: True, "content_encryption": "aesgcm-v1"},
    )
    # Key first: a crash may leave an orphan random key, but never ciphertext whose
    # content cannot be recovered before its retention period has elapsed.
    await store.put_object_key(tenant_id, episode.id, key)
    await store.put_object(
        tenant_id,
        episode,
        event=Event(
            type=EventType.CONNECTOR_WRITE,
            object_id=episode.id,
            actor=Actor.USER,
            provider=surface,
            detail={"principal": principal_id, "captured": True, **(detail or {})},
        ),
    )
    return episode
