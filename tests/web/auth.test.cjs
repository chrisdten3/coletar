// Session handling in the workspace client, against the real auth.js.
//
// Loaded into a VM context with stubbed browser globals rather than mocked, for the
// same reason `tests/extension/*.test.cjs` do it: the interesting behaviour here is
// token lifetime arithmetic and what happens when a refresh fails, and a stub of
// `session` would assert nothing about either.
//
// What is worth pinning, and why each one is a real failure mode:
//
//   * **One refresh at a time.** GoTrue rotates the refresh token on every use, so
//     two API calls racing past expiry each spend it and whichever loses is signed
//     out holding a token that will never exchange again. This is the bug that
//     looks like "it randomly logs me out mid-demo".
//   * **A failed refresh clears the session.** Keeping it means every later call
//     fails identically and the person is never shown a sign-in form — the page
//     just stops working.
//   * **Sign-up without a session says so.** When email confirmation is on, GoTrue
//     returns a user and no session. Treating that as signed in renders a workspace
//     that does not exist yet.
//   * **Storage denied is not sign-in denied.** Private windows and blocked site
//     data make `localStorage` throw; that must degrade to a session that lasts
//     until the tab closes, not to an app that cannot boot.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const SOURCE = fs.readFileSync('src/coletar/inspector/static/auth.js', 'utf8');

const SUPABASE_CONFIG = {
  provider: 'supabase',
  required: true,
  supabaseUrl: 'https://ref.supabase.co',
  supabaseAnonKey: 'anon-key',
  openRegistration: true,
};

/**
 * Load auth.js with browser globals we control.
 *
 * `storage: false` makes every localStorage call throw, which is what a private
 * window does. `fetch` is a queue of responses so a test states exactly what the
 * server said rather than matching on URLs.
 */
function harness({ config = SUPABASE_CONFIG, rawConfig = null, stored = null, storage = true } = {}) {
  let now = 1_700_000_000_000;
  const calls = [];
  let responses = [];
  const store = new Map();
  if (stored) store.set('coleta.session', JSON.stringify(stored));

  const localStorage = {
    getItem(key) {
      if (!storage) throw new Error('denied');
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      if (!storage) throw new Error('denied');
      store.set(key, value);
    },
    removeItem(key) {
      if (!storage) throw new Error('denied');
      store.delete(key);
    },
  };

  const context = vm.createContext({
    window: {},
    document: {
      getElementById: (id) =>
        id === 'sign-in-config'
          ? { textContent: rawConfig !== null ? rawConfig : JSON.stringify(config) }
          : null,
      head: { appendChild() {} },
      createElement: () => ({ setAttribute() {} }),
    },
    localStorage,
    Date: class extends Date {
      static now() {
        return now;
      }
    },
    async fetch(url, init) {
      calls.push({ url, init });
      const next = responses.shift();
      if (!next) throw new Error(`no stubbed response for ${url}`);
      if (next.networkError) throw new Error('offline');
      return {
        ok: next.status < 400,
        status: next.status,
        text: async () => JSON.stringify(next.body ?? {}),
      };
    },
    console,
  });

  vm.runInContext(SOURCE, context);
  return {
    auth: context.window.coletaAuth,
    calls,
    reply: (...items) => {
      responses = items;
    },
    advance: (seconds) => {
      now += seconds * 1000;
    },
    saved: () => {
      const raw = store.get('coleta.session');
      return raw ? JSON.parse(raw) : null;
    },
  };
}

const session = (overrides = {}) => ({
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  expires_at: 1_700_000_000 + 3600,
  user: { id: 'u1', email: 'a@example.com', user_metadata: { full_name: 'A' } },
  ...overrides,
});

test('a live token is returned without touching the network', async () => {
  const h = harness({ stored: session() });
  assert.equal(await h.auth.session.token(), 'access-1');
  assert.equal(h.calls.length, 0);
});

test('a token near expiry is refreshed before it is handed out', async () => {
  const h = harness({ stored: session() });
  h.advance(3570); // 30s left, inside the 90s margin
  h.reply({ status: 200, body: { access_token: 'access-2', refresh_token: 'refresh-2', expires_in: 3600 } });

  assert.equal(await h.auth.session.token(), 'access-2');
  assert.equal(h.calls.length, 1);
  assert.match(h.calls[0].url, /grant_type=refresh_token/);
  assert.equal(h.saved().refresh_token, 'refresh-2');
});

test('concurrent calls past expiry spend the refresh token exactly once', async () => {
  // The demo-breaking race: GoTrue rotates the refresh token on use, so a second
  // exchange of the same one fails and signs the person out.
  const h = harness({ stored: session() });
  h.advance(3600);
  h.reply({ status: 200, body: { access_token: 'access-2', refresh_token: 'refresh-2', expires_in: 3600 } });

  const tokens = await Promise.all([
    h.auth.session.token(),
    h.auth.session.token(),
    h.auth.session.token(),
  ]);

  assert.deepEqual(tokens, ['access-2', 'access-2', 'access-2']);
  assert.equal(h.calls.length, 1, 'refresh must be single-flight');
});

test('a refresh token the server rejects ends the session rather than looping', async () => {
  const h = harness({ stored: session() });
  h.advance(3600);
  h.reply({ status: 400, body: { error: 'invalid_grant' } });

  assert.equal(await h.auth.session.token(), null);
  assert.equal(h.auth.session.signedIn, false);
  assert.equal(h.saved(), null, 'the dead session must not stay in storage');
});

test('signing in stores the session and exposes a provider-neutral user', async () => {
  const h = harness();
  h.reply({
    status: 200,
    body: {
      access_token: 'access-1',
      refresh_token: 'refresh-1',
      expires_in: 3600,
      user: { id: 'u1', email: 'maya@example.com', email_confirmed_at: '2026-09-01T00:00:00Z', user_metadata: { full_name: 'Maya Okonkwo' } },
    },
  });

  await h.auth.session.signIn('maya@example.com', 'correct-horse');
  assert.equal(h.auth.session.signedIn, true);
  // The shape is ours, not GoTrue's — nothing below auth.js knows the provider.
  // Spread into a host object first: this file imports `node:assert/strict`, so
  // every compare is a strict one, and an object constructed inside the VM realm
  // has that realm's `Object` prototype. A strict compare fails on the prototype
  // alone while every value matches.
  assert.deepEqual({ ...h.auth.session.user }, {
    email: 'maya@example.com',
    name: 'Maya Okonkwo',
    emailVerified: true,
  });
  assert.equal(h.saved().access_token, 'access-1');
});

test("a rejected sign-in surfaces the provider's own message", async () => {
  const h = harness();
  h.reply({ status: 400, body: { error_description: 'Invalid login credentials' } });
  await assert.rejects(
    () => h.auth.session.signIn('maya@example.com', 'wrong'),
    /Invalid login credentials/,
  );
  assert.equal(h.auth.session.signedIn, false);
});

test('sign-up that needs email confirmation does not claim a session', async () => {
  const h = harness();
  h.reply({ status: 200, body: { id: 'u2', email: 'new@example.com' } }); // no tokens
  const result = await h.auth.session.signUp('new@example.com', 'hunter2hunter2', 'New Person');

  assert.equal(result.confirm, true);
  assert.equal(result.email, 'new@example.com');
  assert.equal(h.auth.session.signedIn, false, 'must not render a workspace that is not there');
});

test('sign-up on a project with confirmation off signs straight in', async () => {
  const h = harness();
  h.reply({
    status: 200,
    body: { access_token: 'a', refresh_token: 'r', expires_in: 3600, user: { id: 'u2', email: 'new@example.com', user_metadata: {} } },
  });
  const result = await h.auth.session.signUp('new@example.com', 'hunter2hunter2', 'New Person');
  assert.ok(result.session);
  assert.equal(h.auth.session.signedIn, true);
});

test('signing out clears the session even when the revoke call fails', async () => {
  const h = harness({ stored: session() });
  h.reply({ networkError: true });
  await h.auth.session.signOut();
  assert.equal(h.auth.session.signedIn, false);
  assert.equal(h.saved(), null);
});

test('a browser with storage denied still signs in, for the life of the tab', async () => {
  const h = harness({ storage: false });
  h.reply({
    status: 200,
    body: { access_token: 'access-1', refresh_token: 'r', expires_in: 3600, user: { id: 'u1', email: 'a@example.com', user_metadata: {} } },
  });
  await h.auth.session.signIn('a@example.com', 'pw');
  assert.equal(h.auth.session.signedIn, true);
  assert.equal(await h.auth.session.token(), 'access-1');
});

test('an incomplete Supabase config fails closed and says why', async () => {
  const h = harness({ config: { ...SUPABASE_CONFIG, supabaseAnonKey: '' } });
  assert.equal(await h.auth.establishSession(), false);
  assert.match(h.auth.session.fault, /COLETAR_SUPABASE_ANON_KEY/);
});

test('a shell whose config did not render is treated as sign-in required', async () => {
  // The real failure mode: web.py stamps the config into `__SIGN_IN__`, and a
  // placeholder that was never replaced is what reaches the browser if that
  // substitution breaks. Failing closed is visible and safe; reading it as local
  // would serve the workspace to whoever asked.
  for (const raw of ['__SIGN_IN__', 'null', '', '"a string"', '42']) {
    const h = harness({ rawConfig: raw });
    assert.equal(h.auth.session.config.required, true, `raw config ${raw}`);
    assert.equal(h.auth.session.config.provider, 'unknown', `raw config ${raw}`);
    assert.equal(await h.auth.establishSession(), false, `raw config ${raw}`);
  }
});

test('local development has no sign-in and no network calls', async () => {
  const h = harness({ config: { provider: 'local', required: false } });
  assert.equal(await h.auth.establishSession(), true);
  assert.equal(h.auth.session.signedIn, true, 'local mode is always "signed in"');
  assert.equal(await h.auth.session.token(), null);
  assert.equal(h.calls.length, 0);
});

test('sign-up is only offered where registration is actually open', async () => {
  const open = harness();
  assert.equal(open.auth.session.canSignUp, true);
  const closed = harness({ config: { ...SUPABASE_CONFIG, openRegistration: false } });
  assert.equal(closed.auth.session.canSignUp, false);
});

test('authHeaders carries the session and omits it when there is none', async () => {
  const signedIn = harness({ stored: session() });
  // Spread for the cross-realm prototype reason noted above.
  assert.deepEqual({ ...(await signedIn.auth.authHeaders({ 'Content-Type': 'application/json' })) }, {
    'Content-Type': 'application/json',
    Authorization: 'Bearer access-1',
  });

  const anonymous = harness();
  assert.deepEqual({ ...(await anonymous.auth.authHeaders()) }, {});
});
