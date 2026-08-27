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

**M0 — substrate.** The object model, the in-process store, retrieval, the MCP server,
the local proxy, the compression job and the Continuity Score are implemented and
tested. The Postgres/pgvector backend and every provider compiler are scaffolded with
their contracts fixed and their bodies unwritten. See [docs/ROADMAP.md](docs/ROADMAP.md)
for exactly what is real and what is not.

## Quickstart

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env
uv run pytest
```

Everything runs against the in-process store by default, so there is no database to
stand up before you can try it:

```bash
uv run coletar remember "I prefer fixed-point integers over doubles for money" --kind preference
uv run coletar search "how should I represent money"
uv run coletar events
```

### The local-model wedge

Point the proxy at any OpenAI-compatible endpoint. It injects retrieved memory into
the system prompt on the way in and extracts new memory on the way out:

```bash
uv run coletar serve-proxy
```

Then send traffic to `http://localhost:8787/v1` instead of `http://localhost:11434/v1`.
No export, no scraping, no ToS exposure — the whole loop is on your machine.

### The MCP server

```bash
uv run coletar serve-mcp
```

Exposes `search_context`, `write_memory`, `get_project_state` and `list_open_loops`
over streamable HTTP. This is the same server consumers add as a Claude Custom
Connector and developers call directly. See
[docs/CONNECTORS.md](docs/CONNECTORS.md) for the per-provider setup and the
instruction snippets that make tool use actually reliable.

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
- [docs/CONTINUITY_SCORE.md](docs/CONTINUITY_SCORE.md) — the score, defined
- [AGENTS.md](AGENTS.md) — the working agreement for this repo

## License

MIT
