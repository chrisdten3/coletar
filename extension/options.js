const fields = ["endpoint", "apiKey", "shortcut", "recall", "capture"];
const defaults = {
  endpoint: "",
  apiKey: "",
  shortcut: "Ctrl+Shift+M",
  recall: true,
  capture: false,
  captureConsent: false,
};

chrome.storage.sync.get(defaults, (loaded) => {
  // `capture` used to default true. Absence of this new consent marker means an
  // existing value was never an informed opt-in, so do not preserve it silently.
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
  values.captureConsent = values.capture;
  chrome.storage.sync.set(values, () => {
    document.getElementById("status").textContent = "Saved.";
  });
});
