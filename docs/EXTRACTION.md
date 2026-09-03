# Extraction

*Turning a conversational turn into a typed object, and knowing how often that's wrong.*

SCOPE §5 calls the Extraction Layer the place most of the real engineering risk lives,
and it is right: deciding that a sentence is a durable fact, of a particular kind, at a
particular confidence, is a much harder problem than "chunk and embed." This documents
what the live-turn extractor does, what it measures, and where it stops.

## The rule it enforces

Precision over recall, deliberately asymmetric. A wrong memory costs the user a
deletion and some trust; a missing one costs almost nothing, because the user will
simply say it again. So the extractor fires only on unambiguous first-person
declarations, and it is guarded against the ways a keyword match is *not* an assertion
by the user.

## What counts as durable

A turn contains a durable fact if and only if it contains a first-person assertion by
the user that:

1. is about the user, their work, or their standing preferences and instructions,
2. is expected to still be true after this conversation ends, and
3. is **asserted** — not asked, hypothesised, quoted, or attributed to someone else.

That definition was written before
[`tests/fixtures/extraction_set.json`](../tests/fixtures/extraction_set.json) was
labelled, and the set was labelled before either extractor was measured against it.
Labels fitted to an implementation measure nothing.

## The five guards

Each guard was a measured false positive, and each is a property of the sentence
rather than of a particular phrase, so it generalises past the example that motivated
it. Guards are **sentence-scoped**: a question in one sentence cannot silence an
assertion in the next.

| Guard | Rejects | Because |
|---|---|---|
| Question | *"Is it true that I never use semicolons?"* | A question asks; it does not assert. |
| Quotation | *"She said 'I always use vim'"* | The first person inside a quotation is not the user. |
| Attribution | *"The docs say from now on the API requires auth"* | An assertion attributed to someone else is theirs. The reporting verb must **precede** the trigger — *"I prefer that you say less"* is still a preference. |
| Anaphora | *"I'm working on it right now"* | A memory whose subject exists only in this conversation is meaningless once stored. |
| Particle | *"I'm building up my courage"* | A particle changes the verb. Building up courage is not building a thing. |

`that` is deliberately **not** treated as anaphoric. It is ambiguous between a
demonstrative (*"I like that approach"*) and a complementizer (*"I prefer that you say
less"*), and suppressing the second to catch the first trades a real memory for a case
the question guard already covers.

## Meta triggers versus assertion triggers

What gets stored depends on what the matched phrase *is*.

A **meta trigger** is an instruction aimed at the assistant and is no part of the fact,
so only the body is kept:

> "Remember that my timezone is US Eastern." → `my timezone is US Eastern`

An **assertion trigger** is part of the claim, and dropping it changes the meaning:

> "I never use classes when a function will do." → `I never use classes when a function will do`

Storing that as its body alone — `classes when a function will do` — records a
preference *for* classes. **A memory that inverts its source is worse than no memory at
all**, so negated triggers survive into the content.

## The domain shift that M3.4 exposed

The guards above were measured on conversational turns. Run against a **real Claude
Code transcript** — 257 human turns from months of work — the same extractor scored
**0% precision**. All three of its extractions were wrong:

```
[fact] my preferred language is Python."}]                    ← JSON fragment
[goal] I'm working on [the larger task] for [who it's for]    ← a prompt template
[fact] my preferred language is TypeScript."                  ← quoted docs, and it
                                                                contradicts the first
```

A developer's transcript is not a chat. It is full of pasted JSON, code literals,
documentation quotes and prompt templates, and those contain first-person sentences
that nobody said. **Precision measured on one domain does not transfer to another**,
which is the general lesson and worth more than the specific fix.

Two more guards followed, and the cases are now negatives in the labelled set so the
domain is represented rather than rediscovered:

| Guard | Rejects | Because |
|---|---|---|
| Structural | `…my preferred language is Python."}]` | A sentence ending in `"}]` was never a sentence — it is a string literal inside a structure someone pasted. |
| Placeholder | `I'm working on [the larger task]` | A bracketed or angled placeholder is a template, not a statement. |

After them: still 4.3% on the labelled set with recall unchanged at 100%, and **0
extractions** from the same 257 real turns.

That zero is worth reading carefully. It is the right answer for *that* corpus —
coding sessions are mostly technical questions, and durable personal facts are rare in
them — but it also means Claude Code acquisition yields in proportion to how much
someone tells the tool about themselves. Guaranteed capture is not the same as
plentiful capture.

Had the import run instead of a dry run, three junk memories would be in the graph,
one contradicting another. `--dry-run` exists for that reason and should stay the
first thing anyone runs against a new corpus.

## Measured

Against the 50-turn labelled set. Roughly two thirds of the negatives contain the exact
surface forms a keyword extractor keys on, inside a question, a quotation, a
hypothetical, a report of someone else's speech, or a reference to the conversation
itself. A negative set of *"what is the weather"* would prove nothing about precision.

| | Before guards | After guards | Bar |
|---|---|---|---|
| **False-positive write rate** | 37.5% | **4.3%** | under 15% |
| Precision | 62.5% | **95.7%** | — |
| Recall | 90.9% | **100%** | — |
| Correctly typed | 20/20 | **22/22** | — |

Recall is reported because it is not in the acceptance criteria and needs to be. An
extractor that never fires has a 0% false-positive rate and is worthless; quoting only
the required number would be technically honest and substantively misleading.

### The one remaining false positive

> *"I prefer not to say."* → stored as a preference.

Telling that apart from *"I prefer not to use Docker"* — a genuine standing preference
with identical surface form — requires knowing that "say" here means "decline to
answer." That is semantics, and no additional regex reaches it. It is pinned by name in
the test suite rather than absorbed into a percentage, so a regression appears as a new
identifier rather than as a number drifting upward.

## Frontier models measured against the heuristic (2026-09-03)

One run, 55 labelled live-proxy turns, via `scripts/bench_extraction.py`.

| path | precision | recall | `kind` wrong | seconds |
|---|---|---|---|---|
| heuristic (regex) | 0.952 | 0.909 | 0 | ~0 |
| `claude-sonnet-5` | 0.947 | 0.818 | 2 | 269 |
| `claude-opus-5` | 0.944 | 0.773 | 2 | 173 |
| `claude-haiku-4-5` | 0.929 | 0.591 | 1 | 80 |

Read this carefully, because it is easy to over-read.

**On live turns the heuristic wins outright** — precision, recall, `kind`, and
latency at once. M2.2's conclusion that a model on the live path would be
speculative work survives contact with the evidence. Adding one would make the
composer bridge slower *and* worse, and at 1.5–5s per turn the user is waiting.

**Opus is not better than Sonnet here**, at 1.67x the input price. Nothing in this
table justifies the Opus tier for extraction.

**The precision spread is noise.** 0.929 to 0.952 across n=55 is a handful of
turns, and Haiku's `kind` errors moved 0 -> 1 between two runs of the same model on
the same set. The recall gaps are wide enough to believe; the precision ordering is
not.

**This set cannot answer the question the models exist for.** It is 55 live-proxy
turns — the register the patterns were tuned on, which is why they win. The model
path was justified by export prose, where the heuristic measures 31.4% recall and
produced third-party PII on a real corpus. There is no labelled export set, so the
import-path comparison is still unmeasured. Building one is the next honest step,
and until it exists "use a model for backfill" rests on the 31.4% figure and the
Walleye failure rather than on a head-to-head.

## The transient-preference failure, measured (2026-09-03)

The live set flatters the heuristic because it *is* the heuristic's specification.
`tests/fixtures/transient_set.json` is the opposite: 25 turns built from the shapes
the heuristic actually gets wrong on a real export — task context stored as standing
preference — with durable controls mixed in. Wording rewritten so no private content
is committed; run it with `BENCH_SET=transient`.

| | live set precision | **transient precision** | transient recall |
|---|---|---|---|
| heuristic | 0.952 | **0.421** | 0.800 |
| `claude-haiku-4-5` | 0.929 | 0.643 | 0.900 |
| `claude-sonnet-5` | 0.947 | **0.909** | **1.000** |

**The heuristic writes 11 false positives out of 15.** Same extractor, 0.952 on one
set and 0.421 on the other; the only difference is which turns you show it. That
gap is the transient-preference problem stated as a number, and it is why the live
set could never have found it.

**Haiku is not sufficient here.** It looked fine on the live set and lands at 0.643
on this one, well under the 0.85 bar — a spot check of 25 real failing turns had it
correctly dropping 24, but with durable controls mixed in it starts firing on task
context too. **Sonnet clears the bar with perfect recall**, at one false positive.

So the answer to "is the cheap model good enough" is set-dependent, and the set that
matters says no. On current evidence: **Sonnet for backfill, and the heuristic stays
on the live path** — where it beats every model, on a benchmark that is admittedly
its own spec.

n=25, one run. The precision gap between 0.421 and 0.909 is far too large to be
sampling noise; the gap between Sonnet and Haiku is smaller and would be worth
re-running before anything expensive rests on it.

## Where this stops, and what comes next

The heuristic clears M2.2's bar on live turns, so a model on the live path would be
speculative work. It is at **M6.2** that a model becomes necessary, and M6.1 measured
exactly why: 31.4% recall over export prose. `extract_with_model` is now implemented —
see below.

Two known limitations, neither of which the labelled set can fix on its own:

- **Corrections carry unresolved referents.** *"Actually, it's Globex"* stores the
  pronoun. Resolving it needs the surrounding exchange, which is why
  `extract_memories` already accepts `assistant_text` it does not yet use.
- **Only English, and only these surface forms.** The extractor is a floor, not a
  ceiling.

## Cost

§11 names extraction cost at consumer scale as a real risk. The heuristic path is
free — it is regular expressions — which is exactly why it is worth pushing as far as
measurement says it can go before reaching for inference. When the model path lands, it
should run against the user's own local model, where inference costs nothing.


## M6.1 — the same extractor, a different register

An account export is not a proxy transcript. The eight original patterns were tuned
on live turns, where people write "I prefer X"; an export is years of standing
instructions to the assistant, decisions a team took, and tools the user simply uses.

Measured against a 100-turn labelled export set, the extractor fired on **4 of 35**
durable statements — 100% precision, 11.4% recall. **The ≥85% precision bar passes
vacuously at that recall**, which is the reason to report both numbers: precision on
four extractions is not the claim precision on thirty-five would be.

Three patterns were added for forms that recur across every surface rather than
shapes reverse-engineered from one fixture — an imperative addressed to the assistant
(`always`/`never`, anchored to a sentence start so "I would always use X" cannot
reach it), a decision already taken (`we decided/settled/standardised`, where "we
should" deliberately does not match), and habitual use of a named thing (`I use/run`).

| set | before | after |
|---|---|---|
| M2.2 labelled turns — false-positive rate | 4.3% | **4.3%** |
| M6.1 export set — precision | 100% | **100%** |
| M6.1 export set — recall | 11.4% | **31.4%** |

Nearly tripled export recall at **zero cost** on the independent set, which is the
measurement that matters: the M2.2 set was not written for this change.

### What these numbers are not

The export fixture is synthetic and was authored alongside the patterns it measures.
That makes its recall figure indicative rather than authoritative — tuning patterns
against a fixture you wrote is circular, and the honest guard against it is that the
*independent* M2.2 rate did not move. The number to trust is 4.3%.

31.4% is also not a corridor anyone would call working. Regex over open-ended prose
has a ceiling, and it is close. This measurement is the case for M6.2 doing the
extracting with a model, stated in numbers rather than as an assumption — and the
test asserts only that recall has not collapsed, because pinning it would freeze a
limitation in place as though it were a target.


## M6.2 — the model proposes, the guards dispose

The design decision is the whole of it: a model is allowed to change **what gets
proposed**, and nothing else. Every candidate is then located in the transcript and
put through the same sentence guards the regex path uses, so recall is what improves
and precision is defended by machinery a prompt cannot talk its way past.

**Grounding is the anti-fabrication guard**, and it is structural rather than a plea
in the prompt. A proposed memory must have at least `GROUNDING_FLOOR` (0.6) of its
content words present in some sentence of the transcript. A model that invents
*"Chris lives in Berlin"* cannot point at a sentence containing it, so the memory is
dropped no matter how confidently it was asserted.

Everything else follows precision-over-recall. Malformed JSON yields nothing rather
than a salvage; an unrecognised `kind` is dropped rather than coerced to `fact`;
duplicates collapse. An import that finds nothing is recoverable, one that invents is
not.

Model-located memories are written at `DERIVED_SUMMARY` (0.50) rather than
`EXPLICIT_STATEMENT` (0.95). A model finding a claim is weaker evidence than an
unambiguous first-person form matching, and §3.1's table prices that difference so
each caller does not have to remember it.

### Measured

Local `qwen2.5:0.5b`, against the same 100-turn labelled export set:

| | extracted | precision | recall |
|---|---|---|---|
| regex (M6.1) | 11 | 100% | 31.4% |
| **model** | 29 | **100%** | **96.7%** |

**The model run covers 30 of 100 turns, not all of them.** The measuring machine has
8 GB with ~0.4 GB free, and Ollama evicted the model between calls until `keep_alive`
was added — it still stalled out at turn 30. That is a constraint of the hardware
this was measured on, not a property of the code, and the number is reported as
partial rather than extrapolated to a round 100.

Two caveats that the precision/recall figures do not capture:

- **`kind` classification is unreliable at this model size.** A 0.5b model labelled
  both *"I prefer TypeScript"* and *"We standardised on Tailwind"* as `goal`. The
  content is right and grounded; the type is a guess. A larger local model is the fix,
  and the Context Inspector's re-typing is the backstop until then.
- 0.3s/turn once resident, ~10s/turn averaged across reload stalls. On a machine with
  headroom this is a background import; on this one it is not.

### What grounding does not do

Grounding defeats *fabrication*. It does **not** defeat *injection*, because injected
text genuinely is in the transcript — if a planted sentence says the user loves Java,
a memory saying so grounds perfectly. This is pinned by a test so the guard is never
described as stronger than it is.

What holds that line is upstream and downstream. **Upstream:** only the user's own
turns ever reach the extractor — `chatgpt_export` drops assistant and tool messages,
exactly as `claude_code` does — so a planted line has to have been typed or pasted by
the user themselves. **Downstream:** M5.3's review gate means nothing compiles into
another product until a human has read it.

The residual risk is real and worth stating plainly: a user pastes a document
containing injected prose into their own turn, and it becomes a candidate. The
`_STRUCTURAL` guard catches pasted JSON and markup, not prose. The gate is the answer
there, which is one more reason it is enforced in the CLI and not only in the UI.
