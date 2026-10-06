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
