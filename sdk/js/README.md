# @coletar/sdk

Thin async client for the coletar API. No dependencies, no telemetry, no hard delete.

```js
import { Coletar } from "@coletar/sdk";

const client = new Coletar("https://coletar.example", { apiKey: process.env.COLETAR_API_KEY });

await client.remember("I prefer fixed-point integers for money");
const hits = await client.search("how should I represent money", { explain: true });
```

## Server-side only

M7 withholds CORS headers from these routes, so a browser will refuse to hand the
responses to a page. That is the intended boundary rather than an obstacle: **an API
key in browser JavaScript is a key you have published** — visible in devtools, in the
bundle, and to every script on the page.

The browser extension talks to the three bridge endpoints instead, which are
CORS-allowlisted precisely because they cannot enumerate a graph.

## The surface

| | |
|---|---|
| `search(query, {projectId, topK, explain})` | hybrid retrieval; `explain` returns the component scores and versions behind each hit |
| `remember(content, {kind, projectId})` | write through the ingest boundary — a restatement corroborates rather than duplicating |
| `inspect(objectId)` | one object as the graph holds it, provenance included |
| `history(objectId)` | what it used to say, and when it changed |
| `supersede(objectId, content, …)` | correct a fact by writing its replacement |
| `retire(objectId, {reason})` | exclude from retrieval and compile; stays readable |
| `compile({destination, projectId})` | into a destination's native containers |

**There is no `delete`.** Not an omission — there is no endpoint under one. `retire`
is the closest thing and deliberately is not one: the graph never hard-deletes, so a
user can always see what a fact used to say and when it changed, which is exactly
what `history` reads. A convenience method that removed a row would turn that
guarantee into a convention.

**There is no `tenant` parameter.** It comes from the key, server-side, which is what
stops a client naming someone else's graph.

## Errors

`ColetarError` is the base. `Unauthorized` (401/403), `NotFound` (404 — or another
tenant's, or local to another surface; deliberately indistinguishable), and
`RateLimited` carrying `retryAfter` in seconds.

A `compile` blocked by the review gate raises `ColetarError` with `status === 409`:
nothing leaves for another product until a human has seen it.

## Tests

```bash
node --test
```
