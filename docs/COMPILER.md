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
| Claude | Project instructions **and** project knowledge — Claude really does retrieve over uploaded files | memory import: Anthropic re-extracts what you paste |

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

## Claude: two containers, deliberately not scored the same

The Claude compiler targets what Anthropic actually ships, which is not what the
scaffolding for this milestone assumed. There is **no Projects import API**, so the
compiler emits a package and the user pastes and uploads it — the same boundary as
the ChatGPT corridor, for the same reason (hard constraint 2).

**Projects are the strong container.** Custom instructions are injected into every
conversation inside the Project, and unlike Ollama, Claude retrieves over uploaded
project knowledge. Both are therefore `native`, and the confidence floor routes
between them without downgrading either: a low-confidence inference goes to
knowledge rather than standing instructions, but knowledge is still a real
container.

**Memory import is the weak one, and Anthropic says so.** The documented format is a
block of `[date saved, if available] - memory content` lines pasted into
Settings > Memory. The help centre states that Claude "will extract key information
and store it as individual memory entries" rather than storing what was pasted, that
the feature is "experimental and still in active development", and that Claude "may
not always successfully incorporate imported memories". A destination that
re-interprets what it receives and may drop it has not preserved the object, so
global-scope objects are `reconstructed` — never `native`.

`memory.txt` therefore contains the documented format and *nothing else*: no header,
no §11 marker, no explanation. It is pasted into a box whose contents an extractor
consumes, so any framing we added would itself become a memory. The explanation goes
in the instructions the human reads.

### Globals are duplicated into Projects, on purpose

Whether account-level memory is visible inside a Project is **undocumented**. The
repo's rule is not to build against an assumption, and the two failure modes are not
symmetric: duplicating costs some redundancy in the instructions, while omitting
would hand the user a Project that silently lost their global context. So globals are
inherited into each Project's instructions, exactly as they are inherited into project
models on the local compiler.

The manifest still records a global object's home as `memory.txt`, which is the
conservative call and slightly understates what lands. The reasoning: the user's
global context should work *everywhere* in Claude, not only inside the Projects we
told them to create, and outside a Project the only path is the lossy one.

### The comparison goes both ways

A score that cannot rank destinations is decoration, and one that always ranks the
frontier product higher is a preference dressed up as a measurement. On the same
graph:

| Graph | Better destination | Why |
|---|---|---|
| project-scoped | **Claude** | a Project holds instructions *and* retrievable knowledge; Ollama can only bake a system prompt and leave the rest in files it will never read |
| global-scoped | **Ollama** | a system prompt the user controls and the model sees every turn, versus an experimental extractor that may not incorporate the memory at all |

On the seeded graph, which is global-heavy, this comes out as **local 0.906 vs
Claude 0.869**. That is the honest result, and it is the non-obvious one.

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
