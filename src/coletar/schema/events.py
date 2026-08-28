"""Append-only Event / Revision Log (SCOPE §5, §6).

Every mutation of the canonical graph writes one of these. The log is not a debug
convenience -- it is the single source for the observability dashboard (§6), for
provenance in the Context Inspector (§8.2), and for the `staleness` term of the
Continuity Score (§7). Nothing may mutate the graph without appending here.

Two properties this module enforces, because undo and the Inspector both depend on
them:

  * **Immutable.** The model is frozen, and stores hand out deep copies on read, so
    a caller that mutates what it was given cannot corrupt the log behind it. No
    backend ever issues UPDATE or DELETE against the event table.
  * **Self-sufficient.** A write event carries the full before/after object state,
    not a diff. That costs storage, and it buys the thing the diff cannot: any
    single row can be replayed on its own, with no dependence on the rows before it
    having been read correctly. See `coletar.store.replay`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coletar.schema.objects import Provider


class EventType(StrEnum):
    OBJECT_CREATED = "object.created"
    OBJECT_UPDATED = "object.updated"
    OBJECT_SUPERSEDED = "object.superseded"
    OBJECT_RETIRED = "object.retired"
    OBJECT_ACCESSED = "object.accessed"
    EDGE_CREATED = "edge.created"
    COMPRESSION_RUN = "compression.run"
    RETRIEVAL_TRACE = "retrieval.trace"
    STORE_MIGRATED = "store.migrated"
    COMPILE_RUN = "compile.run"
    CONNECTOR_WRITE = "connector.write"


#: Events that carry a full `after` snapshot and therefore participate in replay.
#: `object.accessed` deliberately does not -- reading is not a revision.
REVISION_EVENTS: frozenset[EventType] = frozenset(
    {
        EventType.OBJECT_CREATED,
        EventType.OBJECT_UPDATED,
        EventType.OBJECT_SUPERSEDED,
        EventType.OBJECT_RETIRED,
        EventType.CONNECTOR_WRITE,
    }
)


class Actor(StrEnum):
    """Who caused the mutation. The dashboard groups by this, and it is how a user
    tells "the model wrote this" from "the migration job did"."""

    USER = "user"
    MODEL = "model"
    JOB = "job"
    SYSTEM = "system"
    CONNECTOR = "connector"
    MIGRATION = "migration"
    COMPILER = "compiler"


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    type: EventType
    object_id: str | None = None
    actor: Actor = Actor.SYSTEM
    provider: Provider = Provider.COLETAR
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Full object state either side of the write, as `model_dump(mode="json")`.
    # `before` is None on create; `after` is None only on events that are not
    # revisions (see REVISION_EVENTS).
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_revision(self) -> bool:
        return self.type in REVISION_EVENTS
