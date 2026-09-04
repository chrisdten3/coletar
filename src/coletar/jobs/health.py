"""Is capture still reaching the graph?

From the outside a stalled queue and a quiet user are identical: both look like no
new memories. The difference is visible in exactly two places — how old the oldest
pending episode is, and whether the batch pass has been failing — so this reads both
and says which one is happening.

Deliberately a report with thresholds rather than a monitoring integration. There is
no host yet (docs/TODO.md §2), so anything that assumed a metrics backend would be
built against a guess. A command that exits non-zero is consumable by cron, a
container healthcheck, or a human, and can be pointed at a real alerting path once
one exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from coletar.capture import is_pending
from coletar.jobs.worker import BATCH_LEASE
from coletar.schema.events import EventType
from coletar.schema.objects import ObjectType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

SCAN_LIMIT = 10_000
#: How far back a "recent failure" reaches. Longer than any sane worker interval,
#: short enough that yesterday's resolved outage does not still be paging today.
FAILURE_WINDOW = timedelta(hours=24)


@dataclass
class QueueHealth:
    tenant_id: str
    pending: int = 0
    oldest_pending_hours: float | None = None
    recent_failures: int = 0
    lease_owner: str | None = None
    lease_expired: bool = False
    alerts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.alerts

    def as_dict(self) -> dict[str, object]:
        return {
            "tenant": self.tenant_id,
            "pending": self.pending,
            "oldest_pending_hours": self.oldest_pending_hours,
            "recent_failures": self.recent_failures,
            "lease_owner": self.lease_owner,
            "lease_expired": self.lease_expired,
            "alerts": self.alerts,
            "ok": self.ok,
        }


async def queue_health(
    store: Store,
    tenant_id: TenantId,
    *,
    pending_hours: float | None = None,
    failure_threshold: int | None = None,
) -> QueueHealth:
    """Age of the queue and recent batch failures, with thresholds applied."""
    from coletar.config import get_settings

    settings = get_settings()
    max_age = pending_hours if pending_hours is not None else settings.queue_alert_pending_hours
    max_failures = (
        failure_threshold if failure_threshold is not None else settings.queue_alert_failures
    )

    now = datetime.now(UTC)
    episodes = await store.list_objects(tenant_id, type=ObjectType.EPISODE, limit=SCAN_LIMIT)
    pending = [episode for episode in episodes if is_pending(episode)]

    health = QueueHealth(tenant_id=tenant_id, pending=len(pending))
    if pending:
        oldest = min(episode.created_at for episode in pending)
        health.oldest_pending_hours = round((now - oldest).total_seconds() / 3600, 2)

    failures = await store.list_events(
        tenant_id, since=now - FAILURE_WINDOW, limit=SCAN_LIMIT
    )
    health.recent_failures = sum(
        1 for event in failures if event.type is EventType.EXTRACTION_UNAVAILABLE
    )

    lease = await store.read_lease(tenant_id, BATCH_LEASE)
    if lease is not None:
        health.lease_owner = lease.owner
        health.lease_expired = lease.expires_at <= now

    if health.oldest_pending_hours is not None and health.oldest_pending_hours > max_age:
        health.alerts.append(
            f"oldest pending episode is {health.oldest_pending_hours}h old "
            f"(threshold {max_age}h) — the batch pass is not draining the queue"
        )
    if health.recent_failures >= max_failures:
        health.alerts.append(
            f"{health.recent_failures} extraction failures in the last 24h "
            f"(threshold {max_failures}) — check the extraction provider"
        )
    if health.lease_expired:
        # A lease nobody released is a worker that died mid-pass. The next worker
        # takes it anyway, so this is informational — but a *recurring* expired
        # lease is a worker that keeps dying, which nothing else here would show.
        health.alerts.append(
            f"batch lease held by {health.lease_owner} has expired — "
            "a worker exited without releasing it"
        )
    return health
