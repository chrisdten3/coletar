# Decision stages — Phase 0 evaluation

**Status:** harness built 2026-09-28. First read on the committed fixtures only (see
[Results](#results)): gate passes, reconcile passes only at a 0.90 floor after a
wording change. Neither is a go decision until confirmed on the real labelled sets.

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

## Results

All runs on `jev-1.13.0`, 2026-09-28, against the committed fixtures. These sets are
small: one gate miss moves recall 4.5 points, one reconcile error moves the
false-supersede rate 5.9 points, so < 2% means zero errors.

**Gate**, stage `c501eae842f0`, `extraction_set.json` (55 turns, 22 durable): **pass**.
Threshold 0.28 keeps 21/22 durable turns (95.5%) at a 56.4% pass rate. The miss, `p22`
("Actually it's port 5433, not 5432."), is a correction scored 0.11. Watch
corrections in the real set: losing one leaves a wrong fact standing.

**Reconcile**, `reconcile_seed.json` (24 pairs):

| Stage version | Accuracy | False supersede, no floor | Best floor passing < 2% |
|---|---|---|---|
| `c501eae842f0` | 87.5% | 17.6% (`r07`, `r10`, `r23`) | none; 5.9% at 0.80 |
| `756f200bc52c` | 95.8% | 5.9% (`r07`) | 0.90, flagging 20.8% |

Every error in the first run was a `duplicate` called `supersedes`, including `r07`
at 0.99 confidence: Jev read a later restatement as a replacement. The second
wording says a paraphrase, fuller name or vaguer version is a duplicate whatever its
date, and that `supersedes` needs a changed value or an added concrete detail. `r04`
(a real refinement) stayed `supersedes` at 0.96; `r23` (the less-specific
restatement) became `duplicate` at 0.99.

`r07`'s label is arguable under the new wording ("integer cents" is more concrete than
"fixed-point integers"), and replacing one paraphrase with the other loses nothing.
The wording was revised while looking at these 24 pairs, so the second run is
flattered the same way the gate threshold is.

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
