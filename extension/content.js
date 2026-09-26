// Automatic mode is explicitly consented. Only a trusted user Send/Enter starts
// a turn. No archive access, background page reads, or provider network hooks.
const settings = {endpoint:"", apiKey:"", recall:true, capture:false, captureConsent:false,
  automatic:false, automaticConsent:false, shortcut:"Ctrl+Shift+M"};
let ready = false;
chrome.storage.sync.get(settings, (loaded) => { Object.assign(settings, loaded); ready = true; mount(); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync") return;
  for (const [key, {newValue}] of Object.entries(changes)) settings[key] = newValue;
  pending = null;
  generation++;
  mount();
});
const adapters = {
  "chatgpt.com": {
    composers: ['#prompt-textarea'],
    send: ['button[data-testid="send-button"]', 'button[aria-label="Send prompt"]'],
    stop: ['button[data-testid="stop-button"]', 'button[aria-label="Stop streaming"]'],
    replies: '[data-message-author-role="assistant"]',
    text: '.markdown',
  },
  "claude.ai": {
    composers: ['div[contenteditable="true"][data-lexical-editor="true"]', 'div.ProseMirror[contenteditable="true"]', 'div[contenteditable="true"][role="textbox"]'],
    send: ['button[aria-label="Send message"]', 'button[data-testid="send-button"]'],
    stop: ['button[aria-label="Stop response"]', 'button[aria-label="Stop generating"]'],
    replies: '.font-claude-response',
    text: null,
  },
};
adapters["chat.openai.com"] = adapters["chatgpt.com"];
const adapter = adapters[location.hostname];
const active = () => document.visibilityState === "visible" && document.hasFocus();
const visible = (el) => el && el.isConnected && el.getClientRects().length > 0;
function find(selectors) {
  for (const selector of selectors || []) {
    const match = [...document.querySelectorAll(selector)].find(visible);
    if (match) return match;
  }
  return null;
}
const composer = () => find(adapter?.composers);
const read = (el) => (el.value !== undefined ? el.value : el.innerText || "").trim();
async function write(el, text) {
  try {
  if (el.value !== undefined) {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    if (setter) setter.call(el, text); else el.value = text;
    el.dispatchEvent(new Event("input", {bubbles:true}));
  } else {
    el.focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("insertText", false, text);
  }
    // Let the editor reconcile its input before deciding that insertion failed.
    await new Promise((resolve) => setTimeout(resolve, 0));
    return el.isConnected && ColetaBridge.sameText(read(el), text);
  } catch { return false; }
}
let status = "Coleta ready";
function report(text) {
  status = text;
  const el = document.getElementById("coleta-status");
  if (el) el.textContent = text;
}
function mount() {
  if (!ready || !adapter) return;
  let panel = document.getElementById("coleta-bridge");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "coleta-bridge";
    panel.style.cssText = "position:fixed;bottom:20px;right:20px;z-index:2147483646;padding:10px 14px;border-radius:12px;background:#1e2e23;color:#eef4e8;font:12px system-ui;box-shadow:0 3px 20px #0002;max-width:300px";
    const label = document.createElement("span");
    label.id = "coleta-status";
    label.setAttribute("role", "status");
    label.setAttribute("aria-live", "polite");
    panel.appendChild(label);
    const button = document.createElement("button");
    button.id = "coleta-recall";
    button.type = "button";
    button.textContent = "✦ Add memory";
    button.style.cssText = "display:block;margin-top:8px;border:1px solid #7c9871;border-radius:20px;background:transparent;color:inherit;padding:6px 10px;cursor:pointer";
    button.addEventListener("click", () => manualRecall());
    panel.appendChild(button);
    document.body.appendChild(panel);
  }
  const automatic = ColetaBridge.autoEnabled(settings);
  document.getElementById("coleta-recall").style.display = automatic || !settings.recall ? "none" : "block";
  report(automatic ? "Coleta automatic · ready" : status);
}
async function call(path, body, config = {...settings}, timeout = 4000) {
  if (!config.endpoint || !config.apiKey) return {ok:false};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(config.endpoint.replace(/\/$/, "") + path, {
      method:"POST", credentials:"omit", redirect:"error", signal:controller.signal,
      headers:{"Content-Type":"application/json", "X-API-Key":config.apiKey},
      body:JSON.stringify({...body, surface:location.hostname}),
    });
    const data = await response.json();
    return {ok:response.ok, status:response.status, data};
  } catch { return {ok:false}; }
  finally { clearTimeout(timer); }
}
async function account(config) {
  const bytes = new TextEncoder().encode(`${config.endpoint}\0${config.apiKey}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((v) => v.toString(16).padStart(2,"0")).join("");
}
async function queue(action, config, extra = {}) {
  try {
    return await chrome.runtime.sendMessage({type:"coleta-outbox", action, account:await account(config), ...extra});
  } catch { return {ok:false}; }
}
let flushing = false;
let retryAfter = 0;
let retryDelay = 1000;
async function flush() {
  if (!active() || !ColetaBridge.autoEnabled(settings) || flushing || Date.now() < retryAfter) return;
  flushing = true;
  const config = {...settings};
  const version = generation;
  try {
    const result = await queue("pending", config);
    if (!result?.ok) return;
    if (result.dropped) report("Coleta: expired unsent captures were removed");
    for (const item of result.records) {
      if (!active() || version !== generation || !ColetaBridge.autoEnabled(settings)) break;
      const saved = await call("/v1/capture", item.body, config);
      if (saved.ok && saved.data.stored) {
        await queue("ack", config, {id:item.id});
        retryDelay = 1000;
      } else {
        retryAfter = Date.now() + retryDelay;
        retryDelay = Math.min(retryDelay * 2, 60_000);
        report(saved.data?.error === "capture_not_enabled"
          ? "Coleta: enable raw capture on the server · turn queued locally"
          : "Coleta: capture pending · will retry on this site");
        break;
      }
    }
  } finally { flushing = false; }
}
let manualBusy = false;
async function manualRecall() {
  if (!settings.recall || !active() || manualBusy) return;
  const el = composer();
  if (!el || !read(el)) { report("Coleta: type a prompt first"); return; }
  const snapshot = read(el);
  const route = location.pathname;
  const version = generation;
  manualBusy = true;
  report("Coleta: finding context…");
  try {
    const result = await call("/v1/search", {query:ColetaBridge.strip(snapshot).slice(0,4000), top_k:6, style:"terse"});
    if (!active() || generation !== version || !el.isConnected || read(el) !== snapshot || location.pathname !== route) return;
    if (!result.ok) { report("Coleta: unavailable · prompt unchanged"); return; }
    const text = ColetaBridge.augment(ColetaBridge.strip(snapshot), result.data);
    if (!await write(el, text)) { report("Coleta: editor changed · check your prompt"); return; }
    report(result.data.results.length ? `Coleta: ${result.data.results.length} memories added` : "Coleta: no relevant memory");
  } finally { manualBusy = false; }
}
let generation = 0;
let busy = false;
let bypass = false;
let pending = null;
let lastRoute = location.pathname;
function conversationId() {
  return location.pathname.match(/\/(?:c|chat)\/([\w-]+)/)?.[1] || `new-${crypto.randomUUID()}`;
}
function isNewChat(path) { return ["/", "/new", "/chat", "/chat/new"].includes(path); }
async function intercept(event, el, button) {
  if (busy) { event.preventDefault(); event.stopImmediatePropagation(); return; }
  const draft = read(el);
  const original = ColetaBridge.strip(draft);
  if (!original) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  busy = true;
  pending = null;
  const version = generation;
  const route = location.pathname;
  const config = {...settings};
  const turn = {turn_id:crypto.randomUUID(), conversation_id:conversationId(), text:original, role:"user"};
  const unchanged = () => active() && version === generation && ColetaBridge.autoEnabled(settings) &&
    location.pathname === route && el.isConnected && read(el) === draft;
  report("Coleta: finding context…");
  try {
    // Local durability precedes retrieval. Fail-open applies to both steps; a slow
    // or unavailable extension/server never gets to hold the website indefinitely.
    const queued = await ColetaBridge.bounded(queue("enqueue", config, {body:turn}), 100);
    if (!unchanged()) { report("Coleta: send cancelled · draft kept"); return; }
    const saved = await ColetaBridge.bounded(call("/v1/capture", turn, config, 200), 200);
    if (saved?.ok && saved.data.stored && queued?.id) {
      void queue("ack", config, {id:queued.id});
    }
    if (!unchanged()) { report("Coleta: send cancelled · draft kept"); return; }
    const result = await ColetaBridge.bounded(call("/v1/search", {
      query:original.slice(0,4000), top_k:6, style:"terse",
    }, config, 400), 400);
    if (!unchanged()) {
      report("Coleta: draft changed or page left · send when ready");
      return;
    }
    let augmented = result?.ok ? ColetaBridge.augment(original, result.data) : draft;
    let injectionFailed = false;
    if (augmented !== draft && !await write(el, augmented)) {
      if (!active() || version !== generation || location.pathname !== route) return;
      if (!await write(el, draft)) {
        report("Coleta: could not restore your draft · please check it before sending");
        return;
      }
      augmented = draft;
      injectionFailed = true;
    }
    if (!active() || version !== generation || location.pathname !== route ||
        !el.isConnected || !ColetaBridge.sameText(read(el), augmented)) {
      report("Coleta: draft changed · send when ready");
      return;
    }
    // React may replace the button when the editor changes. Re-resolve only the
    // adapter's known Send control, never a generic submit/stop button.
    const send = find(adapter.send);
    if (!send || send.disabled || send.getAttribute("aria-disabled") === "true") {
      if (ColetaBridge.sameText(read(el), augmented)) await write(el, draft);
      report("Coleta: Send unavailable · prompt kept");
      return;
    }
    pending = {turn, route, newChat:isNewChat(route), config, version,
      before:new Set(document.querySelectorAll(adapter.replies)), node:null,
      text:"", changed:Date.now(), started:Date.now(), sawStreaming:false};
    bypass = true;
    try { send.click(); } finally { bypass = false; }
    report(injectionFailed ? "Coleta: memory insertion failed · sent original prompt" : !queued?.ok ? "Coleta: capture not confirmed · prompt sent" :
      !result?.ok ? "Coleta unavailable · sent without added memory" :
      result.data.results.length ? `Coleta: ${result.data.results.length} memories added` : "Coleta: no relevant memory");
    void flush();
  } finally { busy = false; }
}
function matchesShortcut(event) {
  const parts = settings.shortcut.toLowerCase().split("+").map((s) => s.trim());
  return event.key.toLowerCase() === parts.at(-1) &&
    event.ctrlKey === parts.includes("ctrl") && event.shiftKey === parts.includes("shift") &&
    event.altKey === parts.includes("alt") && event.metaKey === parts.includes("cmd");
}
function routeChanged() {
  if (location.pathname === lastRoute) return;
  if (pending?.newChat && /\/(?:c|chat)\/[\w-]+/.test(location.pathname)) {
    pending.route = location.pathname;
    pending.newChat = false;
  } else { pending = null; generation++; }
  lastRoute = location.pathname;
}
function handleSend(event) {
  if (!ready || !adapter || bypass || !event.isTrusted || !active()) return;
  if (event.type === "click" && event.target.closest?.("a[href]")) {
    pending = null;
    generation++;
  }
  routeChanged();
  const el = composer();
  if (!el) return;
  if (event.type === "keydown" && settings.recall && matchesShortcut(event)) {
    event.preventDefault();
    void manualRecall();
    return;
  }
  const send = find(adapter.send);
  const stop = find(adapter.stop);
  if (event.type === "click" && stop?.contains(event.target)) {
    pending = null;
    report("Coleta: interrupted reply not captured");
    return;
  }
  if (busy && event.type === "keydown" && el.contains(event.target) &&
      event.key === "Enter" && event.repeat && !event.isComposing && !event.shiftKey) {
    event.preventDefault();
    event.stopImmediatePropagation();
    return;
  }
  const trigger = event.type === "keydown" ? el.contains(event.target) && ColetaBridge.sendsOnEnter(event)
    : send?.contains(event.target);
  if (!trigger || !send || send.disabled || send.getAttribute("aria-disabled") === "true") return;
  if (ColetaBridge.autoEnabled(settings)) void intercept(event, el, send);
  else if (settings.capture && settings.captureConsent) {
    const text = ColetaBridge.strip(read(el));
    if (text) void call("/v1/capture", {text}).then((r) => {
      if (!r.ok || r.data.capture_enabled === false) report("Coleta: capture unavailable");
    });
  }
}
document.addEventListener("keydown", handleSend, true);
document.addEventListener("click", handleSend, true);
async function observeReply() {
  if (!active() || !ColetaBridge.autoEnabled(settings)) return;
  routeChanged();
  const turn = pending;
  if (!turn || turn.version !== generation) return;
  if (Date.now() - turn.started > 120_000) {
    pending = null;
    report("Coleta: reply completion not confirmed · not captured");
    return;
  }
  const streaming = Boolean(find(adapter.stop));
  turn.sawStreaming ||= streaming;
  const nodes = [...document.querySelectorAll(adapter.replies)].filter((el) => visible(el) && !turn.before.has(el));
  // Ambiguity (multiple replies, regenerated branches, navigation) loses recall,
  // never causes an old answer to be attributed to this prompt.
  if (nodes.length !== 1) return;
  const node = nodes[0];
  if (turn.node && node !== turn.node) { pending = null; return; }
  turn.node = node;
  const bodies = adapter.text ? [...node.querySelectorAll(adapter.text)] : [node];
  const text = bodies.map((body) => body.innerText || "").join("\n\n").trim();
  if (!text || text.length > 100_000) return;
  if (text !== turn.text) { turn.text = text; turn.changed = Date.now(); return; }
  // A quiet stream is not completion. Require observed native streaming controls
  // to disappear as well as a stable text window; unknown adapters capture nothing.
  if (streaming || !turn.sawStreaming || Date.now() - turn.changed < 1500) return;
  pending = null;
  const result = await queue("enqueue", turn.config, {body:{...turn.turn, role:"assistant", text}});
  report(result?.ok ? "Coleta: reply queued for capture" : "Coleta: reply capture failed");
  void flush();
}
document.addEventListener("visibilitychange", () => {
  if (!active()) { pending = null; generation++; }
});
window.addEventListener("blur", () => { pending = null; generation++; });
window.addEventListener("popstate", () => { pending = null; generation++; lastRoute = location.pathname; });
// Poll only a foreground page. No DOM read occurs while another tab/window is active.
setInterval(() => {
  if (!active()) return;
  if (!document.getElementById("coleta-bridge")) mount();
  void observeReply();
  void flush();
}, 500);
