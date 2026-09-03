"""Retire objects whose `ttl_days` has run out (§6, AGENTS.md constraint 6).

`ttl_days` was declared on every object, written into the Postgres columns, and read
by the Inspector to show a user when something would expire — and acted on by
nothing. A retention field that does not retain is worse than none: it reads as a
promise in the schema and in the dashboard, and the data outlives it silently.

Retire, never delete. Constraint 6 is not negotiable here even though expiry is the
one case where deletion sounds reasonable: the user must still be able to see what a
fact used to say and when it stopped applying, and an object that vanishes cannot
explain its own absence. Retired objects leave retrieval and compile; they stay
readable for provenance.

The `expires_at` calculation deliberately matches the Inspector's, because a job
that expires on a different clock from the dashboard showing the countdown is a bug
the user finds before we do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from coletar.schema.objects import ContextObject
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: A hard ceiling on one pass, mirroring the compression job. An expiry sweep that
#: silently truncates is preferable to one that loads an unbounded graph, and the
#: report says how much it looked at.
SCAN_LIMIT = 10_000

#: The reason recorded on the event, so the Inspector can tell a user *why* an
#: object left retrieval — expiry and supersession are not the same story.
REASON = "ttl_expired"


def expires_at(obj: ContextObject) -> datetime | None:
    """When this object stops applying, or None if it never does.

    Same arithmetic as `inspector.metrics._expires_at`. Kept in step deliberately:
    the dashboard shows a countdown and this job acts on it, and two clocks would
    mean an object shown as live disappearing from retrieval, or the reverse.
    """
    if obj.ttl_days is None:
        return None
    return obj.created_at + timedelta(days=obj.ttl_days)


@dataclass
class ExpiryReport:
    scanned: int
    retired: int

    def as_dict(self) -> dict[str, int]:
        return {"scanned": self.scanned, "retired": self.retired}


async def expire(
    store: Store, tenant_id: TenantId, *, now: datetime | None = None
) -> ExpiryReport:
    """Retire every active object whose TTL has passed.

    `now` is injectable because the alternative is a test that sleeps, and a
    retention job nobody can test at speed is a retention job nobody tests.
    """
    moment = now or datetime.now(UTC)
    objects = await store.list_objects(tenant_id, limit=SCAN_LIMIT)

    retired = 0
    for obj in objects:
        due = expires_at(obj)
        if due is not None and due <= moment and obj.is_active:
            await store.retire_object(tenant_id, obj.id, reason=REASON)
            retired += 1

    return ExpiryReport(scanned=len(objects), retired=retired)
