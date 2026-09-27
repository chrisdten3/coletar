"""Repeat patterns: is the same situation recurring? (SCOPE §6, §8.2)

Two independent questions live here, each backed by its own event type, and they
are kept separate on purpose -- they no longer share a join key, because a
reported decision has nothing to do with what Coleta itself retrieved:

  * `find_repeat_patterns` -- has the *exact same question* asked of Coleta --
    same normalized text, hashed via `query_digest` -- come up more than once,
    and did it keep returning the exact same context every time? Backed by
    `RETRIEVAL_TRACE` (see `retrieval.trace`).
  * `find_repeat_decisions` -- has the same tool, called in the same kind of
    situation, kept landing on the same reported outcome? Backed by
    `DECISION_OBSERVED`, reported by a caller's own code through
    `POST /v1/decisions` (or, eventually, observed directly by the local proxy) --
    never by Coleta guessing at what a decision meant.

The second is the one that actually justifies "swap this tool for a decision
model" -- repeated retrieval alone just means a question keeps coming up.
Nothing here inspects a query's text, a model's output, or a decision's real
content: those are absent by design, so a pattern is described entirely by ids
and caller-chosen labels, never by content this module has read.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from coletar.schema.events import EventType
from coletar.schema.tenancy import TenantId

if TYPE_CHECKING:
    from coletar.store.base import Store

#: Same window `inspector.metrics` reads; a repeat pattern that only shows up
#: further back than this isn't one the user needs surfaced today.
EVENT_LIMIT = 2_000

#: Below this, "it happened more than once" is not yet a pattern worth a user's
#: attention.
MIN_OCCURRENCES = 3


@dataclass(frozen=True)
class RepeatPattern:
    """One repeated question and whether it kept returning the same context.

    `object_ids` is the returned set from the *most recent* occurrence -- always
    the same set when `consistent` is True, and evidence enough to open in the
    Context Inspector either way.
    """

    query_digest: str
    occurrences: int
    consistent: bool
    object_ids: list[str] = field(default_factory=list)


async def find_repeat_patterns(store: Store, tenant_id: TenantId) -> list[RepeatPattern]:
    """Group retrieval traces by repeated question, newest first.

    One pass over the log, the same shape as `inspector.metrics.build_dashboard`:
    read once, filter to `RETRIEVAL_TRACE`, aggregate by the query's hash. A
    group below `MIN_OCCURRENCES` is noise, not a pattern, and is dropped.
    """
    events = await store.list_events(tenant_id, limit=EVENT_LIMIT)

    by_digest: dict[str, list[list[str]]] = defaultdict(list)
    for event in events:
        if event.type is not EventType.RETRIEVAL_TRACE:
            continue
        digest = event.detail.get("query_digest")
        if not digest:
            continue
        returned_ids = [str(oid) for oid in event.detail.get("returned_ids") or []]
        by_digest[digest].append(returned_ids)

    patterns: list[RepeatPattern] = []
    for digest, occurrences in by_digest.items():
        if len(occurrences) < MIN_OCCURRENCES:
            continue
        distinct = {tuple(sorted(ids)) for ids in occurrences}
        patterns.append(
            RepeatPattern(
                query_digest=digest,
                occurrences=len(occurrences),
                consistent=len(distinct) == 1,
                # `list_events` is newest first, so occurrences[0] is the latest.
                object_ids=occurrences[0],
            )
        )
    return patterns


@dataclass(frozen=True)
class RepeatDecision:
    """One (tool, situation) pair and whether the reported outcome kept matching.

    `last_outcome` is the most recent label reported -- always the same one when
    `consistent` is True.
    """

    tool_name: str
    input_signature: str
    occurrences: int
    consistent: bool
    last_outcome: str


async def find_repeat_decisions(store: Store, tenant_id: TenantId) -> list[RepeatDecision]:
    """Group reported tool decisions by (tool, situation), newest first.

    Fed by `DECISION_OBSERVED` events, however they arrived -- a caller's own code
    reporting through `POST /v1/decisions`, or the local proxy observing a tool
    call directly. Both land in this same shape, so this reads them identically.
    """
    events = await store.list_events(tenant_id, limit=EVENT_LIMIT)

    by_situation: dict[tuple[str, str], list[str]] = defaultdict(list)
    for event in events:
        if event.type is not EventType.DECISION_OBSERVED:
            continue
        tool_name = event.detail.get("tool_name")
        input_signature = event.detail.get("input_signature")
        outcome = event.detail.get("outcome_signature")
        if not tool_name or not input_signature or not outcome:
            continue
        by_situation[(str(tool_name), str(input_signature))].append(str(outcome))

    decisions: list[RepeatDecision] = []
    for (tool_name, input_signature), outcomes in by_situation.items():
        if len(outcomes) < MIN_OCCURRENCES:
            continue
        decisions.append(
            RepeatDecision(
                tool_name=tool_name,
                input_signature=input_signature,
                occurrences=len(outcomes),
                consistent=len(set(outcomes)) == 1,
                # `list_events` is newest first, so outcomes[0] is the latest.
                last_outcome=outcomes[0],
            )
        )
    return decisions
