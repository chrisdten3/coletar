"""Keep the turn, then decide about it later (docs/CAPTURE_AND_BATCH.md).

Extraction has two paths with different quality, and they drift apart: backfill can
name a third party, live sync cannot, because the pattern extractor only emits
first-person memories. A user imports their history and coletar knows who Amanda is;
they say the same thing in the composer tomorrow and it does not.

Replacing the live extractor is not the fix — measured, the heuristic beats every
frontier model on live turns on precision, recall, `kind` and latency at once, and
at 1.5–5s per turn a model is felt by the person waiting.

So split the two. Store the turn verbatim as an `EPISODE`, synchronously and
cheaply; run the heuristic immediately so anything it catches is available at once;
and let a batch pass re-extract later at frontier quality. The user gets a memory
immediately at heuristic quality and eventually at frontier quality, from one
pipeline rather than two.

**The turn is captured whether or not extraction found anything.** A turn the
heuristic missed is precisely what the batch pass is for, so capturing only the
successes would defeat the design.

`EPISODE` is §6's own object type, created until now only by `seed.py`. A captured
turn is what it is for.
"""

from __future__ import annotations

from typing import Any

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
    """Store one turn verbatim, before anything has judged it.

    Locality defaults to the *calling surface only*. A raw turn is not a memory the
    user chose to keep and share; it is working material, and defaulting it to every
    surface would put text typed into one assistant in front of another before a
    human ever reviewed it. The batch pass sets locality on what it derives.

    Carries a TTL so `coletar expire` reaches it. An episode without one would
    outlive every retention promise the product makes.
    """
    from coletar.config import get_settings

    settings = get_settings()
    episode = ContextObject(
        type=ObjectType.EPISODE,
        content=text,
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
        payload={PENDING: True},
    )
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
