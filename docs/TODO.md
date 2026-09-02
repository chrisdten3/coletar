# TODO — from engine to product

**Where this actually is.** The substrate is real and unusually well tested: 595
tests, a typed versioned graph with provenance and an immutable event log, hybrid
retrieval measured against a labelled suite, three working compilers, importers for
ChatGPT and Claude verified against a real export, and audit queries across two
temporal axes. Nothing in the competitive set has the audit half.

**What does not exist is the product around it.** There are no user accounts — API
keys are hand-edited into an environment variable. There is no signup, no billing, no
hosted deployment, no onboarding, and no designed interface. The only UI is a
loopback developer tool with no authentication. The browser extension has never been
packaged. CI does not run the tests.

A demo is close. A product someone else can use is not.

**Repositioned 2026-09-02.** The focus is Live Sync and bulk acquisition (backfill),
with the differentiation carried by **selective context (locality)**, **data
provenance**, and **temporal model state**. True Migration is deprioritised — it
stays built and it stays the trust story ("you can leave"), but it is no longer what
the product is sold on.

That repositioning aims almost entirely at things that already exist. What is missing
is *coverage* — which model surfaces are actually wired up — and compliance.

---

## Done

### Core substrate
- [x] Canonical object model — one row type, Memory as a subtype, not a special case
- [x] Provenance required on every object: extraction method, origin, provider, confidence
- [x] Append-only Event/Revision Log with full before/after state, immutable
- [x] Supersession and soft retirement; **nothing ever hard-deletes**
- [x] Replay: `replay_object`, `replay_history` — reconstruct any past state from the log
- [x] Multi-tenancy end to end, adversarially tested on both backends
- [x] Locality — per-object restriction of which surfaces may read a fact
- [x] Valid time (`valid_from`/`valid_until`) as a second axis, distinct from record time

### Storage
- [x] In-process store, zero infrastructure, JSON snapshot with a versioned format
- [x] PostgreSQL + pgvector (HNSW) + pg_trgm, full parity with the in-process store
- [x] Migration runner with a checksum ledger; 4 migrations
- [x] Both backends verified identical to three decimal places on the retrieval suite

### Retrieval
- [x] Hybrid vector + lexical ranking, one published formula, one implementation
- [x] Measured: **hit@5 89.6%**, MRR 0.741, ~21ms p95 at 10k objects, zero leaks
- [x] Labelled 106-query suite across 8 categories, baselines pinned in tests
- [x] Retrieval traces — query digest, components, versions, latency per stage
- [x] Sensitivity policy filter (documented for two milestones before it existed)
- [x] Supersession-aware candidates — corrections 50% → 90%
- [x] §5.1 strategy boundaries; RRF and MMR available and off by default
- [x] Token-budgeted context assembly with dedup and oversized-skip

### Capture and acquisition
- [x] Local proxy for any OpenAI-compatible model — inject on the way in, extract on the way out
- [x] Extraction with 7 guards; **4.3% false-positive rate** on a labelled set
- [x] Model-assisted extraction with a grounding guard against fabrication
- [x] ChatGPT export importer — tree-aware, active branch only
- [x] Claude export importer — conversations, **memories**, projects; verified against a real export
- [x] Claude Code transcript importer
- [x] Folder watcher — content-based detection, zero false positives across 50 files
- [x] Raw archive store, content-hashed, so exports can be re-parsed as extraction improves
- [x] Ingest boundary with dedup/corroboration — a restatement corroborates, never duplicates

### Live Sync
- [x] Hosted MCP server: `search_context`, `write_memory`, `get_project_state`, `list_open_loops`
- [x] API-key auth with scopes; read-only keys rejected **server-side**, not hidden
- [x] Per-credential rate limiting with truthful `Retry-After`
- [x] Browser extension for claude.ai — composer read + inject, zero setup
- [x] Cross-surface propagation harness

### True Migration
- [x] `LocalModelCompiler` → Ollama, **verified live** (`ollama create`, coletar off, model answered)
- [x] `ClaudeCompiler` → Projects + memory import, **verified by hand** in a real Project
- [x] `ChatGPTCompiler` → Custom GPT package (built, never human-verified)
- [x] Migration Manifest and Continuity Score with published weights and `explain()`
- [x] Locality enforced at the compile boundary; withheld objects recorded, not dropped

### Inspector and observability
- [x] Live-store Inspector: review, edit, merge, re-scope
- [x] **Compile gate** — nothing leaves until a human has seen it, enforced in CLI too
- [x] Dashboard: TTL, object size, last access, activity feed, token use, latency by surface
- [x] Agentic view — entity/fact/episode as a filter, with episode lineage
- [x] As-of queries and change diffs across both temporal axes

### Developer surface
- [x] REST API — search, remember, inspect, history, supersede, retire, compile
- [x] Python SDK and JavaScript SDK, held together by a parity test
- [x] **No hard-delete anywhere**; no telemetry in either SDK
- [x] Webhooks — event metadata only, HMAC-signed, published retry policy
- [x] Markdown mirror + Obsidian, one-way with an explicit `--pull`
- [x] Source documents as provenance, content-hashed

---

## To do

### 1. Accounts and auth — nothing here exists
- [ ] User accounts: signup, login, password reset, email verification
- [ ] Replace static `COLETAR_MCP_API_KEYS` with issued-per-user keys in the database
- [ ] Key management UI — create, name, scope, revoke, last-used
- [ ] Session auth for the web UI (the Inspector currently has **none**)
- [ ] OAuth for MCP connectors, so users don't paste bearer tokens by hand
- [ ] Password hashing, rate-limited login, CSRF on all forms
- [ ] Tenant provisioning on signup (the tenancy model is ready; nothing creates one)

### 2. Hosting and infrastructure
- [ ] Decide the host — **Fly trial has ended and deploys are failing**
- [ ] Managed Postgres with backups and a tested restore
- [ ] Secrets management; database credentials out of `.env`
- [ ] Migration strategy for production (runner exists; no zero-downtime story)
- [ ] Health checks, uptime monitoring, error tracking, log aggregation
- [ ] Staging environment separate from production
- [ ] Custom domain and TLS

### 3. CI and quality gates
- [ ] **CI does not run the tests.** Only `fly-deploy.yml` exists, and it fails
- [ ] Run pytest, ruff and mypy on every PR
- [ ] Run the Postgres suite in CI (42 tests skip locally without a DSN)
- [ ] Run the JS SDK tests
- [ ] Branch protection: no merge on red
- [ ] Dependency scanning and update automation

### 4. The web product — no designed UI exists
- [ ] Marketing site (the preview page is a gated demo, not a product site)
- [ ] Signed-in app shell: navigation, account, settings
- [ ] Onboarding: connect a tool, import your history, see your first memories
- [ ] Redesign the Inspector for users rather than developers
- [ ] Empty states, loading states, error states
- [ ] Mobile-responsive; the Inspector is desktop-only
- [ ] Design system: type, colour, spacing, components
- [ ] Accessibility pass

### 5. Distribution
- [ ] Package the extension and publish to the Chrome Web Store (currently unpacked dev-mode)
- [ ] Firefox and Safari builds
- [ ] Extension onboarding and permissions copy
- [ ] Publish the Python SDK to PyPI and the JS SDK to npm
- [ ] Installable desktop app for the proxy and watcher (currently CLI-only)

### 6. Live Sync coverage — **the real gap, audited 2026-09-02**

| Surface | Mechanism | State |
|---|---|---|
| Claude web | extension composer bridge | ✅ verified live |
| Claude web | Custom Connector → hosted MCP | built, **not deployed** |
| Claude Desktop | local MCP via `claude_desktop_config.json` | ✅ **built + verified** |
| Claude Desktop | Custom Connector (remote) | built, **not deployed** |
| Claude Code | transcript importer | ✅ built + tested |
| ChatGPT web | Developer Mode remote MCP | ❌ never wired or tested |
| ChatGPT web | extension composer bridge | ✅ **built** — surface from `Origin` |
| ChatGPT Desktop | anything | ❌ no path investigated |
| Ollama / local | proxy inject + extract | ✅ verified live |

- [x] **stdio transport for the MCP server** — `coletar serve-mcp-stdio`. Identity
      comes from the OS rather than a token, because the client launches us as its
      own subprocess; stdout is kept clean because it *is* the protocol channel
- [ ] ChatGPT connector via Developer Mode remote MCP (read path)
- [ ] Client-side DOM capture for Claude and ChatGPT — **unblocked**; AGENTS.md was
      amended 2026-08-31. Content script per provider, `MutationObserver` on the
      transcript, emitting to the existing `/v1/capture` so extraction, guards,
      dedup and provenance are unchanged — only the source is new
- [ ] Per-provider DOM selectors are the ongoing maintenance cost. MemoryPlugin's
      own docs warn their Gemini sync breaks when that UI changes; budget for the
      same rather than being surprised by it
- [ ] Per-site toggles and a visible capture indicator
- [ ] Reliability harness: how often does a model call the tool unprompted? **Never measured**

### 7. Cloud sync
- [ ] Sync protocol over the event log — cursor per device, pull since last event
- [ ] Conflict surfacing rather than silent resolution
- [ ] End-to-end encryption decision — it conflicts with server-side retrieval
- [ ] Interim: the Markdown mirror already syncs via Dropbox or iCloud for free

### 8. The enterprise wedge — now the primary bet

Verified competitive position (research 2026-09-02): **Mem0's "selective memory" is
write-time extraction filtering, not read-time access control**, and its
`user_id`/`agent_id` are partitions for applying different extraction rules rather
than access policies. No competitor found controls which assistant may *read* an
individual memory. That is coletar's `Locality`, and it is the differentiator.

**But Mem0 is already SOC 2 Type 1, HIPAA and GDPR compliant.** Certification is
table stakes for enterprise, not an edge. The edge is the capability underneath it.

- [ ] Locality in the UI — the whiteboard scenario needs to be a toggle, not an API
      argument. This is the demo, and it currently has no interface
- [ ] Locality presets — "work only", "never leave my local model", per-project rules
- [ ] Temporal validity in the UI — set and view `valid_from`/`valid_until`
- [ ] Audit export: signed, timestamped report of what was known and when
- [ ] PDF ingestion for policy documents (a deliberate dependency decision)
- [ ] Roles and permissions beyond single-user tenants
- [ ] SSO / SAML
- [ ] Audit log of *user* actions, distinct from the graph's event log
- [ ] Data residency and retention policy controls

### 9. Security and compliance
- [ ] Third-party security review before any customer data lands
- [ ] Encryption at rest and in transit, documented
- [ ] Privacy policy, terms of service, DPA
- [ ] GDPR: export and deletion — **note the tension with never-hard-delete**
- [ ] Incident response plan
- [ ] SOC 2 if enterprise is the direction

### 10. Business
- [ ] Pricing and plan limits
- [ ] Billing (Stripe), usage metering, quotas
- [ ] Support: docs, contact, issue triage
- [ ] Analytics with disclosure — the SDKs currently send nothing, which is a promise to keep
- [ ] Legal entity, contracts

### 11. Known gaps in what is built
- [ ] `scope_isolation` stuck at 77.8% — the roadmap's diagnosis was wrong; evidence is in `RETRIEVAL.md`
- [ ] ≥40% token reduction unreachable on a curated corpus; retry against a real import
- [ ] Postgres sparse/full-text candidate path (RRF seam exists, retriever doesn't)
- [ ] Low-confidence clustering pass
- [ ] ChatGPT compiler has **no human verification**
- [ ] `kind` classification unreliable at 0.5b — needs a larger local model
- [ ] Model extraction measured on only 30 of 100 turns (machine ran out of memory)
- [ ] Proxy rate limiting is in-process; two workers means two buckets
- [ ] Webhook SSRF guard does not resolve hostnames (documented, deliberate)
- [ ] **`/v1/remember` has no `local_only`** — the browser bridge can only write
      global memories, so a user on claude.ai cannot mark anything Claude-only from
      the UI. The capability exists on the MCP path (`server.py`, where locality
      binds to the calling principal's surface) but not on the REST bridge. Locality
      is the differentiation; a write path that cannot express it is a product hole,
      not a rough edge
- [ ] **The in-process store is a silent footgun for local testing.** With
      `COLETAR_STORE_BACKEND=memory` the graph lives inside the server process, so
      `coletar remember` writes into a short-lived process and vanishes — no error,
      no warning, the CLI prints an object id. Anyone testing the extension against
      a memory-backed server will conclude retrieval is broken. Either warn on
      startup or have the CLI refuse to write to a backend it does not share

---

## Before any product work: what must be true locally

The instruction is that everything local and model-side works before touching
website, auth or deployment. Audited 2026-09-02 — this is the list.

**Verified working, no deployment needed:**
- Locality across surfaces — the whiteboard scenario runs exactly as drawn: Claude
  sees 3 facts, ChatGPT and the local model see 2, the case detail is withheld
- Local model round trip — a 0.5b model answered from the graph with nothing in the
  prompt, and the retrieval trace was recorded
- Claude export backfill — 205 objects from a real export
- Claude Code transcript import
- Claude web via the extension
- As-of and in-force queries on both temporal axes
- Both storage backends identical on the retrieval suite

**Must be built and tested before product work starts:**
- [x] **stdio MCP transport** — done. Claude Desktop works with no deployment at
      all; verified by a real client handshaking with a real subprocess
- [x] **ChatGPT has a working surface.** The extension bridge serves it with no
      deployment. The gap was never the manifest — which already matched
      chatgpt.com — but the bridge hardcoding `Provider.CLAUDE`, which would have
      injected Claude-only memories into ChatGPT's composer and recorded ChatGPT
      captures as Claude. The surface now comes from the `Origin` header
- [ ] **ChatGPT export parser verified** against a real export (still pending)
- [ ] **Reliability harness** — how often does a model call the tool unprompted?
      Never measured, and it is the number that says whether Live Sync works or
      merely exists
- [ ] **The `kind` problem** — 164 of 205 imported objects came back tagged `fact`.
      Needs a larger local model, and it affects every downstream surface
- [ ] **Full-corpus extraction measurement** — model extraction was measured on 30
      of 100 turns before the machine ran out of memory
- [x] **Locality end-to-end through a real provider** — done 2026-09-02. A
      `local_only` memory scoped to Claude was written to Postgres; the same query,
      the same API key and the same server returned it in claude.ai's composer and
      `nothing relevant` in ChatGPT's. The surface comes from the `Origin` header,
      which Chrome sets and the page cannot forge. This is the first proof of the
      central claim above the `retrieve()` layer
- [ ] **Multi-surface propagation on real providers** — the harness proves it
      in-process; nothing has proven it across two live tools

## The shortest path to a demo

Everything here already works. This is a sequencing note, not new engineering.

1. `uv run coletar import-claude ~/Downloads` — 205 objects of real context
2. `uv run coletar serve-inspector` — show the graph, provenance, the review gate
3. `uv run coletar as-of 2026-03-03 --in-force 2026-01-01` — the audit query nobody else has
4. `uv run coletar compile --destination claude` — install it, ask it something only the graph knew
5. `uv run coletar mirror --out ~/vault` — open it in Obsidian

**Estimated work: none.** The demo exists today on your machine.

## The shortest path to a first customer

1. **CI running tests** — half a day, and everything else depends on trusting the suite
2. **Pick a host and deploy** — one day, blocked on the Fly billing decision
3. **Accounts, keys in the database, session auth** — the largest single gap, one to two weeks
4. **Onboarding: connect, import, see it work** — one week
5. **Publish the extension** — a few days plus store review
6. **Privacy policy and terms** — required before anyone else's data lands

**Realistically four to six weeks** to something a friendly customer could use unattended,
assuming the enterprise items wait.
