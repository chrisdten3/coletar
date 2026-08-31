/**
 * The coletar SDK for JavaScript (ROADMAP M7).
 *
 * A port of the Python client, deliberately method-for-method: two SDKs that drift
 * are two descriptions of one API, and the second one is always the one that lies.
 * A test in the Python suite compares the two surfaces so drift fails CI rather than
 * being discovered by whoever picked the wrong language.
 *
 * **Server-side only.** M7 withholds CORS headers from the SDK routes, so a browser
 * will refuse to hand these responses to a page — which is the intended boundary
 * rather than an obstacle. An API key in browser JavaScript is a key you have
 * published: visible in devtools, in the bundle, and to every script on the page.
 * The browser extension talks to the three bridge endpoints instead, which are
 * CORS-allowlisted precisely because they cannot enumerate a graph.
 *
 * **No dependencies.** Native `fetch`, which Node has had since 18.
 *
 * **No telemetry.** The client contacts the base URL you gave it and nothing else.
 * Every request goes through one method, so there is a single place a second
 * destination could ever appear.
 *
 * **No hard delete**, because there is no endpoint under one. `retire()` excludes an
 * object from retrieval and from compile while leaving it readable, which is what
 * lets `history()` still answer. A convenience method that removed a row would turn
 * a guarantee into a convention.
 */

export class ColetarError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ColetarError";
    this.status = status ?? null;
  }
}

/** The key is missing, wrong, or lacks the scope for this call. */
export class Unauthorized extends ColetarError {
  constructor(message, status) {
    super(message, status);
    this.name = "Unauthorized";
  }
}

/**
 * No such object — or it belongs to another tenant, or is local to another surface.
 * The three are deliberately indistinguishable.
 */
export class NotFound extends ColetarError {
  constructor(message) {
    super(message, 404);
    this.name = "NotFound";
  }
}

/** Too many requests for this credential. `retryAfter` is in seconds. */
export class RateLimited extends ColetarError {
  constructor(message, retryAfter) {
    super(message, 429);
    this.name = "RateLimited";
    this.retryAfter = retryAfter;
  }
}

export class Coletar {
  /**
   * The tenant is not a parameter. It comes from the key, server-side, which is what
   * stops a client naming someone else's graph.
   */
  constructor(baseUrl, { apiKey, fetch: fetchImpl } = {}) {
    if (!apiKey) {
      throw new ColetarError("an apiKey is required; this server has no anonymous mode");
    }
    this.baseUrl = String(baseUrl).replace(/\/+$/, "");
    this._fetch = fetchImpl ?? globalThis.fetch;
    this._headers = { authorization: `Bearer ${apiKey}` };
  }

  async _call(method, path, body) {
    const init = { method, headers: { ...this._headers } };
    if (body !== undefined) {
      init.headers["content-type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const response = await this._fetch(`${this.baseUrl}${path}`, init);

    if (response.status === 429) {
      throw new RateLimited(
        "rate limited for this key",
        Number(response.headers.get("retry-after") ?? 1),
      );
    }
    if (response.status === 401 || response.status === 403) {
      throw new Unauthorized(await messageOf(response), response.status);
    }
    if (response.status === 404) throw new NotFound(await messageOf(response));
    if (response.status >= 400) {
      throw new ColetarError(await messageOf(response), response.status);
    }
    const payload = await response.json();
    return payload && typeof payload === "object" ? payload : { result: payload };
  }

  // --- read -------------------------------------------------------------------

  /**
   * Hybrid retrieval over this key's graph. `explain` returns the component scores
   * and versions behind each hit — a ranked list you cannot interrogate is a list
   * you have to trust.
   */
  async search(query, { projectId = null, topK = 12, explain = false } = {}) {
    const payload = await this._call("POST", "/v1/search", {
      query,
      project_id: projectId,
      top_k: topK,
      explain,
    });
    return Array.isArray(payload.results) ? payload.results : [];
  }

  /** One object exactly as the graph holds it, provenance included. */
  async inspect(objectId) {
    const payload = await this._call("GET", `/v1/objects/${encodeURIComponent(objectId)}`);
    return payload.object ?? {};
  }

  /** What this object used to say, and when it changed. */
  async history(objectId) {
    const payload = await this._call(
      "GET",
      `/v1/objects/${encodeURIComponent(objectId)}/history`,
    );
    return Array.isArray(payload.revisions) ? payload.revisions : [];
  }

  // --- write ------------------------------------------------------------------

  /**
   * Write a memory through the ingest boundary. A restatement of something already
   * held corroborates it rather than creating a duplicate, and the response reports
   * what was stored rather than what was asked for.
   */
  async remember(content, { kind = "fact", projectId = null } = {}) {
    return this._call("POST", "/v1/remember", { content, kind, project_id: projectId });
  }

  /**
   * Correct a fact by writing its replacement. The old object is not edited and not
   * removed; it stops being returned and stays readable.
   */
  async supersede(objectId, content, { kind = "fact", projectId = null } = {}) {
    return this._call("POST", `/v1/objects/${encodeURIComponent(objectId)}/supersede`, {
      content,
      kind,
      project_id: projectId,
    });
  }

  /**
   * Exclude an object from retrieval and from compile. It stays readable. This is
   * the closest thing to a delete and deliberately is not one; a reason is required
   * because a retirement nobody can explain later is indistinguishable from a bug.
   */
  async retire(objectId, { reason } = {}) {
    return this._call("POST", `/v1/objects/${encodeURIComponent(objectId)}/retire`, {
      reason,
    });
  }

  // --- move -------------------------------------------------------------------

  /**
   * Compile the graph into a destination's native containers. Subject to the same
   * review gate as the CLI: a 409 means objects have not been reviewed since they
   * last changed.
   */
  async compile({ destination = "local", projectId = null } = {}) {
    return this._call("POST", "/v1/compile", { destination, project_id: projectId });
  }
}

async function messageOf(response) {
  try {
    const payload = await response.json();
    if (payload && typeof payload === "object") {
      return String(payload.message ?? payload.error ?? JSON.stringify(payload));
    }
    return String(payload);
  } catch {
    return `HTTP ${response.status}`;
  }
}
