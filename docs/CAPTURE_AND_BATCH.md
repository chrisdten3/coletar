# Capture now, extract later

**Status:** local workflow built 2026-09-03. Encrypted raw capture, pending-state,
provider-neutral model extraction, retry-on-unavailable, idempotent materialisation,
episode lineage, queue visibility, early user erasure, TTL crypto-shredding and an
`extract-pending` command exist. Provider-native Batch API transport and scheduled
execution remain deployment optimisations, not correctness prerequisites.

## The problem this solves

Extraction currently has two paths with different quality, and they will drift apart
forever:

| | backfill (import) | live sync (composer bridge) |
|---|---|---|
| extractor | selected semantic model | capture, then the same model path |
| can name a third party | yes | **no** |
| latency budget | none — user walked away | a person is watching |
| cost | once per user | every turn, forever |

That table is the whole problem. A user imports their history and coletar knows who
Amanda is; they say the same thing in the composer tomorrow and it does not, because
the live path can only emit first-person memories. "Why does it know this from my
export but not from yesterday?" has no good answer, and the gap widens every time
either path improves alone.

The original live fixture made the heuristic look strongest, but it is effectively
the heuristic's regression specification. On the transient-context fixture the same
extractor falls to 42.1% precision. That is not enough evidence to expose a regex
guess to retrieval merely because it is fast. A model still does not belong in front
of the composer response: at 1.5–5s per turn, a person feels it.

## The shape

Split *capture* from *extraction*.

1. **Capture (live, synchronous).** A lossless ciphertext copy of the turn is stored
   as an `EPISODE` under a disposable per-object key. No model or provider network.
2. **Model pass (asynchronous).** A job pulls episodes that have not been examined
   and sends them through the selected provider. The first implementation processes
   a bounded group with ordinary provider calls; provider-native Batch API transport
   is the next cost optimization.
3. **Materialise and review.** Derived objects point to the source episode and enter
   the canonical synced graph; only the raw evidence stays surface-local. They remain
   subject to the existing human review gate for compilation.

The user-visible property is honest latency: the turn is acknowledged immediately,
and a memory appears only after semantic extraction. An installation that declines
raw-turn capture may keep the legacy heuristic path, but collect-then-batch does not
write a preliminary regex memory.

## What already supports this

More than expected, because §6 anticipated the shape:

- **`EPISODE` was already an object type**, described in §6 as part of the agentic
  triple. Capture now gives it its production purpose rather than inventing another
  storage model.
- **The Inspector already renders episode → derived-object lineage**
  (`AgenticView.derived_from`, keyed on `provenance.source_object_ids`). Today it
  renders nothing, because importers put *export* ids there — `conversation_id`,
  `node_id` — which are not objects in the graph. Point derived objects at the
  episode instead and the lineage view lights up with no new code: click a memory,
  see the turn it came from, in the user's own words.
- **Stable derived IDs plus `remember`** make retries idempotent and fold matching
  memories rather than duplicating them. Collect-then-batch has no heuristic object
  to replace.
- **`src/coletar/jobs/`** already exists and holds compression — a second job is a
  file, not an architecture.
- **`ttl_days`** is on every object and in the Postgres schema.
- **The event log** records capture, derived writes, completion, retirement and key
  shredding without ever containing plaintext episode content.

## What has to be built

1. ~~**Capture on the live surfaces.**~~ Implemented behind `capture_turns`.
2. ~~**A queue discipline.**~~ Implemented as `payload.needs_model_extraction`.
3. ~~**Reconciliation and retry.**~~ Stable IDs address crash retries; `remember`
   provides similarity folding. Unavailable calls remain pending.
4. ~~**Effective erasure and control.**~~ The graph and log hold ciphertext; expiry
   and the Inspector's erase action destroy the per-object key.
5. **Provider-native batching.** Submit JSONL to a provider batch endpoint when cost
   or throughput justifies it. The current bounded worker is already asynchronous
   from the live request and keeps the provider contract testable.

## Decisions that are not mine

- **How long do raw turns live?** Until extracted plus a grace period? A fixed
  window? Forever? This is the difference between "we hold your conversations" and
  "we hold them briefly to process them", and it is the first question a SOC 2
  auditor and a security-conscious customer will ask.
- **Does the user see and control it?** Yes: `/agentic` shows the pending queue and
  can erase a raw turn immediately. Capture itself remains an explicit configuration
  opt-in rather than a default.
- **Who triggers the batch?** Cron on the Fly deploy, on-demand from the Inspector,
  or on a threshold. Affects whether "eventually" means minutes or a day.
- **Does the heuristic still run at all?** Only as the legacy no-retention path or an
  explicitly requested recogniser. It is not part of `collect_then_batch`.

## Risks

- **This is the biggest data-retention change in the product.** Everything else
  stores derived, reviewable objects; this retains a lossless encrypted copy of what
  the user typed before anything has judged it. It belongs in the compliance scope
  from day one, not retrofitted.
- **Silent queue growth.** If the batch job never runs, capture quietly accumulates
  raw turns forever and the user gets no frontier extraction. **Addressed**: `coletar
  worker` runs the pass on an interval, and `coletar queue-health` exits non-zero
  when the oldest pending episode passes a threshold or extraction has been failing.
  A stalled queue and a quiet user look identical from the outside; those two numbers
  are the only place the difference shows.
- **Concurrent extraction.** Stable IDs make retry after a crash idempotent, but two
  workers can still duplicate corroboration events. **Addressed**: a per-tenant lease
  in the `Store` protocol, held for the length of a pass. A second worker finding it
  taken reports who holds it and exits, which is the system working rather than an
  error. The lease carries a TTL, because a worker killed between acquiring and
  releasing would otherwise wedge the queue until a human noticed.
- **Entity identity is still shallow.** Cross-batch lookup and the Postgres name
  index prevent the same case-insensitive name being recreated, but aliases and two
  different people with the same name still need a stronger resolution policy.

## Sequencing

The safety ordering is now implemented: encryption and key destruction, expiry,
queue visibility, and early erasure precede opt-in capture. The worker lease and
interval scheduling landed 2026-09-04; provider-native Batch transport is a cost
optimisation after the ordinary bounded worker is operationally reliable, and there
is still no volume that demands it.

## Running it

```bash
uv run coletar worker                  # daemon, one pass every 5 minutes
uv run coletar worker --once           # the cron form; the lease makes overlap safe
uv run coletar queue-health            # exits 1 when the queue has stalled
```

The loop deliberately depends on no scheduler. Because the lease decides who works,
cron, systemd, a container process and a supervised daemon are all correct, and none
of them had to be chosen before a host was.
