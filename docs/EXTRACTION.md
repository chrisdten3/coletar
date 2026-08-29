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

## Where this stops, and what comes next

The heuristic clears M2.2's bar on live turns, so a model on the live path would be
speculative work. It is at **M6.2** that a model becomes necessary: a raw ChatGPT export
is prose with no reliable first-person surface forms to key on, and the bar there is 85%
precision over far messier text. `extract_with_model` is stubbed against that milestone.

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
