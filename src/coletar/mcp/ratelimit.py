"""Per-principal rate limiting (SCOPE §9, ROADMAP M7).

M7 gates the public API on this in the same breath as auth and tenant isolation, and
the reason they belong together is that all three answer the same question: what can
one credential do to everyone else's service. Tenant isolation stops a key reading
another graph; this stops a key exhausting the machine both graphs live on.

A **token bucket keyed by principal id**, not by IP. Several users behind one office
NAT are not one caller, and one caller rotating through addresses is not several. The
credential is the thing the server actually knows.

**In-process, and that is a real limitation rather than an oversight.** Two workers
mean two buckets and twice the effective limit. It holds for the single-machine
deployment coletar actually has, and the honest fix when that changes is a shared
counter, not a larger number here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Generous for a human driving one assistant, tight enough that a runaway loop is
#: stopped in seconds rather than after it has filled a graph.
DEFAULT_REQUESTS_PER_MINUTE = 120

#: Burst allowance. A connector that opens a conversation legitimately fires several
#: calls at once; refusing that would break normal use to prevent nothing.
DEFAULT_BURST = 30


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class RateLimiter:
    """Token bucket per principal. Refills continuously rather than on a window edge,
    so a caller cannot save up a minute of quota and spend it in one instant."""

    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    burst: int = DEFAULT_BURST
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    @property
    def _rate(self) -> float:
        return self.requests_per_minute / 60.0

    def check(self, principal_id: str, *, now: float | None = None) -> float | None:
        """None when the call may proceed, else seconds to wait.

        Returning the wait rather than a bare boolean is what lets the caller send a
        truthful `Retry-After`. A client told only "no" retries immediately and makes
        the problem worse.
        """
        now = time.monotonic() if now is None else now
        bucket = self._buckets.get(principal_id)
        if bucket is None:
            self._buckets[principal_id] = _Bucket(tokens=self.burst - 1.0, updated_at=now)
            return None

        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * self._rate)
        bucket.updated_at = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None
        return (1.0 - bucket.tokens) / self._rate

    def reset(self) -> None:
        self._buckets.clear()
