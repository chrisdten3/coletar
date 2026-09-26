const fields = ["endpoint", "apiKey", "shortcut", "recall", "capture", "automatic"];
const defaults = {
  endpoint: "",
  apiKey: "",
  shortcut: "Ctrl+Shift+M",
  recall: true,
  capture: false,
  captureConsent: false,
  automatic: false,
  automaticConsent: false,
};

chrome.storage.sync.get(defaults, (loaded) => {
  // `capture` used to default true. Absence of this new consent marker means an
  // existing value was never an informed opt-in, so do not preserve it silently.
  if (!loaded.automaticConsent) loaded.automatic = false;
  if (!loaded.captureConsent) loaded.capture = false;
  for (const f of fields) {
    const el = document.getElementById(f);
    if (el.type === "checkbox") el.checked = loaded[f];
    else el.value = loaded[f];
  }
});

document.getElementById("save").addEventListener("click", () => {
  const values = {};
  for (const f of fields) {
    const el = document.getElementById(f);
    values[f] = el.type === "checkbox" ? el.checked : el.value.trim();
  }
  try {
    const url = new URL(values.endpoint);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) throw new Error();
    if (url.username || url.password || url.search || url.hash) throw new Error();
    if (!values.apiKey) throw new Error();
    values.endpoint = values.endpoint.replace(/\/$/, "");
  } catch {
    document.getElementById("status").textContent = "Enter an HTTPS server (or localhost) and your Coleta API key.";
    return;
  }
  values.automaticConsent = values.automatic;
  values.captureConsent = values.capture;
  chrome.storage.sync.set(values, () => {
    document.getElementById("status").textContent = "Saved.";
  });
});
