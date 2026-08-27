# Roadmap

Sequencing follows SCOPE §10. The ordering principle: **get the graph, compression and
compiler logic right where there is no ToS risk and no missing API — then touch other
people's gardens.**

Each milestone below states what is *actually implemented* so nobody has to read the
source to find out.

---

## M0 — Substrate ✅ done

The object model and everything that can be built on it without infrastructure.

- [x] `ContextObject` / `Memory` schema with scope, provenance, confidence,
      `extraction_method`, `supersedes`, provider mappings (§2)
- [x] Confidence defaults derived from `extraction_method` — connector writes outrank
      export parsing by construction (§3.1)
- [x] Append-only Event/Revision Log
- [x] In-process store: graph, edges, events, lexical retrieval
- [x] Budgeted context assembly and system-prompt rendering
- [x] Hosted MCP server: `search_context`, `write_memory`, `get_project_state`,
      `list_open_loops`
- [x] Local proxy daemon: inject on the way in, extract on the way out
- [x] Conservative heuristic extraction (precision over recall)
- [x] Compression job: superseded-chain retirement
- [x] Continuity Score with published weights + `explain()`
- [x] CLI, docker-compose, Postgres schema with pgvector

## M1 — Local-model wedge, for real

SCOPE §10 step 1. Sellable as a developer tool on its own, fully dogfoodable, and it
builds the exact hosted MCP server every later step reuses.

- [ ] `PostgresStore` — psycopg wiring against `001_init.sql`; object writes and their
      events must land in one transaction
- [ ] Embedding pipeline against the user's own local model (free inference)
- [ ] Hybrid retrieval: cosine top-k ∪ trigram, re-ranked by confidence and recency
- [ ] `LocalModelCompiler` — Ollama Modelfile `SYSTEM` block + knowledge directory.
      First compiler, and where manifest/score semantics get settled
- [ ] Dedup/merge on write, so the proxy doesn't accumulate near-duplicates
- [ ] Observability read: TTL, size, access-per-object, activity feed — all of it a
      straight read of the event log

## M2 — Claude connector (Live Sync)

SCOPE §10 step 2. Can ship in parallel with M1; depends on no export-parsing work,
since objects arrive already typed.

- [ ] Deploy the MCP server behind HTTPS (ChatGPT will need remote-only too)
- [ ] Auth / per-user scoping — the store is currently single-tenant
- [ ] Ship the instruction snippets in `CONNECTORS.md` as a copy-paste flow
- [ ] LLM-assisted typed extraction with confidence scoring and dedup/merge
- [ ] Extraction on streamed proxy responses (the streaming path skips it today)

## M3 — Claude compiler (True Migration)

SCOPE §10 step 3. The first real True Migration proof point, and the only frontier
surface built against an *official* format rather than a reverse-engineered one.

- [ ] `ClaudeCompiler` → native Claude Project: system prompt + project knowledge
- [ ] Migration Manifest rendering (native / reconstructed / unsupported)
- [ ] Round-trip check: compile, import, verify the destination stands alone

## M4 — ChatGPT → Claude corridor

SCOPE §10 step 4. Highest demand, hardest leg. Ship only once the compiler is proven.

- [ ] Deep-link + desktop folder-watcher for the user-initiated export ZIP
- [ ] ZIP parser → typed objects at `account_export_parse` confidence
- [ ] Context Inspector: review, edit, merge, re-scope before anything compiles
- [ ] `ChatGPTCompiler` → Custom GPT package the **user** uploads. No UI driving.

## M5 — ChatGPT connector (Live Sync, read first)

SCOPE §10 step 5.

- [ ] Developer Mode remote connector, read path
- [ ] Lean on the explicit "remember this" confirmed-write flow, which works today
- [ ] Upgrade to full write when OpenAI extends write-capable custom connectors past
      Business/Enterprise/Edu

## M6 — General release

- [ ] REST API + Python/JS SDKs over the canonical graph
- [ ] Webhooks on the event log
- [ ] Agentic graph explorer (entity / fact / episode view — a filtered rendering of
      the same graph, not a second data model)

---

## Explicitly not on the roadmap

**Gemini.** SCOPE §11: there is no verified third-party MCP-equivalent connector path
for the consumer app, and Gemini conversation data isn't confirmed in the Data
Portability API's supported scopes. Validate the real scopes before spending an hour
on it.

**Anything that automates a provider's UI or reads an authenticated page.** Not a
sequencing question. See the acquisition boundary in the README.
