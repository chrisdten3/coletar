# Portable AI Workspace — v0.2 Product Scope
## Memory as a First-Class Object, with a Real Provider Compiler

**Builds on:** `PORTABLE_AI_WORKSPACE.md` (v0.1)
**Change from v0.1:** v0.1 was mostly architecture. This is a buildable scope — what ships, in what order, for which of the two customers, and where the actual moat is versus where you're just matching Mem0/Zep/MemoryLake table stakes.
**Updated:** incorporates a ToS review of the acquisition/destination methodology (§4, §8, §11) and a full scope for the feed-forward/live-sync connector architecture (§3.1) — the mechanism that lets a memory update on one model become available to every other model, with no chat interface of your own.

---

## 1. Two Customers, One Substrate

Every competitor you found last time picked one lane:

- **Mem0, Zep, Supermemory** — developer infra. API/SDK only. No consumer-facing product; you'd never send your mom to mem0.ai.
- **Echo, MemoryLake, Anuma** — consumer-facing, but thin: a shared bucket + a browser extension. No SDK depth, no real graph, no compiler.

Nobody serves both off the *same* underlying object model. That's the wedge, and it's also why "memory as a first-class object" has to come first — if memory is a schema, not a feature, both audiences read/write the same substrate through different doors:

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

**True Migration Mode** (the actual product — nobody does this today)
A directional, point-in-time **compile**: canonical objects → the destination's *actual native containers*. Not a pasted text blob — a real Claude Project (via Anthropic's existing memory import/export surface), a real ChatGPT Custom GPT + Memory entries (best-effort, since OpenAI has no import API), or a native system-prompt/profile file for a local model. After compiling, the user can disconnect from you entirely and the destination product works on its own. You produce a **Migration Manifest** (object counts, native vs. reconstructed vs. unsupported) and a **Continuity Score** — see §7.

This distinction is also your answer to "isn't this just Mem0" — Mem0's founder literally calls his product "Plaid for memory," but Plaid never asks a bank to *become* your new primary bank. Live Sync is Plaid-for-memory. True Migration is closer to an actual bank-switching product — more like ACH-transfer-and-close-the-old-account than balance-checking.

---

## 3.1 Feed-Forward: The Connector Architecture (Live Sync, Made Concrete)

This is the part v0.1 and the first pass of v0.2 left as a one-line MCP bullet, and it needs to be load-bearing, since it's the mechanism behind the actual requirement: **no chat interface of your own — the user stays on claude.ai, chatgpt.com, or their local model exactly as today, and a memory update on one surface is available to every other surface's next conversation.**

The mechanism is not capture-by-observation (reading what happens in a conversation from the outside). Per the ToS review, passively reading page content is exactly what's prohibited. The mechanism is **capture-by-tool-call**: the canonical store is exposed as a single hosted, remote MCP server, and each provider's own model calls it as a tool — the same way this conversation calls its own memory tools. Nothing about that is scraping; it's the sanctioned integration point each provider built for exactly this purpose.

**Per-provider connector reality, as of today:**

| Provider | Connector mechanism | Read | Write | Notes |
|---|---|---|---|---|
| **Claude** (claude.ai / Desktop) | User adds your server as a Custom Connector | Yes | Yes | Fully sanctioned on individual accounts today. Your strongest live-sync leg. |
| **ChatGPT** | User adds your server as a remote MCP connector via Developer Mode (Plus/Pro and up) | Yes | Currently gated | OpenAI has shipped full read/write MCP support, but individual/consumer-tier custom connectors are, as of mid-2026, largely read (search/fetch); full write-capable custom connectors are restricted to Business/Enterprise/Edu workspace admins. ChatGPT also only accepts *remote HTTPS* MCP servers, never local/stdio, so your server has to be hosted regardless of tier. |
| **Gemini** (consumer app) | Unconfirmed | ? | ? | No verified third-party MCP-equivalent connector path for the consumer app as of this writing. Treat as unvalidated — don't commit engineering time until you've confirmed a real hook exists. |
| **Local models** | Ollama has no native MCP client (open GitHub issue) — needs a bridge (MCPHost, ollama-mcp-bridge, or similar); llama.cpp's own server recently picked up native MCP client support | Yes | Yes | This is exactly the Local Proxy Daemon already scoped in §4/§10 — you're building that bridge yourself, so there's no third-party dependency here at all. |

**How propagation actually works, given the above:**

There's no push-sync job. A write lands in the Canonical Context Graph the moment any provider's model calls `write_memory` — automatically via Claude's connector, or via ChatGPT when the user explicitly says "remember this" (which triggers ChatGPT's standard write-confirmation flow — a fine outcome, since it also means every ChatGPT-sourced write is naturally tagged `extraction_method: explicit_statement`, the highest-confidence tier). The *next* `search_context` call from any other connected surface sees it immediately. Propagation latency is bounded by "when does the person next open another model," not by anything you run on a schedule.

**Two things worth shipping alongside the connector itself, or it won't be reliable:**

1. **Default instruction snippets** — a short paste-in block for Claude's Project instructions and ChatGPT's Custom Instructions, telling the model when to check memory (start of conversation, topic shift) and when to write to it (explicit facts, preferences, decisions). Without this, tool use is inconsistent — model behavior follows the system prompt, not the tool's mere existence.
2. **A confidence distinction between connector writes and migration writes** — already supported by the `extraction_method` field in §2 (`mcp_live_write` vs. `account_export_parse`). Connector writes are structured and explicit at the source (a tool call with typed arguments), so they generally warrant *higher* default confidence than anything pulled out of a raw export.

This layer is largely independent of the True Migration pipeline — it doesn't need the ChatGPT export parser or the extraction/normalization layer at all, since the object arrives already typed. That has a real sequencing consequence — see §10.

---

## 4. Provider Coverage: Frontier + Local

Be honest with yourself about what's actually available per provider — this table is the real engineering plan, not the pitch deck version.

This table covers **Migration-mode acquisition** — the one-time or periodic export-and-compile path. For live, ongoing sync, see the connector table in §3.1; that path has no ToS exposure because nothing is extracted from outside an official integration point.

| Provider | Migration-mode acquisition path | Compile target | Notes |
|---|---|---|---|
| **Claude** | Anthropic's own memory import/export feature (Settings → Privacy) | Native Claude Project (system prompt + project knowledge), via the same official import feature | Best surface by far — you're building *against an existing official format*, not reverse-engineering one |
| **ChatGPT** | Human-initiated: deep-link the user to Settings → Data Controls → Export, they click, they download. Your desktop agent then auto-detects the ZIP in Downloads and parses it. No automated clicking, no session reuse, no page reading — OpenAI's terms flatly prohibit automated/programmatic extraction | Best-effort: a downloadable Custom GPT configuration package (instructions + knowledge files) the user uploads themselves via GPT Builder, plus a Memory-entries text file the user pastes in — not an automated UI-driving script, since OpenAI's terms cover destination-side automation too | No export/import API exists. Highest-demand corridor (per earlier research), least official support — the most fragile leg, and explicitly one where "automate everything" is off the table. Live *writes* from ChatGPT happen through the connector in §3.1, not through this pipeline |
| **Gemini** | Google's Data Portability API is real and OAuth-scoped, built specifically for service-switching — but Gemini conversation/memory data isn't confirmed in its supported scopes. Fall back to a user-initiated Google Takeout archive | Best-effort text/instruction reconstruction only, no Workspace replay | Test the actual supported scopes before building against this API; don't assume Gemini coverage. Deprioritize until validated |
| **Local models (Ollama, LM Studio, vLLM, llama.cpp servers)** | You control the whole loop — no export/scrape needed | A lightweight local proxy that sits in front of the model's OpenAI-compatible endpoint, injects retrieved memory into the system prompt on the way in, and extracts new memory on the way out | **Still your cleanest wedge.** No ToS risk, no missing API, ships first — and it doubles as the reference implementation for the connector pattern in §3.1, since Ollama needs the same kind of bridge you're already building here |

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
                     (typed, versioned, scoped)      (audit, TTL, access)   (vector + graph hybrid)
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

---

## 6. Folding In the Table-Stakes Features (from the screenshots)

You're right that compression, observability, and the agentic graph have to be in scope — a product without them looks primitive next to Mem0/Zep today. The key move is treating them as **views over the same substrate**, not separate subsystems:

- **Memory Compression Engine** (Mem0's pitch) → a background job that walks the graph and collapses low-confidence or superseded nodes into condensed bundles per scope, exposed as a token-budget knob at retrieval time. It's "free" once the graph has `supersedes` and `confidence` fields — you're not building a second thing, you're running a job against the first thing.
- **Observability & Tracking** (Mem0's dashboard) → a straight read of the Event/Revision Log that's already in the architecture for provenance reasons. TTL, size, access-per-object, live activity feed — all of it falls out of fields you needed anyway for migration fidelity.
- **Agentic memory graph** (the Zep/Graphiti-style entity/fact/episode view in your fourth screenshot) → a filtered rendering of the Canonical Context Graph scoped to `origin_type: agent` or a given agent's project. Entities, Facts, Episodes are just three more `ContextObject` types with edges, not a parallel data model.

The point: none of this is a separate roadmap track. It's UI on top of the same graph you're already building for the compiler. If you build them as separate systems (which is what most funded competitors have done, bolting a dashboard onto a vector store), you'll end up maintaining two sources of truth.

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

1. **Connect** — for Migration mode: OAuth-style flow where the provider supports it (Claude's memory export/import), a deep-link-then-desktop-folder-watcher flow for ChatGPT (point the user to the export button, they click it once, everything after that — detecting, validating, parsing the ZIP — is automated), one-click for local models if a supported runtime (Ollama, LM Studio) is detected on the machine. For Live Sync mode: a connector setup flow per §3.1 (add-connector link for Claude, Developer Mode instructions for ChatGPT). The one thing that should never appear anywhere in this flow: automating a click on a provider's own site, or reading an authenticated page. That's the acquisition boundary, not a temporary MVP shortcut.
2. **Context Inspector** — review extracted objects before anything is compiled anywhere. Edit, merge, delete, adjust scope. This is the trust-building screen; skip it and you're just another "give us your ChatGPT history" product.
3. **Compile** — pick a destination, get a Migration Manifest + Continuity Score, get a real native artifact (a Claude Project link, a ChatGPT Custom GPT config, a local model's profile file) — not a zip of markdown.
4. **Dashboard** — the Mem0-style observability/compression view, but framed as "your memory," not "your API usage."

## 9. Developer Surface

- REST API + Python/JS SDKs for read/write against the canonical graph.
- A single hosted, remote MCP server exposing the graph as tools (`search_context`, `get_project_state`, `write_memory`, `list_open_loops`) — see §3.1 for the full per-provider connector picture. This is the same server every consumer connects to via the Connect flow in §8; developers just get raw API/SDK access to the identical tools instead of a connector UI.
- Webhooks on the Event Log for teams building their own agents that need to react to memory changes.

---

## 10. MVP Sequencing

1. **Local-model wedge first.** Canonical schema + local proxy for Ollama/LM Studio + hosted MCP server + compression/observability/graph views. No ToS risk, no missing-API risk, fully dogfoodable, and it's a sellable developer tool on its own. This also builds the exact hosted MCP server every later step reuses.
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
- **Platform risk cuts both ways.** Interoperability regulation could be a tailwind; a lab shipping native portability itself (which would be trivial for them) erases the wedge overnight. Worth monitoring, not worth blocking on.
- **Extraction cost at consumer scale.** LLM-assisted typed extraction on every export is real inference spend — model this before pricing the consumer tier.
- **Trust is the actual product, not a feature.** You're asking people to hand over their entire AI history to a third party. Anuma and Echo lead with encryption-at-rest messaging for a reason — the Context Inspector and a legible ownership story matter more here than in most infra products.
