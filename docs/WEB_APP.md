# Web app prototype

Built on `codex/web-app-foundation`, 8 September 2026. The three supplied Product
Design PDFs are design references, not instructions or evidence of shipped services.

The single-owner hosted version is now deployed on Vercel with Supabase Postgres.
See [DEPLOYMENT.md](DEPLOYMENT.md) for access, real MCP/REST endpoints, OpenAI opt-in,
worker scheduling and hosting limits. Local-mode simulations described below remain
available when running the original Inspector; the hosted setup screens differ.

## Run it

```sh
uv run coletar serve-inspector
```

Open [the product app](http://127.0.0.1:8789/app). The original developer Inspector
remains at `/`. To explore synthetic design examples without touching your graph:

```sh
COLETAR_STORE_BACKEND=memory \
COLETAR_STORE_PATH=/tmp/coletar-product-prototype.json \
COLETAR_DEFAULT_TENANT_ID=design-prototype \
COLETAR_INSPECTOR_PORT=8790 \
uv run coletar serve-inspector
```

Open [the isolated prototype](http://127.0.0.1:8790/app), then use **Load design
examples** in an empty Library. Seeding refuses a nonempty workspace. Sample objects
and their provenance identify the design fixture. Changes persist through the same
Store protocol as the rest of coletar. Browser setup preferences are separate from
the graph and cannot issue credentials or establish connections.

## Screens and working interactions

| Screen | Implemented |
|---|---|
| Library | Live objects, text search, kind/project/restriction/review filters, provider-reach preview, add memory |
| Object | Source provenance, validity dates, chronological events, edit, save reach, review, retire |
| Capture queue | Decrypted owner view of encrypted submitted turns, pending state, source inspection |
| Review | New, low-confidence, supersession and graph-backed conflict groups; review individually or by group; resolve conflict by retaining either side, or review both |
| Audit | Record-time and valid-time queries over the existing replay engine, comparison with today's graph, retrieval trace list, JSON snapshot download |
| Migrate | ChatGPT, Claude and local compiler previews and ZIP downloads; actual continuity score, manifest, withheld records and installation instructions; Markdown owner export |
| Surfaces / onboarding | Provider-specific setup simulation, consent acknowledgement, saved setup preferences; real ZIP/JSON export upload, local extraction and import results |
| Settings | Recorded token totals, user-priced cost calculator, demo key create/revoke, project reach defaults for manual additions in this browser |
| Marketing | Home, proposed pricing and security/boundaries pages, linked to the product app |

The app follows the PDF's cream canvas, compact sidebar, serif headings, monospace
metadata, green selected states, amber restrictions, review comparisons, two-column
audit and settings layouts, and score/manifest migration layout. It also supplies
empty, loading and error states, native dialogs, keyboard focus treatments and
responsive layouts.

### Surface marks

Every place a surface, model or destination is named carries a mark: the library
card's `via claude`, the reach preview tabs, the object's reach toggles, the migrate
destination cards, the audit read log, and the settings usage bar, legend and model
rows. The marks are geometric rather than reproductions of anyone's logo — they
identify a destination inside this UI and claim no trademark. Each has a colour
(`--s-claude`, `--s-chatgpt`, `--s-claude_code`, `--s-local`, `--s-markdown`), and
the usage bar, its legend and the marks all use the same one, so the same surface
reads as the same thing across screens.

### The design fixture populates the dashboards

**Load design examples** seeds the PDF's objects *and* the state those screens are
about, because a seed that pre-reviews everything leaves Review empty and a seed
with no reads leaves both the Audit read log and Settings' usage reading as though
nothing ever happened.

It leaves the supersession, both sides of the conflict and the low-confidence import
unreviewed — so Review opens on the comparisons the reference shows, and Migrate
opens locked. Reviewing them unlocks it. It also writes five ordinary retrieval
traces, one of them a ChatGPT read that returned nothing because reach withheld it.
Those traces are the *only* source of the usage figures and the read log: both
screens read the real event log, not a parallel fixture. Every seeded object and
event carries `design_sample`, and the seeded traces carry query text because the
fixture is synthetic — `record_query_text` remains an explicit per-call opt-in
everywhere else (§11), and the read log falls back to showing a digest.

Migrate previews itself once the gate opens, so the continuity score is computed
from the real compiler rather than waiting for a click. A failed preview is not
retried on every render; the explicit button stays.

`/app` is served with a digest of `product.css` and `product.js` in their URLs, so a
deploy that changes the client without changing its URL cannot be answered from a
browser cache. The digest is computed once per process; a local dev server must be
restarted to pick up asset edits.

## What is real, and what is simulated

Graph writes use existing provenance and event-producing operations. No alternate
memory database was introduced. Reach changes invalidate earlier review; retirements
preserve history. The compile gate is enforced by the API, not merely by disabled
buttons. Captured AES-GCM episodes are source evidence, so they are excluded from
compiler eligibility; their derived memories remain eligible. Both owner export and
provider package generation require review.

Import accepts conversation ZIPs and `conversations.json`, including ChatGPT shards.
It uses the existing provider parsers, local pattern extraction and deduplicating
ingest. It never calls a third-party model, regardless of the global model setting.
Only user turns are considered. Uploads are bounded to 20 MB and ZIP expanded sizes
to 40 MB. This deliberately conservative path is not model-assisted extraction.
Unreviewed memories are blocked from compilation; the existing live retrieval policy
is not changed into a new approval gate.

Setup flows, API-key entries and proposed pricing are explicitly labelled simulations.
No account/session authentication, credential issuance, billing, deployment or actual
connector installation occurs. The app does not verify whether a provider is connected.
API key rows are demo metadata; no real bearer secret is generated. Browser rules
apply only to new manual additions in that browser, not imports or other surfaces.

Claude web, Desktop and Code share the backend's `claude` provider policy. The UI does
not pretend they can have independent reach. Markdown exports are owner exports,
including restricted content; they do not receive a compiler continuity score.
Audit downloads are unsigned JSON, not the PDF's proposed signed report. Usage reads
the latest 2,000 events, not a billing ledger. The app loads at most 10,000 objects;
pagination and scale optimisation are future work. Historical snapshots exclude raw
episodes. Dates are displayed in UTC; event times use the browser's timezone.

The Inspector server remains loopback-only. A separate password-gated hosted
entrypoint serves Vercel. New POST endpoints reject cross-origin browser requests.

## The hosted workspace's contents

As of 8 September 2026 the deployed Supabase graph holds, besides the design
examples: four memories from an early ChatGPT/Claude import, one erased capture
episode, and three `Synthetic deployment check` memories written by connector smoke
tests. All eight were retired before the examples were loaded, so they do not appear
on any screen; they are still in the log, and their events are still replayable.
Nothing here hard-deletes them.

The `mcp` row in Settings' usage is real: 162 tokens served through the MCP door
during those smoke tests, recorded as ordinary retrieval traces.

## Repository assessment and next work

At the starting commit `91fd36e`, the graph, both storage backends, retrieval, three
compilers, importers, encrypted capture and leased worker were already built. Seven
migrations were checked in. TODO's opening 715-test/four-migration counts were
historical. The web work reuses those components through a dependency-free HTML/CSS/JS
client and a small FastAPI router; it does not introduce a second frontend framework.

Next: session auth and tenant provisioning; issued/revocable credentials; provider
connector status; background import jobs and opt-in model extraction; server-side
policy defaults; full source/diff history and signed audit reports; billing.
The extension popup itself remains a separate artifact rather than being embedded
as an app dashboard.

Validation covers tenant isolation, cross-origin refusal, provider import detection
and corroboration, review gating, locality-safe compiler downloads, retirement,
conflict resolution, historical queries, and captured-episode exclusion. Browser
checks exercise review-to-compile, audit, demo keys and desktop/mobile layouts.
Ruff, strict mypy and JavaScript syntax checks pass. CI includes the web JS parse
check. Local integrations without their services/settings are skipped; see the task
report for the exact verification totals and environment limitations.
