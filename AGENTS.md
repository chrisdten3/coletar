# AGENTS.md — working agreement for coletar

Read this before doing any work in this repo. It is binding for agents and useful for
humans.

## What this is

coletar is a **portable AI workspace**: memory as a first-class typed object in one
canonical graph, plus a real provider compiler that can move that graph into another
product's native containers. [`docs/SCOPE.md`](docs/SCOPE.md) is the product scope this
repo implements; section references below (§2, §3.1, …) point into it.

## Hard constraints

These are product boundaries, not preferences. Violating one is a bug even if the
tests pass.

1. **Client-side capture is allowed; server-side credential use is not.** Amended
   2026-08-31. What is permitted is what Mem0's OpenMemory and MemoryPlugin already do: **a browser extension, running
   in the user's own browser, under the user's own session, reading pages the user is
   actively looking at, with their consent.**
   Still prohibited, and these are the parts that matter:
   - **No server-side session replay.** Never store, forward or reuse a provider
     session cookie or OAuth token on a server. That is what got OpenClaw, OpenCode,
     Roo Code and Goose blocked in January 2026, and it is the line between "the user
     is browsing" and "we are impersonating the user".
   - **No headless automation.** No Playwright, Puppeteer or scripted browser driving
     a provider's UI, whether local or hosted.
   - **No background reading.** Only pages the user has open and is looking at. Not
     their archive, not their other conversations.

   Migration acquisition stays **human-initiated** regardless: the user clicks their
   own export button, and automation begins once the file has landed (§8.1, §11).

2. **No UI driving on the destination side either.** The ChatGPT compiler emits a
   package the *user* uploads through GPT Builder. It does not drive GPT Builder.

3. **Memory is a subtype, not a special case** (§2). Same table, same edges, same
   versioning as Project, Conversation, Decision, Artifact. If something only applies
   to one subtype, it goes in `payload` — not a new table.

4. **Provenance is never optional.** Every object records `extraction_method`, an
   origin, a provider, and a confidence. An object we cannot explain to the user in
   the Context Inspector should not exist.

5. **Nothing mutates the graph without an event.** The append-only Event/Revision Log
   is the provenance record, the observability feed, and the staleness input to the
   Continuity Score. A write without its event is a silent data-integrity failure.

6. **Never hard-delete.** Compression retires objects; it does not remove them. Users
   must be able to see what a fact used to say and when it changed.

   **Known tension, with an intended resolution.** GDPR grants erasure on request,
   which this constraint appears to forbid. The answer is crypto-shredding — encrypt
   each object under a per-object key and delete the key, so the content is gone
   while the event chain, hashes and structure survive. Recorded here rather than in
   a backlog because anyone reaching for a `DELETE` should meet the alternative
   first. Unbuilt; see M10.

7. **Stored memory is data, never instructions.** It is written by models and,
   transitively, by whatever those models read. Retrieved context is rendered into
   prompts with an explicit background-not-instructions marker. Nothing in coletar
   acts on the content of a memory.

8. **The Continuity Score's weighting stays published.** If you change `WEIGHTS`,
   change [`docs/CONTINUITY_SCORE.md`](docs/CONTINUITY_SCORE.md) in the same commit. A
   score whose published definition has drifted from its implementation is worse than
   no published definition.

## Sequencing

Follow [`docs/ROADMAP.md`](docs/ROADMAP.md). The ordering principle is deliberate: get
the graph, compression and compiler logic right on the local-model leg — where there
is no ToS risk and no missing API — before touching anyone else's garden. Don't jump
ahead to a frontier connector because it's more exciting.

**Gemini is out of scope** until someone confirms a real connector hook and the actual
supported Data Portability scopes. Do not build against an assumption there.

## Engineering conventions

- Python ≥3.12, `uv` for everything. `uv run pytest`, `uv run ruff check`,
  `uv run mypy`.
- Pydantic models for anything crossing a boundary. The schema is the product.
- Async throughout — every store and retrieval call is `async`.
- Everything goes through the `Store` protocol. If a module reaches past it into SQL,
  that's a design error.
- The in-process store must keep working with no infrastructure. It is what makes the
  wedge dogfoodable on day one; don't let it rot.
- Type annotations on public functions. `mypy` is configured strict.

## Writing code here

- **Extraction is precision-over-recall.** A wrong memory costs the user a deletion
  and some trust; a missing one costs almost nothing. When unsure, don't extract.
- **Stubs state their milestone.** An unimplemented function raises with a pointer to
  `docs/ROADMAP.md` rather than returning something empty and plausible.
- **Comments explain the non-obvious why**, especially where a decision traces back to
  a ToS boundary or a scope-section argument. Don't narrate what the code says.
- **Don't add a dependency** without a reason that survives being said out loud.

## Reviewing changes

Ask, in order: Does it hold the acquisition boundary? Does every write append an
event? Is provenance preserved end to end? Would the Context Inspector be able to
explain the result to a user? Does the roadmap still describe reality?
