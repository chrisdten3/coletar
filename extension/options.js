const fields = ["endpoint", "apiKey", "shortcut", "recall", "capture"];
const defaults = {
  endpoint: "",
  apiKey: "",
  shortcut: "Ctrl+Shift+M",
  recall: true,
  capture: true,
};

chrome.storage.sync.get(defaults, (loaded) => {
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
  chrome.storage.sync.set(values, () => {
    document.getElementById("status").textContent = "Saved.";
  });
});
