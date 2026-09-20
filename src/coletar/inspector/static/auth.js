// Sign-in for the workspace. The whole of the client's dependency on Clerk.
//
// The shape mirrors `coletar/accounts/identity.py` on the server: one small module
// that turns "is anyone signed in" into a token, and nothing else knows the
// provider's name. Everything below `session.token()` works on a string.
//
// **Local development has no sign-in and must keep working with none.** The server
// stamps `required: false` into the shell when `COLETAR_IDENTITY_PROVIDER` is
// `local`, and this module then does nothing at all — no script loaded, no network
// call, no gate. That is not a hole: the local provider is refused outright once
// `COLETAR_PUBLIC_URL` is set, so a hosted deployment cannot reach that branch.
//
// The config is stamped into the HTML rather than fetched. A round trip here would
// be one during which the app does not know whether it may render, which shows the
// workspace for a moment to someone about to be told to sign in.

const CLERK_CDN =
  "https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js";

function readConfig() {
  const el = document.getElementById("sign-in-config");
  if (!el) return { provider: "local", required: false, publishableKey: "" };
  try {
    return JSON.parse(el.textContent);
  } catch {
    // A shell whose config did not render is a deployment fault, not a reason to
    // fall open. Treating it as "sign-in required with no way to sign in" fails
    // closed and is visible; treating it as local would serve the workspace.
    return { provider: "unknown", required: true, publishableKey: "" };
  }
}

const config = readConfig();

const session = {
  config,
  clerk: null,
  /** The bearer token for the current session, or null when there is none. */
  async token() {
    if (!config.required) return null;
    if (!this.clerk?.session) return null;
    try {
      return await this.clerk.session.getToken();
    } catch {
      return null;
    }
  },
  get user() {
    return this.clerk?.user || null;
  },
  get signedIn() {
    return Boolean(this.clerk?.session);
  },
  async signOut() {
    if (this.clerk) await this.clerk.signOut();
  },
};

function loadScript(src, attributes) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.async = true;
    el.crossOrigin = "anonymous";
    for (const [key, value] of Object.entries(attributes || {})) {
      el.setAttribute(key, value);
    }
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(el);
  });
}

/** Full-page states. Deliberately not the app shell: there is no workspace to
    frame until we know whose it is. */
function screen(title, body) {
  document.getElementById("app").innerHTML = `
    <main class="loading signin-screen">
      <span class="brand-mark">c</span>
      <h1>${title}</h1>
      ${body}
    </main>`;
}

/**
 * Resolve who is signed in before the app renders anything.
 *
 * Returns true when the app may proceed. When it returns false it has already
 * painted the reason — a sign-in form, or a configuration error — and the caller
 * must not render the workspace.
 */
async function establishSession() {
  if (!config.required) return true;

  if (!config.publishableKey) {
    screen(
      "Sign-in is not configured",
      `<p>This deployment asks for an account but has no
       <code>COLETAR_CLERK_PUBLISHABLE_KEY</code>. Nobody can sign in until it is
       set.</p>`,
    );
    return false;
  }

  try {
    await loadScript(CLERK_CDN, { "data-clerk-publishable-key": config.publishableKey });
    session.clerk = new window.Clerk(config.publishableKey);
    await session.clerk.load();
  } catch (err) {
    screen(
      "Could not reach the sign-in service",
      `<p>The workspace needs Clerk to verify who you are, and it did not load.
       Check your connection and reload.</p>`,
    );
    return false;
  }

  if (!session.signedIn) {
    screen(
      "Sign in to coleta",
      `<p>Your context is yours. Sign in to open it.</p>
       <div id="clerk-sign-in" class="clerk-mount"></div>`,
    );
    session.clerk.mountSignIn(document.getElementById("clerk-sign-in"));
    // The component reloads the page on success, so nothing here resolves.
    return false;
  }

  return true;
}

/** Headers for a request to /web-api, carrying the session when there is one. */
async function authHeaders(base) {
  const headers = { ...(base || {}) };
  const token = await session.token();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

/**
 * What to show when the server refuses a request.
 *
 * 401 and 403 mean different things here and must not be collapsed: the server
 * distinguishes "your sign-in is not valid" from "your sign-in is fine and you are
 * not on the invite list", and those send a person to two different places.
 */
function handleAuthFailure(status, detail) {
  if (!config.required) return false;
  if (status === 401) {
    screen(
      "Your session has expired",
      `<p>Sign in again to reopen your workspace.</p>
       <div id="clerk-sign-in" class="clerk-mount"></div>`,
    );
    if (session.clerk) {
      session.clerk.mountSignIn(document.getElementById("clerk-sign-in"));
    }
    return true;
  }
  if (status === 403) {
    screen(
      "You are not on the list yet",
      `<p>${detail || "coleta is in invite-only beta."}</p>
       <p class="small muted">Signed in as ${
         session.user?.primaryEmailAddress?.emailAddress || "an unlisted address"
       }.</p>
       <button type="button" data-sign-out>Sign out</button>`,
    );
    document
      .querySelector("[data-sign-out]")
      ?.addEventListener("click", () => session.signOut().then(() => location.reload()));
    return true;
  }
  return false;
}

window.coletaAuth = { session, establishSession, authHeaders, handleAuthFailure };
