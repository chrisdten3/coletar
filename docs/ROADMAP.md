# Roadmap

Milestone numbering follows the **build plan** (`PORTABLE_AI_WORKSPACE_BUILD_PLAN.md`),
which slices SCOPE §10 into requirements / deliverables / acceptance criteria. The
substrate this repo started from was previously labelled "M0"; it is now distributed
across M1 and M2 below, where the build plan puts it.

The ordering principle is unchanged: **get the graph, compression and compiler logic
right where there is no ToS risk and no missing API — then touch other people's
gardens.**

Each milestone states what is *actually implemented* so nobody has to read the source
to find out. A box is only ticked when its acceptance criteria are pinned by a test.

---

## M1 — Canonical schema + storage backend ✅ done

Persistent storage for the §2 object model, with graph relationships, semantic search
and a full audit trail. Everything else reads and writes against this.

### M1.1 Schema definition & migrations ✅

- [x] One `ContextObject` table with a `type` discriminator — Memory is a subtype,
      not a special case (§2)
- [x] Typed edges (`supersedes`, `derived_from`, `belongs_to`, …), idempotent on
      `(src, dst, type)`
- [x] Every §2 `Memory` field present, round-tripping exactly through both backends
      and through the snapshot file
- [x] Provenance and `extraction_method` are non-optional — an object we cannot
      explain to a user cannot be constructed
- [x] "Active" excludes retired **and** superseded objects
- [x] `migrations/001_init.sql` plus a checksum-ledgered runner (`coletar migrate`)
- [x] Seed fixture: one object of every type plus a three-link supersedes chain
      (`coletar seed`, `coletar.seed`)

### M1.2 Vector index ✅

- [x] Embedding on the write path — searchable on the very next call
- [x] Two embedders: a zero-infrastructure hashing default and Ollama against the
      user's own model server — the latter verified against a live `/api/embed`
      in gated tests, having previously had no coverage at all
- [x] Relevance published per backend: **95%** hashing, **100%** nomic-embed-text,
      asserted against `tests/fixtures/relevance_baselines.json` so the documented
      numbers cannot drift from the implementation
- [x] Hybrid vector + lexical ranking, one formula shared by both backends
- [x] Project-scoped search includes global objects, excludes other projects'
- [x] Fixed 20-query relevance set, reused by M4.1 and M6.2
- [x] **95%** top-5 hit rate (bar: 90%); **~21ms** p95 at 10,000 objects (bar: 300ms)

See [RETRIEVAL.md](RETRIEVAL.md) for the published formula and the measured numbers.

### M1.3 Event / Revision Log ✅

- [x] Append-only, with actor, timestamp and full before/after object state
- [x] Frozen events; reads hand out copies, so a caller cannot rewrite history
- [x] Stores hand out **detached** objects — mutating what you read cannot change the
      graph behind the log's back
- [x] Replay: `replay_object(store, id, at=T)` reconstructs state as of T from the
      log alone; `coletar history <id>` is the Inspector timeline in the terminal
- [x] Exactly one log row per write, pinned per write path; logging overhead well
      under the 10ms budget

### M1.1b Postgres + pgvector backend ✅

- [x] `PostgresStore`: object, embedding and event commit in one transaction
- [x] Soft retire only — no DELETE against `context_object` or `event_log` anywhere
- [x] Hybrid candidate narrowing (HNSW cosine ∪ trigram), re-ranked by the shared
      formula
- [x] Integration tests gated on a reachable database, so the suite stays green with
      no infrastructure: `docker compose up -d && uv run coletar migrate`

---

## M2 — Hosted MCP server + local model proxy

SCOPE §10 step 1. Sellable as a developer tool on its own, fully dogfoodable, and it
builds the exact hosted MCP server every later step reuses.

### M2.1 MCP server core ✅

- [x] Four tools registered with typed schemas: `search_context`, `write_memory`,
      `get_project_state`, `list_open_loops`
- [x] Streamable HTTP transport (ChatGPT never accepts local/stdio)
- [x] Auth layer gating every call — ASGI middleware in front of the whole app, so
      it cannot be forgotten by a future tool. Fails closed with no keys configured
- [x] Read/write scopes enforced server-side, which M7.1's ChatGPT leg requires
- [x] The authenticated principal is recorded on every event the connector produces,
      so §6's dashboard can answer "who wrote this"
- [x] Malformed `kind`, `sensitivity`, `scope`, `top_k`, `content` and `supersedes`
      rejected with a message that names the field and enumerates the legal values —
      recoverable by a model that can retry, not a bare "Error executing tool"
- [x] Typed Pydantic response models, so §2 conformance is enforced rather than
      hand-assembled
- [x] Access events record object ids only — never the query text or the retrieved
      content (§11)
- [x] p95 tool round-trip well under 500ms over 500 objects; 200-call fuzz run with
      zero unhandled exceptions
- [ ] **Still single-tenant.** Scopes are enforced, tenancy is not — any valid key
      reaches the whole graph. Per-user isolation is M3.1

### M2.2 Local proxy / bridge ✅

- [x] Proxy in front of any OpenAI-compatible endpoint; injects retrieved memory into
      the system prompt, extracts durable facts on the way out
- [x] Conservative heuristic extraction — precision over recall, now guarded against
      questions, quotation, attribution, anaphora and phrasal particles
- [x] 50-turn labelled set with a written definition of "durable", labelled before
      anything was measured against it. **False-positive rate 37.5% → 4.3%**
      (bar: under 15%), precision 62.5% → 95.7%, recall 100%, 22/22 correctly typed
- [x] Negation survives into stored content — a memory that inverts its source is
      worse than no memory at all
- [x] Extraction on streamed responses: chunks are forwarded first and reassembled
      second, so nothing sits between the model and the screen. A failed stream
      writes nothing
- [x] Proxy writes recorded as connector writes under a `local-proxy` principal
- [x] Added round-trip latency **~2.4ms p95** at 1,000 objects (budget: 2s)
- [x] Documented Ollama setup (docs/EXTRACTION.md, README)
- [x] Live end-to-end run against a real Ollama model. Verified on `qwen2.5:0.5b`
      (a 0.5B model proves the same wire contract as an 8B one, at a tenth of the
      memory): injection, 41 streamed chunks forwarded, extraction firing on both
      the streaming and buffered paths, both writes attributed to `local-proxy`.
      Ollama's real SSE frames are checked in as
      `tests/fixtures/ollama_sse_stream.txt`, so the contract stays tested without
      a model server

### M2.3 Retrieval evaluation and traces ✅

The M1.2 relevance set proves the basic scorer works. This milestone makes candidate
narrowing and final context assembly observable enough to change safely.

- [x] Retrieval restructured into §5.1's stages: policy filter and candidate
      generation in the store, fusion/rerank in `ranking`, deduplication and
      token-budgeted assembly in `context`. The published formula stays the
      deterministic default and the backend-parity contract
- [x] Packing skips an oversized hit rather than terminating, so one long low-ranked
      memory cannot censor every smaller useful result behind it
- [x] Near-duplicate results dropped before packing
- [x] One append-only retrieval trace per search, carrying candidate source,
      component scores, selected object ids, token use, component versions,
      per-stage latency, the calling surface and the calling principal. It
      **replaces** the per-hit access event — twelve rows per search flooded the log
      the §6 dashboard reads
- [x] Tracing lives at the retrieval boundary, not in each caller, so the proxy and
      the CLI are covered by the same guarantee as the MCP tool. A surface that has
      to remember to trace is one that eventually does not
- [x] Traces carry the calling principal, matching write attribution. Anonymising the
      actor is not a privacy measure — the content being absent is; an unattributed
      trace still holds a query-shaped record while being useless to §6 and M3.1
- [x] Raw query text and content excluded structurally: the trace holds a truncated
      digest of the query and object ids only. Content-level debugging is a per-call
      argument, never a global setting
- [x] `explain` mode returns the vector, lexical, confidence and recency contribution
      per hit without changing the default response. Components are carried from the
      ranking path, never recomputed, so an explanation cannot drift from its score
- [x] **106 labelled queries over 58 objects** across all eight categories, with the
      M1.2 twenty carried verbatim so the headline number stays comparable
- [x] Candidate recall@50, hit@1/@5, MRR@5, precision@5, injected tokens and p50/p95
      measured and published with the harness (`uv run coletar evaluate`)
- [x] Postgres ANN/sparse candidate recall vs exact in-process search: **99.54%
      recall@50** against a 98% bar, with **zero** cross-scope leaks and zero
      retired or superseded results. Verified against pgvector/pgvector:pg17

**What the suite found**, and why it was worth building before touching a ranker:

- **Corrections are the weak leg** — 50% on the hashing default, 90% with real
  embeddings. When a fact is superseded the old object is correctly hidden, but the
  correction often does not mention the old value, so querying by the stale term
  finds nothing. The fix is graph-shaped rather than ranking-shaped and lands at M4.
- **A better embedder is not uniformly better** — `nomic-embed-text` is *worse* at
  exact identifiers (100% → 93.8%), which is the concrete case for keeping the
  hybrid's lexical half.
- **`scope_isolation` is 77.8% on both backends**, so no embedder will move it.
  Isolation itself is intact (zero leaks); ranking within the correct scope is what
  fails.
- **The queued stemmer "fix" was measured and rejected.** Emitting the raw token
  alongside its stem made every metric worse — hit@5 85.8% → 79.2%, and exact_id
  100% → 93.8%, worse at the category it was meant to help. Pinned by a test with
  the reasoning, so it is not "fixed" again unmeasured.

---

## M3 — Claude connector (Live Sync)

SCOPE §10 step 2. The first time coletar does the thing it exists to do. Everything
through M2 is substrate — a graph, two backends, measured retrieval, an authenticated
server — and none of it has yet touched a frontier model. M3 is where the central
claim gets tested: *a memory written on one surface is available to every other
surface's next conversation.*

Claude is the only new provider, and deliberately so. It is the one frontier surface
where the path is fully sanctioned — Custom Connectors are read **and** write on
individual accounts, objects arrive already typed through `write_memory`, and no
export parser or page reading is involved. ChatGPT is read-plus-confirmed-write until
OpenAI extends write scope (M7.1); Gemini has no verified path at all. Claude tests
the real integration with the fewest unrelated variables.

**Two decisions taken, so they are not re-litigated mid-slice:**

- **Tenancy is a flat `tenant_id`.** No user/org hierarchy. Hierarchy is easy to add
  later and hard to remove, and nothing in §2 or §9 needs one yet.
- **The MCP service deploys to Fly.io**, a long-running container host. Not
  serverless: the server runs stateful streamable HTTP (`stateless_http=False`) and
  holds a psycopg connection pool, and the in-process store cannot exist on
  serverless at all. Making it work on functions means changing the transport and
  re-verifying MCP session handling across invocations — real work whose only payoff
  is the host. A consumer UI can live on Vercel later; the service should not.

The ordering below front-loads everything that is testable for free. Propagation here
is pull-based — there is no sync job, the next `search_context` simply sees the write
— so the *mechanism* is graph-level and provable locally, before a single dollar of
hosting. If cross-surface propagation is broken at the graph level, that should
surface before deployment, not after.

### M3.1 Tenant isolation — local, no infrastructure

The largest structural change since M1, and the one thing blocking any real
deployment. Nothing in the codebase is tenant-aware today: every table is
unqualified and every query returns everything.

- [ ] Migration `002` adding `tenant_id` to `context_object`, `context_edge`,
      `object_embedding`, `event_log` and `compile_run`
- [ ] `Principal` carries a `tenant_id`; the store resolves every call against it
- [ ] **All six read paths filtered**, each of which leaks independently:
      - [ ] `search` and `list_objects` — the obvious pair
      - [ ] `get_object` — knowing an id must not grant access
      - [ ] `list_events`, and therefore `replay` — **the worst leak**, because event
            rows carry full `before`/`after` object state, so an unfiltered log
            leaks *content* rather than merely ids
      - [ ] `edges_from` / `edges_to` — graph structure is not public either
      - [ ] retrieval traces — they carry a principal, result ids and query digests
- [ ] Isolation tests against real Postgres, including a direct fetch of a known
      foreign id, a cross-tenant `supersedes`, and a foreign object id passed to
      every read path
- [ ] Auth validation adds no more than 50ms per call

### M3.2 Cross-surface propagation — local, no infrastructure

The product's actual promise is cross-*surface*, not cross-conversation. This proves
the mechanism with no deployment, no Anthropic API key and no cost, by pointing the
local proxy and the MCP server at the same graph.

- [ ] Harness: write through the proxy, read through the MCP server, and back
- [ ] Propagation latency under 1s at p95, tenant-scoped both directions
- [ ] Runnable on demand and in CI

**Known shortcut, recorded rather than drifted into:** the proxy calls `build_store()`
directly, so it bypasses authentication, tenant resolution and scope enforcement — it
*is* the trusted process. That is acceptable for a single-user local daemon and
unacceptable for anything else. See the M4 item.

### M3.3 Deployment and the real Claude connector

- [ ] Fly.io deployment of the MCP service, managed Postgres, secrets, migrations
- [ ] Registered as a Claude Custom Connector, completed from Claude's own settings
- [ ] A simulated OAuth handshake issues a token scoped to one tenant
- [ ] Ship the instruction snippets in `CONNECTORS.md` as a copy-paste flow
- [ ] Cross-conversation propagation: a fact written in Claude conversation A is
      retrievable in a fresh conversation B, under 1s at p95. The build plan is
      explicit that this is **not optional** before M3 is done — M3.2 is a
      prerequisite step, never a substitute
- [ ] **Synthetic data only** until isolation, fail-closed auth, secret handling and
      log inspection have all been verified against the deployment

No Anthropic API key is needed for any of this. Claude's cloud calls *our* endpoint;
the credential that matters is a coletar token.

### M3.4 Tool-use reliability harness

A different kind of measurement from anything so far. Every bar to date is
deterministic — same input, same output. This measures whether a *model chooses* to
call a tool, which is non-deterministic and needs enough trials to be a rate rather
than an anecdote.

- [ ] Scripted conversations driven through the Messages API against the deployed
      connector. **This is where an Anthropic API key becomes necessary** — a
      separate credential from the coletar token, pointing the other way
- [ ] `write_memory` fires on ≥85% of clear preference statements
- [ ] `search_context` called within the first two turns ≥80% of the time
- [ ] Spurious writes under 10% on neutral conversations
- [ ] Versioned instruction snippet, so a reliability number is attributable to the
      prompt that produced it

### Not satisfiable in code

Stated here rather than discovered at the end:

- **"Connector setup completes without support intervention ≥90% of the time in an
  internal dogfood test"** needs real people doing setup. The flow and its
  documentation can be built; the statistic cannot be manufactured.
- Registering a real Custom Connector needs a deployed endpoint and a Claude account.

---

## M4 — Table-stakes layer: compression, observability, agentic graph

SCOPE §6. Views over the substrate M1–M3 already built, not a second data model.

- [x] Compression job: superseded-chain retirement, schedulable and on-demand
- [ ] **The local proxy becomes an MCP client.** It calls `build_store()` directly
      today, so it bypasses authentication, tenant resolution and scope enforcement.
      Fine for a single-user local daemon, wrong for anything else: both surfaces
      should pass through the same auth, tenancy and event semantics rather than one
      of them holding database credentials
- [ ] Retrieval strategy interfaces separate candidate generation, fusion, reranking
      and context assembly; the current published formula remains the deterministic
      default and backend-parity contract
- [ ] **Supersession-aware candidate generation.** M2.3 measured corrections at 50%
      on the hashing default: a superseded object is correctly hidden, but the
      correction rarely repeats the old value, so "is Chris still at Acme?" matches
      nothing. Match the superseded object for recall, follow its `supersedes` edge,
      return the replacement — and never the stale object itself
- [ ] **Ranking within a scope.** `scope_isolation` sits at 77.8% on *both* backends,
      so it is structural rather than semantic: global and project objects compete
      and the right one loses. Zero leaks either way — isolation is not the problem
- [ ] Postgres sparse/full-text candidate path supplements HNSW ANN; trigram matching
      remains an identifier/fuzzy-match signal rather than the lexical retriever
- [ ] Configurable reranking: reciprocal-rank fusion and MMR first; optional bounded
      local cross-encoder only if it improves the labelled suite within the latency
      budget. No reranker may bypass scope, sensitivity, retirement or supersession
- [x] Context assembly deduplicates near-identical results and skips an oversized hit
      when a later useful hit still fits, instead of terminating packing immediately
      — **delivered in M2.3**, since the retrieval trace could not report
      `deduplicated` and `skipped_oversized` without the assembly stage that produces
      them
- [ ] Token budget honoured at retrieval time, with ≥40% token reduction on the
      seeded corpus and no loss from the M1.2 top-5 set
- [ ] Low-confidence clustering pass (needs embeddings — now available)
- [ ] Observability dashboard over the event log: TTL, object size, last access,
      live activity feed, retrieval score explanation, token use and latency
- [ ] Agentic graph explorer (entity / fact / episode — a filtered rendering of the
      same graph, not a second store)
- [ ] Evaluate entity overlap, graph-distance and temporal-validity signals against
      the labelled suite before enabling them; preserve episode-to-derived-object
      lineage and never introduce a parallel graph store

---

## M5 — Claude compiler (True Migration) + Inspector + Continuity Score

SCOPE §10 step 3. The first real True Migration proof point, and the only frontier
surface built against an *official* format rather than a reverse-engineered one.

- [x] Continuity Score with published weights and `explain()`
- [ ] Context Inspector: review, edit, merge, re-scope — and no compile action until
      every compile-eligible object has been shown at least once
- [ ] `ClaudeCompiler` → native Claude Project: system prompt + project knowledge
- [ ] Migration Manifest rendering (native / reconstructed / unsupported)
- [ ] `object_coverage` ≥95%, `scope_preservation` = 100% (a hard gate — it is the
      actual differentiator)
- [ ] `LocalModelCompiler` → Ollama Modelfile `SYSTEM` block + knowledge directory.
      Build this first: it is the one compiler with no third-party constraint, so it
      is where manifest and score semantics get settled.

---

## M6 — ChatGPT migration corridor

SCOPE §10 step 4. Highest demand, hardest leg. Ship only once the compiler is proven.

- [ ] Desktop folder-watcher for the user-initiated export ZIP — detection within
      10s, zero false positives across 50 unrelated files
- [ ] ZIP parser → typed objects at `account_export_parse` confidence, ≥85%
      extraction precision against a hand-labelled 100-object fixture set
- [ ] Raw archive stored separately from derived objects, so it can be re-parsed as
      extraction improves
- [ ] `ChatGPTCompiler` → Custom GPT package the **user** uploads. No UI driving.

---

## M7 — ChatGPT connector + general release + polish

SCOPE §10 steps 5–6.

- [ ] Developer Mode remote connector, read path; write attempts rejected
      server-side, not merely hidden client-side
- [ ] REST API + thin async Python/JS SDKs over the canonical graph, released only
      after auth and tenant isolation, with rate limiting
- [ ] SDK exposes `remember`, `search`, `inspect`, `history`, `supersede`, `retire`
      and `compile`; it preserves canonical IDs, provenance and event semantics and
      deliberately exposes no hard-delete shortcut
- [ ] `search(..., explain=True)` exposes component scores and component versions;
      SDK telemetry is private/redacted by default and never undisclosed outbound
      analytics
- [ ] Webhooks on the event log, with a documented retry policy

---

## Explicitly not on the roadmap

**Gemini.** SCOPE §11: there is no verified third-party MCP-equivalent connector path
for the consumer app, and Gemini conversation data isn't confirmed in the Data
Portability API's supported scopes. Validate the real scopes before spending an hour
on it.

**Anything that automates a provider's UI or reads an authenticated page.** Not a
sequencing question. See the acquisition boundary in the README.

**A second memory or graph source of truth.** Mem0-style SDK ergonomics,
Zep/Graphiti-style temporal retrieval and Letta-style context budgeting may be
implemented as interfaces, ranking signals and views over the canonical graph. They
do not justify a parallel flat memory store, graph database model or agent-owned
write path that bypasses the Store and Event Log.
