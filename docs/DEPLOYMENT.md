# Deploying the MCP service

*What has to be true before a Claude Custom Connector can reach coletar.*

Claude's connector requests originate from Anthropic's cloud, even when you are using
Claude Desktop. Anthropic cannot reach your `localhost`, so M3.3 needs a publicly
reachable HTTPS endpoint. That is the only reason this document exists — everything
in M3.1 and M3.2 was deliberately built to be provable without it.

**Only the MCP server is deployed.** The local proxy is a daemon that sits in front of
a model on your own machine; deploying it would be meaningless.

## Why a container host rather than serverless

The server runs **stateful** streamable HTTP (`stateless_http=False`), holds a psycopg
connection pool, and — on the zero-infrastructure path — an in-process store that
cannot exist on functions at all. Running it on serverless means changing the
transport and re-verifying MCP session handling across invocations: real work whose
only payoff is the host. A long-running container runs the existing ASGI app as-is.

A consumer UI can live on Vercel later. The service should not.

## What is in the repository

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage, `uv sync --frozen` so a deploy installs exactly what the test suite ran against. Non-root, ~93MB. |
| `fly.toml` | Non-secret configuration only. Migrations as a release command, `/healthz` as the check. |
| `.dockerignore` | Keeps the build context small, and `.env` out of it. |

## Two guards that fail at boot rather than at 3am

**No API keys, no server.** `COLETAR_MCP_API_KEYS` is required; there is no flag to
disable auth. "Unauthenticated requests are rejected" must not degrade into
"everything is allowed" because a secret was not set.

**No public bind on the in-process store.** `COLETAR_STORE_BACKEND` defaults to
`memory`, which is right for a fresh clone and catastrophic for a deployment: a
reachable server whose entire graph evaporates on the next restart, silently, while
every request succeeds. Binding `0.0.0.0` without Postgres is refused.

Both are verified in the container, not just asserted:

```
$ docker run --rm coletar-mcp:test
AuthError: No API keys configured. This server does not run unauthenticated.
```

## Deploying

You run these — they need your credentials, and nothing in this repository should
ever hold them.

```bash
fly auth login
fly launch --no-deploy --name coletar-mcp   # decline its offer to rewrite fly.toml
```

### 1. A database

The requirement is narrower than "managed Postgres": the schema runs
`CREATE EXTENSION vector`, so the database must have pgvector **available *and*
installable by the user you connect as**. Those are different things, and the
difference is where an afternoon goes.

| Option | Verdict |
|---|---|
| **Supabase** | ✅ What this project uses. `CREATE EXTENSION vector` succeeds; `vector` 0.8.2, `pg_trgm` 1.6. |
| Neon | ✅ Also supports pgvector. A second account, but Postgres and nothing else. |
| Fly Managed Postgres (`fly mpg`) | ❌ **Tried and rejected.** pgvector 0.8.2 is present on the server, but `CREATE EXTENSION vector` fails with *"Must be superuser"*, and the exposed `fly-user` is only `schema_admin`. The CLI has `--enable-postgis-support` and no vector equivalent, so extensions are curated at creation and pgvector is not in the set. Also $38/month on the Basic plan. |
| Fly unmanaged Postgres (`fly postgres`) | ❌ Fly explicitly disclaims support for it. |

**Verify before building on it.** One query is cheaper than a failed release:

```bash
psql "$COLETAR_DEPLOY_DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql "$COLETAR_DEPLOY_DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

#### Creating it with the Supabase CLI

```bash
brew install supabase/tap/supabase
supabase login                       # yours to run — it is a credential
supabase orgs list
supabase projects create coletar \
  --org-id <org> --region us-east-1 \
  --db-password "$(openssl rand -base64 36 | tr -d '/+=' | cut -c1-32)"
```

`us-east-1` puts it beside Fly's `iad`.

#### Which connection string

This one matters, and the obvious choice is wrong.

| Endpoint | Use it? |
|---|---|
| Session pooler, port **5432** (`aws-0-<region>.pooler.supabase.com`) | ✅ |
| Direct (`db.<ref>.supabase.co:5432`) | ✅ if it resolves — new projects may not publish it |
| Transaction pooler, port **6543** | ❌ **breaks the server** |

psycopg3 auto-prepares a statement after a few executions, and transaction-mode
pooling cannot serve prepared statements. We also bring our own
`AsyncConnectionPool`, so a transaction pooler buys nothing and costs correctness.

Then:

```bash
fly secrets set COLETAR_DATABASE_URL="postgresql://postgres.<ref>:…@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```

### 2. Keys

One entry per connector. The `tenant_id` is the only thing deciding which graph that
key reaches, so generate a real secret rather than typing one:

```bash
python3 -c "import secrets; print('sk-live-' + secrets.token_urlsafe(32))"
```

```bash
fly secrets set COLETAR_MCP_API_KEYS='[
  {"id":"chris-claude","secret":"sk-live-…","tenant_id":"tenant_chris"}
]'
```

Secrets go through `fly secrets`, never into `fly.toml` and never into the image.

### 3. Deploy

```bash
fly deploy
```

`release_command = "coletar migrate"` runs migrations once, before the new version
takes traffic. Running them at boot instead would mean every instance racing every
other one for the same `ALTER TABLE`.

## Verifying, before trusting it with anything

```bash
# Open, and reports nothing about the graph.
curl https://coletar-mcp.fly.dev/healthz

# Rejected — with a WWW-Authenticate header, not a 500.
curl -i -X POST https://coletar-mcp.fly.dev/mcp

# Accepted.
curl -s -X POST https://coletar-mcp.fly.dev/mcp \
  -H "Authorization: Bearer sk-live-…" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The startup log states its own configuration, so a misconfigured deploy is visible
immediately rather than on first use:

```
coletar mcp -> 0.0.0.0:8788  backend=postgres  tenants=2
```

## Synthetic data only, until all of this is true

Do not put real personal history into a deployment before:

- [ ] Tenant isolation verified **against this deployment**, not only locally — two
      keys, two tenants, each blind to the other
- [ ] Auth confirmed to fail closed, including with a malformed and an absent token
- [ ] `COLETAR_DATABASE_URL` and `COLETAR_MCP_API_KEYS` set as secrets and absent from
      the image (`fly ssh console -C env` should not surprise you)
- [ ] Persistent Postgres confirmed — restart a machine and check the graph survived
- [ ] Logs inspected for raw memory content. Retrieval traces store a query *digest*
      and object ids by design (§11); confirm nothing else is leaking

## Rolling back

```bash
fly releases
fly deploy --image <previous-image-ref>
```

Migrations are forward-only and additive by design, so a rolled-back image runs
against the newer schema. `002_tenancy.sql` adds columns and constraints; it does not
drop or rewrite data, so an older image will fail on the missing tenant argument
rather than corrupt anything. If a migration ever needs to be destructive, that is a
conversation, not a script.

## Testing against a hosted database

The gated Postgres suite creates and drops a database **per test**, so that each
assertion about what a tenant can see is not polluted by the last one. A pooled
endpoint holds connections open, so `DROP DATABASE` fails with `ObjectInUse` — which
is a property of pooled access, not a defect.

So the split is deliberate:

- **`COLETAR_TEST_DATABASE_URL`** → a direct Postgres. The compose container is the
  default, and `docker compose up -d` is all the suite needs.
- **`COLETAR_DEPLOY_DATABASE_URL`** → the hosted database, used only as the source
  for `fly secrets set`.

A hosted database can still be verified functionally without the per-test isolation —
migrations, writes, hybrid search, tenant isolation by id and by event log,
cross-tenant `supersedes`, and repeat queries to prove prepared statements survive the
pooler. That is what was run against Supabase before deploying.

## Local equivalence

The same image runs against the local compose Postgres, which is how the artifacts in
this repository were verified:

```bash
docker compose up -d
docker build -t coletar-mcp:test .
NET=coletar_default
DSN=postgresql://coletar:coletar@coletar-postgres:5432/coletar
docker run --rm --network $NET -e COLETAR_DATABASE_URL=$DSN coletar-mcp:test coletar migrate
docker run -d --network $NET -p 8790:8788 \
  -e COLETAR_DATABASE_URL=$DSN \
  -e COLETAR_MCP_API_KEYS='[{"id":"me","secret":"sk-dev","tenant_id":"tenant_local"}]' \
  coletar-mcp:test
```
