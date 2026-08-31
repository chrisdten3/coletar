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
| ChatGPT | Custom GPT instructions (8,000 chars), up to 20 knowledge files, and account-level Custom Instructions | — |

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

## ChatGPT: a capacity ceiling the others do not have

A Custom GPT caps instructions at **8,000 characters** and knowledge at **20 files**.
Those are not cosmetic. The Claude compiler writes one knowledge file per object,
which breaks here the moment a scope holds 21 — so this one **bundles knowledge by
type** into a handful of files. A destination limit changing the artifact's shape is
what compiling to a real product means, as opposed to exporting and hoping.

Instructions are rendered most-confident first and trimmed to the budget, so anything
cut to fit is the least certain thing rather than whatever happened to sort last. The
account-level box is smaller still and **truncates silently on paste**, so the file
carries a warning when it would overrun rather than letting the user discover it by
noticing something missing months later.

### Global scope lands better on ChatGPT than on Claude

| destination | global container | fidelity |
|---|---|---|
| ChatGPT | account Custom Instructions — a plain text box the user controls and can read back | `native` |
| Claude | memory import — Anthropic re-extracts it, calls it experimental, warns it may not be incorporated | `reconstructed` |

On the same four-object graph that scores **0.775** compiling to Claude, ChatGPT
scores **1.000**. That is not a preference for one vendor: it is the difference
between a box you can verify and an extractor you cannot, and a score that could not
express it would not be measuring anything.

## Locality filters harder at a compile, not softer

`Locality` decides which connected surfaces may read an object back. The Store
protocol treats `caller_surface=None` as a trusted internal caller and applies no
restriction — right for the CLI and for background jobs, and wrong for the compiler,
which was originally listed among them.

A Live Sync read is something the user watches happen and can revoke by removing a
key. **A compile is a transfer to another company that cannot be taken back.** So
every compiler declares the surface it compiles *to* (`LocalModelCompiler` is
`Provider.LOCAL`, `ClaudeCompiler` is `Provider.CLAUDE`) and carries only what the
user allowed there.

Withheld objects are **recorded in the manifest, not silently dropped** — the
`## Withheld` section is the only evidence the user has that the thing they asked to
stay put actually stayed put. They are deliberately not a fourth `Fidelity` and never
enter `source_object_count`: the fidelity categories answer "what could the
destination hold", which is a fact about the destination, while this answers "what
was it allowed to receive", which is an instruction from the user. Scoring them
together would make using the feature look like a migration failure.

The Context Inspector shows locality on every card for the same reason. The reviewer
is the last check before a compile, so the page has to say where an object can end
up, not only what it says.

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

## The Claude leg, verified by hand

There is no way to automate this one — no Projects import API exists, so the package
is installed by a human and the only honest test is a human installing it. Compiled
from a four-object graph, pasted into a new Project's custom instructions, then asked
a question with nothing of ours running:

> **What do you know about EventBook's matching engine?**
>
> EventBook's matching engine is single-threaded by design, and the codebase ships
> C++20.
>
> If you want, I can dig into specifics — e.g. how the single-threaded design affects
> throughput/latency tradeoffs, or how you're handling money representation
> (fixed-point over doubles) in the matching logic.

Three things that answer settles, none of which the test suite could:

**Project instructions are a real native container.** Claude used them on the first
turn, unprompted, with no instruction snippet — unlike the connector, which needs one
to be reliable. The `native` classification is earned rather than assumed.

**The scope fan-out works, including the inheritance decision.** It combined the
project fact with `Chris ships C++20 for EventBook`, and then reached for a *global*
preference — fixed-point over doubles — inside a project conversation. That is
exactly the case M5.2 could not settle from documentation: whether account memory
reaches inside a Project is undocumented, so globals are duplicated into each
Project's instructions instead. This is that duplication paying off.

**The §11 marker held.** The block contains `[goal, confidence 0.95] I am building
coletar, a portable AI workspace`. Claude treated it as background about the user and
offered to help, rather than adopting it as a directive. Compiled memory arriving as
data rather than as instructions is the property that matters most here, because it
is the one nobody would notice failing until it already had.

**What this does not establish:** one observation on cooperative content. It says
nothing about how the marker holds against a memory written *to* subvert it, which is
a different test and belongs with the extraction guards.

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
