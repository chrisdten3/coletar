(() => {
  "use strict";

  // ---------------------------------------------------------------------
  // Password gate. Client-side only — good enough to keep a private preview
  // link out of casual browsing and search engines, not real security.
  // ---------------------------------------------------------------------
  const PASSWORD = "byqiyas";
  const UNLOCK_KEY = "coletar_preview_unlocked";

  const gate = document.getElementById("gate");
  const site = document.getElementById("site");
  const gateInput = document.getElementById("gate-input");
  const gateError = document.getElementById("gate-error");

  function unlock() {
    try { sessionStorage.setItem(UNLOCK_KEY, "1"); } catch (_) { /* ignore */ }
    gate.hidden = true;
    site.hidden = false;
  }

  function tryUnlock() {
    if (gateInput.value === PASSWORD) {
      unlock();
    } else {
      gateError.textContent = "That's not it — try again.";
      gateInput.value = "";
      gateInput.focus();
    }
  }

  let alreadyUnlocked = false;
  try { alreadyUnlocked = sessionStorage.getItem(UNLOCK_KEY) === "1"; } catch (_) { /* ignore */ }

  if (alreadyUnlocked) {
    unlock();
  } else {
    document.getElementById("gate-submit").addEventListener("click", tryUnlock);
    gateInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") tryUnlock();
    });
  }

  // ---------------------------------------------------------------------
  // Demo: Live Sync
  // ---------------------------------------------------------------------
  const SURFACES = ["claude", "chatgpt", "local"];
  const SURFACE_LABEL = { claude: "Claude", chatgpt: "ChatGPT", local: "Local model" };

  const SAY_LINES = {
    claude: [
      "I prefer dark mode and tabs over spaces.",
      "Always answer in a formal tone for this project.",
    ],
    chatgpt: [
      "My timezone is Pacific — schedule things accordingly.",
      "I like short, direct answers, no filler.",
    ],
    local: [
      "I'm working on a Rust CLI called 'ledger'.",
      "Use fixed-point integers for anything involving money.",
    ],
  };
  const sayIndex = { claude: 0, chatgpt: 0, local: 0 };

  const syncLog = document.getElementById("sync-log");
  let syncLogStarted = false;

  function timestamp() {
    return new Date().toLocaleTimeString([], { hour12: false });
  }

  function logLine(text) {
    if (!syncLogStarted) {
      syncLog.innerHTML = "";
      syncLogStarted = true;
    }
    const line = document.createElement("div");
    line.className = "log-line";
    line.textContent = `[${timestamp()}] ${text}`;
    syncLog.appendChild(line);
    syncLog.scrollTop = syncLog.scrollHeight;
  }

  function clearPlaceholder(panel) {
    const placeholder = panel.querySelector(".surface-placeholder");
    if (placeholder) placeholder.remove();
  }

  function sayInSurface(from) {
    const text = SAY_LINES[from][sayIndex[from] % SAY_LINES[from].length];
    sayIndex[from] += 1;

    const fromPanel = document.getElementById(`surface-${from}`);
    clearPlaceholder(fromPanel);
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = text;
    fromPanel.appendChild(bubble);
    fromPanel.scrollTop = fromPanel.scrollHeight;

    logLine(`${SURFACE_LABEL[from]} wrote a memory — every other surface can read it now.`);

    const others = SURFACES.filter((s) => s !== from);
    others.forEach((surface, i) => {
      const panel = document.getElementById(`surface-${surface}`);
      window.setTimeout(() => {
        clearPlaceholder(panel);
        const chip = document.createElement("div");
        chip.className = "context-chip";
        chip.innerHTML = `<span class="tag">via coletar — from ${SURFACE_LABEL[from]}</span>${text}`;
        panel.appendChild(chip);
        panel.scrollTop = panel.scrollHeight;
        // Force layout so the fade-in transition actually plays.
        requestAnimationFrame(() => chip.classList.add("show"));
      }, 350 + i * 220);
    });
  }

  document.querySelectorAll("[data-sync-from]").forEach((btn) => {
    btn.addEventListener("click", () => sayInSurface(btn.getAttribute("data-sync-from")));
  });

  document.getElementById("sync-reset").addEventListener("click", () => {
    SURFACES.forEach((s) => {
      const panel = document.getElementById(`surface-${s}`);
      panel.innerHTML = '<span class="surface-placeholder">No messages yet.</span>';
    });
    syncLogStarted = false;
    syncLog.innerHTML = '<div class="log-line">Waiting for the first message…</div>';
    SURFACES.forEach((s) => { sayIndex[s] = 0; });
  });

  // ---------------------------------------------------------------------
  // Demo: True Migration
  // ---------------------------------------------------------------------
  const compileBtn = document.getElementById("migration-compile");
  const manifest = document.getElementById("migration-manifest");
  const scoreRing = document.getElementById("score-ring");
  const scoreValue = document.getElementById("score-value");
  const disconnectBtn = document.getElementById("migration-disconnect");
  const disconnectStatus = document.getElementById("disconnect-status");

  compileBtn.addEventListener("click", () => {
    compileBtn.disabled = true;
    const original = compileBtn.textContent;
    compileBtn.textContent = "Compiling…";

    window.setTimeout(() => {
      document.getElementById("manifest-native").textContent = "5";
      document.getElementById("manifest-reconstructed").textContent = "1";
      document.getElementById("manifest-unsupported").textContent = "0";
      scoreRing.style.setProperty("--pct", "96");
      scoreValue.textContent = "96%";
      manifest.classList.add("show");
      compileBtn.textContent = "Recompile";
      compileBtn.disabled = false;
    }, 900);
  });

  disconnectBtn.addEventListener("click", () => {
    disconnectStatus.textContent = "✅ Still works — Claude now holds everything natively.";
    disconnectStatus.classList.add("ok");
    disconnectBtn.disabled = true;
    disconnectBtn.textContent = "Disconnected";
  });

  // ---------------------------------------------------------------------
  // Demo: Selective Context
  // ---------------------------------------------------------------------
  const SURFACE_COLOR = { claude: "#c76b4a", chatgpt: "#3d9b7f", local: "#6b6fb0" };
  const SURFACE_ICON = { claude: "C", chatgpt: "G", local: "L" };

  const memories = [
    { text: "Prefers TypeScript over JavaScript", mode: "synced", surface: null },
    { text: "Internal deployment secrets layout", mode: "local", surface: "claude" },
    { text: "Timezone is Pacific", mode: "synced", surface: null },
    { text: "Personal note best kept off work tools", mode: "local", surface: "local" },
  ];

  const memoryList = document.getElementById("memory-list");

  function renderMemories() {
    memoryList.innerHTML = "";
    memories.forEach((mem, idx) => {
      const item = document.createElement("div");
      item.className = "memory-item";

      const text = document.createElement("div");
      text.className = "memory-text";
      text.textContent = mem.text;
      item.appendChild(text);

      const controls = document.createElement("div");
      controls.className = "memory-controls";

      const icons = document.createElement("div");
      icons.className = "visibility-icons";
      SURFACES.forEach((surface) => {
        const icon = document.createElement("div");
        const visible = mem.mode === "synced" || mem.surface === surface;
        icon.className = "vis-icon" + (visible ? "" : " dim");
        icon.style.background = SURFACE_COLOR[surface];
        icon.title = `${SURFACE_LABEL[surface]}${visible ? " can see this" : " cannot see this"}`;
        icon.textContent = SURFACE_ICON[surface];
        icons.appendChild(icon);
      });
      controls.appendChild(icons);

      const toggle = document.createElement("div");
      toggle.className = "locality-toggle";
      const state = document.createElement("span");
      state.className = "state " + (mem.mode === "synced" ? "synced" : "local");
      state.textContent = mem.mode === "synced" ? "Synced everywhere" : "Local only";
      toggle.appendChild(state);

      if (mem.mode === "local") {
        const select = document.createElement("select");
        SURFACES.forEach((surface) => {
          const opt = document.createElement("option");
          opt.value = surface;
          opt.textContent = SURFACE_LABEL[surface];
          if (surface === mem.surface) opt.selected = true;
          select.appendChild(opt);
        });
        select.addEventListener("click", (e) => e.stopPropagation());
        select.addEventListener("change", () => {
          mem.surface = select.value;
          renderMemories();
        });
        toggle.appendChild(select);
      }

      toggle.addEventListener("click", () => {
        mem.mode = mem.mode === "synced" ? "local" : "synced";
        if (mem.mode === "local" && !mem.surface) mem.surface = "claude";
        renderMemories();
      });
      controls.appendChild(toggle);

      item.appendChild(controls);
      memoryList.appendChild(item);
      void idx; // stable insertion order only
    });
  }

  renderMemories();
})();
