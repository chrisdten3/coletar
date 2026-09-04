// coletar composer bridge.
//
// THE BOUNDARY, and it is the reason this extension is defensible:
//
//   This script reads the *composer* — the box you type into — and nothing else.
//   It never reads the assistant's replies, and it never reads conversation
//   history. Reading what you type into a text field is the category browser
//   software has always occupied: password managers, text expanders, spell
//   checkers. Reading the model's Output is what both providers' terms name, and
//   this does not do it.
//
//   That is enforced structurally rather than promised. COMPOSERS below is the
//   only DOM lookup in this file. There is no selector for a message, a response
//   or a transcript, so there is no code path that could read one.
//
// It does two things, both from your own text:
//   RECALL   pull relevant memory into the box, visibly, so you see it before sending
//   CAPTURE  when explicitly enabled, offer what you typed to the configured
//            capture policy; passive inference is otherwise off

const COMPOSERS = [
  'div[contenteditable="true"]',
  "textarea",
];

const MARKER = "— coletar —";

const settings = {
  endpoint: "",
  apiKey: "",
  recall: true,
  capture: false,
  captureConsent: false,
  // Chrome owns a lot of Cmd+Shift combinations on macOS — Cmd+Shift+M is the
  // profile switcher, N is incognito, T reopens a tab. So the shortcut is
  // configurable and defaults to one Chrome does not claim. The button below is the
  // real affordance; this is for people who would rather not reach for the mouse.
  shortcut: "Ctrl+Shift+M",
};

chrome.storage.sync.get(settings, (loaded) => Object.assign(settings, loaded));
chrome.storage.onChanged.addListener((changes) => {
  for (const [key, { newValue }] of Object.entries(changes)) settings[key] = newValue;
});

// The single point at which this script touches the page. Everything else works
// on the string it returns.
function composer() {
  for (const selector of COMPOSERS) {
    const candidates = [...document.querySelectorAll(selector)];
    const visible = candidates.filter((el) => el.offsetParent !== null && !el.disabled);
    if (visible.length) return visible[visible.length - 1];
  }
  return null;
}

function readComposer(el) {
  return (el.value !== undefined ? el.value : el.innerText || "").trim();
}

function writeComposer(el, text) {
  if (el.value !== undefined) {
    el.value = text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    el.focus();
    // execCommand keeps the site's own editor state consistent; setting innerText
    // directly leaves React frameworks believing the box is still empty.
    document.execCommand("selectAll", false, null);
    document.execCommand("insertText", false, text);
  }
}

async function call(path, body) {
  if (!settings.endpoint || !settings.apiKey) return null;
  try {
    const response = await fetch(settings.endpoint.replace(/\/$/, "") + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": settings.apiKey },
      body: JSON.stringify({ ...body, surface: location.hostname }),
    });
    if (!response.ok) {
      toast(response.status === 401 ? "coletar: key rejected" : `coletar: ${response.status}`);
      return null;
    }
    return await response.json();
  } catch (err) {
    toast("coletar: unreachable");
    return null;
  }
}

function toast(message) {
  const el = document.createElement("div");
  el.textContent = message;
  el.style.cssText =
    "position:fixed;bottom:20px;right:20px;z-index:2147483647;padding:8px 14px;" +
    "border-radius:8px;background:#1c1c1c;color:#eee;font:13px system-ui;" +
    "border:1px solid #444;opacity:0.95";
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

// RECALL. Explicit, and visible: the memory goes into the box above what you wrote,
// so you read it and send it yourself. Nothing is added to a message you did not see.
async function recall() {
  if (!settings.endpoint || !settings.apiKey) {
    toast("coletar: set the server and key in the extension options");
    return;
  }
  const el = composer();
  if (!el) {
    toast("coletar: could not find the prompt box on this page");
    return;
  }
  const text = readComposer(el);
  if (!text) {
    toast("coletar: type something first, then press the button");
    return;
  }
  if (text.includes(MARKER)) {
    toast("coletar: memory already added");
    return;
  }

  // Terse: a person is about to read this in their own composer, and a
  // confidence score is not something they can act on.
  const data = await call("/v1/search", { query: text, top_k: 6, style: "terse" });
  if (!data || !data.prompt_block) {
    toast("coletar: nothing relevant");
    return;
  }
  writeComposer(el, `${data.prompt_block}\n\n${MARKER}\n\n${text}`);
  toast(`coletar: added ${data.results.length} memor${data.results.length === 1 ? "y" : "ies"}`);
}

// CAPTURE. The server policy is off by default. In collect-then-batch mode this
// queues encrypted working material; it does not make a preliminary memory claim.
let lastCaptured = "";
async function capture() {
  // The separate marker prevents an old stored `capture: true` default from being
  // treated as consent after this safer default ships.
  if (!settings.capture || !settings.captureConsent) return;
  const el = composer();
  if (!el) return;
  let text = readComposer(el);
  if (!text || text === lastCaptured) return;
  // Never send back an injected block as though the user had typed it.
  if (text.includes(MARKER)) text = text.split(MARKER).pop().trim();
  if (!text) return;
  lastCaptured = text;
  const data = await call("/v1/capture", { text });
  if (data && data.count) toast(`coletar: remembered ${data.count}`);
  else if (data && data.queued) toast("coletar: queued for extraction");
}

function matchesShortcut(event) {
  const parts = (settings.shortcut || "").split("+").map((p) => p.trim().toLowerCase());
  if (!parts.length) return false;
  const key = parts[parts.length - 1];
  if (event.key.toLowerCase() !== key) return false;
  const want = (name) => parts.includes(name);
  return (
    want("ctrl") === event.ctrlKey &&
    want("shift") === event.shiftKey &&
    want("alt") === event.altKey &&
    want("cmd") === event.metaKey
  );
}

document.addEventListener(
  "keydown",
  (event) => {
    const el = composer();
    if (!el) return;
    if (settings.recall && matchesShortcut(event)) {
      event.preventDefault();
      recall();
      return;
    }
    // Enter sends on both surfaces; read the box before the site clears it.
    if (event.key === "Enter" && !event.shiftKey && document.activeElement === el) capture();
  },
  true,
);

// The button. A hidden shortcut is a feature nobody finds, and every modifier
// combination worth having is already claimed by the browser or the page.
function mountButton() {
  if (document.getElementById("coletar-recall")) return;
  const button = document.createElement("button");
  button.id = "coletar-recall";
  button.type = "button";
  button.textContent = "✦ memory";
  button.title = "Bring your portable memory into the box (coletar)";
  button.style.cssText =
    "position:fixed;bottom:22px;right:22px;z-index:2147483646;padding:8px 14px;" +
    "border-radius:999px;background:#1c1c1c;color:#eee;font:13px system-ui;" +
    "border:1px solid #4a4a4a;cursor:pointer;opacity:0.85";
  button.addEventListener("mouseenter", () => (button.style.opacity = "1"));
  button.addEventListener("mouseleave", () => (button.style.opacity = "0.85"));
  button.addEventListener("click", (event) => {
    event.preventDefault();
    recall();
  });
  document.body.appendChild(button);
}

// These are single-page apps that rebuild the DOM as you navigate, so the button
// has to be re-mounted rather than added once.
new MutationObserver(() => mountButton()).observe(document.body, {
  childList: true,
  subtree: false,
});
mountButton();

document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest?.('button[type="submit"], button[aria-label*="end" i]');
    if (button) capture();
  },
  true,
);
