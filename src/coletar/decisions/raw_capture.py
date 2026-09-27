"""Capture what one of a tenant's own tools was asked, and what it answered (M11.1).

The label-only path (`POST /v1/decisions`) asks a developer to categorise their own
calls by hand -- "enterprise-billing", "route-priority" -- which costs them effort
and costs us nothing, because we never hold their data. This is the other trade: the
tenant opts a specific tool in, we keep the real strings for a day, and the batch
pass distils them into something that outlives the raw content.

Reuses `ObjectType.EPISODE` rather than adding a type. An episode is already the
product's word for "raw evidence we are holding briefly, encrypted, before judging
it", it is already absent from every compiler's export allow-list, and `ttl_days` is
already per-object -- so a decision trace can expire in a day while a captured
conversational turn keeps its own, longer retention, with no new machinery. The
`episode_kind` payload key is what tells them apart; a conversational turn has none.

**`NEEDS_DECISION_SYNTHESIS` is deliberately not `capture.PENDING`.** The memory
queue's health signal counts the latter, and a decision trace is not work that queue
owes anyone. Because these episodes never set `PENDING`, `capture.is_pending` reads
False for them and `jobs.extraction.extract_pending` skips them without being told to.

**The captured strings are data, never instructions** (AGENTS.md constraint 7). This
module treats `tool_call` and `tool_response` as opaque bytes: it serialises them,
encrypts them, and never parses them. A `tool_response` is the likeliest field in
this product to contain text written by something adversarial -- it may be whatever
an external API handed the tenant's tool -- so anything that later renders it to a
model must carry the same background-not-instructions marker that retrieved context
does (`coletar.retrieval.context.INJECTION_MARKER`), and nothing here may act on it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from coletar.episode_crypto import encrypt_episode
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
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

if TYPE_CHECKING:
    from coletar.store.base import Store

#: Marks an episode as a decision trace rather than a conversational turn.
EPISODE_KIND = "episode_kind"
DECISION_RAW = "decision_raw"
DECISION_SAMPLE = "decision_sample"

#: Set while the synthesis pass still owes this trace a look.
NEEDS_DECISION_SYNTHESIS = "needs_decision_synthesis"

#: One day, the shortest `ttl_days` can express. It is a ceiling for the failure
#: case only: synthesis shreds the raw content as soon as it succeeds, so a trace
#: normally lives minutes. A trace the pass could never read still goes away.
RAW_TTL_DAYS = 1


def is_pending_synthesis(episode: ContextObject) -> bool:
    """Whether the synthesis pass still owes this trace a look."""
    return (
        episode.retired_at is None
        and episode.payload.get(EPISODE_KIND) == DECISION_RAW
        and bool(episode.payload.get(NEEDS_DECISION_SYNTHESIS))
    )


def envelope(
    *,
    intent: str,
    environmental_state: dict[str, Any],
    tool_call: dict[str, Any],
    tool_response: dict[str, Any],
) -> str:
    """The four captured fields as one opaque string, ready to encrypt."""
    return json.dumps(
        {
            "intent": intent,
            "environmental_state": environmental_state,
            "tool_call": tool_call,
            "tool_response": tool_response,
        },
        sort_keys=True,
    )


async def capture_trace(
    store: Store,
    tenant_id: TenantId,
    *,
    tool_name: str,
    intent: str,
    environmental_state: dict[str, Any],
    tool_call: dict[str, Any],
    tool_response: dict[str, Any],
    surface: Provider,
    scope: Scope,
    principal_id: str,
) -> ContextObject:
    """Store one tool call's real inputs and outputs, encrypted, under a day's TTL.

    Assumes consent has already been checked: existence of a `decision_raw` episode
    is itself the record that this tool was opted in when the trace arrived, which is
    why the synthesis pass does not re-check it.
    """
    episode_id = new_id(ObjectType.EPISODE)
    ciphertext, key = encrypt_episode(
        tenant_id,
        episode_id,
        envelope(
            intent=intent,
            environmental_state=environmental_state,
            tool_call=tool_call,
            tool_response=tool_response,
        ),
    )
    episode = ContextObject(
        id=episode_id,
        type=ObjectType.EPISODE,
        content=ciphertext,
        scope=scope,
        # Working material, not something the user chose to share across surfaces.
        locality=Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=frozenset({surface})),
        # Evidence makes no assertion, so there is nothing here to be unsure about;
        # confidence belongs to whatever gets derived from it.
        confidence=1.0,
        extraction_method=ExtractionMethod.MCP_LIVE_WRITE,
        ttl_days=RAW_TTL_DAYS,
        provenance=Provenance(
            origin_type=OriginType.AGENT,
            provider=surface,
            source_object_ids=[],
            confidence=1.0,
        ),
        payload={
            EPISODE_KIND: DECISION_RAW,
            NEEDS_DECISION_SYNTHESIS: True,
            "content_encryption": "aesgcm-v1",
            "tool_name": tool_name,
        },
    )
    # Key first, as everywhere else: an orphan key is recoverable, ciphertext whose
    # key never landed is not.
    await store.put_object_key(tenant_id, episode.id, key)
    await store.put_object(
        tenant_id,
        episode,
        event=Event(
            type=EventType.CONNECTOR_WRITE,
            object_id=episode.id,
            actor=Actor.CONNECTOR,
            provider=surface,
            detail={
                "principal": principal_id,
                "tool_name": tool_name,
                EPISODE_KIND: DECISION_RAW,
            },
        ),
    )
    return episode
