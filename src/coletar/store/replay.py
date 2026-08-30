"""Event-log replay (SCOPE §5, §6).

The Event/Revision Log is the provenance record, so it has to be able to answer the
question a user actually asks in the Context Inspector: *what did this say before,
and when did it change?* That is what this module is for -- and it is also the
undo primitive, since restoring a prior state is writing a replayed object back.

Replay reads only the log. It never consults the object table, which is the point:
if the two ever disagree, the log is what we can defend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from coletar.schema.events import Event
from coletar.schema.objects import ContextObject, object_from_record
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Replay has to see an object's whole history, not a page of it.
_HISTORY_LIMIT = 10_000


@dataclass(frozen=True)
class Revision:
    at: datetime
    event: Event
    state: ContextObject


async def replay_object(
    store: Store, tenant_id: TenantId, object_id: str, *, at: datetime | None = None
) -> ContextObject | None:
    """The object's state as of `at` (default: now).

    Returns None when the log holds no revision for it at or before that moment --
    which is the honest answer for "what did this look like before it existed".
    """
    events = await store.list_events(
        tenant_id, object_id=object_id, until=at, limit=_HISTORY_LIMIT
    )
    # list_events is newest-first, so the first revision we meet is the latest one
    # at or before `at`.
    for event in events:
        if event.is_revision and event.after is not None:
            return object_from_record(event.after)
    return None


async def replay_history(store: Store, tenant_id: TenantId, object_id: str) -> list[Revision]:
    """Every recorded state of one object, oldest first — the Inspector's timeline."""
    events = await store.list_events(tenant_id, object_id=object_id, limit=_HISTORY_LIMIT)
    return [
        Revision(at=event.at, event=event, state=object_from_record(event.after))
        for event in reversed(events)
        if event.is_revision and event.after is not None
    ]
