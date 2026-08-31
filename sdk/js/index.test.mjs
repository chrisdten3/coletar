/**
 * M7 — the JavaScript SDK.
 *
 * Driven against a stub `fetch` rather than a live server, because what these need
 * to check is the client's own behaviour: which URL it builds, which errors it
 * raises, and — mostly — what it refuses to do. The Python suite drives the same
 * surface against the real API, and a parity test there compares the two.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { Coletar, ColetarError, NotFound, RateLimited, Unauthorized } from "./index.mjs";

const KEY = "sk-test";

function stub(handler) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init, calls.length);
  };
  return { calls, client: new Coletar("https://api.example", { apiKey: KEY, fetch: fetchImpl }) };
}

function json(body, { status = 200, headers = {} } = {}) {
  return {
    status,
    headers: { get: (name) => headers[name.toLowerCase()] ?? null },
    json: async () => body,
  };
}

// --- the guarantees --------------------------------------------------------------

test("exposes no hard delete", () => {
  const names = Object.getOwnPropertyNames(Coletar.prototype).filter((n) => n !== "constructor");
  assert.equal(names.filter((n) => /delete|purge|destroy/i.test(n)).length, 0);
  for (const required of ["remember", "search", "inspect", "history", "supersede", "retire", "compile"]) {
    assert.ok(names.includes(required), `missing ${required}`);
  }
});

test("every request goes to the configured host and nowhere else", async () => {
  // Behavioural, because a grep for "telemetry" trips on the comment saying there
  // is none. Drive the whole surface and check where each call actually went.
  const { calls, client } = stub(() => json({ results: [], object: {}, revisions: [] }));
  await client.search("x");
  await client.remember("x");
  await client.inspect("mem_1");
  await client.history("mem_1");
  await client.supersede("mem_1", "y");
  await client.retire("mem_1", { reason: "z" });
  await client.compile({});

  assert.equal(calls.length, 7);
  for (const { url } of calls) {
    assert.ok(url.startsWith("https://api.example/"), url);
  }

  // And structurally: one place a second destination could ever appear.
  const source = readFileSync(new URL("./index.mjs", import.meta.url), "utf8");
  assert.equal(source.match(/this\._fetch\(/g).length, 1);
});

test("has no dependencies", () => {
  const manifest = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));
  assert.equal(manifest.dependencies, undefined);
  assert.equal(manifest.peerDependencies, undefined);
});

test("refuses to construct without a key", () => {
  assert.throws(() => new Coletar("https://api.example", { apiKey: "" }), ColetarError);
});

test("no call can name a tenant", async () => {
  // Behavioural rather than a grep: drive every method and check that nothing the
  // client sends could select someone else's graph. The tenant comes from the key.
  const { calls, client } = stub(() => json({ results: [], object: {}, revisions: [] }));
  await client.search("x");
  await client.remember("x");
  await client.inspect("mem_1");
  await client.history("mem_1");
  await client.supersede("mem_1", "y");
  await client.retire("mem_1", { reason: "z" });
  await client.compile({});

  assert.equal(calls.length, 7);
  for (const { url, init } of calls) {
    assert.ok(!/tenant/i.test(url), url);
    const body = init.body ? JSON.parse(init.body) : {};
    assert.ok(!Object.keys(body).some((key) => /tenant/i.test(key)), init.body);
  }
});

// --- the surface -----------------------------------------------------------------

test("search posts the query and unwraps results", async () => {
  const { calls, client } = stub(() => json({ results: [{ id: "mem_1", content: "x" }] }));
  const hits = await client.search("money", { explain: true, topK: 3 });

  assert.deepEqual(hits, [{ id: "mem_1", content: "x" }]);
  assert.equal(calls[0].url, "https://api.example/v1/search");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    query: "money",
    project_id: null,
    top_k: 3,
    explain: true,
  });
  assert.equal(calls[0].init.headers.authorization, `Bearer ${KEY}`);
});

test("inspect and history escape the object id", async () => {
  const { calls, client } = stub(() => json({ object: {}, revisions: [] }));
  await client.inspect("mem/../../etc/passwd");
  assert.ok(!calls[0].url.includes("../"));
  assert.ok(calls[0].url.includes("mem%2F..%2F..%2Fetc%2Fpasswd"));
});

test("retire sends a reason and does not use the DELETE verb", async () => {
  const { calls, client } = stub(() => json({ retired: true, readable: true }));
  await client.retire("mem_1", { reason: "no longer true" });

  assert.equal(calls[0].init.method, "POST");
  assert.equal(JSON.parse(calls[0].init.body).reason, "no longer true");
});

test("supersede targets the object being corrected", async () => {
  const { calls, client } = stub(() => json({ supersedes: "mem_1" }));
  await client.supersede("mem_1", "Chris works at Globex");
  assert.equal(calls[0].url, "https://api.example/v1/objects/mem_1/supersede");
});

// --- errors ----------------------------------------------------------------------

test("a rate limit carries the wait", async () => {
  const { client } = stub(() =>
    json({ error: "rate limited" }, { status: 429, headers: { "retry-after": "7" } }),
  );
  await assert.rejects(client.search("x"), (error) => {
    assert.ok(error instanceof RateLimited);
    assert.equal(error.retryAfter, 7);
    return true;
  });
});

test("401 and 403 are Unauthorized, 404 is NotFound", async () => {
  for (const status of [401, 403]) {
    const { client } = stub(() => json({ error: "no" }, { status }));
    await assert.rejects(client.search("x"), Unauthorized);
  }
  const { client } = stub(() => json({ error: "not_found" }, { status: 404 }));
  await assert.rejects(client.inspect("mem_missing"), NotFound);
});

test("a blocked compile surfaces the review gate", async () => {
  const { client } = stub(() =>
    json({ error: "review_required", message: "3 of 4 not reviewed" }, { status: 409 }),
  );
  await assert.rejects(client.compile({ destination: "claude" }), (error) => {
    assert.equal(error.status, 409);
    assert.match(error.message, /not reviewed/);
    return true;
  });
});
