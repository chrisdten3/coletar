# Portable AI Workspace — v0.2 Product Scope
## Memory as a First-Class Object, with a Real Provider Compiler

**Builds on:** `PORTABLE_AI_WORKSPACE.md` (v0.1)
**Change from v0.1:** v0.1 was mostly architecture. This is a buildable scope — what ships, in what order, for which of the two customers, and where the actual moat is versus where you're just matching Mem0/Zep/MemoryLake table stakes.
**Updated:** incorporates a ToS review of the acquisition/destination methodology (§4, §8, §11) and a full scope for the feed-forward/live-sync connector architecture (§3.1) — the mechanism that lets a memory update on one model become available to every other model, with no chat interface of your own.

---

## 1. Two Customers, One Substrate

Every competitor picked one lane:

- **Mem0, Zep, Supermemory** — developer infra. API/SDK first. Mem0's own docs are
  explicit that it *"sits between your application and your model"*: **you** call
  `add` after a turn and `search` before the next one. That works wherever you own
  the loop and reaches nothing else — for claude.ai and chatgpt.com they ship an MCP
  server with exactly the same discretionary property everyone's has.
- **MemoryLake** — consumer-facing. **Correction, Aug 2026:** this was previously
  described here as "a shared bucket + a browser extension." That is wrong on
  mechanism. MemoryLake exposes one project as an **MCP endpoint** with a Key ID,
  Secret and bearer token, and Claude, ChatGPT and Gemini all read from it — the
  same architecture this document proposes. The extension is a secondary bridge for
  injecting context into ChatGPT prompts. What *is* still true: the store is flat
  entries ("individual Memory entries"), there is no typed graph, and their
  ChatGPT↔Claude "migration" pages are how-to guides rather than a compiler.
- **Anuma, Echo** — Echo could not be verified in an Aug 2026 search and may be
  defunct or renamed. Treat the earlier characterisation as unsupported.

**Live Sync is not a differentiator and this document should stop implying it is.**
Convergent architecture on a sanctioned protocol is expected: there is one way to do
capture-by-tool-call, and everyone doing it correctly looks alike. What still
differs is *underneath* — a typed graph with scope, provenance, confidence and
supersession versus flat entries — and *afterwards*, in the compiler.

Two places the substrate is genuinely stronger than the field, worth naming because
they are cheap to lose:

- **Scoping is enforced, not conventional.** Mem0's docs say *"Always scope searches
  with filters such as `user_id`"* — a rule the developer must remember. Here
  `tenant_id` is a required argument on every store method with no default, typed so
  a swapped pair will not typecheck (§9, M3.1). Impossible to forget beats important
  to remember.
- **Corrections are typed.** Mem0's extraction path is *"ADD ONLY"*: both facts are
  stored and the application must issue an explicit update or delete. Here a
  correction carries `supersedes`, and supersession excludes the stale fact from
  retrieval the moment it is written.

Nobody serves both audiences off the *same* underlying object model. That's the
wedge, and it's also why "memory as a first-class object" has to come first — if memory is a schema, not a feature, both audiences read/write the same substrate through different doors:

```
                    CANONICAL MEMORY SUBSTRATE
                    (typed graph, provenance, confidence)
                              │
              ┌───────────────┼────────────────┐
              ▼                                 ▼
     Consumer Web App                   Developer Surface
     (no terminal, ever)                (API / SDK / MCP server)
     - Connect flow                     - REST + SDK (py/js)
     - Context Inspector                - MCP server (any MCP client
     - Compile / Migrate button           reads live: Claude Desktop,
     - Dashboard (TTL, graph,             agentic IDEs, custom apps)
       compression, activity)            - Webhooks / event stream
```

A feature built for developers (the graph, the compiler, the compression job) should be *visible* to consumers as a dashboard, not rebuilt. A feature built for consumers (the Connect flow, the Inspector) should be *callable* by developers as an endpoint. One substrate, two skins.

---

## 2. The Object Model (unchanged core, tightened)

Keep the v0.1 `ContextObject` schema — it already had the right bones (scope, provenance, confidence, provider_mappings). The thing to be disciplined about: **Memory is a subtype, not a special case.** Same table, same edges, same versioning as Project, Conversation, Decision, Artifact.

```yaml
Memory:
  id: mem_...
  kind: fact | preference | instruction | goal | correction | inference
  content: string
  scope: { type: global | project, id: optional }
  confidence: 0.0-1.0
  extraction_method: explicit_statement | account_export_parse
                    | browser_capture | mcp_live_write | derived_summary
  sensitivity: normal | personal | sensitive | restricted
  supersedes: optional
  provenance: { origin_type, provider, source_object_ids, confidence }
  provider_mappings: { provider_name: { external_id, external_type } }
```

`extraction_method` is the field v0.1 didn't have and needs: it's what lets the Context Inspector (below) show a user *how sure* the system is and *where it came from*, which is the whole "provenance-preserving" promise made concrete instead of a slide bullet.

---

## 3. Two Operating Modes — say this distinction out loud in the product itself

This is the part every competitor blurs, and it's worth making a literal UI toggle, not just an internal architecture note.

**Live Sync Mode** (table stakes — this is what Mem0/MemoryLake/Echo already do)
The canonical store stays authoritative. Every connected surface — Claude Desktop over MCP, a local Ollama model through the proxy, a developer's agent through the SDK — queries it in real time. Nothing is ever "moved." You're always a client of your own memory.

**True Migration Mode** (the actual product — but see the correction below)
A directional, point-in-time **compile**: canonical objects → the destination's *actual native containers*. Not a pasted text blob — a real Claude Project (via Anthropic's existing memory import/export surface), a real ChatGPT Custom GPT + Memory entries (best-effort, since OpenAI has no import API), or a native system-prompt/profile file for a local model. After compiling, the user can disconnect from you entirely and the destination product works on its own. You produce a **Migration Manifest** (object counts, native vs. reconstructed vs. unsupported) and a **Continuity Score** — see §7.

This distinction is also your answer to "isn't this just Mem0" — Mem0's founder literally calls his product "Plaid for memory," but Plaid never asks a bank to *become* your new primary bank. Live Sync is Plaid-for-memory. True Migration is closer to an actual bank-switching product — more like ACH-transfer-and-close-the-old-account than balance-checking.

### Correction, Aug 2026: "nobody does this today" is no longer true

OpenAI shipped **Import** in ChatGPT: *"Bring setup, projects, and chats from other AI
apps into ChatGPT"*, with an **autosync** toggle — *"Automatically sync new and updated
content from connected sources"* — and automatic detection of locally installed AI
apps (observed detecting Claude Code and Claude Cowork, with an import history showing
repeated session imports). It appears to work by reading what those apps write to the
local filesystem.

This is §11's platform risk arriving, and it has to be said plainly rather than
absorbed: a lab shipped cross-vendor context movement natively, for free, with
continuous sync.

**What it is, precisely.** It is *one-directional, into ChatGPT*. It does not make a
user's context portable; it makes it OpenAI's. It is an acquisition funnel — the same
lock-in with a better on-ramp — and there is no corresponding "export my ChatGPT
context to somewhere else."

**What it costs this product.** The highest-demand corridor in §10 step 4 —
"I want my Claude context inside ChatGPT" — is now solved by the destination vendor,
natively, at no cost. That specific pitch is gone. Building it anyway would be
building a worse version of a free feature.

**What survives, and it is the more defensible half.** A *neutral* store the user
owns, readable by anything, compilable *out* to any destination including back out of
ChatGPT. OpenAI will happily import your context; it will not help you leave. The
interesting product is the one that takes it out again — which makes the compiler
(§7, and still entirely unbuilt) more central than before, not less.

**Same week, smaller version of the same force:** Claude ships native memory, and
Claude Code has shipped Auto Memory on by default since Feb 2026, writing its own
`MEMORY.md`. In live testing, Claude preferred its own memory over an installed
connector's `write_memory` tool until told explicitly to use the connector. Any
third-party memory layer now competes with a first-party feature that needs no setup
and no approval prompt. See §4's revised acquisition table for the consequence.

---

## 3.1 Feed-Forward: The Connector Architecture (Live Sync, Made Concrete)

This is the part v0.1 and the first pass of v0.2 left as a one-line MCP bullet, and it needs to be load-bearing, since it's the mechanism behind the actual requirement: **no chat interface of your own — the user stays on claude.ai, chatgpt.com, or their local model exactly as today, and a memory update on one surface is available to every other surface's next conversation.**

There are two permitted mechanisms. **Capture-by-tool-call** exposes the canonical
store as a hosted remote MCP server and lets the provider's model call it through an
official integration point. **Consent-based client capture** runs in the user's own
browser session and can read the composer text the user is actively submitting. It
must not read provider output, other conversations, archives, or pages in the
background, and it must never forward or replay a session credential. This is the
same boundary as a password manager or spelling extension: the user is browsing;
coletar is not impersonating them from a server.

**Per-provider connector reality, as of today:**

| Provider | Connector mechanism | Read | Write | Notes |
|---|---|---|---|---|
| **Claude** (claude.ai / Desktop) | Custom Connector; optional consent-based composer extension | Yes | Yes | MCP writes are model-discretionary; the extension gives deterministic capture of user-submitted composer text without reading model output. |
| **ChatGPT** | Remote MCP connector; optional consent-based composer extension | Yes | Connector writes currently gated | The extension supplies deterministic user-turn capture without relying on write-capable MCP. ChatGPT accepts remote HTTPS MCP servers, never local/stdio. |
| **Gemini** (consumer app) | Unconfirmed | ? | ? | No verified third-party MCP-equivalent connector path for the consumer app as of this writing. Treat as unvalidated — don't commit engineering time until you've confirmed a real hook exists. |
| **Local models** | Ollama has no native MCP client (open GitHub issue) — needs a bridge (MCPHost, ollama-mcp-bridge, or similar); llama.cpp's own server recently picked up native MCP client support | Yes | Yes | This is exactly the Local Proxy Daemon already scoped in §4/§10 — you're building that bridge yourself, so there's no third-party dependency here at all. |

**How propagation actually works, given the above:**

An explicit connector write lands in the Canonical Context Graph immediately. An
opted-in composer capture instead stores an encrypted, surface-local `EPISODE`; a
bounded asynchronous worker applies the selected semantic extractor and then writes
the derived object to the graph. There is no provisional regex write. The next
`search_context` after materialisation can see it from another permitted surface.

**Two things worth shipping alongside the connector itself, or it won't be reliable:**

1. **Default instruction snippets** — a short paste-in block for Claude's Project instructions and ChatGPT's Custom Instructions, telling the model when to check memory (start of conversation, topic shift) and when to write to it (explicit facts, preferences, decisions). Without this, tool use is inconsistent — model behavior follows the system prompt, not the tool's mere existence.
2. **A confidence distinction between connector writes and migration writes** — already supported by the `extraction_method` field in §2 (`mcp_live_write` vs. `account_export_parse`). Connector writes are structured and explicit at the source (a tool call with typed arguments), so they generally warrant *higher* default confidence than anything pulled out of a raw export.

Connector tool writes are independent of the True Migration extraction pipeline
because they arrive typed. Consent-based raw-turn capture deliberately reuses the
same semantic proposal, grounding, and policy path as backfill so live and imported
text do not acquire different meanings.

---

## 4. Provider Coverage: Frontier + Local

Be honest with yourself about what's actually available per provider — this table is the real engineering plan, not the pitch deck version.

This table covers **Migration-mode acquisition** — the one-time or periodic
export-and-compile path. For live sync, see §3.1. Official connectors and narrowly
scoped, user-consented composer capture are permitted; server-side session replay,
headless UI driving, transcript scraping, and background reading are not.

| Provider | Migration-mode acquisition path | Compile target | Notes |
|---|---|---|---|
| **Claude** | Anthropic's own memory import/export feature (Settings → Privacy) | Native Claude Project (system prompt + project knowledge), via the same official import feature | Best surface by far — you're building *against an existing official format*, not reverse-engineering one |
| **ChatGPT** | Human-initiated: deep-link the user to Settings → Data Controls → Export, they click, they download. Your desktop agent then auto-detects the ZIP in Downloads and parses it. No automated clicking, no session reuse, no page reading — OpenAI's terms flatly prohibit automated/programmatic extraction | Best-effort: a downloadable Custom GPT configuration package (instructions + knowledge files) the user uploads themselves via GPT Builder, plus a Memory-entries text file the user pastes in — not an automated UI-driving script, since OpenAI's terms cover destination-side automation too | No export/import API exists. Highest-demand corridor (per earlier research), least official support — the most fragile leg, and explicitly one where "automate everything" is off the table. Live *writes* from ChatGPT happen through the connector in §3.1, not through this pipeline |
| **Gemini** | Google's Data Portability API is real and OAuth-scoped, built specifically for service-switching — but Gemini conversation/memory data isn't confirmed in its supported scopes. Fall back to a user-initiated Google Takeout archive | Best-effort text/instruction reconstruction only, no Workspace replay | Test the actual supported scopes before building against this API; don't assume Gemini coverage. Deprioritize until validated |
| **Local models (Ollama, LM Studio, vLLM, llama.cpp servers)** | You control the whole loop — no export/scrape needed | A lightweight local proxy that sits in front of the model's OpenAI-compatible endpoint, injects retrieved memory into the system prompt on the way in, and extracts new memory on the way out | **Still your cleanest wedge.** No ToS risk, no missing API, ships first — and it doubles as the reference implementation for the connector pattern in §3.1, since Ollama needs the same kind of bridge you're already building here |

### 4.1 Capture is tiered, and no single mechanism covers everything (Aug 2026)

The question "how do we capture every interaction on every surface" has no single
answer, and it is worth writing down why rather than re-asking it. Everything depends
on **whether we own the loop**:

| Surface | Own the loop? | Capture | Mechanism |
|---|---|---|---|
| Local models | Yes | **Guaranteed** | Proxy sits in the request path |
| **Claude Code** | Yes | **Guaranteed** | Hooks + the session `.jsonl` and project files it writes to disk |
| A developer's own app | Yes | **Guaranteed** | SDK, the Mem0 pattern (§9) |
| ChatGPT export | — | Guaranteed, delayed | User-initiated archive (§10 step 4) |
| claude.ai / Claude Desktop | No | **Guaranteed for submitted user turns when opted in; otherwise discretionary** | Consent-based composer extension; MCP tool calls |
| ChatGPT web | No | **Guaranteed for submitted user turns when opted in; otherwise discretionary** | Consent-based composer extension; MCP read + confirmed writes |

Mem0's application-side `add` and `search` pattern is the first row. The browser
extension adds a second client-side position for hosted chat surfaces, but only for
the active composer and only after explicit consent; it is not general access to an
account or transcript.

**The strategic consequence: maximise the rows where we own the loop.** Guaranteed
capture on a surface the user works in daily is worth more than tuning the odds on a
surface where we are a guest. Claude Code is the largest unclaimed such surface, and
it is where a great deal of real work now happens.

**Reading local files is not the prohibited thing, and the line matters.** §8.1 and
§11 prohibit automating a provider UI and reading authenticated account content,
apart from the narrow, consented active-composer boundary above. Files an app writes
to the user's own disk, at the user's instruction, are the user's—reading them is
closer to reading your own shell history than to scraping. OpenAI's Import feature
(§3) is exactly this, which is a useful validation of the approach.

That said, not all local data is one category, and the boundary should be drawn
deliberately:

- **Documented, user-facing artifacts** — Claude Code's `~/.claude` session
  transcripts, `CLAUDE.md`, an exported archive. These are yours. **In scope.**
- **An application's internal cache** — e.g. a desktop client's Electron
  `Session Storage`/LevelDB. Undocumented, unstable across releases, and it sits
  beside session tokens, so reading it means handling credentials to reach content.
  **Out of scope**, closer in spirit to reading an authenticated session.
- **The rendered page in a browser.** **Never.** This is the acquisition boundary,
  and Anthropic has enforcement history against exactly it (§11).

The local-model leg deserves more weight than it's getting in most people's mental model of this space — it's the one place where "first-class memory object across every model" isn't fighting anyone's walled garden, because there's no garden. It's a good place to get the graph, compression, and compiler logic *right* before you touch anyone else's ToS.

---

## 5. System Architecture (six components, same as v0.1, now with owners)

```
Context Link (consumer UI)  ─┐
Developer SDK/API           ─┼─→ Auth/Permissions ─→ Sync Engine
Local Proxy Daemon          ─┘                            │
                                                            ▼
                                          Provider Adapters (per §4 table)
                                                            │
                                                            ▼
                                    Normalization / Extraction Layer
                                    (LLM-assisted typed-object extraction,
                                     confidence scoring, dedup/merge)
                                                            │
                              ┌─────────────────────────────┼───────────────────┐
                              ▼                              ▼                   ▼
                     Canonical Context Graph        Event/Revision Log     Search/Retrieval Index
                     (typed, versioned, scoped)      (audit, TTL, access)   (ANN + sparse + graph)
                              │
                              ▼
                     Provider Compiler ──→ Migration Manifest + Continuity Score
                              │
              ┌───────────────┼────────────────┐
              ▼                ▼                ▼
        Claude Project    ChatGPT Custom GPT   Local model profile
```

The Extraction Layer is where most of the real engineering risk lives — turning a raw ChatGPT export or a live conversation stream into correctly-typed, correctly-scoped, correctly-confidence-scored objects is a much harder problem than "chunk and embed," which is all the current players actually do.

Worth stating plainly since it's easy to read past a box diagram: this requires a real persistent backend, not just the MCP server. At minimum, a graph-capable store for the canonical objects and their edges (the Memory schema from §2), plus a vector index alongside it for semantic retrieval (what `search_context` actually queries), plus the append-only Event/Revision Log. The MCP server is the interface in front of all three, not a replacement for them. Specific tech (Postgres with a graph extension vs. a dedicated graph DB, pgvector vs. a standalone vector store) is an implementation choice, not a scope decision — pick what you're fastest with for the local-model wedge in §10 step 1, since that's where this gets built first.

### 5.1 Retrieval is a measured pipeline, not a vector-store feature

The retrieval boundary has to remain independent of the storage backend. Its logical
stages are:

```
scope / activity / sensitivity policy filter
                    ↓
       ANN candidates ∪ sparse candidates
                    ↓
       rank fusion + policy-aware reranker
                    ↓
          diversity / deduplication
                    ↓
       token-budgeted context assembly
```

The existing confidence- and recency-aware formula is the deterministic default
reranker and remains the parity contract between the in-process and Postgres stores.
Other rerankers are optional strategies behind the same interface: reciprocal-rank
fusion for differently-scaled candidate sources, maximal marginal relevance for
diversity, and a bounded local cross-encoder where its measured precision gain pays
for the added latency. No model-based reranker may bypass scope, sensitivity,
retirement or supersession policy.

Postgres should narrow with HNSW ANN plus a real sparse/full-text candidate path;
trigram similarity remains useful for approximate identifiers but is not a substitute
for keyword relevance. Entity overlap, graph distance and temporal validity become
additional signals only after labelled tests show that they improve the queries
coletar actually receives. They operate over the Canonical Context Graph, never a
second graph-shaped source of truth.

Every retrieval strategy is evaluated at two boundaries. Candidate recall measures
whether narrowing retained the relevant object; final ranking measures whether it
landed in the context actually shown to the model. The checked-in suite must cover
exact identifiers, paraphrases, temporal questions, corrections, negation, scope
isolation, multi-hop questions and deliberate near-misses. Track candidate recall,
hit rate, reciprocal rank, precision, token cost and p50/p95 latency. A reranker
cannot repair an object that candidate generation discarded, and a high hit rate
obtained by flooding the context is not a retrieval win.

One append-only retrieval trace records component scores, candidate source, selected
object IDs, model/index/reranker versions, token use and stage timings. Raw queries
and retrieved content are not telemetry by default: store a redacted representation
or hash unless the user explicitly enables content-level debugging. This is product
observability over the existing log, not outbound analytics hidden in an SDK.

---

## 6. Folding In the Table-Stakes Features (from the screenshots)

You're right that compression, observability, and the agentic graph have to be in scope — a product without them looks primitive next to Mem0/Zep today. The key move is treating them as **views over the same substrate**, not separate subsystems:

- **Memory Compression Engine** (Mem0's pitch) → a background job that walks the graph and collapses low-confidence or superseded nodes into condensed bundles per scope, exposed as a token-budget knob at retrieval time. It's "free" once the graph has `supersedes` and `confidence` fields — you're not building a second thing, you're running a job against the first thing.
- **Observability & Tracking** (Mem0's dashboard) → a straight read of the Event/Revision Log that's already in the architecture for provenance reasons. TTL, size, access-per-object, live activity feed and retrieval traces — candidate sources, score components, latency and token use — all fall out of data needed to explain and tune the product anyway.
- **Agentic memory graph** (the Zep/Graphiti-style entity/fact/episode view in your fourth screenshot) → a filtered rendering of the Canonical Context Graph scoped to `origin_type: agent` or a given agent's project. Entities, Facts, Episodes are just three more `ContextObject` types with edges, not a parallel data model.

The point: none of this is a separate roadmap track. It's UI and policy over the same
graph you're already building for the compiler. If you build compression, telemetry
or an agentic graph as separate systems, you'll end up maintaining multiple sources
of truth.

The competitive boundary is equally explicit. Mem0 is the reference for SDK
ergonomics, filtering, score explanations and retrieval evaluation; Zep/Graphiti is
the reference for temporal and graph-aware retrieval; Letta is the reference for
working-context budgeting. Those are techniques to integrate where measurement
justifies them, not product identities to copy. coletar remains the portable,
inspectable, provenance-preserving graph and provider compiler. Better retrieval is
how that moat reaches the model; it is not the moat by itself.

---

## 7. Continuity Score — make it real, not a marketing number

This doesn't exist anywhere today (confirmed — nothing in the memory-infra literature or competitor sites uses this framing), which means you get to define it, but it has to survive scrutiny or it's vaporware. Rough shape:

```
continuity_score = weighted_avg(
    object_coverage:      % of source objects successfully mapped to a destination type,
    fidelity:             % mapped without lossy flattening (native vs. reconstructed vs. unsupported,
                           per the Appendix D manifest categories from v0.1),
    scope_preservation:   % of project-scoped facts that landed in the correct destination
                           container rather than being flattened to global,
    staleness:            time since last sync / compile
)
```

Publish the weighting. If it's a black-box percentage, it's not a differentiator, it's a badge — and sophisticated early adopters (the people who'd actually pay for this before it's mainstream) will see through that in about five minutes.

---

## 8. Consumer Surface — the "no terminal, ever" requirement

Concretely, this means:

1. **Connect** — for Migration mode: OAuth-style flow where the provider supports it (Claude's memory export/import), a deep-link-then-desktop-folder-watcher flow for ChatGPT (point the user to the export button, they click it once, everything after that — detecting, validating, parsing the ZIP — is automated), one-click for local models if a supported runtime (Ollama, LM Studio) is detected on the machine. For Live Sync mode: a connector setup flow per §3.1 and an explicit consent control for the composer extension. The flow must never automate a provider UI, replay a session credential, read model output or other conversations, or read any page in the background. Reading the active user's submitted composer text locally is the narrow permitted exception, not permission to inspect the account.
2. **Context Inspector** — review extracted objects before anything is compiled anywhere. Edit, merge, retire, adjust scope. Retirement removes an object from retrieval and compilation without erasing its history. This is the trust-building screen; skip it and you're just another "give us your ChatGPT history" product.
3. **Compile** — pick a destination, get a Migration Manifest + Continuity Score, get a real native artifact (a Claude Project link, a ChatGPT Custom GPT config, a local model's profile file) — not a zip of markdown.
4. **Dashboard** — the Mem0-style observability/compression view, but framed as "your memory," not "your API usage."

## 9. Developer Surface

- REST API + thin async Python/JS SDKs for read/write against the canonical graph,
  released only after authentication and tenant isolation are enforced. Match the
  low-friction shape developers expect (`remember`, `search`, `inspect`, `history`,
  `supersede`, `retire`, `compile`) without flattening the canonical schema into a
  generic memory document. There is no hard-delete convenience method. Search can
  return score explanations and provenance, and every write still goes through the
  same event-producing Store path.
- A single hosted, remote MCP server exposing the graph as tools (`search_context`, `get_project_state`, `write_memory`, `list_open_loops`) — see §3.1 for the full per-provider connector picture. This is the same server every consumer connects to via the Connect flow in §8; developers just get raw API/SDK access to the identical tools instead of a connector UI.
- Webhooks on the Event Log for teams building their own agents that need to react to memory changes.

---

## 10. MVP Sequencing

1. **Local-model wedge first.** Canonical schema + local proxy for Ollama/LM Studio + hosted MCP server + measured hybrid retrieval + compression/observability/graph views. Instrument candidate generation and ranking before adding sophisticated rerankers; ship the deterministic scorer first, then enable optional strategies only when the labelled suite shows a gain. No ToS risk, no missing-API risk, fully dogfoodable, and it's a sellable developer tool on its own. This also builds the exact hosted MCP server every later step reuses.
2. **Claude connector (Live Sync).** Point that same hosted MCP server at Claude as a Custom Connector, ship the default instruction snippets. Zero ToS risk, no extraction pipeline needed — the object arrives already typed. This can ship in parallel with step 1, not strictly after it, since it doesn't depend on any export-parsing work.
3. **Claude compiler (True Migration).** Build against Anthropic's official import/export format — the one frontier surface that isn't reverse-engineered. First real "True Migration" proof point.
4. **ChatGPT → Claude corridor**, consumer-facing: human-initiated export + desktop folder-watcher + Context Inspector + Compile button. This is the single highest-demand direction from your earlier research (TechCabal, MemoryLake's own guides), and it's also the hardest — ship it once the compiler logic is proven on the local + Claude legs.
5. **ChatGPT connector (Live Sync, read-path first).** Add ChatGPT as a Developer Mode connector once step 1 exists; ship read-only retrieval now, upgrade to write when OpenAI extends write-capable custom connectors past Business/Enterprise/Edu — or lean on the explicit "remember this" confirmed-write flow in the meantime, which already works today.
6. **General SDK/MCP release** + polish on the agentic graph explorer for developers.

---

## 11. Risks Worth Naming Now

- **No official export APIs from OpenAI or Google, and the ToS language is unambiguous, not gray.** Both OpenAI ("automatically or programmatically extract data or Output") and Anthropic ("access through automated or non-human means... except via an API key or where explicitly permitted") explicitly prohibit exactly the kind of browser automation Echo-style products lean on. Anthropic has real enforcement history here — they suspended accounts running third-party automation tools (OpenClaw/Clawdbot) in early 2026, against a user's *own* subscription, not just scraping. Design to the human-initiated-click boundary in §8, not around it.
- **ChatGPT write-capable connectors are currently gated above the individual tier.** Full read/write custom MCP connectors are, as of mid-2026, restricted to Business/Enterprise/Edu workspaces; individual Plus/Pro users get read (search/fetch). This caps the ChatGPT leg of Live Sync at read-plus-confirmed-writes until OpenAI extends the write scope — plan §10 around that, not against it.
- **Gemini's consumer-app connector story is unvalidated.** Don't commit engineering time to a Gemini connector, or to the Data Portability API for Gemini specifically, until you've confirmed the real supported scopes.
- **Platform risk cuts both ways — and it has now cut. (Updated Aug 2026.)** This
  entry used to say a lab shipping native portability "would be trivial for them" and
  was "worth monitoring, not worth blocking on." Monitoring is over. OpenAI shipped
  Import-with-autosync into ChatGPT; Claude ships native memory; Claude Code ships
  Auto Memory by default. Three first-party features in the same quarter, each eating
  part of this product's stated territory.

  The correct response is not to abandon the thesis but to move to the part labs have
  no incentive to build. Every one of those features moves context **toward** its own
  vendor. None helps a user leave. Neutrality and exit are structurally unattractive
  to a platform and structurally the whole point here — so the compiler (§7) stops
  being the differentiator-in-waiting and becomes the reason to exist. It should be
  built earlier than §10 currently sequences it.
- **Extraction cost at consumer scale.** LLM-assisted typed extraction on every export is real inference spend — model this before pricing the consumer tier.
- **Extraction is a separate data transfer from acquisition.** The user chooses the
  backend, with local Ollama as the default. A third-party extractor receives only
  the candidate turn being mined—never the graph or accepted memories—and must be
  named as a subprocessor. Model output remains untrusted until it is grounded in
  that turn and passes the same policy guards as every other write.
- **Retrieval telemetry can become a second copy of the user's private history.** Do
  not persist raw queries or returned content by default, and do not turn SDK
  instrumentation into undisclosed outbound analytics. Redacted traces must still be
  sufficient to reproduce ranking decisions by object ID and component version.
- **Trust is the actual product, not a feature.** You're asking people to hand over their entire AI history to a third party. Anuma and Echo lead with encryption-at-rest messaging for a reason — the Context Inspector and a legible ownership story matter more here than in most infra products.
