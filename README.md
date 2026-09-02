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

**Focus, as of 2026-09-02.** The product is Live Sync and bulk acquisition, and what
differentiates it is **selective context**, **provenance**, and **temporal state** —
deciding which assistant may read which fact, knowing where every fact came from, and
being able to ask what the graph believed at a past moment. True Migration stays
built and stays the trust story; it is not what the product is sold on.

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
Continuity Score computed from manifest facts. Both legs are verified the only way the claim can be
verified — by installing the package and asking. Ollama: compiled, `ollama create`d,
queried with coletar not running. Claude: pasted into a real Project, which then
answered from the graph, pulled an inherited global preference into a project
conversation, and treated a compiled goal as background rather than as an
instruction.

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

**Not yet built:** M7 — the ChatGPT read connector, the REST API and SDKs, and
webhooks on the event log.
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

The proxy reaches the graph as a **named principal with stated scopes**, not as a
database client (M4.2). By default that principal is in-process, so there is still
nothing to deploy. Set `COLETAR_MCP_URL` and `COLETAR_MCP_API_KEY` and the same
daemon becomes an MCP client of the hosted server instead, holding an API key rather
than database credentials and passing through the same auth, tenancy and scope checks
as any other connector.

Both the buffered and the streaming paths inject and extract. Streamed chunks are
forwarded before they are parsed, so reassembly never sits between the model and your
screen — measured overhead is ~2.4ms p95 at 1,000 stored objects.

Extraction is precision-first: 4.3% false-positive write rate against a 50-turn
labelled set, measured in [docs/EXTRACTION.md](docs/EXTRACTION.md).

### The MCP server

```bash
uv run coletar serve-mcp        # streamable HTTP, for hosted connectors
uv run coletar serve-mcp-stdio  # stdio, for Claude Desktop — no deployment at all
```

Exposes `search_context`, `write_memory`, `get_project_state` and `list_open_loops`
over streamable HTTP. This is the same server consumers add as a Claude Custom
Connector and developers call directly. See
[docs/CONNECTORS.md](docs/CONNECTORS.md) for the per-provider setup and the
instruction snippets that make tool use actually reliable.

The stdio form is the cheapest working connector: Claude Desktop launches coletar
as a subprocess and speaks MCP on stdin and stdout, so there is no host, no TLS and
no public URL. Identity comes from the operating system rather than a token,
because the client launched the process — the same trust model as the local proxy,
and safe for the same reason: nothing binds a port.

### The Context Inspector

```bash
uv run coletar serve-inspector
```

Open `http://localhost:8789`. Bound to the live store: review, edit, merge and
re-scope objects, with the Event/Revision Log beside them. Two more views on the same
server — `/dashboard` for TTL, object size, last access, token use, latency and an
explanation of the last search, and `/agentic` for the entity / fact / episode
rendering with episode lineage.

Both are **views**: every number is derived from objects and events that already
exist, and opening a page writes nothing.

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

### Importing a ChatGPT export

```bash
uv run coletar import-chatgpt ~/Downloads/chatgpt-export.zip
uv run coletar import-claude  ~/Downloads/claude-export.zip
uv run coletar import-claude-code                              # local transcripts
uv run coletar watch-downloads                                 # or notice one landing
```

You click your own export button — ChatGPT's settings, or Claude's Settings >
Privacy > Export Data — and they email you the archive; automation starts once the
file is on your disk (§8.1).

Both providers ship a file called `conversations.json` holding **different shapes**,
so the watcher discriminates on structure and routes to the matching importer. For
ChatGPT the parser walks only the **active branch** of each conversation, because
that file is a tree and an answer you edited away should not enter the graph beside
one you kept. Claude's export is a **manifest plus five archives** — conversations, memories,
projects, design chats and metadata — each behind a single-use URL. Memories and
projects are the valuable half: memories are facts Claude already extracted, and
projects carry the container a conversation belonged to.

Everything imported lands at `account_export_parse` confidence (0.60), through the
same extractor and the same ingest boundary as every other surface, so a preference
restated across years of chats corroborates one object instead of creating a hundred.

### Markdown mirror

```bash
uv run coletar mirror --out ~/coletar-vault    # project the graph to Markdown
uv run coletar mirror --pull                   # apply edits you made in Obsidian
```

One file per object with frontmatter carrying its provenance, plus the event log by
month. Supersession renders as an Obsidian wikilink, so a correction chain is
navigable in the graph view.

**The vault is a projection, not the source of truth.** The typed graph stays
canonical, because supersession, provenance and an immutable event log are things a
directory of files cannot make true — and they are what the audit story rests on.
Edits are welcome: `--pull` applies them through the same ingest path every other
surface writes through, so they land as real events rather than as a silent change.

Deterministic, so the vault can live in git and a diff means the graph moved.

### Auditable context

```bash
uv run coletar as-of 2026-03-03                      # the graph as it stood
uv run coletar as-of 2026-03-03 --query "retention"  # search the past
uv run coletar changes 2026-03-01 --until 2026-04-01 # what moved, as a diff
```

Reconstructed from the event log alone, never the object table — if the two disagree,
the log is what you can defend. **Supersession is evaluated as of then**: a fact
corrected last week was still the current answer in March.

This is what an immutable log with full before/after state buys, and it is the part a
memory layer storing only current values cannot retrofit.

### True Migration

```bash
uv run coletar compile --destination local   --out build/local
uv run coletar compile --destination claude  --out build/claude
uv run coletar compile --destination chatgpt --out build/chatgpt
```

Compiles the graph into each destination's *actual* native containers — one per
scope, so a project fact can never surface in an unrelated conversation. Ollama gets
a Modelfile `SYSTEM` block per scope; Claude gets a Project per scope plus a
`memory.txt` in Anthropic's documented import format. Both get a `MANIFEST.md`
naming every object's destination and a `PROVENANCE.md` explaining where it came
from.

An object you have marked local to one surface is **withheld** from every other
destination's compile, and listed in that manifest's `## Withheld` section so you
can confirm it stayed put.

Neither compiler drives the destination's UI. Ollama you run yourself with
`ollama create`; Claude has no Projects import API, so the package tells you exactly
what to paste and upload. After that coletar is out of the loop — which is the point.

Where each object lands and why is published in
[docs/COMPILER.md](docs/COMPILER.md).

### The SDK

```python
from coletar.sdk import Coletar

async with Coletar("https://coletar.example", api_key="sk-…") as client:
    await client.remember("I prefer fixed-point integers for money")
    hits = await client.search("how should I represent money", explain=True)
```

`remember`, `search`, `inspect`, `history`, `supersede`, `retire`, `compile` — and no
`delete`, because there is no endpoint under one. `retire` excludes an object from
retrieval and from compile while leaving it readable, which is what lets `history`
still answer.

There is no `tenant` parameter: it comes from the key, server-side. And the client
sends **no telemetry** — it contacts the base URL you gave it and nothing else.

The same surface in JavaScript lives in [sdk/js](sdk/js), with no dependencies. It
is server-side only: the SDK routes deliberately get no CORS headers, because an
API key in browser JavaScript is a key you have published.

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

A product constraint, not an MVP shortcut, and enforced in the code. Amended
2026-08-31 to permit client-side capture; the lines below are what remains, and they
are the ones that matter.

- **Never** store, forward or reuse a provider session cookie or OAuth token on a
  server. That is what got OpenClaw, OpenCode, Roo Code and Goose blocked in January
  2026, and it is the line between "the user is browsing" and "we are impersonating
  the user".
- **Never** drive a provider's UI with Playwright, Puppeteer or any headless browser.
- **Never** read in the background — only pages the user has open and is looking at,
  not their archive.

What *is* permitted is what Mem0's OpenMemory and MemoryPlugin already do: a browser
extension, in the user's own browser, under their own session, reading pages they are
actively viewing, with their consent.

**The risk is real and is stated so it stays visible.** Anthropic's Consumer Terms
prohibit automated access and prohibit powering an application from a consumer
subscription, and permit suspension without notice. **The account at risk is the
user's, not ours.** Anyone selling on provenance should be able to answer "how do you
obtain this data" well, and that answer is now more complicated than it was.

Migration acquisition stays **human-initiated** regardless: the user clicks their own
export button, and everything after the file lands is automated.

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
