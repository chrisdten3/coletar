# Decision stages — Phase 0 evaluation

**Status:** harness built 2026-09-28. Not yet run against Jev: needs a TypeSafe API
key and the two labelled sets below.

The extraction pipeline has two stages whose answer is a label rather than text:

| Stage | Question | Where | Dangerous error |
|---|---|---|---|
| **Gate** | Is anything in this turn worth remembering? | Live sync, every turn | False negative — a rejected turn is never looked at again |
| **Reconcile** | What does this candidate do to that existing memory: `new`, `duplicate`, `supersedes`, `contradicts`? | Backfill and live sync, every candidate | False `supersedes` — it retires a true memory |

The plan puts Jev, TypeSafe's decision model, in both. Phase 0 checks that before any
pipeline code depends on it.

## Go / no-go

| Stage | Pass if |
|---|---|
| Gate | recall ≥ 95% of human-labelled durable turns at the chosen threshold |
| Reconcile | false-supersede rate < 2% at the chosen confidence floor |

If a stage fails, it falls back to the small extraction model behind the same
function interface. Below the floor, reconcile answers are routed to *keep both and
flag*, which is always safe. The report shows what each floor costs in flags.

## Running it

```sh
export TYPESAFE_API_KEY=...        # or COLETAR_JEV_API_KEY
export ANTHROPIC_API_KEY=...       # drafting only

# First read, on committed fixtures (no private data needed):
uv run python scripts/eval_jev.py gate      tests/fixtures/extraction_set.json
uv run python scripts/eval_jev.py reconcile tests/fixtures/reconcile_seed.json

# The real sets, from your own data:
uv run python scripts/build_decision_set.py gate-draft ~/Downloads/<export>
uv run python scripts/build_decision_set.py reconcile-draft --tenant <tenant>
uv run python scripts/build_decision_set.py review ~/coletar-decisions/gate.json
uv run python scripts/build_decision_set.py review ~/coletar-decisions/reconcile.json
uv run python scripts/eval_jev.py gate      ~/coletar-decisions/gate.json
uv run python scripts/eval_jev.py reconcile ~/coletar-decisions/reconcile.json
```

Drafting sends turns (gate) or stored memories (reconcile) to Anthropic, and the eval
sends them to TypeSafe. Sets and results hold private text and are written to
`~/coletar-decisions/`, outside the repo.

## Pieces

- `src/coletar/extraction/jev.py`: the only code that knows Jev's wire format.
  One attempt per call; `JevUnavailable` (timeouts, 429, 5xx) is retryable,
  `JevConfigurationError` (key, request or response shape) stops a run.
- `src/coletar/extraction/decisions.py`: the gate and reconcile questions.
  `STAGE_VERSION` hashes their wording, and every result records it.
- `src/coletar/extraction/evaluation.py`: set formats and metrics (tested in
  `tests/test_jev.py`).
- `tests/fixtures/reconcile_seed.json`: 24 hand-written hard pairs covering every
  label, including hypotheticals, temporary states and a less-specific restatement
  that must not replace the specific one.

## Endpoint note

The client targets `POST /v1/systemone`, the path in TypeSafe's own Python SDK
(`typesafe-sdk` 0.7.2). The `/v1/evaluate` name comes from gateway integrations such
as the AI SDK's experimental evaluate API. If you call Jev through one of those, only
`SYSTEM_ONE_PATH` and `_parse` in `jev.py` change.

## Known limits

- **The threshold is picked on the same set it is scored on**, which flatters it.
  Confirm it on a second set before shipping.
- **The gate set is stratified on the draft.** A durable turn the draft rejected and
  the random negative sample also missed is never seen. The size of the negative
  sample bounds that blind spot.
- **Exports hold only user turns**, so the gate set's "previous turn" is the user's
  previous message. Live sync also sees the assistant's reply, so these numbers
  slightly understate the live gate.
- **Reviewers can anchor on the draft.** `review` shows the draft only on request,
  and the set records how often a human overruled it.
