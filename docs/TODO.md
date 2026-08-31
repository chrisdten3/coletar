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

### 6. Live Sync coverage
- [ ] ChatGPT connector via Developer Mode remote MCP (read path)
- [ ] Client-side DOM capture for Claude and ChatGPT — **blocked on the AGENTS.md amendment**
- [ ] Per-site toggles and a visible capture indicator
- [ ] Reliability harness: how often does a model call the tool unprompted? **Never measured**

### 7. Cloud sync
- [ ] Sync protocol over the event log — cursor per device, pull since last event
- [ ] Conflict surfacing rather than silent resolution
- [ ] End-to-end encryption decision — it conflicts with server-side retrieval
- [ ] Interim: the Markdown mirror already syncs via Dropbox or iCloud for free

### 8. The enterprise wedge
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

---

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
