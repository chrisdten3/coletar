// The worker holds an encrypted delivery outbox. It never reads provider pages,
// contacts providers, or replays a provider session. Uploads retain the browser's
// Origin by running in the matching, visible content script.
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
const HOSTS = new Set(["claude.ai", "chatgpt.com", "chat.openai.com"]);
const TTL = 24 * 60 * 60 * 1000;
const MAX_BYTES = 2_000_000;
let serial = Promise.resolve();
let database;
function db() {
  if (!database) database = new Promise((resolve, reject) => {
    const request = indexedDB.open("coleta-outbox", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("keys");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return database;
}
async function encryptionKey() {
  const database = await db();
  const existing = await new Promise((resolve, reject) => {
    const request = database.transaction("keys").objectStore("keys").get("outbox");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  if (existing) return existing;
  const key = await crypto.subtle.generateKey({name:"AES-GCM", length:256}, false, ["encrypt", "decrypt"]);
  await new Promise((resolve, reject) => {
    const tx = database.transaction("keys", "readwrite");
    tx.objectStore("keys").put(key, "outbox");
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
  return key;
}
function bytesToBase64(bytes) {
  let result = "";
  for (const byte of bytes) result += String.fromCharCode(byte);
  return btoa(result);
}
const base64ToBytes = (value) => Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
async function outbox(message, sender) {
  const url = new URL(sender.url || "about:blank");
  if (url.protocol !== "https:" || !HOSTS.has(url.hostname) || sender.frameId !== 0 || !sender.tab) {
    throw new Error("unsupported page");
  }
  const opts = await chrome.storage.sync.get({automatic:false, automaticConsent:false});
  if (!opts.automatic || !opts.automaticConsent) throw new Error("automatic capture is off");
  const tab = await chrome.tabs.get(sender.tab.id);
  const window = await chrome.windows.get(tab.windowId);
  if (!tab.active || !window.focused) throw new Error("page is not active");
  if (!/^[a-f0-9]{64}$/.test(message.account || "")) throw new Error("invalid account");
  const scope = `${url.hostname}:${message.account}`;
  const stored = await chrome.storage.local.get({outbox:[]});
  const records = stored.outbox.filter((r) => Date.now() - r.created < TTL);
  const dropped = stored.outbox.length - records.length;
  const aad = new TextEncoder().encode(scope);
  let queuedId;
  if (message.action === "enqueue") {
    const body = message.body;
    if (!body || !["user", "assistant"].includes(body.role) ||
        !/^[\w-]{1,128}$/.test(body.turn_id || "") ||
        typeof body.text !== "string" || !body.text.trim() || body.text.length > 100_000) {
      throw new Error("invalid or oversized turn");
    }
    const id = `${scope}:${body.turn_id}:${body.role}`;
    queuedId = id;
    if (!records.some((r) => r.id === id)) {
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = await crypto.subtle.encrypt({name:"AES-GCM", iv, additionalData:aad},
        await encryptionKey(), new TextEncoder().encode(JSON.stringify(body)));
      const record = {id, scope, created:Date.now(), iv:bytesToBase64(iv), ciphertext:bytesToBase64(new Uint8Array(ciphertext))};
      if (records.length >= 100 || JSON.stringify(records).length + JSON.stringify(record).length > MAX_BYTES) {
        throw new Error("capture queue is full");
      }
      records.push(record);
    }
  } else if (message.action === "ack") {
    const index = records.findIndex((r) => r.scope === scope && r.id === message.id);
    if (index >= 0) records.splice(index, 1);
  } else if (message.action !== "pending") throw new Error("unknown action");
  await chrome.storage.local.set({outbox:records});
  if (message.action !== "pending") return {ok:true, id:queuedId, dropped};
  const result = [];
  for (const record of records.filter((r) => r.scope === scope).slice(0, 10)) {
    const plaintext = await crypto.subtle.decrypt({name:"AES-GCM", iv:base64ToBytes(record.iv), additionalData:aad},
      await encryptionKey(), base64ToBytes(record.ciphertext));
    result.push({id:record.id, body:JSON.parse(new TextDecoder().decode(plaintext))});
  }
  return {ok:true, records:result, dropped};
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.type !== "coleta-outbox") return;
  serial = serial.then(() => outbox(message, sender))
    .then(respond, () => respond({ok:false, error:"Capture queue unavailable, disabled, full, or turn too large"}));
  return true;
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync") return;
  if ((changes.automatic && !changes.automatic.newValue) ||
      (changes.automaticConsent && !changes.automaticConsent.newValue)) {
    serial = serial.then(() => chrome.storage.local.remove("outbox")).catch(() => {});
  }
});
// Expire queue ciphertext even when the user has no supported tab open.
chrome.alarms.create("coleta-expire-outbox", {periodInMinutes:1});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== "coleta-expire-outbox") return;
  serial = serial.then(async () => {
    const stored = await chrome.storage.local.get({outbox:[]});
    await chrome.storage.local.set({outbox:stored.outbox.filter((r) => Date.now() - r.created < TTL)});
  }).catch(() => {});
});
