# coletar

**A portable AI workspace. Memory as a first-class object, with a real provider compiler.**

Your context — facts, preferences, decisions, project state — lives in one canonical,
typed, versioned graph that you own. Every model you use reads and writes that graph.
coletar has no chat interface of its own, and never will: you stay on claude.ai,
chatgpt.com, or your local model exactly as you do today.

Two modes sit on top of the same substrate:

| Mode | What it does | Status |
|---|---|---|
| **Live Sync** | The canonical store stays authoritative. Every connected surface queries it in real time over MCP. Nothing is ever moved. | table stakes — Mem0/Zep/MemoryLake do this |
| **True Migration** | A directional, point-in-time **compile** of canonical objects into a destination's *actual native containers*, with a Migration Manifest and a Continuity Score. Afterwards you can disconnect from coletar entirely and the destination still works. | nobody does this today |

Live Sync is "Plaid for memory." True Migration is closer to actually switching banks.

## Status

**M1 and M2 complete.** The §2 object model, both store backends (zero-infrastructure
in-process and Postgres/pgvector), hybrid retrieval, the append-only Event/Revision
Log with replay, the authenticated MCP server, the local proxy, and a measured
retrieval evaluation suite are implemented, with every acceptance criterion pinned by
a test — including the measured ones:

| | Measured | Bar |
|---|---|---|
| Retrieval hit@5, 106 labelled queries | 85.8% hashing / **92.5%** nomic-embed-text | — |
| Candidate recall@50, Postgres vs exact scan | **99.54%**, zero leaks | ≥98% |
| Search p95, 10,000 objects | **~21ms** | 300ms |
| Extraction false-positive rate | **4.3%** | <15% |
| Proxy added round-trip latency | **~2.4ms p95** | 2s |
| MCP tool round-trip p95 | well under budget | 500ms |

Retrieval is measured across eight query categories, and
[docs/RETRIEVAL.md](docs/RETRIEVAL.md) publishes where it *fails* as well as where it
succeeds — corrections are the weak leg, and a better embedder is not uniformly
better. The compression job and the Continuity Score exist and work; their remaining
acceptance criteria are M4.

**Live Sync is working on a frontier surface.** A memory written in one Claude
conversation is retrievable in another, and the browser bridge does the same with no
Project and no instruction snippet — read *and* write on claude.ai, touching only the
box you type into. The store is multi-tenant end to end (M3.1): `tenant_id` is
required on every store call, enforced by composite keys in Postgres, and proved by an
adversarial suite run against both backends.

**True Migration works on two destinations.** The local-model compiler (M5.1) emits
real Ollama containers, and the Claude compiler (M5.2) emits real Claude ones — one
container per scope, a Migration Manifest naming every object's destination, and a
Continuity Score computed from manifest facts. The local leg was verified the only
way that claim can be verified: compiled, `ollama create`d, then queried with
coletar not running.

| | Measured |
|---|---|
| `object_coverage` on the seeded graph | **1.00** |
| `scope_preservation` (hard gate) | **1.00** — no project fact reaches the global model |
| `fidelity` | 0.69 local / 0.56 Claude — see below |

The score ranks destinations in **both** directions, which is what keeps it a
measurement rather than a preference: Claude wins on project-scoped context (a
Project holds instructions *and* retrievable knowledge), Ollama wins on global
context (a system prompt you control, versus a memory import Anthropic documents as
experimental and re-extracted). On the global-heavy seeded graph that nets out to
local **0.906** vs Claude **0.869**.

**Not yet built:** the ChatGPT compiler (M6) and the observability dashboard.
See [docs/ROADMAP.md](docs/ROADMAP.md) for exactly what is real.

## Quickstart

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env
uv run pytest
```

Everything runs against the in-process store by default, so there is no database to
stand up before you can try it:

```bash
uv run coletar seed          # one object of every type, plus a supersedes chain
uv run coletar remember "I prefer fixed-point integers over doubles for money" --kind preference
uv run coletar search "how should I represent money"
uv run coletar events        # the append-only Event/Revision Log
uv run coletar history mem_… # what one object used to say, and when it changed
```

For the real backend, stand Postgres up and migrate into it:

```bash
docker compose up -d
COLETAR_STORE_BACKEND=postgres uv run coletar migrate
```

### The local-model wedge

Point the proxy at any OpenAI-compatible endpoint. It injects retrieved memory into
the system prompt on the way in and extracts new memory on the way out:

```bash
uv run coletar serve-proxy
```

Then send traffic to `http://localhost:8787/v1` instead of `http://localhost:11434/v1`.
No export, no scraping, no ToS exposure — the whole loop is on your machine.

Both the buffered and the streaming paths inject and extract. Streamed chunks are
forwarded before they are parsed, so reassembly never sits between the model and your
screen — measured overhead is ~2.4ms p95 at 1,000 stored objects.

Extraction is precision-first: 4.3% false-positive write rate against a 50-turn
labelled set, measured in [docs/EXTRACTION.md](docs/EXTRACTION.md).

### The MCP server

```bash
uv run coletar serve-mcp
```

Exposes `search_context`, `write_memory`, `get_project_state` and `list_open_loops`
over streamable HTTP. This is the same server consumers add as a Claude Custom
Connector and developers call directly. See
[docs/CONNECTORS.md](docs/CONNECTORS.md) for the per-provider setup and the
instruction snippets that make tool use actually reliable.

### The Context Inspector

```bash
uv run coletar serve-inspector
```

Open `http://localhost:8789`. Bound to the live store: review, edit, merge and
re-scope objects, with the Event/Revision Log beside them.

**Nothing compiles until you have seen it.** A compile is blocked while any
compile-eligible object has not been reviewed since it last changed — a review is a
statement about what an object said at the time, so editing it withdraws the
approval. The gate is enforced in the CLI too (`--skip-review` overrides it, and the
override is recorded in the `compile.run` event), because a gate one surface can walk
around is not a gate.

Review state is derived from the event log rather than stored on the object: the log
is already the provenance record, and a `reviewed` column would be a second source of
truth that replay could not reconstruct.

Binds loopback only — it performs authenticated-user actions with no auth of its own.

### True Migration

```bash
uv run coletar compile --destination local  --out build/local
uv run coletar compile --destination claude --out build/claude
```

Compiles the graph into each destination's *actual* native containers — one per
scope, so a project fact can never surface in an unrelated conversation. Ollama gets
a Modelfile `SYSTEM` block per scope; Claude gets a Project per scope plus a
`memory.txt` in Anthropic's documented import format. Both get a `MANIFEST.md`
naming every object's destination and a `PROVENANCE.md` explaining where it came
from.

Neither compiler drives the destination's UI. Ollama you run yourself with
`ollama create`; Claude has no Projects import API, so the package tells you exactly
what to paste and upload. After that coletar is out of the loop — which is the point.

Where each object lands and why is published in
[docs/COMPILER.md](docs/COMPILER.md).

### With Postgres

```bash
docker compose up -d
COLETAR_STORE_BACKEND=postgres uv run coletar search "anything"
```

## Architecture

```
Context Link (consumer UI)  ─┐
Developer SDK/API           ─┼─→ Auth/Permissions ─→ Sync Engine
Local Proxy Daemon          ─┘                            │
                                                          ▼
                                              Provider Adapters
                                                          │
                                                          ▼
                                       Normalization / Extraction Layer
                                                          │
                            ┌─────────────────────────────┼──────────────────┐
                            ▼                             ▼                  ▼
                   Canonical Context Graph        Event/Revision Log   Search Index
                            │
                            ▼
                   Provider Compiler ──→ Migration Manifest + Continuity Score
```

Compression, observability and the agentic graph are **views over this substrate**,
not separate subsystems — that's the whole reason the graph carries `supersedes`,
`confidence` and an append-only event log.

| Module | Role |
|---|---|
| `coletar.schema` | The object model (§2). Memory is a subtype, not a special case. |
| `coletar.store` | Canonical graph + vector index + append-only event log. |
| `coletar.retrieval` | Hybrid retrieval and budgeted context assembly. |
| `coletar.mcp` | The hosted MCP server — the one interface every surface talks to. |
| `coletar.proxy` | Local proxy daemon for OpenAI-compatible model servers. |
| `coletar.extraction` | Raw text → correctly-typed, correctly-scored objects. |
| `coletar.compiler` | True Migration: native artifacts, manifests, Continuity Score. |
| `coletar.jobs` | Compression and other background passes over the graph. |

## The acquisition boundary

This is a product constraint, not an MVP shortcut, and it is enforced in the code:

- **Never** automate a click on a provider's site.
- **Never** read an authenticated provider page.
- **Never** reuse a provider session cookie.

Live Sync happens by **capture-by-tool-call** — the provider's own model calls the
MCP server, through the integration point that provider built for the purpose.
Migration acquisition is **human-initiated**: the user clicks their own export
button, and everything after the file lands is automated. Anthropic and OpenAI both
prohibit programmatic extraction in unambiguous language, and Anthropic has
suspended accounts over it. Design to the boundary, not around it.

## Continuity Score

Published weighting, computed from manifest facts, with the arithmetic printable on
demand. A black-box percentage is a badge, not a differentiator — see
[docs/CONTINUITY_SCORE.md](docs/CONTINUITY_SCORE.md).

## Docs

- [docs/SCOPE.md](docs/SCOPE.md) — the v0.2 product scope this repo implements
- [docs/ROADMAP.md](docs/ROADMAP.md) — milestones, and what is stubbed today
- [docs/CONNECTORS.md](docs/CONNECTORS.md) — per-provider connector reality + snippets
- [docs/COMPILER.md](docs/COMPILER.md) — True Migration: native containers, fidelity, scope
- [docs/CONTINUITY_SCORE.md](docs/CONTINUITY_SCORE.md) — the score, defined
- [AGENTS.md](AGENTS.md) — the working agreement for this repo

## License

MIT
