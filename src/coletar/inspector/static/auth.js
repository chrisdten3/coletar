// Sign-in for the workspace. The whole of the client's dependency on an auth provider.
//
// The shape mirrors `coletar/accounts/identity.py` on the server: one small module
// that turns "is anyone signed in" into a token, and nothing else knows the
// provider's name. Everything below `session.token()` works on a string.
//
// **Supabase is spoken to over its REST API rather than through its SDK.** The
// client here is dependency-free by convention, and what this needs -- sign in,
// sign up, refresh, sign out, recover -- is less code than the import would be. It
// also means the sign-in *form* is ours, which is the difference between a page that
// matches the rest of the site and a widget that is visibly from somewhere else.
// Clerk keeps its mounted component, because Clerk has no equivalent REST surface
// that a browser may call with a publishable key.
//
// **Local development has no sign-in and must keep working with none.** The server
// stamps `required: false` into the shell when `COLETAR_IDENTITY_PROVIDER` is
// `local`, and this module then does nothing at all -- no network call, no gate.
// That is not a hole: the local provider is refused outright once
// `COLETAR_PUBLIC_URL` is set, so a hosted deployment cannot reach that branch.
//
// The config is stamped into the HTML rather than fetched. A round trip here would
// be one during which the app does not know whether it may render, which shows the
// workspace for a moment to someone about to be told to sign in.

const CLERK_CDN =
  "https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js";

// Where the Supabase session is kept between page loads. A tokens-in-localStorage
// arrangement is what every Supabase browser client does; the access token is short
// lived and the refresh token is single-use and rotated on every exchange.
const STORE_KEY = "coleta.session";

// Refresh this many seconds before `expires_at`. Enough that a request in flight
// when the clock runs out still carries a live token.
const REFRESH_MARGIN = 90;

function readConfig() {
  const el = document.getElementById("sign-in-config");
  if (!el) return { provider: "local", required: false };
  // A shell whose config did not render is a deployment fault, not a reason to
  // fall open. Treating it as "sign-in required with no way to sign in" fails
  // closed and is visible; treating it as local would serve the workspace.
  const closed = { provider: "unknown", required: true };
  let parsed;
  try {
    parsed = JSON.parse(el.textContent);
  } catch {
    return closed;
  }
  // Valid JSON that is not an object — `null`, a number, a string — parses without
  // throwing and would then be read for `.required` at module scope, taking the
  // whole client down with a TypeError before anything is drawn. A half-rendered
  // stamp is exactly the case this branch exists for, so it fails closed too.
  if (!parsed || typeof parsed !== "object") return closed;
  return parsed;
}

const config = readConfig();

/* --- Supabase, over GoTrue's REST API ------------------------------------- */

function authBase() {
  return `${String(config.supabaseUrl || "").replace(/\/+$/, "")}/auth/v1`;
}

function stored() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    // A browser with storage denied is not a browser that may not sign in; it is
    // one whose session lasts until the tab closes.
    return null;
  }
}

function keep(session) {
  try {
    if (session) localStorage.setItem(STORE_KEY, JSON.stringify(session));
    else localStorage.removeItem(STORE_KEY);
  } catch {
    /* memory-only session; see `stored`. */
  }
}

/**
 * Call GoTrue and raise its message rather than a status code.
 *
 * The provider's own wording is better than anything invented here -- "Invalid
 * login credentials", "User already registered", "Password should be at least 6
 * characters" are all things a person can act on -- and deliberately does not
 * distinguish "no such address" from "wrong password", which is the property that
 * keeps a sign-in form from being an account-enumeration oracle.
 */
async function gotrue(path, { method = "POST", body, token } = {}) {
  const headers = { apikey: config.supabaseAnonKey || "" };
  if (body) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(`${authBase()}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(
      "Couldn’t reach the sign-in service. Check your connection and try again.",
    );
  }
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(
      payload.error_description ||
        payload.msg ||
        payload.message ||
        payload.error ||
        "That didn’t work. Please try again.",
    );
  }
  return payload;
}

/** Normalise a GoTrue token response into what we persist. */
function asSession(payload) {
  if (!payload || !payload.access_token) return null;
  return {
    access_token: payload.access_token,
    refresh_token: payload.refresh_token || "",
    // GoTrue sends `expires_at` in seconds; derive it when only `expires_in` came.
    expires_at:
      payload.expires_at ||
      Math.floor(Date.now() / 1000) + Number(payload.expires_in || 3600),
    user: payload.user || null,
  };
}

const supabase = {
  session: stored(),
  // One in-flight refresh at a time. Without this, several API calls racing past
  // expiry each spend the refresh token, and because GoTrue rotates it on use
  // every loser of that race is signed out.
  refreshing: null,

  async refresh() {
    if (!this.session?.refresh_token) return null;
    if (this.refreshing) return this.refreshing;
    this.refreshing = (async () => {
      try {
        const next = asSession(
          await gotrue("/token?grant_type=refresh_token", {
            body: { refresh_token: this.session.refresh_token },
          }),
        );
        this.set(next);
        return next;
      } catch {
        // A refresh token that will not exchange is a session that is over. Clear
        // it rather than retrying: keeping it means every later call fails the
        // same way and the person is never shown a sign-in form.
        this.set(null);
        return null;
      } finally {
        this.refreshing = null;
      }
    })();
    return this.refreshing;
  },

  set(session) {
    this.session = session;
    keep(session);
  },

  async token() {
    if (!this.session) return null;
    const expiring =
      Number(this.session.expires_at || 0) - Date.now() / 1000 < REFRESH_MARGIN;
    if (expiring) {
      const next = await this.refresh();
      return next?.access_token || null;
    }
    return this.session.access_token;
  },

  async signIn(email, password) {
    const next = asSession(
      await gotrue("/token?grant_type=password", { body: { email, password } }),
    );
    if (!next) throw new Error("That sign-in did not return a session.");
    this.set(next);
    return next;
  },

  /**
   * Create an account.
   *
   * Returns `{ session }` when the project has email confirmation switched off --
   * GoTrue signs the person straight in -- and `{ confirm: true }` when it is on
   * and a link is on its way. The caller must not assume the first case: showing a
   * workspace that is not there yet is worse than saying to check an inbox.
   */
  async signUp(email, password, fullName) {
    const payload = await gotrue("/signup", {
      body: {
        email,
        password,
        data: fullName ? { full_name: fullName } : {},
      },
    });
    const next = asSession(payload);
    if (next) {
      this.set(next);
      return { session: next };
    }
    return { confirm: true, email };
  },

  async recover(email) {
    await gotrue("/recover", { body: { email } });
  },

  async signOut() {
    const token = this.session?.access_token;
    this.set(null);
    if (token) {
      // Best effort. The session is already gone locally, and a failed revoke must
      // not leave the person looking at a workspace they asked to leave.
      try {
        await gotrue("/logout", { token });
      } catch {
        /* already invalid, or offline. */
      }
    }
  },
};

/* --- Clerk ---------------------------------------------------------------- */

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

const clerk = { instance: null };

/* --- The provider-agnostic surface the rest of the app uses --------------- */

const session = {
  config,
  /** Why sign-in cannot work at all, or "" when it can. */
  fault: "",

  get provider() {
    return config.provider;
  },

  /** The bearer token for the current session, or null when there is none. */
  async token() {
    if (!config.required) return null;
    if (config.provider === "supabase") return supabase.token();
    if (!clerk.instance?.session) return null;
    try {
      return await clerk.instance.session.getToken();
    } catch {
      return null;
    }
  },

  /** Enough of the signed-in person to render a chip. Shape is ours, not a provider's. */
  get user() {
    if (config.provider === "supabase") {
      const user = supabase.session?.user;
      if (!user) return null;
      const meta = user.user_metadata || {};
      return {
        email: user.email || "",
        name: meta.full_name || meta.name || "",
        emailVerified: Boolean(
          user.email_confirmed_at || meta.email_verified || false,
        ),
      };
    }
    const user = clerk.instance?.user;
    if (!user) return null;
    return {
      email: user.primaryEmailAddress?.emailAddress || "",
      name: user.fullName || "",
      emailVerified: true,
    };
  },

  get signedIn() {
    if (!config.required) return true;
    if (config.provider === "supabase") return Boolean(supabase.session);
    return Boolean(clerk.instance?.session);
  },

  /** Whether this deployment lets a stranger create their own workspace. */
  get canSignUp() {
    return config.provider === "supabase" && Boolean(config.openRegistration);
  },

  signIn: (email, password) => supabase.signIn(email, password),
  signUp: (email, password, name) => supabase.signUp(email, password, name),
  recover: (email) => supabase.recover(email),

  async signOut() {
    if (config.provider === "supabase") return supabase.signOut();
    if (clerk.instance) await clerk.instance.signOut();
  },

  /** Mount Clerk's component. A no-op under Supabase, which uses our own form. */
  mountSignIn(el) {
    if (config.provider !== "supabase" && clerk.instance && el) {
      clerk.instance.mountSignIn(el);
    }
  },
};

/**
 * Resolve who is signed in, before the app renders anything.
 *
 * **This no longer gates the whole app**, which is the behaviour change from the
 * Clerk-only version. Refusing to render until someone signs in also hid the
 * marketing pages behind the login wall -- a visitor could not read what coleta is
 * without an account, and the sign-in screen was the home page. Deciding which
 * routes need an account belongs to the router, which knows what is being asked
 * for; this function's job is only to answer "who is this".
 *
 * Returns true when the app may render *something*. False means it has already
 * painted a configuration fault, and there is nothing to render on top of.
 */
async function establishSession() {
  if (!config.required) return true;

  if (config.provider === "supabase") {
    if (!config.supabaseUrl || !config.supabaseAnonKey) {
      session.fault =
        "This deployment asks for an account but has no COLETAR_SUPABASE_URL or COLETAR_SUPABASE_ANON_KEY set. Nobody can sign in until they are.";
      return false;
    }
    // Exchange a stale token now rather than on the first API call, so the first
    // render already knows whether this person is signed in.
    if (
      supabase.session &&
      Number(supabase.session.expires_at || 0) - Date.now() / 1000 <
        REFRESH_MARGIN
    ) {
      await supabase.refresh();
    }
    return true;
  }

  if (config.provider === "clerk") {
    if (!config.publishableKey) {
      session.fault =
        "This deployment asks for an account but has no COLETAR_CLERK_PUBLISHABLE_KEY. Nobody can sign in until it is set.";
      return false;
    }
    try {
      await loadScript(CLERK_CDN, {
        "data-clerk-publishable-key": config.publishableKey,
      });
      clerk.instance = new window.Clerk(config.publishableKey);
      await clerk.instance.load();
    } catch {
      session.fault =
        "The workspace needs Clerk to verify who you are, and it did not load. Check your connection and reload.";
      return false;
    }
    return true;
  }

  session.fault = `This deployment is configured for an identity provider the app does not know how to talk to (${config.provider}).`;
  return false;
}

/** Headers for a request to /web-api, carrying the session when there is one. */
async function authHeaders(base) {
  const headers = { ...(base || {}) };
  const token = await session.token();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

window.coletaAuth = { session, establishSession, authHeaders };
