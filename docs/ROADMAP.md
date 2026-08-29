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

### M3.1 Tenant isolation — local, no infrastructure ✅

The largest structural change since M1, and the one thing blocking any real
deployment. Nothing in the codebase was tenant-aware: every table was unqualified and
every query returned everything.

**The rule everything follows from:** the Store never assumes a tenant; only
application boundaries resolve one. Every `Store` method takes `tenant_id` explicitly
and none of them defaults it. The call sites are noisier for it, and the noise is the
point — each one names the tenant out loud, and a background job cannot drift into a
shared graph.

- [x] Migration `002`: `tenant_id` on all five tables, identity becomes
      `(tenant_id, id)`, and the column's `DEFAULT` is dropped immediately after
      back-filling — the implicit path exists only for the duration of the migration
- [x] Tenant-aware foreign keys, so Postgres refuses a cross-tenant edge or
      `supersedes` even when application code asks for one
- [x] `TenantId` is a `NewType`, not a `str`: signatures read `(tenant_id, object_id)`
      and a swapped pair would otherwise typecheck while reading another tenant's data
- [x] `Principal` carries a `tenant_id`; the MCP server derives the tenant from it
      alone, with no configuration fallback and no tenant argument on any tool
- [x] API keys moved to JSON. Adding the tenant to the colon-delimited form would
      have made it a four-field positional string whose third field silently decides
      whose data you reach
- [x] **All six read paths filtered**: `search`, `list_objects`, `get_object`,
      `list_events` (and therefore `replay`), `edges_from`/`edges_to`, and retrieval
      traces
- [x] The in-process store implements *exactly* the same semantics, namespaced per
      tenant with its own vector index — cross-tenant candidates never enter scoring,
      rather than being filtered afterwards. A backend that isolates differently is
      worse than one that does not isolate at all: the tests pass locally and the
      graph leaks in production
- [x] Both backends raise the same `CrossTenantError`, so a caller never has to know
      which one it is talking to
- [x] Snapshot `format_version` 2, with a version-1 file upgraded to the named legacy
      tenant — visibly, via both a warning and a `store.migrated` event
- [x] Adversarial isolation suite run against **both** backends: a known foreign id
      pushed through every read path, cross-tenant `supersedes` and edges, retirement,
      identical ids in two tenants, per-tenant scopes, and an empty tenant
- [x] Auth validation p95 well under the 50ms budget at 500 configured keys
- [x] Tenant is visible, not implied: `--tenant` on every graph command, writes report
      where they landed, and `coletar tenant` prints what the default resolves to

### M3.2 Cross-surface propagation — local, no infrastructure ✅

The product's actual promise is cross-*surface*, not cross-conversation. This proves
the mechanism with no deployment, no Anthropic API key and no cost, by pointing the
local proxy and the MCP server at the same graph.

- [x] Harness driving both **real** surfaces: a memory extracted from a conversation
      turn by the proxy is returned by the connector's `search_context`, and a
      connector's `write_memory` lands in the next local model's system prompt
- [x] Latency **~0.2ms p50 / 0.4ms p95** against a 1s budget — because propagation is
      pull-based and there is no sync job to wait for
- [x] Both directions measured separately: a store propagating one way only would
      still be broken
- [x] Tenant-scoped both directions, including the misconfigured case
- [x] Verified against Postgres as well, where the graph is genuinely out of process
      rather than a dict two surfaces happen to share
- [x] Runnable on demand (`uv run coletar propagation`) and in CI

The harness takes **callables** rather than surfaces, so M3.3 measures the same thing
against a deployed Claude connector by passing a different pair of functions instead
of being rewritten.

**Two things the harness found.** Writing the same fact from both directions into one
graph propagates only once — M2.3's near-duplicate deduplication working correctly,
and a reminder that a propagation test needs distinct facts per direction. And a
proxy whose configured tenant disagrees with the connector's principal produces two
surfaces that each work perfectly while nothing propagates: correct isolation,
indistinguishable from a broken store unless you know to look, now pinned as
documented behaviour rather than left as a debugging story.

**Known shortcut, recorded rather than drifted into:** the proxy calls `build_store()`
directly, so it bypasses authentication, tenant resolution and scope enforcement — it
*is* the trusted process. That is acceptable for a single-user local daemon and
unacceptable for anything else. See the M4 item.

### M3.3 Deployment and the real Claude connector

**What live testing showed (Aug 2026):** the connector works — a `write_memory` call
from a Claude conversation landed in Postgres with the right tenant, principal,
`mcp_live_write` provenance and embedding. But Claude used its **own** native memory
first, and only called the connector when named explicitly. Our tool descriptions are
the reason: `write_memory` reads as *"Record one durable fact, preference,
instruction, goal or correction"*, which describes native memory exactly and gives a
model no reason to prefer ours. They need to say *why* coletar — portability — since
that is both true and the only actual differentiator (§3 correction).

The consequence for sequencing: **on this surface, reads matter more than writes.**
Writing competes with a free first-party feature and losing is cheap, because the
fact still exists in Claude's memory. Reading has no competitor — Claude's memory
cannot contain what a local model learned or what an import carried in. The graph
fills from tier-1 surfaces (§4.1) regardless.


- [x] Deployment artifacts: `Dockerfile` (multi-stage, `uv sync --frozen`, non-root,
      ~93MB), `fly.toml` with migrations as a release command and `/healthz` as the
      check, `.dockerignore`, and [DEPLOYMENT.md](DEPLOYMENT.md) with the exact
      command sequence
- [x] Two boot-time guards, verified in the container rather than asserted: no API
      keys means no server, and a public bind on the in-process store is refused —
      a reachable endpoint whose graph evaporates on restart is a configuration
      mistake, not a choice
- [x] Image verified end to end against Postgres over real HTTP: migrations ran as
      the release command, `/healthz` open, `/mcp` 401 without a token, four tools
      discovered by a real MCP client, and two keys in two tenants each blind to the
      other
- [ ] `fly deploy` itself — needs Fly credentials, which are the user's to enter
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

### M3.6 Composer bridge — web capture without a Project ✅

Measured in M3.3: reliable unprompted reads on claude.ai require the instruction
snippet in a Project, and the MCP server's own `instructions` field is not enough.
This removes that requirement.

- [x] REST surface on the same app, same auth, same tenancy: `/v1/search`,
      `/v1/capture`, `/v1/remember`. Three endpoints and deliberately nothing for
      enumerating a graph or reading conversations — an extension has no business
      doing either (§4.1)
- [x] CORS as an allowlist with an unauthenticated preflight. A browser strips
      credentials from a preflight by definition, so gating it on auth would fail
      every cross-origin call before the real request was sent. Rejections carry the
      headers too, or the browser hides a 401 and it looks like a network failure
- [x] MV3 extension reading **only the composer**. `COMPOSERS` is the single DOM
      lookup in the file; there is no selector for a message, a response or a
      transcript, so no code path can reach one
- [x] Recall is explicit and visible — memory is written into the box above the
      user's text, so they read it and send it themselves. Nothing is added to a
      message they did not see
- [x] Capture runs the precision-first extractor server-side rather than storing
      turns, so most turns store nothing (4.3% false positives on the labelled set)
- [x] **Verified end to end on claude.ai, 29 Aug 2026, with no Project and no
      snippet.** Read: the button retrieved the stored preference and injected it
      visibly (`surface=claude.ai`, 27ms). Write: typing *"I never use an ORM; every
      query in my projects is plain SQL"* and pressing send stored it at
      `explicit_statement` / confidence 0.95 / `origin=user` — the top tier, because
      they are the user's own words rather than a model's inference of them. The
      negation survived intact, which is the M4 trigger-preservation fix holding in
      production. A money *question* sent earlier stored nothing, which is the
      precision-first extractor declining correctly
- [ ] Measure whether recall+capture in practice beats the Project snippet

**The line, and why it holds.** Reading what a user types into a text field is the
category password managers, text expanders and spell checkers occupy. Reading the
model's Output is what both providers' terms name. The extension does the first and
has no ability to do the second.

### M3.4 Claude Code acquisition — guaranteed capture ✅

Added Aug 2026, and placed ahead of the reliability harness deliberately: guaranteed
capture on a surface the user works in daily is worth more than tuning the odds on a
surface where we are a guest. This is the largest unclaimed row in §4.1's tier-1
table, and OpenAI's Import feature validates the approach by doing the same thing.

- [x] Reads `~/.claude/projects/*/*.jsonl` through the same extractor and the same
      ingest path the proxy and the browser bridge use. A turn typed into Claude Code
      is not a different kind of statement, so it gets no different treatment
- [x] **Only human turns.** In a real session file, 930 records had `type: "user"`
      and **873 were `tool_result`** — 94% of what looks like the user speaking is
      tool output being fed back. The discriminator is the content shape, never the
      record type, so pasted file contents and stack traces never reach the extractor
- [x] Working directory becomes the project scope, so a fact stated while working on
      one repository does not surface as global context in another. The scope id is
      derived from the directory name, not the path, so the graph never records where
      on disk someone works
- [x] Incremental by line offset, and `--rescan` re-reads everything for after the
      extractor improves — deduplication makes that safe
- [x] Transcripts stay on disk; events point at the session file rather than the
      graph holding a second copy of the conversation
- [x] `--dry-run`, which earned itself immediately (below)
- [ ] Hooks in `settings.json` for live capture, so a turn is seen within seconds
      rather than at the next import
- [x] **Scope boundary, from §4.1:** documented user-facing artifacts only. Not a
      desktop client's Electron cache — undocumented, unstable, and adjacent to
      session tokens. Never the rendered page.

**What the dry run found, and why it matters more than the feature.** Against 257
real human turns the extractor scored **0% precision** — all three of its extractions
were first-person sentences quoted inside pasted JSON or prompt templates full of
`[placeholders]`. The M2.2 labelled set was conversational turns; a developer's
transcript is a different domain, and precision measured on one does not transfer to
the other. Two guards and five new labelled negatives later: 0 junk from the same
corpus, and the labelled set unchanged at 4.3% false positives with 100% recall.

Had the import run instead of the dry run, three junk memories would be in the graph,
one contradicting another.

### M3.5 Tool-use reliability harness

**The bars need a qualifier before they are measured.** Testing on 28 Aug 2026 showed
`search_context` fires unprompted *inside a Project carrying the instruction snippet*
and does not fire without one — with the MCP server's own `instructions` field set and
saying the same thing. So "≥80% read-at-start" is only meaningful as "≥80% inside a
Project with the snippet". Measured any other way it would describe a configuration
the product cannot deliver.



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
- [x] **Dedup/merge on write** (`coletar.ingest`). Near-duplicates used to be dropped
      only at *assembly* time, which protects retrieval and not the compiler:
      `list_objects` is what a compile reads, so True Migration would have emitted
      every duplicate the proxy ever wrote. Ten restatements are now one object.
      A duplicate **corroborates** — the existing object gets an
      `object.corroborated` event, because "the user said it again in a different
      session" is real provenance — and confidence is deliberately *not* inflated,
      since repetition is weak evidence. Corrections are never folded into what they
      correct, which would discard the correction and leave the stale fact standing.
      Lives at the ingest boundary rather than in `Store`, so a compiler or replay
      can still write exact objects
- [x] **Extraction off the response path.** The proxy queues extraction as a
      background task; the streaming path already had this property. 0.1ms today
      with the regex extractor, seconds once M6.2's model does the extracting — and
      a failing extractor can no longer break a chat, since the reply has already
      left
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
