"""The scheduled side of collect-then-batch (docs/CAPTURE_AND_BATCH.md).

Capture is synchronous and cheap; judging the turn is neither. That only works if
something actually runs the batch pass, and until now nothing did — `extract-pending`
and `expire` existed as commands a human could remember to type. A queue whose
draining depends on someone remembering is a queue that silently stops draining,
which is the risk that document names.

Two things live here, and they are separable on purpose:

**A lease**, so two workers cannot process the same episode. Extraction is already
idempotent by stable id, so the duplicate objects would collapse — but corroboration
events would not, and a corroboration count is evidence the user reads to decide
whether a fact is well attested. Inflating it by running two workers would be a
silent integrity failure of exactly the kind constraint 5 exists to prevent.

**A loop**, so the same code runs as a local daemon today and as a container process
on whatever host gets chosen. It deliberately does not depend on a scheduler: the
lease makes overlapping invocations safe, so cron, systemd or a supervised process
are all fine, and none of them is baked in.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field

from coletar.jobs.expiry import expire
from coletar.jobs.extraction import extract_pending
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

logger = logging.getLogger(__name__)

#: One lease name per tenant's batch work. Extraction and expiry share it because
#: they are one pass over one tenant's queue, and a worker holding two leases can
#: hold one and lose the other halfway through.
BATCH_LEASE = "batch"


def worker_identity() -> str:
    """Who this process is, for the lease and for anyone reading it back.

    Host and pid so an operator can find the process; a random suffix because a pid
    is reused, and a restarted worker inheriting the pid of the one that died would
    be treated as the same owner and allowed to take its lease early.
    """
    return f"{socket.gethostname()}/{os.getpid()}/{uuid.uuid4().hex[:8]}"


@dataclass
class WorkerPass:
    """What one pass did, or why it did nothing."""

    tenant_id: str
    owner: str
    skipped: bool = False
    held_by: str | None = None
    extraction: dict[str, int] = field(default_factory=dict)
    expiry: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "tenant": self.tenant_id,
            "owner": self.owner,
            "skipped": self.skipped,
            "held_by": self.held_by,
            "extraction": self.extraction,
            "expiry": self.expiry,
            "error": self.error,
        }


async def run_pass(
    store: Store,
    tenant_id: TenantId,
    *,
    owner: str | None = None,
    lease_ttl_seconds: float | None = None,
) -> WorkerPass:
    """Extract pending episodes and expire what has aged out, once, under a lease.

    Returns rather than raises when another worker holds the lease: a second worker
    finding the queue busy is the system working, not an error, and a scheduler that
    treats it as a failure will page someone about correct behaviour.
    """
    from coletar.config import get_settings

    settings = get_settings()
    identity = owner or worker_identity()
    ttl = lease_ttl_seconds or settings.worker_lease_ttl_seconds

    lease = await store.acquire_lease(
        tenant_id, BATCH_LEASE, owner=identity, ttl_seconds=ttl
    )
    if lease is None:
        held = await store.read_lease(tenant_id, BATCH_LEASE)
        logger.info("batch lease held by %s; skipping", held.owner if held else "another worker")
        return WorkerPass(
            tenant_id=tenant_id,
            owner=identity,
            skipped=True,
            held_by=held.owner if held is not None else None,
        )

    result = WorkerPass(tenant_id=tenant_id, owner=identity)
    try:
        extraction = await extract_pending(store, tenant_id)
        result.extraction = extraction.as_dict()
        expiry = await expire(store, tenant_id)
        result.expiry = expiry.as_dict()
    except Exception as exc:  # noqa: BLE001 - a bad pass must not kill the loop
        result.error = f"{exc.__class__.__name__}: {exc}"
        logger.exception("batch pass failed for %s", tenant_id)
    finally:
        # Always release, including after a failure: holding the lease until it
        # expires would make one bad pass cost a full TTL of queue latency.
        await store.release_lease(tenant_id, BATCH_LEASE, owner=identity)
    return result


async def run_forever(
    store: Store,
    tenant_id: TenantId,
    *,
    interval_seconds: float | None = None,
    owner: str | None = None,
    max_passes: int | None = None,
) -> list[WorkerPass]:
    """Run passes on an interval until cancelled.

    `max_passes` exists so this is testable without a clock: a loop that can only be
    verified by waiting is a loop nobody verifies.
    """
    from coletar.config import get_settings

    interval = interval_seconds or get_settings().worker_interval_seconds
    identity = owner or worker_identity()
    passes: list[WorkerPass] = []
    while max_passes is None or len(passes) < max_passes:
        passes.append(await run_pass(store, tenant_id, owner=identity))
        if max_passes is not None and len(passes) >= max_passes:
            break
        await asyncio.sleep(interval)
    return passes
