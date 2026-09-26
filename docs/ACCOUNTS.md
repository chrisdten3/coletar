# Accounts, identity and issued keys

Built on `product/v1-real-data`, 9 September 2026. This is what "user me" means
mechanically, and what adopting Clerk or Supabase Auth will and will not involve.

## The one decision everything else follows from

**coletar stores no credential a human chose.** There is no password column, no
password hash, no session secret — not deferred, absent. An account records *which*
identity provider vouches for a person and *what that provider calls them*:

```
account(id, tenant_id, email, display_name, identity_provider, external_id, …)
```

That is the whole of the coupling. It is deliberately not a stance about hashing
algorithms; it is what keeps the provider swappable. The alternative — password
columns now, ripped out when Clerk lands — would leave real user secrets in the
schema and in every backup taken before the switch, long after the code stopped
reading them.

The one credential coletar *does* issue is an API key, because a connector cannot
complete an interactive sign-in. Even that is stored as a SHA-256 hash plus an
eight-character prefix. The plaintext exists once, in the response to the call that
minted it, and is not recoverable afterwards.

## Why this is not in the `Store` protocol

`store/base.py` states the rule: *every method takes `tenant_id`, and none of them
defaults it*, because a default is how a background job silently falls into someone
else's graph. Looking an account up is inherently cross-tenant — you hold an email
or a token subject and you want to know *whose* graph to open.

So accounts live behind a separate `Directory` protocol. The Store stays about one
tenant's graph; the Directory is the application boundary that resolves which tenant
that is. Two implementations, in-process and Postgres, held to identical behaviour
by `tests/test_accounts.py` running against both.

## Adopting Clerk or Supabase Auth

The entire dependency is `coletar/accounts/identity.py`. A provider turns whatever
the browser presents into an `ExternalIdentity(provider, subject, email)`. That is
the contract.

1. Implement `IdentityProvider.verify` against the provider's JWKS. Both Clerk and
   Supabase issue standard JWTs, so this is a signature check and a claims read.
2. Register it in `build_identity_provider`.
3. Set `COLETAR_IDENTITY_PROVIDER`.

Nothing else moves:

- **No credential migrates**, because none is stored.
- **No account rows change.** They already carry `(identity_provider, external_id)`.
- **Accounts provisioned locally survive.** `link_identity` claims an existing
  account the first time its owner signs in through the real provider, so a
  workspace built on a laptop today is still theirs afterwards.

`local` trusts whoever can reach the port. That is a laptop assumption and it is
**refused outright** when `COLETAR_PUBLIC_URL` is set, so a hosted deployment cannot
end up on development auth by nobody changing a setting. An unrecognised provider
name is also refused rather than falling back — a typo must not be the thing that
opens a workspace.

### Identity matches on the subject, never the email

`account_for_identity` matches `(provider, subject)`. An email is a claim a provider
makes and can change; the subject is that provider's own stable handle. Matching the
mutable one is how account takeover by address reuse happens. A unique partial index
enforces the pair in Postgres.

## Issued keys replace `COLETAR_MCP_API_KEYS`

The static environment variable still works, and `DirectoryAuthenticator` checks it
*second*, so a deployment can move to issued keys without a flag day.

What changes is revocation. An env var can only be changed by a redeploy, so
"revoke this key" meant "redeploy the server". Issued keys are a row, nothing is
cached, and the next request is the one that fails. The per-request cost is one
indexed read on a hash column — cheaper than the retrieval the call is about to do.

A key fixes its **surface** at issuance, for the reason `Principal` already
documents: a connector that could name its own surface is not gated at all. Issuing
a key is therefore the moment someone decides which assistant that credential *is*.

## Commands

```sh
coletar account create you@example.com --name "Your Name" --surface claude
coletar account list
coletar account issue-key you@example.com --name dashboard --surface chatgpt --read-only
coletar account keys you@example.com
coletar account revoke-key key_…
```

The tenant id is derived from the email rather than random, so re-provisioning the
same person cannot silently strand their graph under a second tenant. Creating an
account for an address that already has one is refused, never upserted: quietly
returning the existing account would make `create` a way to acquire someone else's
graph by guessing their address.

## Running the tests against a real database

`tests/test_accounts.py` runs against both directories, but the Postgres half
**skips silently** when nothing is reachable on 5433 — which is how the `touch_key`
bug below survived a green suite. Bring one up before trusting a result:

```sh
docker compose up -d          # or, with no Docker, a local Postgres on 5433
uv run pytest tests/test_accounts.py
```

With a database: 800 passed, 3 skipped across the whole suite. Without: 732 passed,
67 skipped. A run that reports 67 skips has not tested any SQL.

### The bug that justifies the discipline

`touch_key` ran an `UPDATE` with no `RETURNING` through the row-fetching helper.
psycopg raises when asked for records a statement did not produce, so it failed on
Postgres and passed in memory. It is called on **every authenticated request**, so
the production failure mode was every connector call erroring while CI stayed green.

Fixed with a separate `_execute` for statements that return nothing, and guarded at
the call site: refusing a valid credential because a last-used stamp could not be
written would turn a bookkeeping problem into an outage.

## Clerk, as actually adopted — 2026-09-18

The three steps above were taken. `coletar/accounts/clerk.py` implements `verify`
against Clerk's JWKS; `build_identity_provider` dispatches on `clerk`; the hosted
deployment sets `COLETAR_IDENTITY_PROVIDER=clerk`. Nothing else in the account model
moved, which is what the seam was for.

Two things the adoption added that the plan above did not anticipate:

**The email-claim path needed a verification gate.** Claiming an existing account by
address is what lets a workspace built under `local` survive the move to Clerk. But
Clerk will hold an *unverified* address on a user, and without checking that claim
the path reads "type someone else's email into a sign-up form, receive their graph".
`resolve_account` now requires `email_verified` before claiming, and refuses outright
when the address is already linked to a different subject. See
`coletar/accounts/session.py`.

**Resolving a tenant per request was the bulk of the work, not the token check.**
`inspector/web.py` had a module-level `tenant()` returning
`COLETAR_DEFAULT_TENANT_ID`, called from about twenty routes — a correct answer on a
laptop and the wrong one for any deployment with two users. Every route now takes
`owner: Tenant`. Local development keeps the old behaviour, and cannot keep it in a
hosted deployment, because `local` is refused once `COLETAR_PUBLIC_URL` is set.

The scheduled capture job was single-tenant for the same reason and now runs across
every active account, under one shared time budget.

## Supabase Auth, as actually adopted — 2026-09-26

The hosted deployment now runs `COLETAR_IDENTITY_PROVIDER=supabase`.
`coletar/accounts/supabase.py` implements `verify`; `build_identity_provider`
dispatches on `supabase`; Clerk stays implemented and switchable with that one
value. Nothing in the account model moved — which is the second time the seam has
been asked to do its job, and the first time it was asked by a provider it was not
written against.

**Why the switch, given Clerk already worked.** The deploy database is Supabase. An
account's credential therefore already lived in the same Postgres as the account
row, and Clerk put a second identity system beside it. One database where `account`
joins `auth.users` is both simpler to operate and simpler to answer questions about
— see `public.app_users` below.

**The asymmetric-keys requirement is not optional.** Supabase's legacy arrangement
signs tokens with a shared `JWT_SECRET`, and a secret that verifies a token can also
mint one: holding it would make this server able to forge its own users' sessions.
`SupabaseIdentityProvider` pins `algorithms` to `["ES256", "RS256"]` and so refuses
HS256 outright rather than supporting both. A project must be on JWT signing keys
(dashboard → Authentication → JWT Keys). The property this preserves is the one the
Clerk section already claims: **coleta reads no auth provider's secret anywhere.**

Three checks have no Clerk counterpart:

  * **`aud` must be `authenticated`.** A service-role token is correctly signed and
    is not a person signing in.
  * **Anonymous sessions are refused.** Supabase will issue a valid token for a user
    who proved nothing. Such a token must never reach `resolve_account`, which would
    read it as a person and provision a workspace for it.
  * **The issuer is the project.** Clerk needed a separate `azp` allowlist because
    many sites share one Clerk instance; here a token from another project carries a
    different `iss` and is signed by a JWKS we never fetch, so pinning `iss` is the
    whole of that property. `issuer` and `jwks_url` are both derived from
    `COLETAR_SUPABASE_URL` so a partial environment edit cannot check one project's
    issuer against another's keys.

### `public.app_users`, and why it is a view

Migration 013 adds a view joining `account` to `auth.users`: address, tenant,
identity provider, `email_confirmed_at`, `last_sign_in_at`, and a live count of
issued keys. It is **not** a table, and there is still no password column anywhere
in coletar's schema. The password is in `auth.users.encrypted_password`, hashed by
GoTrue, which is Supabase's job; the view does not select it.

A table duplicating either half would be two rows that can disagree about whether
someone exists. Everything interesting about a user — is the address confirmed, when
did they last sign in, whose graph is theirs — is a question about a join, not about
a row somebody has to keep in step.

It is created with `security_invoker = true`, so the caller's own privileges apply
rather than the owner's: `anon` and `authenticated` have no policy on `account` and
so still see nothing through it. It is also conditional on the `auth` schema
existing, because the same migration runs against the local Postgres a laptop and
the test suite use.

### Adopting an existing graph

`create_account` now takes an optional `tenant_id`. Graphs predate accounts here: a
workspace built under `local`, or imported from an export before anyone signed in,
already has a tenant id that no email hashes to. Without this the only ways to give
such a graph an owner were to rewrite every row's `tenant_id` or to leave the
account pointing at an empty workspace.

Deriving stays the default, for the documented reason — it makes provisioning
idempotent, so re-running it cannot strand someone's graph under a second tenant.
Passing the argument is an explicit claim that the caller knows which graph it is
attaching, and the one-tenant-per-account uniqueness constraint still refuses to
attach one twice.

### Demo accounts

`scripts/provision_demo.py` creates the Supabase auth user, the account and the
graph in one pass, because the three have to agree: an auth user with no account
signs in to a 401, an account with no graph opens an empty workspace, and a graph
with no account is unreachable. It is the only place in this repository that reads a
service role key, from the environment, on the machine of whoever runs it. Generated
passwords are written to a gitignored file rather than printed, because a password
echoed to a terminal is a password in scrollback and in any recording of the demo.

`scripts/copy_tenant.py` moves one tenant's rows between two databases preserving
ids, timestamps, embeddings and the event chain — re-creating objects through the
write path would stamp today's date on facts recorded months ago and replace the
real event log with a synthetic one, leaving the Context Inspector explaining a
history that did not happen.

## Not built

A key-management UI. Settings' API-key panel remains the labelled simulation it has
always been — it does not yet show issued keys. Billing is not built, and
registration is invite-gated (`COLETAR_INVITE_ALLOWLIST`) rather than public.
