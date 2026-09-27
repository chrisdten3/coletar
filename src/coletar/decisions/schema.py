"""What a raw trace becomes once it has been distilled (M11.2).

This is the shape a decision model eventually trains on or is queried with, and it
is written as an honest placeholder rather than a guess. The vendor schemas this is
meant to feed -- the Choice/Score/Noul patterns a product like Jev or Laya expects --
are not documented to us yet, so inventing field names for them would produce a
model that looks authoritative and matches nothing.

Instead: the fields we know we have, plus `vendor_payload` as the one open dict a
future mapper writes into. Same reasoning as `ContextObject.payload` -- a subtype's
extras live in a payload rather than earning columns before anyone knows what they
are (§2).

`outcome_label` is the join to the label-only path. When a caller uses both -- raw
capture for the richer signal, `/v1/decisions` for the label they chose themselves --
this is where the two meet, and a sample carrying one is worth more than one without,
because the label is the closest thing to ground truth a tenant has stated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class SynthesizedDecisionSample(BaseModel):
    """One distilled (situation -> outcome) observation, derived from a raw trace."""

    #: Bumped when the fields below change meaning, so a stored sample can always be
    #: read back by the code that understands the version that wrote it.
    schema_version: int = 1

    sample_id: str
    #: The raw trace this came from. It will normally be shredded by the time anyone
    #: reads this, which is the point -- the id survives as provenance, the content
    #: does not.
    source_episode_id: str
    tool_name: str

    intent: str
    environmental_state: dict[str, Any] = Field(default_factory=dict)
    tool_call: dict[str, Any] = Field(default_factory=dict)
    tool_response: dict[str, Any] = Field(default_factory=dict)

    #: From the label-only `/v1/decisions` stream, when the caller reports one.
    outcome_label: str | None = None

    synthesized_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    #: Where a vendor-specific mapping writes its own fields, once one exists.
    vendor_payload: dict[str, Any] = Field(default_factory=dict)
