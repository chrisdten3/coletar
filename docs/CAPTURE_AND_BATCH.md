# Capture now, extract later

**Status:** design, not built. Proposed 2026-09-03.

## The problem this solves

Extraction currently has two paths with different quality, and they will drift apart
forever:

| | backfill (import) | live sync (composer bridge) |
|---|---|---|
| extractor | model, once wired | pattern heuristic |
| can name a third party | yes | **no** |
| latency budget | none — user walked away | a person is watching |
| cost | once per user | every turn, forever |

That table is the whole problem. A user imports their history and coletar knows who
Amanda is; they say the same thing in the composer tomorrow and it does not, because
the live path can only emit first-person memories. "Why does it know this from my
export but not from yesterday?" has no good answer, and the gap widens every time
either path improves alone.

Measurement makes it worse rather than better. On live turns the heuristic beats
every frontier model on precision, recall, `kind` **and** latency
([`EXTRACTION.md`](EXTRACTION.md)) — so replacing it is not the fix. And at 1.5–5s
per turn a model in the composer path is felt by the person waiting.

## The shape

Split *capture* from *extraction*.

1. **Capture (live, synchronous).** The turn is stored verbatim as an `EPISODE`
   object. No model, no network, no latency beyond a write.
2. **Heuristic pass (live, synchronous).** The existing pattern extractor runs
   immediately, so anything it catches is available in the next tool right away.
   This is the path that already beats the models on this register.
3. **Batch pass (asynchronous).** A job pulls episodes that have not been
   model-extracted, batches them, and sends them through the Batches API at half
   price. Results supersede or extend what the heuristic produced.

The user-visible property: a memory is available *immediately* at heuristic quality
and *eventually* at frontier quality, from one pipeline rather than two.

## What already supports this

More than expected, because §6 anticipated the shape:

- **`EPISODE` is already an object type**, described in §6 as part of the agentic
  triple. It is currently created only by `seed.py`. A captured turn is exactly what
  an episode is for, so this gives the type its actual purpose rather than inventing
  one.
- **The Inspector already renders episode → derived-object lineage**
  (`AgenticView.derived_from`, keyed on `provenance.source_object_ids`). Today it
  renders nothing, because importers put *export* ids there — `conversation_id`,
  `node_id` — which are not objects in the graph. Point derived objects at the
  episode instead and the lineage view lights up with no new code: click a memory,
  see the turn it came from, in the user's own words.
- **`supersedes` and object versioning** already express "the batch pass replaced
  what the heuristic guessed", with the earlier version still queryable. §6 retires,
  it does not delete.
- **`src/coletar/jobs/`** already exists and holds compression — a second job is a
  file, not an architecture.
- **`ttl_days`** is on every object and in the Postgres schema.
- **The event log** gives the whole thing an audit trail for free: capture, heuristic
  write, batch write, supersede.

## What has to be built

1. **Capture on the live surfaces.** `/v1/capture` and the MCP write path store an
   episode before extracting. Small.
2. **A queue discipline.** Which episodes still need a model pass. The honest
   cheapest version is a `payload` flag plus an index, not a new table — §2 says a
   subtype's extras go in `payload`.
3. **The batch job.** Pull unprocessed episodes, chunk them, submit to the Batches
   API, poll, materialise results, mark the episodes done. `ExtractionUnavailable`
   turns stay unprocessed and are retried; they must never be marked done, which is
   the same failure the provider layer already had once.
4. **Reconciliation.** When the batch pass finds what the heuristic already wrote,
   corroborate rather than duplicate; when it contradicts, supersede. `remember`
   already does content-similarity folding — this needs to reuse it, not reimplement.
5. **Retention enforcement.** `ttl_days` is declared on the schema and enforced
   **nowhere**. Storing raw turns without an expiry that actually runs is the single
   biggest reason not to ship this as written.

## Decisions that are not mine

- **How long do raw turns live?** Until extracted plus a grace period? A fixed
  window? Forever? This is the difference between "we hold your conversations" and
  "we hold them briefly to process them", and it is the first question a SOC 2
  auditor and a security-conscious customer will ask.
- **Does the user see and control it?** Capture is currently invisible. Storing raw
  turns is a materially larger commitment than storing extracted memories, and the
  Inspector should probably show the episode queue with a delete affordance.
- **Who triggers the batch?** Cron on the Fly deploy, on-demand from the Inspector,
  or on a threshold. Affects whether "eventually" means minutes or a day.
- **Does the heuristic still run at all** once batch extraction is reliable? On this
  evidence it should — it wins on live turns — but that rests on a benchmark that is
  the heuristic's own specification.

## Risks

- **This is the biggest data-retention change in the product.** Everything else
  stores derived, reviewable objects; this stores what the user typed, verbatim,
  before anything has judged it. It belongs in the compliance scope from day one,
  not retrofitted.
- **Silent queue growth.** If the batch job never runs, capture quietly accumulates
  raw turns forever and the user gets no frontier extraction. It needs to be visible
  in the Inspector and to fail loudly.
- **Double extraction.** A turn processed twice creates duplicate memories unless
  reconciliation is right. `remember`'s dedup helps; the episode's processed flag is
  what actually prevents it.
- **A merged-entity problem that grows.** Entity dedup is currently per-import and
  casefolded-name only. Continuous capture makes that permanent rather than
  per-file, so `payload->>'name'` needs an index and a real lookup first.

## Sequencing

Retention enforcement before capture. Building the queue before `ttl_days` does
anything means shipping a product that accumulates users' raw conversations with no
expiry, and the fix afterwards is a migration over exactly the data you least want
to be handling.
