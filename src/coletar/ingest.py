"""The ingest boundary (SCOPE §5, §4.1).

Every path that turns *observed* text into stored objects goes through here: the
local proxy's extraction, the MCP connector's `write_memory`, and later the export
parser. What they share is that the same fact will arrive more than once — a user
states a preference in January and again in March, or the proxy sees it in two
conversations — and the graph should not grow a copy each time.

**Why this is not in `Store`.** A compiler, a migration job or a replay must be able
to write an exact object without an ingest policy quietly interfering. `put_object`
stays a faithful put; deduplication is a property of *ingestion*, so it lives at the
boundary the observing paths share and nowhere else.

**Why it matters more than it looks.** Near-duplicates are already dropped at
assembly time, which protects retrieval — the model never sees the same fact twice.
It does not protect the compiler: a compile reads `list_objects`, so True Migration
would faithfully emit every duplicate the proxy ever wrote, into a destination with
finite context. Read-time deduplication hides the problem from the place that can
tolerate it and leaves it in the place that cannot.

**A duplicate corroborates; it does not vanish.** That the user said something again,
in a different session, is real provenance. Dropping it silently throws that away.
So the existing object gets an `object.corroborated` event and a refreshed
`updated_at`, and its confidence is deliberately left alone -- repetition is weak
evidence, and inflating a score on it is arithmetic nobody asked for.

**Reconcile.** Token overlap can say "these look alike" but not what the new memory
does to the old one: "Moved to Denver" and "Lives in Boston" share no words, while
"Uses Postgres" and "Uses Postgres 16 with pgvector" share nearly all of them and
must not be folded together. So, by default, the nearest few stored memories are
put to Jev as a four-way question (docs/DECISION_EVAL.md). The answer is acted on
only above `reconcile_floor`: a wrong `supersedes` retires something true, so below
the floor both memories are kept and the new one is flagged. Whenever Jev cannot
answer, the token-overlap check runs instead, because it is what this boundary did
before and it never retires anything.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from coletar.extraction.decisions import (
    STAGE_VERSION,
    ReconcileDecision,
    Statement,
    reconcile,
)
from coletar.extraction.jev import JevConfigurationError, JevUnavailable
from coletar.retrieval.context import NEAR_DUPLICATE_THRESHOLD
from coletar.retrieval.embedding import tokenize
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    ContextObject,
    Edge,
    EdgeType,
    ObjectType,
    Provider,
    Scope,
)
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: What a new claim is compared against: other claims. Entities, projects,
#: conversations and artifacts are descriptions or containers ("a project the user
#: is building"), and folding a claim into one would leave no claim at all.
RECONCILABLE: frozenset[ObjectType] = frozenset(
    {ObjectType.MEMORY, ObjectType.FACT, ObjectType.DECISION}
)

#: How many candidates to consider. Deduplication only needs the nearest few: if the
#: same fact is not in the top handful for its own text, it is not a duplicate.
_CANDIDATES = 8


@dataclass(frozen=True)
class IngestResult:
    """What ingestion did, so a caller can say so rather than guess."""

    object_id: str
    created: bool
    #: Set when the write was folded into an object that already said this.
    corroborated: str | None = None
    #: Set when the new memory replaced a stored one.
    superseded: str | None = None
    #: Stored memories the new one may conflict with, kept rather than acted on.
    flagged: list[str] = field(default_factory=list)

    @property
    def stored(self) -> bool:
        return self.created


def is_near_duplicate(a: str, b: str) -> bool:
    """The same notion of sameness the assembly stage uses, so a fact judged a
    duplicate at read time is judged a duplicate at write time."""
    left, right = set(tokenize(a)), set(tokenize(b))
    if not left or not right:
        return False
    return len(left & right) / min(len(left), len(right)) >= NEAR_DUPLICATE_THRESHOLD


async def find_duplicate(
    store: Store,
    tenant_id: TenantId,
    content: str,
    *,
    scope: Scope | None = None,
    caller_surface: Provider | None = None,
    types: frozenset[ObjectType] | None = None,
) -> ContextObject | None:
    """The context lookup that happens *before* a write.

    `types`, when given, limits what may count as the duplicate.

    `caller_surface` matters here for the same reason it matters on every other
    read path: without it, a write from surface A could silently corroborate an
    object that is `local_only` to surface B, leaving the fact invisible on the very
    surface that just "wrote" it. Filtering the duplicate search the same way a
    normal read would keeps a write's own next read consistent with it.
    """
    for hit in await store.search(
        tenant_id, content, scope=scope, caller_surface=caller_surface, top_k=_CANDIDATES
    ):
        # A captured EPISODE may contain exactly the same sentence as the MEMORY
        # derived from it. They are evidence and claim, not duplicates. Folding the
        # memory into the episode leaves the graph with no memory at all.
        if hit.obj.type is ObjectType.EPISODE:
            continue
        if types is not None and hit.obj.type not in types:
            continue
        if is_near_duplicate(content, hit.obj.content):
            return hit.obj
    return None


async def _neighbours(
    store: Store,
    tenant_id: TenantId,
    memory: ContextObject,
    caller_surface: Provider | None,
    limit: int,
) -> list[ContextObject]:
    hits = await store.search(
        tenant_id, memory.content, scope=memory.scope, caller_surface=caller_surface,
        top_k=_CANDIDATES,
    )
    # Claims only. This also keeps out episodes, which are evidence rather than
    # claims (see `find_duplicate`) and are the raw turns reconcile must never send.
    return [h.obj for h in hits if h.obj.type in RECONCILABLE][:limit]


def _said(obj: ContextObject) -> Statement:
    return Statement(obj.content, (obj.valid_from or obj.created_at).date())


@dataclass
class _Reconciled:
    duplicate: ContextObject | None = None
    supersedes: ContextObject | None = None
    contradicts: list[ContextObject] = field(default_factory=list)
    flagged: list[tuple[ContextObject, ReconcileDecision]] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


async def _reconcile(
    store: Store,
    tenant_id: TenantId,
    memory: ContextObject,
    caller_surface: Provider | None,
    client: httpx.AsyncClient | None,
) -> _Reconciled | None:
    """Ask Jev about each neighbour. None means Jev could not answer."""
    from coletar.config import get_settings

    settings = get_settings()
    neighbours = await _neighbours(
        store, tenant_id, memory, caller_surface, settings.reconcile_neighbours
    )
    result = _Reconciled(detail={"provider": "jev", "stage": STAGE_VERSION, "answers": []})
    if not neighbours:
        return result
    candidate = _said(memory)
    try:
        decisions = await asyncio.gather(
            *(reconcile(candidate, _said(n), client=client) for n in neighbours)
        )
    except (JevUnavailable, JevConfigurationError):
        return None

    best_supersede = 0.0
    for neighbour, decision in zip(neighbours, decisions, strict=True):
        result.detail["answers"].append(
            {
                "object_id": neighbour.id,
                "label": decision.label,
                "confidence": decision.confidence,
                "model": decision.evaluation.model,
                "request_id": decision.evaluation.request_id,
            }
        )
        if decision.label == "new":
            continue
        if decision.confidence < settings.reconcile_floor:
            result.flagged.append((neighbour, decision))
        elif decision.label == "duplicate":
            result.duplicate = result.duplicate or neighbour
        elif decision.label == "supersedes":
            if decision.confidence > best_supersede:
                best_supersede = decision.confidence
                result.supersedes = neighbour
        else:
            result.contradicts.append(neighbour)
    return result


async def remember(
    store: Store,
    tenant_id: TenantId,
    memory: ContextObject,
    *,
    event: Event | None = None,
    dedup: bool = True,
    caller_surface: Provider | None = None,
    jev_client: httpx.AsyncClient | None = None,
) -> IngestResult:
    """Store one observed memory or fact, reconciling it against stored claims.

    Facts come through here as well as memories. A fact is as much a claim about the
    user as a memory is, and "I transferred to Stanford" must be able to retire "I
    am a student at Georgetown" whichever of the two types extraction chose.

    `dedup=False` exists for paths that must write exactly what they were given --
    a replay, or a test asserting about storage rather than about ingestion. It is
    not the default, because every path that observes text should be reconciling.
    """
    from coletar.config import get_settings

    # A correction is *supposed* to resemble what it corrects, so it must never be
    # folded into it -- that would silently discard the correction and leave the
    # stale fact standing. The caller has already said what it replaces.
    if not dedup or memory.supersedes is not None:
        stored = await store.put_object(tenant_id, memory, event=event)
        return IngestResult(object_id=stored.id, created=True)

    # Reconcile may set `supersedes` or add a flag; the caller's object stays as given.
    memory = memory.model_copy(deep=True)
    outcome: _Reconciled | None = None
    fallback: str | None = None
    if get_settings().reconcile_provider == "jev":
        outcome = await _reconcile(store, tenant_id, memory, caller_surface, jev_client)
        if outcome is None:
            fallback = "jev_unavailable"

    if outcome is None:
        existing = await find_duplicate(
            store, tenant_id, memory.content, scope=memory.scope,
            caller_surface=caller_surface, types=RECONCILABLE,
        )
        detail: dict[str, Any] = {"provider": "token_overlap"}
        if fallback:
            detail["fallback_reason"] = fallback
        outcome = _Reconciled(duplicate=existing, detail=detail)

    if outcome.duplicate is not None:
        await store.append_event(
            tenant_id,
            Event(
                type=EventType.OBJECT_CORROBORATED,
                object_id=outcome.duplicate.id,
                actor=(event.actor if event else Actor.SYSTEM),
                provider=memory.provenance.provider,
                detail={
                    "restated_as": memory.content,
                    "extraction_method": memory.extraction_method,
                    "reconcile": outcome.detail,
                },
            ),
        )
        return IngestResult(
            object_id=outcome.duplicate.id, created=False, corroborated=outcome.duplicate.id
        )

    if outcome.supersedes is not None:
        memory.supersedes = outcome.supersedes.id
    if outcome.flagged:
        # Kept, not acted on. The flag lives in payload so the Context Inspector
        # can show the pair and a human can decide what the model would not.
        memory.payload = {
            **memory.payload,
            "reconcile_flags": [
                {"object_id": n.id, "label": d.label, "confidence": d.confidence}
                for n, d in outcome.flagged
            ],
        }
    write_event = event or Event(
        type=EventType.OBJECT_CREATED,
        object_id=memory.id,
        actor=Actor.SYSTEM,
        provider=memory.provenance.provider,
        detail={"type": memory.type, "scope": str(memory.scope)},
    )
    write_event = write_event.model_copy(
        update={"detail": {**write_event.detail, "reconcile": outcome.detail}}
    )
    stored = await store.put_object(tenant_id, memory, event=write_event)
    for other in outcome.contradicts:
        await store.add_edge(
            tenant_id, Edge(src_id=stored.id, dst_id=other.id, type=EdgeType.CONTRADICTS)
        )
    return IngestResult(
        object_id=stored.id,
        created=True,
        superseded=outcome.supersedes.id if outcome.supersedes else None,
        flagged=[n.id for n, _ in outcome.flagged] + [o.id for o in outcome.contradicts],
    )
