// The History page's fetch loop, against the real product.js.
//
// This file exists because of one production failure. `/web-api/pricing` began
// returning 500 on the hosted deployment, and the page did not show an error and
// carry on: it locked up. Every loader on the page ended in `render()`, `render`
// re-ran `bindHistory`, and `bindHistory` called the loaders again — which, for a
// loader that had nothing to cache because its fetch failed, is a loop. The
// symptom in the browser was a thousand identical 500s in the console, a main
// thread with no time left to run a click handler, and navigation that appeared
// to be broken because nothing else ever got a turn.
//
// So what is pinned here is the invariant that breaks the loop, not the UI around
// it: a panel that failed is not fetched again until someone asks for it. The
// three cases that matter are a failure (fetch once, not forever), a retry (the
// Try again button must actually re-fetch), and two callers racing (one fetch).
//
// Loaded into a VM with stubbed browser globals, the same way `auth.test.cjs`
// drives `auth.js`. Only top-level *function declarations* become properties of
// the VM's global object — `const` arrows stay in script scope — which is why the
// tests below drive `loadPanel` and `bindHistory` rather than the loaders.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const SOURCE = fs.readFileSync('src/coletar/inspector/static/product.js', 'utf8');

/**
 * Load product.js with just enough browser to get through its top level.
 *
 * `routes` maps a path under /web-api to the status it answers with, so a test
 * states what the server said rather than matching on URLs. Every request is
 * recorded, which is the whole measurement here: the bug was a request count.
 */
const DASHBOARD = {
  panels: {},
  bucket: 'week',
  examples: [],
  thread_provider: 'none',
};

function harness({ hash = '#/audit', routes = {}, bodies = {} } = {}) {
  const answers = { '/history/dashboard': DASHBOARD, ...bodies };
  const requests = [];
  const element = () => ({
    innerHTML: '',
    setAttribute() {},
    focus() {},
    querySelector: () => element(),
    querySelectorAll: () => [],
  });

  const context = vm.createContext({
    console,
    URL,
    Set,
    Map,
    Intl,
    Date,
    JSON,
    Number,
    Math,
    Object,
    Array,
    String,
    Boolean,
    Error,
    Promise,
    Infinity,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: (fn) => fn(),
    matchMedia: () => ({ matches: false }),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    location: { hash },
    window: { addEventListener() {}, scrollTo() {}, scrollY: 0 },
    document: {
      currentScript: { src: 'http://localhost/static/product.js?v=test' },
      querySelector: () => element(),
      querySelectorAll: () => [],
      activeElement: null,
      addEventListener() {},
    },
    // The workspace client's session, stubbed to "no sign-in required" so that
    // `api` takes its ordinary path and a 500 stays a 500.
    coletaAuth: {
      authHeaders: async (headers) => headers || {},
      session: { config: { required: false }, signedIn: true, user: null },
      // Held open deliberately. The file's last statement is a bootstrap that
      // resolves a session and then renders the whole app; these tests are about
      // the History loaders, so the bootstrap is left pending and the loaders are
      // driven directly. A promise that never settles is the quietest way to say
      // "not this part" without editing the source under test.
      establishSession: () => new Promise(() => {}),
    },
    fetch: async (path) => {
      requests.push(path);
      // Keyed on the path alone: the dashboard carries its window as a query
      // string, and a route table that had to repeat it would be matching on
      // the thing these tests change.
      const route = String(path).replace('/web-api', '').split('?')[0];
      const status = routes[route] ?? 200;
      return {
        ok: status < 400,
        status,
        json: async () =>
          status < 400
            ? answers[route] || {}
            : { detail: 'Request failed. Please try again.' },
      };
    },
  });
  context.globalThis = context;
  context.window.location = context.location;
  vm.runInContext(SOURCE, context, { filename: 'product.js' });
  return { context, requests, countOf: (part) => requests.filter((p) => String(p).includes(part)).length };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('a panel that failed is not fetched again on the next render', async () => {
  const { context } = harness();
  let calls = 0;
  const failing = async () => {
    calls += 1;
    throw new Error('Internal Server Error');
  };

  // Three renders' worth of calls. Before the fix this was three fetches, and in
  // the browser it was three thousand, because each one triggered the next.
  await context.loadPanel('pricing', null, false, failing, []);
  await context.loadPanel('pricing', null, false, failing, []);
  await context.loadPanel('pricing', null, false, failing, []);
  assert.equal(calls, 1);
});

test('the Try again button is what re-fetches a failed panel', async () => {
  const { context } = harness();
  let calls = 0;
  const failing = async () => {
    calls += 1;
    throw new Error('Internal Server Error');
  };

  await context.loadPanel('pricing', null, false, failing, []);
  await context.loadPanel('pricing', null, true, failing, []);
  assert.equal(calls, 2, 'force is the user asking again, and must be honoured');
});

test('a recovered panel stops reporting the failure it recovered from', async () => {
  const { context } = harness();
  let attempt = 0;
  const flaky = async () => {
    attempt += 1;
    if (attempt === 1) throw new Error('Internal Server Error');
  };

  await context.loadPanel('reach', null, false, flaky, []);
  await context.loadPanel('reach', null, true, flaky, []);
  // Rendered after the retry succeeded: the tab must show data, not the warning
  // left over from the attempt before it.
  assert.equal(context.reachTab().includes('Try again'), false);
});

test('two callers racing produce one request', async () => {
  const { context } = harness();
  let calls = 0;
  const slow = async () => {
    calls += 1;
    await settle();
  };

  await Promise.all([
    context.loadPanel('ideas', null, false, slow, []),
    context.loadPanel('ideas', null, false, slow, []),
  ]);
  assert.equal(calls, 1);
});

test('opening History with a 500 on /pricing asks for it once', async () => {
  const { context, countOf } = harness({ routes: { '/pricing': 500 } });

  // Each `bindHistory` stands for one render of the page. The loop ran through
  // `render` -> `bindHistory` -> loader -> `render`; this drives the same edge
  // directly, which is the part that has to terminate.
  for (let i = 0; i < 5; i += 1) {
    context.bindHistory();
    await settle();
  }

  assert.equal(countOf('/pricing'), 1, 'a broken endpoint is asked once, not per render');
  // And the panels that answered are not punished for it: the dashboard was
  // fetched once and kept, rather than re-fetched alongside the failing one.
  assert.equal(countOf('/history/dashboard'), 1);
});

test('a failed panel does not blank the panels that loaded', async () => {
  const { context } = harness({ routes: { '/pricing': 500 } });
  for (let i = 0; i < 3; i += 1) {
    context.bindHistory();
    await settle();
  }

  // Cost is the tab that needs prices, and says so.
  assert.ok(context.costTab().includes('Try again'));
  // Overview needs none, and must not inherit Cost's failure — the shared
  // `historyError` this replaced put a warning on every tab on the page.
  assert.equal(context.overviewTab().includes('Try again'), false);
});
