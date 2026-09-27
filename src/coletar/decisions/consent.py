"""Per-tool consent for decision capture and vendor use (M11.1).

Two consents live here, and they are deliberately separate, for the same reason
`history_thread_provider` is not derived from `extraction_provider` in
`coletar.config`: "keep my tool's real inputs and outputs, encrypted, for a day"
and "send them to a third-party decision vendor" are different promises, and
granting one must never quietly grant the other.

Consent is scoped to **one tool at a time**. A tenant enabling raw capture for a
ticket-routing tool has said nothing about the tool that touches patient records,
and a tenant-wide boolean would put words in their mouth.

Stored as workspace settings rather than graph objects (`Store.get_setting` --
consent has no provenance, no locality and nothing to compile). The grant and
revoke paths append events anyway, which is the one place this module departs from
`put_setting`'s "configuration does not belong in the event log" rule: every other
setting is a preference, and this one is the record of what a tenant permitted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from coletar.schema.events import Actor, Event, EventType
from coletar.schema.objects import Sensitivity
from coletar.schema.tenancy import TenantId

if TYPE_CHECKING:
    from coletar.store.base import Store

ConsentType = Literal["raw_capture", "vendor_send"]

#: "Coleta may store this tool's raw inputs and outputs, encrypted, briefly."
RAW_CAPTURE: ConsentType = "raw_capture"
#: "Coleta may send this tool's data to the configured decision-model vendor."
VENDOR_SEND: ConsentType = "vendor_send"


def setting_key(consent_type: str, tool_name: str) -> str:
    return f"decisions.consent.{consent_type}.{tool_name}"


async def is_consented(
    store: Store, tenant_id: TenantId, tool_name: str, consent_type: ConsentType
) -> bool:
    """Whether this tenant has granted this consent for this specific tool."""
    record = await store.get_setting(tenant_id, setting_key(consent_type, tool_name))
    return bool(record and record.get("enabled"))


async def grant(
    store: Store,
    tenant_id: TenantId,
    tool_name: str,
    consent_type: ConsentType,
    *,
    sensitivity: Sensitivity,
    principal_id: str,
) -> dict[str, object]:
    """Record a consent, and say in the log who granted it and over what."""
    record: dict[str, object] = {
        "enabled": True,
        "tool_name": tool_name,
        "consent_type": consent_type,
        "sensitivity": str(sensitivity),
        "granted_by": principal_id,
        "granted_at": datetime.now(UTC).isoformat(),
    }
    await store.put_setting(tenant_id, setting_key(consent_type, tool_name), record)
    await store.append_event(
        tenant_id,
        Event(
            type=EventType.CONSENT_GRANTED,
            actor=Actor.USER,
            detail={
                "tool_name": tool_name,
                "consent_type": consent_type,
                "sensitivity": str(sensitivity),
                "principal": principal_id,
            },
        ),
    )
    return record


async def revoke(
    store: Store,
    tenant_id: TenantId,
    tool_name: str,
    consent_type: ConsentType,
    *,
    principal_id: str,
) -> None:
    """Withdraw a consent. The caller is responsible for purging what it covered.

    Disabled rather than deleted, so "this was granted and then taken back" stays
    distinguishable from "this was never granted" -- the same reason nothing else
    in this product hard-deletes.
    """
    key = setting_key(consent_type, tool_name)
    existing = await store.get_setting(tenant_id, key) or {}
    await store.put_setting(
        tenant_id,
        key,
        {
            **existing,
            "enabled": False,
            "tool_name": tool_name,
            "consent_type": consent_type,
            "revoked_by": principal_id,
            "revoked_at": datetime.now(UTC).isoformat(),
        },
    )
    await store.append_event(
        tenant_id,
        Event(
            type=EventType.CONSENT_REVOKED,
            actor=Actor.USER,
            detail={
                "tool_name": tool_name,
                "consent_type": consent_type,
                "principal": principal_id,
            },
        ),
    )
