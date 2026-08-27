"""Append-only Event / Revision Log (SCOPE §5, §6).

Every mutation of the canonical graph writes one of these. The log is not a debug
convenience -- it is the single source for the observability dashboard (§6), for
provenance in the Context Inspector (§8.2), and for the `staleness` term of the
Continuity Score (§7). Nothing may mutate the graph without appending here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from coletar.schema.objects import Provider


class EventType(StrEnum):
    OBJECT_CREATED = "object.created"
    OBJECT_UPDATED = "object.updated"
    OBJECT_SUPERSEDED = "object.superseded"
    OBJECT_RETIRED = "object.retired"
    OBJECT_ACCESSED = "object.accessed"
    EDGE_CREATED = "edge.created"
    COMPRESSION_RUN = "compression.run"
    COMPILE_RUN = "compile.run"
    CONNECTOR_WRITE = "connector.write"


class Actor(StrEnum):
    USER = "user"
    MODEL = "model"
    JOB = "job"
    SYSTEM = "system"


class Event(BaseModel):
    id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    type: EventType
    object_id: str | None = None
    actor: Actor = Actor.SYSTEM
    provider: Provider = Provider.COLETAR
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: dict[str, Any] = Field(default_factory=dict)
