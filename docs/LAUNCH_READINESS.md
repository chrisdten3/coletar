# Launch readiness

This is the release gate for a small, invite-only beta. `PRODUCT_LAUNCH.md` sets the
first audience: ten friends using real workflows before business outreach. A public
or team launch has additional gates below. A passing unit test is not evidence that
a provider's current website or desktop client works.

## 1. Make personal Live Sync dependable

Ship this before adding another surface or sharing graph access.

- **Capture and retrieval:** On both supported web pages, a trusted Send adds
  relevant context once, sends the prompt once, and queues the original user turn
  once. A slow or failed upload cannot delay retrieval. A failed lookup sends the
  original prompt with a visible status. Changes to the draft, route, focus, or
  consent cancel the interception without overwriting the draft. Check this in a
  real browser after each provider UI change; the synthetic adapter tests cannot
  validate DOM selectors.
- **Propagation:** Write a unique explicit memory on Claude web, ChatGPT web, and
  the local proxy in turn. In a new conversation on each *other* surface, retrieve
  that memory, verify its exact content and provenance, and inspect the retrieval
  trace. Include a restricted memory and prove that an ineligible surface never
  receives it. Run against the persistent hosted store, not a separate in-process
  store per command.
- **Desktop:** Verify Claude Desktop over stdio and remote MCP separately. Check
  the current ChatGPT Desktop connector capability before claiming support there;
  `docs/ROADMAP.md` records no verified path. A native desktop app for the proxy is
  distribution work, while the CLI proxy already serves local OpenAI-compatible
  models.
- **Failures:** Test expired/revoked keys, a stopped server, slow retrieval, a
  changed composer, and an unavailable extraction provider. Captured turns must
  stay queued or show a clear failure. An unsure gate must not silently discard a
  turn, and an unsure reconcile decision must not retire a claim.

Evidence to record for every row: client/version, date, source write ID, destination
retrieval trace ID, whether the context reached the model, elapsed time, and the
actual failure/status when it did not. A connector configured in Settings is not a
verified connection.

### Automated evidence and remaining live checks (2026-10-06 UTC)

- On top of `fa75f99`, trusted composer edits now invalidate an interception,
  including edits made during rich-text insertion/reconciliation and edits reverted
  to the original text during lookup. Failed insertion must not restore an older
  draft over a newer user edit. Starting another draft also cancels pending reply
  attribution conservatively. The original turn may already be queued; cancellation
  does not retract a captured turn or mutate the graph.
- `node --test tests/extension/*.test.cjs`: **33 passed**, including paired ChatGPT
  and Claude synthetic tests for these races, and synchronous trusted input emitted
  by the extension's own rich-text insertion. These exercise the content script;
  they do **not** verify current provider DOM selectors or delivery to a model.
- Hosted propagation, restricted-memory exclusion, deployed auth/logout, and
  desktop/local-model delivery were unverified in the cloud checkout, where no
  Coletar or Supabase credentials/environment were configured. The local checkout
  has the ignored environment files; hosted checks below supersede that access
  limitation. No Docker/pgvector suite was run. Record actual write and trace IDs
  before marking any provider client supported.

### Hosted test-account evidence (2026-10-05)

- The test account signed in through Supabase Auth. `/web-api/state` returned 401
  without a token and with a bad token, and 200 with the test account's token. Its
  response tenant matched the separately configured test tenant.
- Supabase logout returned 204 and rejected reuse of that session's refresh token.
  The already-issued access JWT continued to receive 200 until expiry. The browser
  clears its local session and reloads on sign-out; the new client regression test
  ensures a refresh finishing late cannot silently sign the user back in.
- Three short-lived, read-only keys bound to the test tenant were issued for Claude,
  ChatGPT, and local policies. Each received 200 from hosted `/v1/search` and saw
  the same pre-existing boxing memory. All were revoked; a further request with a
  revoked key received 401. This proves the hosted REST/auth/store path, not that
  the provider clients themselves called it. No test memory was added.
- Before deployment, the hosted service returned the unrelated sentiment memory
  alongside the boxing result on all three keys. The branch filters hash-only matches in
  browser REST (both styles), MCP search, and the local proxy. Tests cover all
  three paths. A read-only Supabase check of the branch with the test tenant's
  actual memories returned three unfiltered hits (lexical coverage 1.0, 0.0,
  0.0) and one guarded hit (1.0) for the boxing query. The deployed repeat check
  is recorded below.
- Before deployment, the test account's `/web-api/state` response was 6.24 MiB for 3,836
  objects and 2,000 events, taking about 4.2 seconds over HTTP. The inspector
  change defers event snapshots until an object is opened and
  reads review timestamps through the Store protocol. A read-only check against
  the configured Supabase data returned the same 3,836 objects and review counts
  in a 2.88 MiB snapshot, taking 2.78 seconds cold and 2.23 seconds warm. This
  was source-level integration evidence. The 2,000-event scan used for usage totals
  remains a cost to profile if the hosted page is still slow.
- An opt-in hosted smoke test now exercises the configured Supabase test tenant and
  production REST API (`tests/test_hosted_live_sync_smoke.py`). On 6 October 2026
  UTC, a Claude-bound key wrote `mem_697f8430b7ab481b`. Claude, ChatGPT, and local
  keys all retrieved that ID and its exact content in the returned prompt block.
  Their trace IDs and server retrieval times were:

  | Policy | Trace ID | Server retrieval |
  | --- | --- | --- |
  | Claude | `evt_26c7c4f273df45e3` | 25.721 ms |
  | ChatGPT | `evt_05a7fc28baec4ece` | 24.361 ms |
  | Local | `evt_bb03c35b97b34673` | 22.997 ms |

  A Claude-only object, `mem_3be163d1a4244858`, appeared under the Claude policy
  and was absent under ChatGPT and local policies. Both test objects were retired
  with creation and retirement events; temporary keys were revoked. This checks
  hosted HTTP, auth, storage, provenance, and prompt-block assembly. It does not
  show that any provider client sent the block to a model. This run preceded the
  precision deployment and included unrelated hits.

### Production deployment and repeat checks (2026-10-06 UTC)

- PR #64 merged as `ca50aed9`; GitHub and Vercel report a successful Production
  deployment of that exact commit. The canonical `/healthz` returned 200. No
  migration files changed in the PR.
- The hosted smoke passed again after deployment: Claude-bound write
  `mem_aefca0439e164963` was returned with its exact content by the Claude,
  ChatGPT, and local policy keys. Their trace IDs were respectively
  `evt_1ab7c7eb5d60453a`, `evt_3d078fd93f984a04`, and
  `evt_aeb858cc7e6a4696`. Claude-only `mem_b48051925c0e458b` appeared only
  under the Claude policy. The test retired both objects and revoked its keys.
- Production HTTP `/v1/search` for `i like to watch boxing` returned only
  `mem_ab7b818d505fff1f` in both `terse` and `full` styles, with no unrelated
  sentiment in the prompt block. Trace IDs were `evt_41fd441369674508`
  (47.688 ms server retrieval) and `evt_d60088b9d250485e` (38.281 ms).
  A temporary read-only ChatGPT key used for this check was revoked.
- Authenticated production `/web-api/state` returned 3,840 objects and no bulk
  events in 3,025,210 bytes and 3.82 seconds over HTTP. Event history remains
  available per object. This improves payload size; page latency still deserves
  profiling before calling the inspector fast.
- The owner supplied screenshots of fresh, visible ChatGPT and Claude web chats.
  Each showed one submitted user message containing only the boxing memory above
  the original prompt, plus the extension's "1 memories added" status. ChatGPT
  showed a reply; Claude was still generating in its screenshot. These observations
  verify current-page Send and visible injection for this one prompt, not every
  editor state or future provider layout.
- The corresponding production traces were `evt_a0fd1a25a3344cc3` at 01:49:36 UTC
  (`chatgpt.com`) and `evt_5a945e2c972348b0` at 01:50:47 UTC (`claude.ai`). Each
  returned only `mem_ab7b818d505fff1f`. Coletar also recorded one encrypted user
  turn for each Send: `evt_af330dee61c1476f` for ChatGPT and
  `evt_44d80141c3f448fb` for Claude. Assistant-turn capture was not observed in
  this check; the screenshots and traces alone do not prove it completed.
- At 02:00 UTC the owner left a second Claude reply to finish. The page showed
  "reply queued for capture", and the event log contains the paired encrypted
  user and assistant capture events `evt_4542b71206bf4809` and
  `evt_5033015efe3545b7` under the same turn ID. This closes the completed-reply
  capture check for that one Claude turn; ChatGPT reply capture remains unverified.
- That second prompt, `i like steak`, also exposed a relevance failure: production
  trace `evt_8a20b8f5b2504f8f` returned the boxing memory because `like` was
  shared. The reply mentioned a boxing card. PR #67 (`0a3d169`) added a topic-word
  guard across REST, MCP, and local proxy; all CI checks passed and Vercel reported
  a successful Production deployment of the merge commit. On 2026-10-07 UTC,
  production HTTP checks using short-lived, read-only keys for both Claude and
  ChatGPT returned only the stored steak preference for `i like steak` and only
  the boxing preference for `i like to watch boxing`, in both `full` and `terse`
  styles. The keys were revoked. The steak preference had been captured after
  the original failure, so the correct post-deployment result is steak rather
  than an empty context block. A fresh visible-page Send check is still needed.
- **Still open:** test a failed lookup, changed draft, stopped stream, and completed
  ChatGPT assistant capture on the live page. Repeat after provider UI changes. Native
  desktop and local-model delivery need their own client-level evidence.

## 2. Finish account access before broader invites

Supabase session sign-in, sign-up, refresh, sign-out, verified-email linking, and
per-account tenant resolution are implemented. The older unchecked items in
`docs/TODO.md` and older sections of `docs/WEB_APP.md` predate that work. The beta
gate is an end-to-end check in the deployed app, not a second auth implementation:

1. New invited user signs up, confirms email, signs in, reloads, refreshes an
   expired access token, and signs out. Back and reload after sign-out show no
   private workspace. Account recovery and invalid-link states are understandable.
2. Two users create separate memories and cannot read, search, edit, export, or
   compile each other's graph. A connector key cannot become a web session.
3. Each user can issue a surface-bound key, copy it once, inspect its name and last
   use, revoke it, and see the next connector request fail. The UI must stop
   describing demo keys as real credentials.
4. Onboarding explains the extension's separate capture and automatic-mode
   consents, tests the endpoint/key, and shows the last successful read and write
   rather than a stored "connected" preference.

## 3. Build teams as an explicit graph-access feature

Current ownership is one account to one tenant. Do not turn a personal tenant into
a team tenant or let a member's personal key read every team object. A first team
release needs a separate team tenant, membership directory, and explicit routing of
reads and writes at the authenticated boundary.

| Role | Team context | Membership and keys |
| --- | --- | --- |
| Owner | Read, write, review, export | Invite/remove members, appoint admins, close team |
| Admin | Read, write, review, export | Invite/remove members and manage team keys |
| Member | Read and write | Manage only their own credentials |

Invitation acceptance must bind a verified identity to the invited address or a
single-use invitation, with expiry and revocation. Removing a member must revoke
their team credentials and deny their next read, including MCP, REST, SDK, and web
routes. Personal context remains separate unless its owner explicitly shares an
object into the team; that share and any retirement append events with provenance.
Retrieval should say whether a result came from personal or team context and obey
both locality and team membership. The same adversarial access suite must run on
in-process and Postgres stores, with Postgres row protection verified separately.

Before implementation, settle the product rule for whether members can publish
directly to team context or require admin review, and whether admins may export all
team history. Those choices determine the permission model and UI wording.

## 4. Release operations and trust

- Restore a production backup into staging and run the cross-surface smoke test
  there. Apply migrations before serving code that needs them; migration 014 has
  already shown the failure mode (`docs/DEPLOYMENT.md`).
- Add uptime and queue-health monitoring, error tracking, a support contact, and a
  rollback procedure. Test the capture worker with its configured provider credits
  and a deliberately failed provider call.
- Publish accurate privacy, terms, subprocessor, retention, export, and erasure
  documentation before inviting people to store private context. Name TypeSafe's
  gate and reconcile flows separately, as `docs/DEPLOYMENT.md` does.
- Perform accessibility and keyboard checks on sign-in, onboarding, Library,
  review, and team management. Add tooltips only for controls whose meaning remains
  unclear after the main label and explanatory copy are fixed.
- Package and review the extension permissions and consent copy. Document the
  supported browser and provider versions. Create a Mintlify documentation site
  from the verified setup, security, and troubleshooting flows, then link it from
  the product. Do not publish a surface guide as working until its live check passes.

## Release decisions

**Invite-only personal beta:** gates 1, 2, and 4 must pass on the actual hosted
environment. Use the first ten friends from `PRODUCT_LAUNCH.md` to find workflow
failures; keep a dated issue log and fix blockers before business outreach.

**Team beta:** the personal beta gates plus gate 3 and its access tests must pass.
Team sharing is a separate release because it changes who may read stored context.

**Public launch:** complete the team beta gates if teams are advertised, plus
extension distribution, self-service billing/support, tested restore and incident
response, and legal review. Marketing claims must match the verified surface matrix.
