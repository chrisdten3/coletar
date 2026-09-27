"""The human-approval gate in front of automated decisions (M11.3).

`history.patterns.find_repeat_decisions` can tell you a pattern has held a hundred
times. It cannot tell you it is safe to stop thinking about, and nothing in this
package treats it as if it could: a pattern reaches autopilot only when a person
holding the `consent` scope says so, about that specific tool and situation, having
seen the evidence. There is no threshold that promotes anything by itself.

That is the single property the rest of the design leans on. `resolve` consults a
promotion record *before* it consults a backend, so an unapproved pattern never
reaches a vendor at all -- the gate is not a filter on the answer, it is a refusal
to ask the question.

Two further rules are enforced here rather than left to judgement:

  * **The promotion bar is not the display bar.** `MIN_OCCURRENCES` in
    `history.patterns` is tuned for "worth showing a human"; three consistent
    occurrences is nowhere near "worth acting on unsupervised". `PROMOTION_MIN_OCCURRENCES`
    is its own, much higher number.
  * **Safety-gating tools cannot be promoted at all.** Not "promoted with a higher
    bar" -- refused. A tool whose decision gates whether something consequential may
    proceed keeps a slower thinker in the loop in this build, and evidence-gathering
    for it continues so that the choice can be revisited with data rather than
    reopened by default.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from coletar.schema.events import Actor, Event, EventType
from coletar.schema.tenancy import TenantId

if TYPE_CHECKING:
    from coletar.store.base import Store

#: Deliberately far above `history.patterns.MIN_OCCURRENCES`. A tunable, not a
#: measured figure: it is the number of identical outcomes a person should want to
#: see before letting a machine stop asking, and it should be raised rather than
#: lowered until there is evidence either way.
PROMOTION_MIN_OCCURRENCES = 25


def setting_key(tool_name: str, input_signature: str) -> str:
    return f"decisions.promotion.{tool_name}.{input_signature}"


async def get_promotion(
    store: Store, tenant_id: TenantId, tool_name: str, input_signature: str
) -> dict[str, Any] | None:
    """The live promotion for this pattern, or None if it is not on autopilot.

    A demoted record reads as None: it stays stored so the history of "this was
    approved and then taken back" survives, but it grants nothing.
    """
    record = await store.get_setting(tenant_id, setting_key(tool_name, input_signature))
    if not record or not record.get("active"):
        return None
    return record


async def promote(
    store: Store,
    tenant_id: TenantId,
    tool_name: str,
    input_signature: str,
    *,
    evidence: dict[str, Any],
    principal_id: str,
) -> dict[str, Any]:
    """Put one pattern on autopilot, recording who approved it and on what evidence."""
    record: dict[str, Any] = {
        "active": True,
        "tool_name": tool_name,
        "input_signature": input_signature,
        "evidence": evidence,
        "approved_by": principal_id,
        "approved_at": datetime.now(UTC).isoformat(),
    }
    await store.put_setting(tenant_id, setting_key(tool_name, input_signature), record)
    await store.append_event(
        tenant_id,
        Event(
            type=EventType.AUTOMATION_PROMOTED,
            actor=Actor.USER,
            detail={
                "tool_name": tool_name,
                "input_signature": input_signature,
                "evidence": evidence,
                "principal": principal_id,
            },
        ),
    )
    return record


async def demote(
    store: Store,
    tenant_id: TenantId,
    tool_name: str,
    input_signature: str,
    *,
    reason: str,
    principal_id: str,
) -> bool:
    """Take a pattern off autopilot. Returns False if it was not on one.

    Read synchronously by `resolve` on every call, so this takes effect on the next
    request -- a kill switch that needed a deploy, or waited out a cache, would not
    be one.
    """
    key = setting_key(tool_name, input_signature)
    existing = await store.get_setting(tenant_id, key)
    if not existing or not existing.get("active"):
        return False
    await store.put_setting(
        tenant_id,
        key,
        {
            **existing,
            "active": False,
            "demoted_by": principal_id,
            "demoted_at": datetime.now(UTC).isoformat(),
            "demotion_reason": reason,
        },
    )
    await store.append_event(
        tenant_id,
        Event(
            type=EventType.AUTOMATION_DEMOTED,
            actor=Actor.USER,
            detail={
                "tool_name": tool_name,
                "input_signature": input_signature,
                "reason": reason,
                "principal": principal_id,
            },
        ),
    )
    return True
