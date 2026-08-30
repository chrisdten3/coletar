"""Cross-surface propagation (SCOPE §3.1, M3.2).

The product's central claim, stated in §3.1: *a memory update on one surface is
available to every other surface's next conversation, with no chat interface of our
own.* Everything built so far is substrate underneath that sentence. This measures it.

**The mechanism is deliberately trivial, and saying so matters.** There is no sync
job and there should not be one. A write lands in the canonical graph the moment any
surface calls it, and the *next* retrieval from any other surface sees it because it
queries the same graph. Propagation latency is therefore bounded by "when does the
person next open another model", not by anything we run on a schedule.

So this harness is not proving that a hard thing works. It is proving that an easy
thing is actually true, and guarding it: the day someone introduces a read cache, a
deferred write path or a per-surface store, this is what fails.

The harness takes **callables** rather than surfaces on purpose. M3.3 runs the same
measurement against a deployed Claude connector, where "write" is a model choosing to
call a tool over the network. A harness that hardcoded the local proxy would have to
be rewritten to get there; this one takes a different pair of functions.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

#: Build plan M3.3: propagation latency stays under 1 second at p95.
LATENCY_BUDGET_MS = 1_000.0

#: Writes one memory on the source surface and returns the object id it created.
Writer = Callable[[str], Awaitable[str]]
#: Reads from the destination surface and returns the object ids it can see.
Reader = Callable[[str], Awaitable[set[str]]]


@dataclass(frozen=True)
class Trial:
    direction: str
    content: str
    object_id: str
    latency_ms: float
    visible: bool


@dataclass
class PropagationReport:
    """What one run of the harness measured.

    `visible` is the claim; `latency_ms` is the bar. A run where everything is fast
    and nothing propagated is a failure, so the two are never reported apart.
    """

    trials: list[Trial] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.trials)

    @property
    def propagated(self) -> int:
        return sum(1 for t in self.trials if t.visible)

    @property
    def failures(self) -> list[Trial]:
        return [t for t in self.trials if not t.visible]

    def percentile_ms(self, fraction: float) -> float:
        latencies = sorted(t.latency_ms for t in self.trials if t.visible)
        if not latencies:
            return float("inf")
        return latencies[max(0, int(fraction * len(latencies)) - 1)]

    @property
    def p50_ms(self) -> float:
        return self.percentile_ms(0.50)

    @property
    def p95_ms(self) -> float:
        return self.percentile_ms(0.95)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trials": self.total,
            "propagated": self.propagated,
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "max_ms": round(max((t.latency_ms for t in self.trials), default=0.0), 2),
            "budget_ms": LATENCY_BUDGET_MS,
            "within_budget": self.p95_ms < LATENCY_BUDGET_MS,
            "by_direction": {
                direction: sum(1 for t in self.trials if t.direction == direction and t.visible)
                for direction in sorted({t.direction for t in self.trials})
            },
        }

    def report(self) -> str:
        d = self.as_dict()
        lines = [
            f"  propagated       {d['propagated']}/{d['trials']}",
            f"  latency p50/p95  {d['p50_ms']:.1f}ms / {d['p95_ms']:.1f}ms "
            f"(budget {d['budget_ms']:.0f}ms)",
            f"  worst            {d['max_ms']:.1f}ms",
        ]
        lines += [f"  {k:<16} {v} propagated" for k, v in d["by_direction"].items()]
        lines += [f"  DID NOT PROPAGATE: {t.content!r}" for t in self.failures]
        return "\n".join(lines)


async def measure_direction(
    *,
    direction: str,
    write: Writer,
    read: Reader,
    contents: list[str],
    query_for: Callable[[str], str],
) -> list[Trial]:
    """Write on one surface, then read on the other, timing the gap.

    The clock starts when the write returns, not when it is issued: the question is
    how long a *completed* write takes to become visible elsewhere, not how long the
    write itself took. Including write time would flatter a slow surface and punish a
    fast one for reasons that have nothing to do with propagation.
    """
    trials: list[Trial] = []
    for content in contents:
        object_id = await write(content)
        started = time.perf_counter()
        visible_ids = await read(query_for(content))
        latency_ms = (time.perf_counter() - started) * 1000.0
        trials.append(
            Trial(
                direction=direction,
                content=content,
                object_id=object_id,
                latency_ms=latency_ms,
                visible=object_id in visible_ids,
            )
        )
    return trials


@dataclass(frozen=True)
class Direction:
    """One surface writing and another reading.

    `contents` is per-direction rather than shared, because writing the *same* fact
    from both surfaces into one graph is not a propagation test -- the assembly stage
    deduplicates near-identical results, correctly, and the second copy never reaches
    the context. Each direction therefore needs facts of its own.
    """

    name: str
    write: Writer
    read: Reader
    contents: list[str]


async def measure_round_trip(
    *, directions: list[Direction], query_for: Callable[[str], str]
) -> PropagationReport:
    """Every direction. A store that propagated one way only would still be broken —
    the claim is that every surface reads what every other surface wrote."""
    report = PropagationReport()
    for direction in directions:
        report.trials.extend(
            await measure_direction(
                direction=direction.name,
                write=direction.write,
                read=direction.read,
                contents=direction.contents,
                query_for=query_for,
            )
        )
    return report


def summarize(reports: list[PropagationReport]) -> dict[str, float]:
    """Aggregate several runs, for a stable number rather than one sample."""
    latencies = [t.latency_ms for r in reports for t in r.trials if t.visible]
    if not latencies:
        return {"p50_ms": float("inf"), "p95_ms": float("inf")}
    ordered = sorted(latencies)
    return {
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[max(0, int(0.95 * len(ordered)) - 1)],
    }
