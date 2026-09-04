# Does the model call the tools unprompted?

Every connector in [CONNECTORS.md](CONNECTORS.md) rests on one unmeasured
assumption. The mechanism is capture-by-tool-call, so Live Sync only works if a
model handed these four tools *chooses* to call them — at the right moments, with
nobody asking it to. `docs/TODO.md` §6 recorded that as **never measured**. This is
the measurement.

The worry was that models would rarely call unprompted, leaving Live Sync as a
manual lookup the user has to request by name. That is not what happens. The
failure is the opposite one for reading, and a different one for writing.

## Method

`scripts/bench_tool_calls.py` against
[`tests/fixtures/tool_call_set.json`](../tests/fixtures/tool_call_set.json): 40
turns, 16 labelled as needing a `search_context` call, 8 as containing a durable
fact that should reach `write_memory`. Neither label is derivable from the other,
and the negatives are adversarial — hypotheticals, third-party facts, quoted
prose, and one explicit "don't save this anywhere". The definitions were written
before the set was labelled and the set before any model ran, the same order the
extraction sets used.

Tool schemas are pulled live from `coletar.mcp.server`, never copied. A copy would
measure a description no client ever sees and would keep scoring well after
someone edited the real one.

Three conditions, and **two rounds each**. The second round matters more than it
sounds: a model that searches first can only reach `write_memory` after that
result returns, so a single-shot harness reports "it never writes" when what it
saw was "it searched first". The first run of this harness did exactly that — 0%
write recall, which the second round corrected to 62.5%. The tool result fed back
is empty, so what is measured is the model's judgement about the turn rather than
its reaction to content we chose to inject.

| Condition | Setup |
|---|---|
| `warm` | Four messages into an unrelated chat, server `instructions` present. Production. |
| `cold` | The turn is the first message. The easy case, since the description says "call at the start of a conversation". |
| `bare` | `warm`, with no server instructions — only the tool descriptions. |

## Results

Whole-turn numbers (both rounds), one run per cell, 2026-09-04.

| Model | Condition | `search_context` recall | false-fire | `write_memory` recall | false-fire |
|---|---|---|---|---|---|
| gpt-5.6-terra | warm | 100% (16/16) | **100%** (24/24) | 62.5% (5/8) | 3.1% (1/32) |
| gpt-5.6-terra | cold | 100% | 100% | 62.5% | 0% |
| gpt-5.6-terra | bare | 100% | 95.8% | 37.5% | 0% |
| gpt-5.6-luna | warm | 100% | 50.0% (12/24) | 37.5% (3/8) | 0% |
| gpt-5.6-luna | cold | 100% | 58.3% | 50.0% | 0% |
| gpt-5.6-luna | bare | 87.5% | **12.5%** (3/24) | 50.0% | 0% |

"False-fire" is the share of turns that did *not* need the tool where the model
called it anyway. It is the number that matters here, because the cost of a call
is latency and noise on **every** turn.

## What this says

**Reading is not under-called. It is barely a decision.** `gpt-5.6-terra` called
`search_context` on 118 of 120 turns across all three conditions — including
"What's 17% of 240?" and "Who wrote The Left Hand of Darkness?". Recall is 100%
because it always fires, not because it judges well; precision equals the base
rate of positives in the set. The connector will work, and it will do a retrieval
round-trip on arithmetic.

**The server instructions are the lever, and they are currently tuned to "always
call".** Removing them takes `gpt-5.6-luna` from 50% false-fire to 12.5% at a cost
of two turns of recall — precision 57.1% → 82.4%. The line doing the work is
almost certainly *"Call search_context at the start of a conversation and after
any topic shift. Do not assume you lack context about this user until you have
checked here."* That is a reasonable instruction to have written before anyone had
data. It is now the first thing to change, and this harness is how to tell whether
a rewrite helped.

**Writing never happens as an opening move.** `gpt-5.6-terra` wrote on the first
round in 1 of 120 turns; over a full turn it reaches 62.5%. Every write it made
came *after* a search returned. Two consequences: a client that stops after one
tool round captures nothing, and write coverage is bounded by whether a search
happened first — which today is "always", but will not be once the instructions
are tightened. Tightening reading without watching writing would trade one failure
for another.

**The write misses are consistent between models.** `w04` (timezone), `w05`
(cap table), `w06` (tabs over spaces) and `s16` (thesis date) were missed by both.
All four state a durable fact *and* ask for immediate work, and the models did the
work. `w01` — a bare standing instruction with no task attached — was written by
everything.

**One wrong write, and it is a known hole.** `gpt-5.6-terra` wrote from *"My friend
Dana is allergic to shellfish"* — a third-party fact stored as if it were the
user's. That is precisely the gap `docs/TODO.md` §11 records as "third-party facts
have nowhere to live", now observed from the model side rather than the extractor
side.

**Nothing wrote from the turn that said not to.** Neither model wrote on `x05`
("Don't save this anywhere, but…"), in any condition.

## What this does not measure

- **No Claude model was measured.** No Anthropic key was available for this run,
  and per CONNECTORS.md the Claude Custom Connector is the strongest live-sync leg
  — so the most important cell in the table is empty. The harness takes
  `anthropic:claude-sonnet-5` and needs only the key.
- **This is not the connector.** Claude Desktop, the claude.ai connector and
  ChatGPT Developer Mode cannot be driven from here — driving a provider's UI is
  prohibited outright (AGENTS.md constraint 2) and the connector is not deployed.
  What is measured is the same tool schemas, through the API, without the client's
  own system prompt. Read these as the decision quality of the tool descriptions,
  not as end-to-end connector behaviour.
- **n is small, especially for writing.** 16 search positives and 8 write
  positives. The gap between terra's 62.5% and luna's 37.5% write recall is two
  turns; treat it as noise. The search finding survives any reasonable interval —
  24 of 24 is not a sampling artefact.
- **One run per cell.** No repeat-run variance was measured. `BENCH_RUNS`-style
  repetition is worth adding before anyone tunes the instructions against these
  numbers, because that is exactly the setting where noise gets mistaken for
  improvement.
- **Author-labelled.** Same provenance weakness as the extraction sets, same
  remedy: `scripts/label_turns.py` takes independent labels if this ever needs to
  be demonstrable to someone else.

## Reproducing

```bash
export OPENAI_API_KEY=...          # or ANTHROPIC_API_KEY
uv run python scripts/bench_tool_calls.py openai:gpt-5.6-terra
```

`BENCH_CONDITIONS`, `BENCH_LIMIT`, `BENCH_CONCURRENCY` and `BENCH_OUT` narrow a
run. This calls a paid API and is deliberately not part of CI.
