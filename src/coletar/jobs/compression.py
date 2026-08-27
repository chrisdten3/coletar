"""Memory Compression Engine (SCOPE §6).

Not a second subsystem — a background job over the graph that already has
`supersedes`, `confidence` and `retired_at`. It collapses superseded chains and
low-confidence clusters into condensed bundles per scope, which is what turns the
retrieval token budget into a real knob instead of a truncation point.

Implemented here: the superseded-chain pass, which needs no model and is exactly
correct. The low-confidence clustering pass needs embeddings, so it lands with the
Postgres backend (M2).
"""

from __future__ import annotations

from dataclasses import dataclass

from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import ObjectType, Scope
from coletar.store.base import Store


@dataclass
class CompressionReport:
    scanned: int
    retired: int
    bundled: int

    def as_dict(self) -> dict[str, int]:
        return {"scanned": self.scanned, "retired": self.retired, "bundled": self.bundled}


async def compress(store: Store, *, scope: Scope | None = None) -> CompressionReport:
    """Retire every object that something newer supersedes.

    Retired, not deleted: the object stays readable so the Context Inspector can
    still show a user what a fact used to say and when it changed.
    """
    objects = await store.list_objects(type=ObjectType.MEMORY, scope=scope, limit=10_000)
    superseded_ids = {o.supersedes for o in objects if o.supersedes}

    retired = 0
    for obj in objects:
        if obj.id in superseded_ids and obj.is_active:
            await store.retire_object(obj.id, reason="superseded")
            retired += 1

    report = CompressionReport(scanned=len(objects), retired=retired, bundled=0)
    await store.append_event(
        Event(type=EventType.COMPRESSION_RUN, actor=Actor.JOB, detail=report.as_dict())
    )
    return report
