# The Provider Compiler

Live Sync keeps the canonical store authoritative and every surface querying it
forever. **A compile is the opposite: directional, point-in-time, and designed so
that you can then disconnect.** The test in
[`compiler/base.py`](../src/coletar/compiler/base.py) is the whole product claim —
after a compile runs, the user can remove coletar entirely and the destination still
works. A zip of markdown does not pass that test.

## What "native container" means

A native container is something the destination product *already runs* without
anything of ours in the loop. Everything else is preservation, and the manifest says
so rather than rounding it up.

| Destination | Native | Not native |
|---|---|---|
| Ollama | the `SYSTEM` block — `ollama create` bakes it into the model and it is present on every turn | knowledge files: Ollama ships no retrieval, so a fact that lands only in a file is preserved but inert |
| Claude (M5.2) | Project instructions + project knowledge | — |

This distinction is why `fidelity` in the [Continuity Score](CONTINUITY_SCORE.md)
measures something instead of always reading 1.0.

## Scope compiles into model identity

Ollama has exactly one system prompt per model and no notion of a project. A single
Modelfile holding every scope would put project context into every unrelated
conversation, and **nothing in the destination would reveal it** — the model would
simply know things it should not, in a product coletar no longer touches.

So each scope compiles to its own model:

```
coletar-global          global objects
coletar-proj_ledger     proj_ledger objects + inherited globals
coletar-proj_atlas      proj_atlas  objects + inherited globals
```

Globals are inherited *into* project models, because global means "applies
everywhere" and a project model that lost them would be worse than the one the user
already had. Project objects are never lifted out. That asymmetry is the entire
mechanism behind `scope_preservation`, which is a **hard gate at 100%**, not a
target: every other kind of loss is visible to the user, and a leak is not.

Inheritance does not double-count. A global object appearing in three models is
still one object that moved once, so it gets one manifest entry against the model
that owns its scope. Counting per appearance would push `object_coverage` above 1.0
and make the score read best exactly when the graph is most fragmented.

## Where an object lands, and why

Decided once, in `LocalModelCompiler._fidelity`, and reported in the manifest with
the reason attached.

| Condition | Fidelity | Why |
|---|---|---|
| `sensitivity = restricted` | **unsupported** | `ollama create` bakes the Modelfile into a blob that can be pushed to a registry. Reported as a coverage loss, which is honest: the destination cannot hold it safely |
| `sensitivity = sensitive` | reconstructed | kept out of the baked block, preserved in a file |
| type is conversation / artifact / entity / episode | reconstructed | source material, not a standing fact. Asserting a whole conversation in a system prompt costs tokens on every turn to say nothing |
| `confidence < 0.7` | reconstructed | a 0.5-confidence inference baked into a system prompt becomes a fact the model will defend |
| content > 400 chars | reconstructed | a system prompt is a budget, not a bucket |
| otherwise | **native** | into the `SYSTEM` block |

Confidence gates *promotion*, not inclusion. Nothing is dropped for being uncertain;
it is moved somewhere it cannot be asserted.

## What is not a loss

Retired and superseded objects are filtered out of the denominator rather than
counted against the destination. The graph already decided they no longer state the
current truth, so scoring them as migration failures would blame Ollama for
coletar's own compression. Everything surviving that filter *is* in the denominator,
so an object the compiler cannot place counts against coverage instead of quietly
disappearing.

## §11 applies to compiled memory too

The `SYSTEM` block carries the same background-not-instructions boundary as
retrieval-time injection. Compiled memory was written by models and, transitively,
by whatever those models read; baking it into a system prompt is precisely where it
would stop looking like data. Preferences and instructions are rendered
descriptively — `[preference, confidence 0.95, via local] …` — because a model
decides what to do with a description and obeys a command.

Content is escaped for the delimiter. Ollama closes `SYSTEM` on a triple quote, so an
unescaped one truncates the block and every later fact vanishes into a Modelfile that
still parses — a silent loss with nothing anywhere to report it.

## Compiling does not mutate the graph

`compile()` is pure: objects in, files out, no store. The caller appends one
`compile.run` event, because a compile is a fact about the graph's history even
though it changed nothing in it.

`provider_mappings` is deliberately *not* written back. A compile produces an
artifact the user may never install; recording "this object lives in Ollama now"
before they run `ollama create` would put a claim in the graph that nothing has made
true yet.

## Verified end to end

Compiled from a seeded graph against `qwen2.5:0.5b`, baked with `ollama create`, then
queried with coletar not running:

| Asked | Answer |
|---|---|
| `coletar-global` — what does Chris prefer for money? | "fixed-point integers over doubles" |
| the **base** model, same question | nonsense — so the `SYSTEM` block is what did it |
| `coletar-proj_ledger` — what bookkeeping method? | "double-entry bookkeeping" |
| `coletar-global` — the same project question | **"I was not told."** |

`object_coverage` 1.00, `scope_preservation` 1.00, `fidelity` 0.69, total **0.906**.

## Running it

```bash
uv run coletar compile --out build/compile --base-model llama3.1
```

Writes a `MANIFEST.md` naming every object and its destination, a `PROVENANCE.md`
carrying origin, confidence and extraction method for each one, and one directory per
model. It prints the Continuity Score arithmetic and the exact commands to run.
