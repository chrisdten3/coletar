# Hosted coletar

Production: <https://coletar-five.vercel.app/app>. Vercel project `coletar` in
`christopher-tengeys-projects`; Supabase project `dyfagpnjijccwirimmhd` in us-east-1.
This is a single-owner deployment, not a public multi-user launch. The former
container-host instructions are preserved in [DEPLOYMENT_FLY.md](DEPLOYMENT_FLY.md).

## Access and configuration

The workspace uses a generated HTTP Basic password. Full account/session auth is
deferred. Private access instructions are in `data/hosted-access.md`; deployment
configuration is in `.env.vercel`. Both are Git-ignored, upload-excluded, mode 0600.
Do not share either file. Production and preview environment variables are set in
Vercel; secrets are stored as sensitive variables and never embedded in JavaScript.

The local Inspector still binds loopback. The hosted entrypoint is `app.py`, backed
by `coletar.hosted.create_app`. It exposes only the new web router, not the old
unauthenticated Inspector forms. Owner passwords, connector bearer keys and the cron
key have separate authorities. Owner writes enforce same-origin browser requests.

The startup refuses an in-memory store or an inadequately configured owner password.
Supabase is the canonical Postgres store. Its eight migrations were checked against
the ledger; pending migrations 004–007 and new migration 008 were applied on 2026-09-08. The project had
been paused and was resumed before validation. Managed backups and a tested restore
procedure remain follow-up work.

Migration 008 closes a confirmed Supabase public-role exposure: all backend tables,
including content keys, now have RLS enabled with no browser-client policies. Tests
under the actual `anon` and `authenticated` roles could not read graph, history or
key rows; the privileged backend connection retained access. New direct Supabase
client access requires explicit tenant policies, not disabling RLS.

## Surfaces

- MCP: `https://coletar-five.vercel.app/mcp`. The hosted transport is stateless JSON
  Streamable HTTP, so requests can reach different Vercel instances. The existing
  stateful local/container transport is unchanged.
- REST and browser-extension server: `https://coletar-five.vercel.app`. Routes are
  under `/v1`; use `Authorization: Bearer <surface key>`.
- Claude web, Desktop and Code use the Claude key and backend locality policy.
  Configure the provider's supported remote MCP client manually. For a web account
  without compatible bearer-key connectors, use the existing composer extension.
- ChatGPT uses its own surface key. Developer-mode remote MCP support depends on
  the account/client's authentication support; the composer extension is the REST
  alternative. OAuth connector onboarding is not implemented.
- Local clients set `COLETAR_MCP_URL` to the MCP URL and `COLETAR_MCP_API_KEY` to the
  local key, then run `uv run coletar serve-proxy` alongside their local model server.
- The extension is loaded unpacked from `extension/`. Set its server URL and key in
  Options. Capture stays off in the extension until the user explicitly enables it.
  Never automate a provider's UI or copy its session credentials to the server.

Keys are deployment configuration, not user-issued database credentials. Rotate them
by replacing the appropriate entry in `COLETAR_MCP_API_KEYS` and redeploying. The
hosted Settings screen points to real setup instructions rather than demo key rows.
The server can be verified independently of a provider; a ready endpoint is not
evidence that a particular provider account has installed its connector.

## Capture and extraction

The owner explicitly opted into OpenAI extraction and transferring the existing
OpenAI API key to Vercel's sensitive environment. OpenAI is the extraction
subprocessor. Only a captured candidate turn is sent; the stored graph, other
conversations, provider sessions and accepted memories are not sent. The existing
grounding and schema guards still apply. `store=False` is used by the adapter.

`COLETAR_CAPTURE_TURNS=true`, `COLETAR_LIVE_EXTRACTION_MODE=collect_then_batch`,
`COLETAR_EXTRACTION_PROVIDER=openai`, and a five-turn batch limit are configured.
The model is `gpt-5.6-terra`. Captured source episodes are encrypted with per-object
keys in Supabase; derived objects carry their source IDs and extraction events.

The initial live extraction check was blocked by OpenAI `credit_balance_exhausted`
(HTTP 429). The API key and model are valid, but the API account needs credits.
Capture worked and the turn remained pending. The synthetic source was then
crypto-shredded. Successful model extraction is not claimed until retested after funding.

Vercel invokes `/api/jobs/capture` daily at 03:00 UTC (Hobby scheduling may run within
the scheduled hour). It requires `CRON_SECRET`. The Capture queue also offers a
password-protected, same-origin **Process pending turns with OpenAI** action. Each
pass uses the existing tenant lease, processes at most five turns, expires aged
sources through crypto-shredding, and has a 240-second execution bound. A backlog
larger than five turns needs additional manual runs or a more frequent worker.

Uploaded exports currently use deterministic pattern extraction on the hosted server,
without model calls. The hosted limit is 4 MB per upload to fit Vercel's 4.5 MB body
limit; local imports retain the 20 MB UI limit. Large background imports remain work.

## Deployment and operation

Use Vercel CLI 59.11.7 or later from the linked repository. `vercel deploy --prod`
builds the current checkout, including uncommitted changes. A first deployment may
automatically become production. The branch remains `codex/web-app-foundation` until
the changes are reviewed and committed. The linked GitHub integration does not mean
these local changes have been pushed.

Python is pinned to 3.13. Vercel installs dependencies from `uv.lock`; the entrypoint
resolves the repository's `src` directory explicitly because the editable install
link is not retained in the deployed runtime. `.vercelignore` excludes secrets,
exports, local stores, test data and dependency caches.

`/healthz` is public liveness only. `/web-api/connections` requires the workspace
password and tests a real Store read. MCP hostnames use an explicit allowlist; add
any new domain to `COLETAR_MCP_ALLOWED_HOSTS` before using it for MCP. The production
alias is configured; arbitrary preview hostnames are not automatically trusted.

The Postgres pool disables prepared statements for pooler compatibility, limits each
instance to four connections and serializes initialization. Rate limits remain per
instance; distributed quotas and account login abuse controls are not built.
Preview and production currently point to the same database; isolated staging is
still needed before testing real user data in previews.

Migrate returns ZIP downloads. The legacy `/v1/compile` server-file response is
disabled on this host because function files are ephemeral. Owner exports include
restricted objects; provider compilers enforce destination reach and the review gate.
Audit downloads are unsigned. No billing, account signup or third-party session
automation is enabled.

## Verification

Real HTTPS checks exercised all three surface keys through MCP writes and reads,
REST locality filtering, CORS, invalid-key rejection, workspace isolation, review,
four ZIP download formats and historical audit. Clearly synthetic check objects
were retired afterwards; their event history remains. The initial cold-start import
failure was fixed before these checks passed. Browser visual verification is
separately limited by the browser client's access error; API checks are not a claim
of provider-account installation or browser UI verification.

The protected scheduler was invoked successfully with an empty queue; anonymous
calls were rejected. Automatic daily execution itself has not yet been observed.

See the task report for the latest extraction and regression results. Relevant
platform references: [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi),
[function limits](https://vercel.com/docs/functions/limitations), and
[cron scheduling](https://vercel.com/docs/cron-jobs/manage-cron-jobs).
