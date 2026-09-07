"""Inspector operations and the compile gate (SCOPE §8.2, ROADMAP M5).

Everything the Inspector can do to the graph lives here rather than in the HTTP
layer, so the gate and the edits are testable without a browser and reusable from
the CLI. The web app is a rendering of this module, not the other way round.

**Review state is derived, never stored.** There is no `reviewed` column. An object
has been reviewed when the event log holds an `object.reviewed` for it that is *not
older than the object's last change* — so reviewing a fact and then editing it does
not leave a stale approval standing. That falls out of §2 (a property that applies
to one workflow does not earn a column) and of the log already being the provenance
record; adding a boolean would have created a second source of truth that replay
could not reconstruct.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from coletar.compiler.emit import compile_eligible
from coletar.inspector.library import SWITCHABLE
from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import (
    GLOBAL_LOCALITY,
    ContextObject,
    Locality,
    LocalityMode,
    ObjectType,
    Provider,
    Scope,
)
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: The surfaces a user can actually choose between. Imported from the library so
#: the switcher and the reach editor cannot disagree about what exists.
SWITCHABLE_SURFACES = SWITCHABLE

#: The log is read in one pass and can be long; this bounds it without bounding the
#: graph, which is the thing the gate actually reasons about.
_EVENT_SCAN_LIMIT = 100_000


class InspectorError(Exception):
    """A refusal the user can act on, phrased for them rather than for a log."""


@dataclass(frozen=True)
class ReviewStatus:
    eligible: list[ContextObject]
    unreviewed: list[ContextObject]

    @property
    def can_compile(self) -> bool:
        return not self.unreviewed

    @property
    def reviewed_count(self) -> int:
        return len(self.eligible) - len(self.unreviewed)


async def _reviewed_at(store: Store, tenant_id: TenantId) -> dict[str, datetime]:
    """Most recent review per object, from the log."""
    latest: dict[str, datetime] = {}
    for event in await store.list_events(tenant_id, limit=_EVENT_SCAN_LIMIT):
        if event.type is EventType.OBJECT_REVIEWED and event.object_id:
            current = latest.get(event.object_id)
            if current is None or event.at > current:
                latest[event.object_id] = event.at
    return latest


async def review_status(store: Store, tenant_id: TenantId) -> ReviewStatus:
    """What the compile gate is looking at.

    The eligible set is the compiler's own, so the gate can never be checking a
    different population than the one that would be compiled.
    """
    eligible = compile_eligible(await store.list_objects(tenant_id, limit=10_000))
    reviewed = await _reviewed_at(store, tenant_id)
    unreviewed = [
        obj
        for obj in eligible
        # A review older than the object's last change is not a review of what the
        # object now says.
        if obj.id not in reviewed or reviewed[obj.id] < obj.updated_at
    ]
    return ReviewStatus(eligible=eligible, unreviewed=unreviewed)


async def _load(store: Store, tenant_id: TenantId, object_id: str) -> ContextObject:
    obj = await store.get_object(tenant_id, object_id)
    if obj is None:
        raise InspectorError(f"no object {object_id!r} in this tenant")
    return obj


async def mark_reviewed(store: Store, tenant_id: TenantId, object_id: str) -> None:
    """Record that a human has actually looked at this object."""
    await _load(store, tenant_id, object_id)
    await store.append_event(
        tenant_id,
        Event(type=EventType.OBJECT_REVIEWED, object_id=object_id, actor=Actor.USER),
    )


async def erase_episode(store: Store, tenant_id: TenantId, object_id: str) -> None:
    """Retire a captured raw turn and destroy the only key that can read it."""
    obj = await _load(store, tenant_id, object_id)
    if obj.type is not ObjectType.EPISODE:
        raise InspectorError("only a raw episode can be erased from the episode queue")
    await store.retire_object(tenant_id, object_id, reason="user_erased_raw_episode")
    await store.shred_object_key(
        tenant_id, object_id, reason="user_erased_raw_episode"
    )


async def edit(
    store: Store, tenant_id: TenantId, object_id: str, *, content: str
) -> ContextObject:
    """Correct what an object says, in place.

    In place rather than as a supersession: a supersedes chain means "this used to
    be true and now something else is", which is a claim about the world. A typo or
    a bad extraction is a claim about *the record*, and inventing a false history
    for it would make the chain useless for the thing it exists to express. Nothing
    is lost either way — the event carries full before/after state, so `coletar
    history` still shows what it used to say (constraint 6).
    """
    content = content.strip()
    if not content:
        raise InspectorError("content cannot be empty; retire the object instead")
    obj = await _load(store, tenant_id, object_id)
    if content == obj.content:
        return obj
    obj.content = content
    stored = await store.put_object(tenant_id, obj)
    # Editing is reviewing: the user just read it closely enough to change it.
    await mark_reviewed(store, tenant_id, object_id)
    return stored


async def rescope(
    store: Store, tenant_id: TenantId, object_id: str, *, scope: Scope
) -> ContextObject:
    """Move an object between global and a project.

    The single most consequential thing a human can fix here. `scope_preservation`
    is a hard gate on the compiler, but the compiler can only preserve the scope it
    is given — a fact filed globally that belongs to one project will be compiled
    faithfully into every destination the user owns. This is where that gets caught.
    """
    obj = await _load(store, tenant_id, object_id)
    if obj.scope == scope:
        return obj
    before = str(obj.scope)
    obj.scope = scope
    stored = await store.put_object(
        tenant_id,
        obj,
        event=Event(
            type=EventType.OBJECT_RESCOPED,
            object_id=object_id,
            actor=Actor.USER,
            detail={"from": before, "to": str(scope)},
        ),
    )
    await mark_reviewed(store, tenant_id, object_id)
    return stored


async def set_locality(
    store: Store, tenant_id: TenantId, object_id: str, *, surfaces: frozenset[Provider]
) -> ContextObject:
    """Choose which surfaces may read this object back.

    `surfaces` is the whole intended set, not a delta, so the caller cannot express
    a half-applied change: a form that posts three checkboxes is one decision, and
    applying it as three separate writes would leave the graph briefly in a state
    the user never asked for and the log holding three events for one act.

    An empty set is refused rather than stored. `Locality` already rejects
    `LOCAL_ONLY` with no surfaces, but the message it raises is about the model;
    this one is about what the user just tried to do — restrict a memory to nobody,
    which is retirement wearing the wrong control.

    Every surface selected means the object is `SYNCED` again, not `LOCAL_ONLY`
    naming all of them. Those two are indistinguishable to a reader today and would
    diverge the moment a fifth surface exists: the first says "wherever I go", the
    second freezes a list that was accurate when it was written.
    """
    if not surfaces:
        raise InspectorError(
            "an object restricted to no surface is unreachable; retire it instead"
        )
    obj = await _load(store, tenant_id, object_id)
    intended = (
        GLOBAL_LOCALITY
        if surfaces >= frozenset(SWITCHABLE_SURFACES)
        else Locality(mode=LocalityMode.LOCAL_ONLY, surfaces=surfaces)
    )
    if intended == obj.locality:
        return obj

    before = str(obj.locality)
    obj.locality = intended
    stored = await store.put_object(
        tenant_id,
        obj,
        event=Event(
            type=EventType.OBJECT_LOCALITY_CHANGED,
            object_id=object_id,
            actor=Actor.USER,
            detail={"from": before, "to": str(intended)},
        ),
    )
    # Same convention as rescope and edit: you cannot decide where a fact may go
    # without having read it.
    await mark_reviewed(store, tenant_id, object_id)
    return stored


async def merge(
    store: Store, tenant_id: TenantId, *, survivor_id: str, absorbed_id: str
) -> ContextObject:
    """Fold one object into another that says the same thing.

    Expressed as a supersession, which is already how the graph says "this replaces
    that": the absorbed object drops out of retrieval and out of compile, and stays
    readable for provenance. Nothing is deleted (constraint 6).
    """
    if survivor_id == absorbed_id:
        raise InspectorError("an object cannot be merged into itself")
    survivor = await _load(store, tenant_id, survivor_id)
    await _load(store, tenant_id, absorbed_id)
    if survivor.supersedes is not None and survivor.supersedes != absorbed_id:
        # Chaining would silently drop the earlier link and with it the older
        # object's route back into history. Better to refuse and say why.
        raise InspectorError(
            f"{survivor_id} already supersedes {survivor.supersedes}; merge into the "
            "head of that chain instead"
        )
    survivor.supersedes = absorbed_id
    stored = await store.put_object(
        tenant_id,
        survivor,
        event=Event(
            type=EventType.OBJECT_MERGED,
            object_id=survivor_id,
            actor=Actor.USER,
            detail={"absorbed": absorbed_id},
        ),
    )
    await mark_reviewed(store, tenant_id, survivor_id)
    return stored
