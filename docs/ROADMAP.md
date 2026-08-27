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
      user's own model server
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
- [ ] Live end-to-end run against a real Ollama model — deferred: the dev machine
      is an 8GB M1 and could not host a 4.7GB model alongside the rest of its
      working set. The logic is covered by fake-upstream tests; this is HTTP-shape
      confirmation only

### M2.3 Retrieval evaluation and traces

The M1.2 relevance set proves the basic scorer works. This milestone makes candidate
narrowing and final context assembly observable enough to change safely.

- [ ] Append-only retrieval trace with scope/filter decisions, candidate source,
      component scores, selected object IDs, token use, component versions and
      per-stage latency
- [ ] Raw query text and retrieved content excluded from telemetry by default;
      content-level debugging is an explicit user opt-in
- [ ] `explain` mode returns the vector, lexical, confidence and recency contribution
      for each hit without changing the default MCP response
- [ ] Expand the fixed set to at least 100 labelled queries covering exact IDs,
      paraphrase, temporal, correction, negation, scope isolation, multi-hop and
      deliberate near-miss cases
- [ ] Measure candidate recall@50, hit@1, hit@5, MRR@5, precision@5, injected tokens
      and p50/p95 latency; publish the harness and baseline together
- [ ] Postgres ANN/sparse candidate recall checked against exact in-process search:
      ≥98% recall@50 on the labelled corpus, with zero cross-scope leaks and zero
      retired/superseded results

---

## M3 — Claude connector (Live Sync)

SCOPE §10 step 2. Can ship in parallel with M2; depends on no export-parsing work,
since objects arrive already typed.

- [ ] Deploy behind HTTPS and register as a Claude Custom Connector
- [ ] Per-user scoped auth; user A's token cannot read user B's objects
- [ ] Ship the instruction snippets in `CONNECTORS.md` as a copy-paste flow
- [ ] Tool-use reliability: ≥85% write-on-statement, ≥80% read-in-first-two-turns,
      <10% spurious writes
- [ ] Cross-conversation propagation harness — a fact written in conversation A is
      retrievable in conversation B within 1s at p95. This is the direct proof of the
      product's central claim.

---

## M4 — Table-stakes layer: compression, observability, agentic graph

SCOPE §6. Views over the substrate M1–M3 already built, not a second data model.

- [x] Compression job: superseded-chain retirement, schedulable and on-demand
- [ ] Retrieval strategy interfaces separate candidate generation, fusion, reranking
      and context assembly; the current published formula remains the deterministic
      default and backend-parity contract
- [ ] Postgres sparse/full-text candidate path supplements HNSW ANN; trigram matching
      remains an identifier/fuzzy-match signal rather than the lexical retriever
- [ ] Configurable reranking: reciprocal-rank fusion and MMR first; optional bounded
      local cross-encoder only if it improves the labelled suite within the latency
      budget. No reranker may bypass scope, sensitivity, retirement or supersession
- [ ] Context assembly deduplicates near-identical results and skips an oversized hit
      when a later useful hit still fits, instead of terminating packing immediately
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
