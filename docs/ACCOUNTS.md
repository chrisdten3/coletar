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

## Not built

Sign-up and sign-in flows, sessions, and a key-management UI. The web app still
resolves its tenant from `COLETAR_DEFAULT_TENANT_ID`; wiring it to the directory is
the next step and needs the identity provider chosen first. Settings' API-key panel
remains the labelled simulation it has always been — it does not yet show issued
keys.
