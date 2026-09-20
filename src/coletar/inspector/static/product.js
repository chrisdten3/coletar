/* Local product UI. All graph writes go through /web-api and its event-producing Store. */
"use strict";
const $ = (s, root = document) => root.querySelector(s);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
// Vendored Lucide (shadcn's interface icon family) and brand assets. See ICONS.md.
const iconSprite =
  "/static/icons.svg?v=" +
  new URL(document.currentScript.src).searchParams.get("v");
const paths = {
  library: "layers",
  capture: "inbox",
  review: "clipboard-check",
  audit: "history",
  migrate: "arrow-right-from-line",
  surfaces: "panels-top-left",
  settings: "sliders-horizontal",
  search: "search",
  plus: "plus",
  arrow: "arrow-right",
  back: "arrow-left",
  check: "check",
  lock: "lock",
  upload: "upload",
  download: "download",
  close: "x",
  info: "info",
  external: "arrow-up-right",
  menu: "menu",
  chevron: "chevron-down",
  file: "file-text",
  shield: "shield-check",
  network: "network",
  sparkles: "sparkles",
  reset: "rotate-ccw",
  cpu: "cpu",
  code: "code-xml",
  dot: "circle-dot",
};
const icon = (name) =>
  `<svg class="icon lucide" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><use href="${iconSprite}#${paths[name] || paths.info}"/></svg>`;
const marks = {
  claude: "brand-claude",
  claude_code: "brand-claude_code",
  chatgpt: "brand-chatgpt",
  local: "cpu",
  markdown: "brand-markdown",
  coletar: "circle-dot",
  ollama: "brand-ollama",
};
marks.desktop = marks.claude;
marks.code = marks.claude_code;

marks.qwen = marks.local;
marks.gpt = marks.chatgpt;
marks.openai = marks.chatgpt;
marks.all = marks.coletar;
/* Which mark stands for a surface, model or destination id. */
const markFor = (name) => {
  const key = String(name || "").toLowerCase();
  if (marks[key]) return key;
  if (key.includes("claude_code") || key.includes("claude-code"))
    return "claude_code";
  if (key.includes("claude") || key.includes("opus") || key.includes("sonnet"))
    return "claude";
  if (key.includes("gpt") || key.includes("openai")) return "chatgpt";
  if (key.includes("markdown") || key.includes("obsidian")) return "markdown";
  if (key.includes("local") || key.includes("ollama") || key.includes("qwen"))
    return "local";
  return "coletar";
};
const mark = (name, label = "") => {
  const key = markFor(name);
  return `<svg class="mark mark-${key}" viewBox="0 0 24 24" ${label ? `role="img" aria-label="${esc(label)}"` : 'aria-hidden="true"'}><use href="${iconSprite}#${marks[key]}"/></svg>`;
};
/* A surface named in running text, with its mark. */
const tag = (name, text) =>
  `<span class="ptag" data-surface="${esc(markFor(name))}">${mark(name)}<span>${esc(text ?? name)}</span></span>`;
const date = (value) =>
  value
    ? new Date(value).toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      })
    : "—";
const time = (value) =>
  value
    ? new Date(value).toLocaleString("en-GB", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
const number = (value) =>
  new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value || 0);
const getPrefs = () => {
  try {
    return JSON.parse(localStorage.getItem("coletar-design-prefs-v1") || "{}");
  } catch {
    return {};
  }
};
let prefs = getPrefs();
let connections = null;
let state,
  surface = "all",
  filter = "all",
  query = "",
  reviewTab = "corrections",
  destination = "chatgpt",
  manifest = null,
  previewing = false,
  previewFailed = null,
  auditResult = null;
let auditAt = "2026-03-03",
  auditValid = "2026-01-01",
  importReport = null,
  toastTimer;
// The Library's sections, fetched alongside state. Null until the first load and
// after any write, because a memory added or retired changes what belongs where.
let libraryIndex = null;
// Sections the reader has collapsed, and duplicate clusters they have opened.
const collapsed = new Set();
const expandedClusters = new Set();
const savePrefs = () => {
  try {
    localStorage.setItem("coletar-design-prefs-v1", JSON.stringify(prefs));
  } catch {
    toast("Browser preferences could not be saved.", true);
  }
};
const activeObjects = () => {
  const replaced = new Set(state.objects.map((o) => o.supersedes));
  return state.objects.filter(
    (o) => !o.retired_at && !replaced.has(o.id) && o.type !== "episode",
  );
};
const objById = (id) => state.objects.find((o) => o.id === id);
const isRestricted = (o) => o.locality.mode === "local_only";
const canRead = (o, s) =>
  !isRestricted(o) ||
  o.locality.surfaces.includes(s === "claude_code" ? "claude" : s);
const pending = () =>
  state.objects.filter(
    (o) => o.type === "episode" && !o.retired_at && o.pending,
  );
const reachLabel = (o) =>
  isRestricted(o) ? `${o.locality.surfaces.join(", ")} only` : "";
const conflictPairs = () =>
  (state.conflicts || []).filter(
    ([a, b]) => !objById(a)?.retired_at && !objById(b)?.retired_at,
  );
const category = (o) =>
  conflictPairs().some((pair) => pair.includes(o.id))
    ? "conflicts"
    : o.supersedes
      ? "corrections"
      : o.confidence < 0.7
        ? "low"
        : "new";
const reviewObjects = () =>
  activeObjects().filter((o) => state.unreviewed.includes(o.id));
const statusNotice = () =>
  state.can_compile
    ? `<div class="notice">${icon("check")} All eligible objects have been reviewed. Compile is unlocked.</div>`
    : `<div class="notice warning">${icon("lock")} Nothing compiles to another surface until every object has been seen once. <b>${state.unreviewed.length} remaining.</b> <a href="#/review">Review now</a></div>`;
async function api(path, body, method) {
  const response = await fetch("/web-api" + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: await coletaAuth.authHeaders(
      body === undefined ? {} : { "Content-Type": "application/json" },
    ),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    let problem;
    try {
      problem = await response.json();
    } catch {
      problem = { detail: "Request failed. Please try again." };
    }
    // A session that expired mid-visit is not a failed form submission, and
    // showing it as a toast over a workspace the server will no longer serve is
    // worse than useless. `handleAuthFailure` paints the real state and takes over.
    if (coletaAuth.handleAuthFailure(response.status, problem.detail)) {
      throw new Error(problem.detail || "Sign in again.");
    }
    throw new Error(
      typeof problem.detail === "string"
        ? problem.detail
        : "Check the form values and try again.",
    );
  }
  return response.json();
}
async function refresh() {
  state = await api("/state");
  if (state.hosted) connections = await api("/connections");
  manifest = null;
  libraryIndex = null;
  graphData = null;
}
function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "visible" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = ""), 5000);
}
function empty(title, description, action = "") {
  return `<div class="empty">${icon("library")}<h2>${title}</h2><p>${description}</p>${action}</div>`;
}
function meta(o) {
  return `<div class="memory-meta"><span class="badge">${esc(o.kind || o.type)}</span>${o.scope.id ? `<span class="badge">${esc(o.scope.id)}</span>` : ""}${isRestricted(o) ? `<span class="badge reach">${esc(reachLabel(o))}</span>` : ""}<span class="via">via ${tag(o.provenance.provider)} · ${esc(o.extraction_method)} · conf ${Number(o.confidence).toFixed(2)}</span>${o.supersedes ? '<span class="green">supersedes 1</span>' : ""}</div>`;
}
function card(o) {
  return `<a class="memory ${isRestricted(o) ? "restricted" : ""}" href="#/object/${encodeURIComponent(o.id)}"><p>${esc(o.content)}</p>${meta(o)}</a>`;
}
const brand =
  '<a class="brand" href="#/home">coleta</a>';
function accountChip() {
  const user = coletaAuth.session.user;
  if (!user) {
    // Local development, where there is no sign-in by design.
    return `<a class="account" href="#/settings"><span class="avatar">${state.sample ? "DS" : state.hosted ? "PW" : "LW"}</span>${state.sample ? "Design sample workspace" : isPublic() ? "Public workspace" : state.hosted ? "Hosted workspace" : "Local workspace"}</a>`;
  }
  const email = user.primaryEmailAddress?.emailAddress || "";
  const name = user.fullName || email || "Your workspace";
  const initials =
    (user.firstName?.[0] || email[0] || "?").toUpperCase() +
    (user.lastName?.[0] || "").toUpperCase();
  return `<div class="account-row"><a class="account" href="#/settings" title="${esc(email)}"><span class="avatar">${esc(initials)}</span>${esc(name)}</a><button type="button" class="quiet small" data-sign-out title="Sign out">${icon("migrate")}<span class="sr-only">Sign out</span></button></div>`;
}
function shell(title, body, actions = "") {
  const counts = {
    Library: activeObjects().length,
    "Capture queue": pending().length,
    Review: state.unreviewed.length,
  };
  const links = [
    ["library", "Library"],
    ["capture", "Capture queue"],
    ["review", "Review"],
    ["audit", "History"],
    ["surfaces", "Connections"],
    ["migrate", "Export & migrate"],
    ["settings", "Settings"],
  ];
  title =
    { Audit: "History", Migrate: "Export & migrate", Surfaces: "Connections" }[
      title
    ] || title;
  const total = Object.values(state.usage).reduce((a, b) => a + b, 0);
  return `<aside class="sidebar">${brand}<div class="rail-label eyebrow">Your workspace</div><nav aria-label="Workspace">${links.map(([route, label]) => `<a class="nav-link ${title === label || (title === "Object" && label === "Library") || (title === "Get set up" && label === "Connections") ? "active" : ""}" ${title === label ? 'aria-current="page"' : ""} href="#/${route}">${icon(route)}<span>${label}</span>${counts[label] !== undefined ? `<span class="count">${counts[label]}</span>` : ""}</a>`).join("")}</nav><div class="sidebar-bottom"><div class="row between"><span>Context served</span><span class="mono">${number(total)} tokens</span></div><div class="progress"><span style="width:${Math.min((total / 2000000) * 100, 100)}%"></span></div>${accountChip()}<span class="prototype-label">${isPublic() ? "Anyone with the link can read and change this" : state.hosted ? "Hosted preview" : "Local prototype"} · ${state.sample ? "synthetic examples" : "your configured store"}</span></div></aside><div class="workspace"><header class="topbar"><div class="workspace-breadcrumb">Workspace <span>/</span> ${title}</div><div class="top-actions">${actions}<button class="command-trigger" data-command aria-label="Quick search">${icon("search")}<span>Quick search</span><kbd>⌘ K</kbd></button></div></header><main id="content"><div class="workspace-heading"><div><span class="eyebrow">${state.sample ? "Design sample / synthetic data" : isPublic() ? "Public workspace" : state.hosted ? "Hosted workspace" : "Local workspace"}</span><h1>${title === "Library" ? "A place for what matters." : title === "Object" ? "Context inspector" : title}</h1></div><span class="heading-symbol" aria-hidden="true">${icon(title === "Library" ? "library" : "audit")}</span></div>${body}</main></div>`;
}
/* The deployment reports this; the app does not infer it from being hosted. */
const isPublic = () => Boolean(connections?.public_workspace);
function surfaceTabs() {
  return `<span class="small muted">Viewing as</span><div class="surface-tabs" aria-label="Preview surface reach">${[
    ["all", "All surfaces"],
    ["claude", "Claude"],
    ["chatgpt", "ChatGPT"],
    ["local", "Local"],
  ]
    .map(
      ([s, l]) =>
        `<button data-surface="${s}" class="${surface === s ? "active" : ""}" aria-pressed="${surface === s}">${mark(s)}<span>${l}</span></button>`,
    )
    .join("")}</div>`;
}
/* --- The Library as sections ------------------------------------------------
   A flat list sorted by when something landed is the right view of forty objects
   and the wrong view of several thousand. Grouping is derived from edges the
   extractor already wrote, never inferred: a heading with no provenance is a claim
   the Context Inspector could not explain. */

let libraryLoading = false;
function loadLibraryIndex() {
  if (libraryLoading) return;
  libraryLoading = true;
  api("/library")
    .then((data) => {
      libraryIndex = data;
      render();
    })
    .catch(() => {
      // A failed grouping must not cost the reader their Library. The flat list
      // is the fallback, and it is the view that existed before sections did.
      libraryIndex = { groups: [], loose: null, total: 0, failed: true };
      render();
    })
    .finally(() => (libraryLoading = false));
}

/* Near-identical restatements are the single biggest reason the real corpus reads
   as noise — one entity had thirty-nine facts that were mostly the same sentence.
   Collapsing them is honest about what the graph holds rather than hiding it: the
   cluster says how many, and opens. */
const dupKey = (o) =>
  o.content
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 8)
    .join(" ");

function cluster(objects) {
  const seen = new Map();
  for (const o of objects) {
    const key = dupKey(o);
    if (!seen.has(key)) seen.set(key, []);
    seen.get(key).push(o);
  }
  return [...seen.values()];
}

/* One line per memory rather than a card. Roughly three times the density, which
   is the difference between scanning a section and scrolling one. */
function row(o) {
  const marks = [
    o.kind || o.type,
    o.scope?.id || "",
    isRestricted(o) ? "restricted" : "",
    state.unreviewed.includes(o.id) ? "unreviewed" : "",
  ].filter(Boolean);
  return `<a class="lib-row ${isRestricted(o) ? "restricted" : ""}" href="#/object/${encodeURIComponent(o.id)}"><span class="lib-text">${esc(o.content)}</span><span class="lib-marks">${marks
    .map((m) => `<span class="lib-mark">${esc(m)}</span>`)
    .join("")}</span></a>`;
}

function clusterBlock(group) {
  if (group.length === 1) return row(group[0]);
  const key = group[0].id;
  const open = expandedClusters.has(key);
  const rest = group.length - 1;
  return `<div class="lib-cluster ${open ? "open" : ""}">${row(group[0])}<button type="button" class="lib-more" data-cluster="${esc(key)}" aria-expanded="${open}">${open ? "Hide" : `${rest} similar`}</button>${open ? group.slice(1).map(row).join("") : ""}</div>`;
}

function section(label, kind, description, objects) {
  const key = `${kind}:${label}`;
  const shut = collapsed.has(key);
  return `<section class="lib-section ${shut ? "shut" : ""}"><button type="button" class="lib-head" data-section="${esc(key)}" aria-expanded="${!shut}"><span class="lib-head-label">${esc(label)}</span>${description ? `<span class="lib-head-desc">${esc(description)}</span>` : ""}<span class="lib-head-count">${objects.length}</span></button>${
    shut ? "" : `<div class="lib-rows">${cluster(objects).map(clusterBlock).join("")}</div>`
  }</section>`;
}

/* The grouped body, or null when there is nothing to group by — a workspace with
   no entities and no projects should not be given a single section called
   "Everything". */
function groupedLibrary(filtered) {
  if (!libraryIndex || libraryIndex.failed) return null;
  const visible = new Map(filtered.map((o) => [o.id, o]));
  const sections = [];
  for (const g of libraryIndex.groups) {
    const members = g.object_ids.map((id) => visible.get(id)).filter(Boolean);
    if (members.length) sections.push(section(g.label, g.kind, g.description, members));
  }
  const loose = (libraryIndex.loose || []).map((id) => visible.get(id)).filter(Boolean);
  if (!sections.length && !loose.length) return null;
  if (loose.length) {
    sections.push(
      section("Not connected to anything yet", "loose", "", loose),
    );
  }
  return sections.join("");
}

/* Grouped view body: its own loading and empty states, because the sections
   arrive on a second request and an empty screen while they do reads as a bug. */
function groupedBody(filtered) {
  if (!libraryIndex) {
    loadLibraryIndex();
    return '<p class="atlas-status">Sorting your context…</p>';
  }
  const grouped = groupedLibrary(filtered);
  if (grouped) return grouped;
  if (libraryIndex.failed) {
    return (
      '<p class="atlas-status">Sections could not be loaded; showing everything in order.</p>' +
      filtered.sort((a, b) => b.updated_at.localeCompare(a.updated_at)).map(row).join("")
    );
  }
  return empty(
    filtered.length ? "Nothing to group yet" : "No matching context",
    filtered.length
      ? "These memories do not mention an entity or belong to a project yet. Import more history and sections appear."
      : "Try a different search or filter.",
    '<button data-library-view-set="list">Show them as a list</button>',
  );
}

function library() {
  let objects = activeObjects();
  const restricted = objects.filter(isRestricted).length;
  const filtered = objects.filter(
    (o) =>
      (surface === "all" || canRead(o, surface)) &&
      (!query || o.content.toLowerCase().includes(query.toLowerCase())) &&
      (filter === "all" ||
        filter === (o.kind || o.type) ||
        filter === o.scope.id ||
        (filter === "restricted" && isRestricted(o)) ||
        (filter === "unreviewed" && state.unreviewed.includes(o.id))),
  );
  const chips = [
    ["all", "All"],
    [
      "preference",
      `Preferences ${objects.filter((o) => o.kind === "preference").length}`,
    ],
    ["fact", `Facts ${objects.filter((o) => o.kind === "fact").length}`],
    ["decision", "Decisions"],
    ...Array.from(new Set(objects.map((o) => o.scope.id).filter(Boolean))).map(
      (p) => [p, p],
    ),
    ["restricted", `Restricted ${restricted}`],
    ["unreviewed", `Unreviewed ${state.unreviewed.length}`],
  ];
  return shell(
    "Library",
    `<form id="search-form" class="search-row"><div class="search-box">${icon("search")}<input id="search" name="q" type="search" aria-label="Search your context" placeholder="Search your context" value="${esc(query)}"></div><button class="quiet" type="submit">Search</button><button type="button" data-action="add">${icon("plus")} Add memory</button></form><div class="chips">${chips.map(([v, l]) => `<button class="chip ${filter === v ? "active" : ""}" data-filter="${esc(v)}" aria-pressed="${filter === v}">${esc(l)}</button>`).join("")}</div><div class="library-view-switch"><div class="view-buttons" role="group" aria-label="Library view"><button data-library-view="grouped" aria-pressed="${libraryView === "grouped"}" class="${libraryView === "grouped" ? "active" : ""}">Grouped</button><button data-library-view="list" aria-pressed="${libraryView === "list"}" class="${libraryView === "list" ? "active" : ""}">List</button><button data-library-view="atlas" aria-pressed="${libraryView === "atlas"}" class="${libraryView === "atlas" ? "active" : ""}">Atlas</button></div><span class="small muted">Your knowledge, connected.</span></div><div class="list-summary"><span>${filtered.length} objects · ${restricted} restricted · ${state.unreviewed.length} awaiting review${surface !== "all" ? ` · ${objects.filter((o) => !canRead(o, surface)).length} withheld from this preview` : ""}</span><span>sorted by last written</span></div><div class="library-collection ${libraryView === "atlas" ? "atlas-view" : ""}${libraryView === "grouped" ? " grouped-view" : ""}">${
      libraryView === "atlas"
        ? atlasGraph()
        : libraryView === "grouped"
        ? groupedBody(filtered)
        : filtered
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        .map(card)
        .join("") ||
      empty(
        objects.length ? "No matching context" : "Your library starts here",
        objects.length
          ? "Try a different search or filter."
          : "Add a memory, import your history, or explore the design examples.",
        objects.length
          ? '<button data-action="clear">Clear filters</button>'
          : '<button class="primary" data-action="sample">Load design examples</button> <a class="btn" href="#/surfaces">Import your history</a>',
      )
    }</div><p class="caption">Withheld objects are recorded in a compile manifest, never dropped.</p>`,
    surfaceTabs(),
  );
}
function humanEvent(e) {
  if (e.detail?.field === "locality") return "Reach changed";
  return (
    {
      "object.created": "Created",
      "object.reviewed": "Reviewed by you",
      "object.updated": "Edited",
      "object.retired": "Retired",
      "object.rescoped": "Scope changed",
      "connector.write": "Captured or imported",
      "object.merged": "Merged",
      "object.superseded": "Superseded",
    }[e.type] || e.type
  );
}
function detail(id) {
  const o = objById(id);
  if (!o)
    return shell(
      "Object",
      empty(
        "Object not found",
        "This object is not in the current workspace.",
        '<a class="btn" href="#/library">Back to Library</a>',
      ),
    );
  const events = state.events
    .filter((e) => e.object_id === id)
    .sort((a, b) => a.at.localeCompare(b.at));
  const allowed = ["claude", "chatgpt", "local"].filter((s) => canRead(o, s));
  const sourceIds = o.provenance.source_object_ids || [];
  return shell(
    "Object",
    `<div class="row mono muted mb"><a href="#/library">← Library</a> ${esc(o.id)}</div><article class="panel flush"><div class="panel-head"><p>${esc(o.content)}</p>${meta(o)}<div class="row wrap small muted mt"><span class="eyebrow">In force · UTC</span><span class="badge">${date(o.valid_from)}</span> → <span class="badge">${date(o.valid_until)}</span><span>${o.valid_until ? "after which this stops being retrieved" : "No end date set"}</span></div></div><div class="object-grid"><section><div class="eyebrow"><span class="green">←</span> Lineage · read-only</div><ol class="timeline">${events.map((e) => `<li><strong>${humanEvent(e)}</strong><span class="mono">${time(e.at)} · ${esc(e.actor)}${e.detail?.design_sample ? " · design example" : ""}${e.detail?.field === "locality" ? ` · ${esc(e.detail.to)}` : ""}</span></li>`).join("") || "<li>No recorded events in this window.</li>"}</ol><p class="mono muted">Origin: ${esc(o.provenance.origin_type)} · confidence ${o.provenance.confidence.toFixed(2)}</p>${o.provenance.note ? `<p class="small muted">${esc(o.provenance.note)}</p>` : ""}${sourceIds.length ? `<h3>Source objects</h3>${sourceIds.map((s) => (objById(s) ? `<a class="mono" href="#/object/${encodeURIComponent(s)}">${esc(s)}</a>` : `<p class="mono muted">${esc(s)} · external source ID</p>`)).join("")}` : ""}<p class="caption">Oldest first, because a history read newest-first is a list of surprises.</p></section><section><div class="eyebrow"><span class="green">→</span> Reach · editable</div><form id="reach-form" data-id="${esc(id)}" class="mt">${[
      ["claude", "Claude · web, Desktop & Code"],
      ["chatgpt", "ChatGPT"],
      ["local", "Local model"],
    ]
      .map(
        ([s, l]) =>
          `<label class="reach-row ${allowed.includes(s) ? "" : "withheld"}"><span class="mono">${mark(s)} ${esc(l)}</span><span class="badge ${allowed.includes(s) ? "ok" : "reach-off"}">${allowed.includes(s) ? "may read" : "withheld"}</span><input class="switch" name="surfaces" type="checkbox" value="${s}" aria-label="${l} may read" ${allowed.includes(s) ? "checked" : ""} ${o.retired_at || o.type === "episode" ? "disabled" : ""}></label>`,
      )
      .join(
        "",
      )}<div class="notice warning subtle mt">Withheld from ${3 - allowed.length} of 3 provider policies. Claude Code shares Claude’s policy in the current backend.</div><div class="row wrap"><button class="primary" ${o.retired_at || o.type === "episode" ? "disabled" : ""}>${icon("check")} Save reach</button>${o.type !== "episode" ? `<button type="button" class="quiet" data-action="edit" data-id="${esc(id)}" ${o.retired_at ? "disabled" : ""}>Edit memory</button><button type="button" class="quiet danger" data-action="retire" data-id="${esc(id)}" ${o.retired_at ? "disabled" : ""}>${o.retired_at ? "Retired" : "Retire"}</button>` : ""}</div></form>${state.unreviewed.includes(id) ? `<button class="wide mt" data-action="review-one" data-id="${esc(id)}">${icon("check")} Mark reviewed</button>` : ""}</section></div></article>`,
    '<span class="small muted">Owner view · all provenance visible</span>',
  );
}
function review() {
  const objects = reviewObjects();
  const counts = { new: 0, corrections: 0, conflicts: 0, low: 0 };
  objects.forEach((o) => counts[category(o)]++);

  const visible = objects
    .filter((o) => category(o) === reviewTab)
    .filter(
      (o, i, list) =>
        reviewTab !== "conflicts" ||
        !list
          .slice(0, i)
          .some((p) =>
            conflictPairs().some(
              (pair) => pair.includes(o.id) && pair.includes(p.id),
            ),
          ),
    );
  counts.conflicts = conflictPairs().filter((pair) =>
    pair.some((id) => state.unreviewed.includes(id)),
  ).length;
  return shell(
    "Review",
    `${statusNotice()}<div class="stat-grid">${[
      ["new", "new, high confidence"],
      ["corrections", "corrections & supersessions"],
      ["conflicts", "conflicts"],
      ["low", "low confidence"],
    ]
      .map(
        ([v, l]) =>
          `<button class="stat-card ${reviewTab === v ? "active" : ""}" data-review-tab="${v}"><b>${counts[v]}</b><span>${l}</span></button>`,
      )
      .join(
        "",
      )}</div><div class="list-summary"><span>Reviewing ${visible.length} ${reviewTab === "low" ? "low-confidence objects" : reviewTab}</span><span>review what changed</span></div>${
      visible
        .map((o) => {
          const pair = conflictPairs().find((p) => p.includes(o.id));
          const conflict = pair
            ? objById(pair.find((id) => id !== o.id))
            : null;
          const before = conflict || objById(o.supersedes);
          return `<article class="panel review-card"><div class="memory-meta"><span class="badge">${conflict ? "conflict" : before ? "supersession" : esc(o.kind || o.type)}</span><span>${before ? "both statements are preserved in the graph" : "via " + esc(o.provenance.provider) + " · confidence " + o.confidence.toFixed(2)}</span></div>${before ? `<div class="comparison"><div class="${conflict ? "" : "old"}"><p>${esc(before.content)}</p><span class="mono muted">via ${esc(before.provenance.provider)} · ${date(before.created_at)} · conf ${before.confidence.toFixed(2)}</span></div><div class="new"><p>${esc(o.content)}</p><span class="mono muted">via ${esc(o.provenance.provider)} · ${date(o.created_at)} · conf ${o.confidence.toFixed(2)}</span></div></div>` : `<p class="mt">${esc(o.content)}</p>${meta(o)}`}<div class="row wrap mt">${conflict ? `<button data-resolve="${esc(o.id)}" data-reject="${esc(conflict.id)}">Keep right</button><button data-resolve="${esc(conflict.id)}" data-reject="${esc(o.id)}">Keep left</button>` : ""}<button class="primary" data-action="review-one" data-id="${esc(o.id)}">${icon("check")} ${conflict ? "Keep both statements" : before ? "Accept the newer statement" : "Accept memory"}</button><button data-action="edit" data-id="${esc(o.id)}">Edit before accepting</button><button class="quiet" data-action="retire" data-id="${esc(o.id)}">Retire</button><a class="quiet btn" href="#/object/${encodeURIComponent(o.id)}">View provenance</a></div></article>`;
        })
        .join("") ||
      empty(
        objects.length ? "Nothing in this category" : "You’re all caught up",
        objects.length
          ? "No unreviewed objects in this category. Choose another group."
          : "Your context is reviewed and ready to compile.",
        '<a class="btn" href="#/migrate">Go to Migrate</a>',
      )
    }`,
    `<button data-action="review-visible" ${visible.length ? "" : "disabled"}>${icon("check")} Accept this group</button>`,
  );
}
function capture() {
  const episodes = pending();
  return shell(
    "Capture queue",
    `<div class="row between mb"><div><h2>Captured, not yet remembered.</h2><p class="muted">Your submitted turns are kept as encrypted source material until extraction runs.</p></div><span class="badge">${episodes.length} pending</span></div>${episodes.map((o) => `<article class="panel mb"><div class="row between"><h3>${tag(o.provenance.provider)} <span class="badge">awaiting extraction</span></h3><span class="mono muted">${time(o.created_at)}</span></div><p class="capture-content">${esc(o.content)}</p><div class="row between wrap"><span class="capture-lock">${icon("lock")} Encrypted at rest · only your submitted turn</span><a class="btn" href="#/object/${encodeURIComponent(o.id)}">Inspect source</a></div></article>`).join("") || empty("The capture queue is clear", "Submitted turns will appear here when consented capture is enabled.", '<a class="btn" href="#/surfaces">Set up a surface</a>')}<div class="columns mt"><section class="panel"><span class="eyebrow">What happens next</span><h3 class="mt">Capture now. Judge later.</h3><p class="muted">The configured background worker extracts durable context, grounds it in the source turn, and records its provenance. ${connections?.capture_enabled ? "OpenAI receives candidate turns only; stored memories are not sent. Run a batch below or wait for the daily schedule." : "Extraction does not run in this page."}</p>${connections?.capture_enabled ? '<button class="primary mt" data-action="process-captures">Process pending turns with OpenAI</button>' : ""}<a href="#/review">Open the review queue →</a></section><section class="panel"><span class="eyebrow">Your control</span><h3 class="mt">Only turns you submit.</h3><p class="muted">coleta does not read assistant replies, other conversations, or background tabs. Capture requires explicit consent in the extension.</p><a href="#/surfaces">Manage surfaces →</a></section></div>`,
  );
}
/* The strip is a fixed five-stage legend, so the marker is placed by where the
   record time falls between the earliest event and now — not by pixel guesswork. */
function asOfOffset() {
  const events = state.events
    .map((e) => Date.parse(e.at))
    .filter(Number.isFinite);
  if (!events.length || !auditResult) return 50;
  const first = Math.min(...events);
  const last = Math.max(Math.max(...events), Date.now());
  const at = Date.parse(auditResult.at);
  if (!(last > first)) return 50;
  // Kept off the ends so the marker's label never collides with a stage name.
  return Math.min(Math.max(((at - first) / (last - first)) * 100, 10), 90);
}
function audit() {
  const snapshots = auditResult?.objects || [];
  const changed = snapshots.filter((o) => {
    const current = objById(o.id);
    return (
      current &&
      (current.content !== o.content ||
        JSON.stringify(current.locality) !== JSON.stringify(o.locality) ||
        current.retired_at ||
        state.objects.some((n) => n.supersedes === o.id))
    );
  });
  const traces = state.events
    .filter((e) => e.type === "retrieval.trace")
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return shell(
    "Audit",
    `<form id="audit-form" class="panel date-form"><label><span class="eyebrow">As of — record time</span><input aria-label="Record time" name="at" type="date" value="${auditAt}" required><p class="small">when the graph was asked</p></label><label><span class="eyebrow">In force — valid time</span><input aria-label="Valid time" name="valid" type="date" value="${auditValid}" required><p class="small">when the fact itself applied</p></label><button class="primary">${icon("audit")} Run query</button></form><div class="audit-heading">${auditResult ? `What the graph believed on ${date(auditResult.at)}, about facts in force on ${date(auditResult.valid)}. ${changed.length} ${changed.length === 1 ? "object differs" : "objects differ"} from today.` : "Two dates. One explainable history."}</div><div class="panel event-strip">${auditResult ? `<span class="as-of" style="left:${asOfOffset()}%"><i>as of ${esc(date(auditResult.at))}</i></span>` : ""}${[
      ["written", "green"],
      ["reviewed", "green"],
      ["edited", "green"],
      ["reach restricted", "amber"],
      ["superseded", "green"],
    ]
      .map(([x, tone]) => `<span class="${tone}-dot">${x}</span>`)
      .join(
        "",
      )}</div><div class="columns asymmetric"><section><h2>What changed since</h2>${
      auditResult
        ? changed.length
          ? changed
              .map((o) => {
                const now = objById(o.id),
                  replacement = state.objects.find(
                    (n) => n.supersedes === o.id,
                  );
                return `<div class="inset change"><span class="badge">then</span> <span class="mono muted">${date(auditResult.at)}</span><p>${esc(o.content)}</p><span class="mono green">↓ ${replacement ? "superseded; no longer retrieved" : now.retired_at ? "retired; history retained" : JSON.stringify(now.locality) !== JSON.stringify(o.locality) ? "reach changed" : "corrected in place"}</span>${replacement || now.content !== o.content ? `<p>${esc((replacement || now).content)}</p>` : ""}</div>`;
              })
              .join("")
          : empty(
              "No differences in this snapshot",
              "Facts at the selected dates match the current state, or no facts existed yet.",
            )
        : `<div class="inset"><p>Run a query to reconstruct the graph from its revision log.</p><span class="mono muted">Record dates are interpreted at the end of the selected UTC day.</span></div>`
    }${auditResult ? `<h2 class="mt">In force at that time <span class="muted">${snapshots.length}</span></h2>${snapshots.map(card).join("")}` : ""}</section><section><h2>Who has read this</h2><p class="muted">Every read is logged, so “which assistant has seen this fact” is answerable rather than inferred. Query text appears only for traces whose caller opted in; the rest show a digest.</p><div class="panel table-wrap"><table><thead><tr><th>Surface</th><th>Read</th><th>In answer to</th></tr></thead><tbody>${
      traces
        .slice(0, 12)
        .map((e) => {
          const returned = (e.detail.returned_ids || []).length;
          // A surface that asked and got nothing back is a reach decision, not a
          // gap in the log; the reference shows it as an attempt.
          const withheld = !returned && e.detail.withheld;
          return `<tr class="${withheld ? "withheld-row" : ""}"><td>${tag(e.detail.surface)}</td><td class="mono">${withheld ? "—" : time(e.at)}</td><td>${withheld ? `<span class="amber mono">withheld — ${esc(e.detail.withheld)} attempts, 0 reads</span>` : `${e.detail.query_text ? `<span class="quoted">${esc(e.detail.query_text)}</span>` : `<span class="mono muted">digest ${esc(e.detail.query_digest || "—")}</span>`} <span class="mono muted">· ${returned} returned</span>`}</td></tr>`;
        })
        .join("") ||
      '<tr><td colspan="3" class="muted">No retrievals recorded in this workspace.</td></tr>'
    }</tbody></table></div><button class="wide mt" data-action="audit-export" ${auditResult ? "" : "disabled"}>${icon("download")} Export audit snapshot</button><p class="caption">JSON snapshot covering both selected time axes. This prototype export is not cryptographically signed. Showing the latest 2,000 workspace events.</p></section></div>`,
  );
}
/* The reference shows Migrate with a score already on it. Compute it from the
   real compiler the moment the gate allows, instead of making the user ask. */
function autoPreview() {
  if (
    manifest ||
    previewing ||
    !state.can_compile ||
    destination === "markdown"
  )
    return;
  // One attempt per destination. A preview that failed must not be retried on
  // every render; the explicit Preview button stays available.
  if (previewFailed === destination || !activeObjects().length) return;
  previewing = true;
  api("/compile/preview", { destination })
    .then((report) => {
      manifest = report;
      previewFailed = null;
      if (location.hash.startsWith("#/migrate")) render();
    })
    .catch(() => {
      previewFailed = destination;
    })
    .finally(() => {
      previewing = false;
    });
}
function migrate() {
  autoPreview();
  const destinations = [
    [
      "chatgpt",
      "ChatGPT",
      "Custom GPT package",
      "you upload it in GPT Builder",
    ],
    [
      "claude",
      "Claude",
      "Project + memory import",
      "you import it into Claude",
    ],
    [
      "local",
      "Local model",
      "Ollama Modelfile",
      "runs independently of coleta",
    ],
    [
      "markdown",
      "Markdown",
      "Obsidian vault",
      "owner export · all reviewed context",
    ],
  ];
  const entries = manifest
    ? [
        ...manifest.entries.map((e) => ({ ...e, status: e.fidelity })),
        ...manifest.withheld.map((e) => ({ ...e, status: "withheld" })),
      ]
    : activeObjects().map((o) => ({
        source_id: o.id,
        status:
          destination === "markdown"
            ? "owner export"
            : canRead(o, destination)
              ? "awaiting compile"
              : "withheld",
      }));
  const score = manifest?.score;
  return shell(
    "Migrate",
    `<div class="destination-grid">${destinations.map(([d, l, sub, hint]) => `<button class="destination ${destination === d ? "active" : ""}" data-destination="${d}"><b>${mark(d)}${l}</b><span>${sub}</span><span class="green">${hint}</span></button>`).join("")}</div><div class="panel score-panel"><div><div class="score-number">${score ? score.total.toFixed(2) : "—"}</div><div class="eyebrow">${destination === "markdown" ? "Owner export" : "Continuity score"}</div></div><div><div class="score-terms">${Object.entries(
      {
        object_coverage: 0.4,
        fidelity: 0.3,
        scope_preservation: 0.2,
        staleness: 0.1,
      },
    )
      .map(
        ([k, w]) =>
          `<div class="score-term"><span>${k}</span><div class="progress"><span class="${k === "staleness" ? "amber-fill" : ""}" style="width:${score ? score[k] * 100 : 0}%"></span></div><span>${score ? score[k].toFixed(2) : "—"} × ${w.toFixed(2)}</span></div>`,
      )
      .join(
        "",
      )}</div><p class="caption">${score ? "Weights are published. Fresh for one day; staleness then decays over 30 days." : destination === "markdown" ? "Markdown is an owner export and has no compiler continuity score." : "Review your context, then preview to calculate the score from the actual compiler."}</p></div></div><div class="columns asymmetric"><section><div class="list-summary"><span>Migration manifest · ${entries.length} objects</span><span>${entries.filter((e) => e.status === "withheld").length} withheld</span></div>${entries.map((e) => `<div class="manifest-row ${e.status === "withheld" ? "withheld" : ""}"><span>${esc(objById(e.source_id)?.content || e.source_id)}</span><span class="mono ${e.status === "withheld" ? "amber" : "green"}">${esc(e.status)}</span></div>`).join("") || empty("No context to migrate", "Add or import memories to start.")}</section><section>${statusNotice()}<div class="panel"><span class="eyebrow">What leaves</span><p class="mt">${destination === "markdown" ? "All reviewed objects, including restricted ones, are included in your owner export." : `${entries.filter((e) => e.status !== "withheld").length} objects may compile to ${destinations.find((d) => d[0] === destination)[1]}. Withheld objects appear in the manifest, not in the destination’s context.`}</p><p class="muted">Afterwards you can disconnect coleta entirely. That is the point of compiling rather than syncing.</p></div><button class="primary wide mt" data-action="download" ${state.can_compile && entries.length ? "" : "disabled"}>${icon("download")} Build package</button><button class="wide" style="margin-top:10px" data-action="preview" ${state.can_compile && entries.length ? "" : "disabled"}>Preview what ${destinations.find((d) => d[0] === destination)[1]} will see</button>${manifest ? `<details class="panel mt"><summary>Installation instructions</summary><p class="small mt" style="white-space:pre-wrap">${esc(manifest.instructions)}</p></details>` : ""}</section></div>`,
  );
}
const surfaceList = [
  ["claude", "claude.ai", "browser extension · composer bridge"],
  ["desktop", "Claude Desktop", "local MCP · stdio"],
  ["code", "Claude Code", "transcript import"],
  ["chatgpt", "chatgpt.com", "browser extension · composer bridge"],
  ["local", "Local model", "OpenAI-compatible proxy · Ollama"],
];
function hostedSurfaces() {
  return (
    `<div class="notice">${icon("check")} Supabase database connected</div>` +
    surfaceList
      .map(
        ([id, title]) =>
          `<div class="panel surface"><div><h3>${mark(id)}${title}</h3><span class="mono">${id === "local" ? "Local proxy → hosted MCP" : "Remote MCP / REST bridge"}</span></div><div class="row"><span class="mono muted">client setup required</span><button data-connect="${id}">Set up</button></div></div>`,
      )
      .join("") +
    `<p class="caption">Raw-turn capture: ${connections.capture_enabled ? "enabled for consenting clients" : "off"}. Extraction: ${esc(connections.extraction_backend)} · ${esc(connections.worker_schedule)}.</p>`
  );
}
function hostedKeys() {
  return `<h2>Connector credentials</h2><div class="panel"><p>Separate bearer keys are configured for Claude, ChatGPT and local clients. Your workspace password cannot call these APIs.</p><p class="muted">Credentials are in the private deployment file supplied at launch. To rotate a key, update the Vercel environment and redeploy.</p><a class="btn" href="#/surfaces">Connection instructions</a></div>`;
}
function hostedConnect(id) {
  const title = surfaceList.find((s) => s[0] === id)[1];
  const keyName = ["claude", "desktop", "code"].includes(id) ? "claude" : id;
  const url = connections.mcp_url;
  const instructions = {
    claude:
      "Configure a remote MCP connector where supported, or set the browser extension's base URL and bearer key. Enable extension capture only after reviewing its consent screen.",
    chatgpt:
      "Configure the remote MCP URL in ChatGPT's supported developer connector settings. If bearer authentication is unavailable in your account, use the coleta browser extension with the REST base URL. Provider-side setup must be done by you.",
    desktop:
      "Configure a remote MCP connection using the HTTPS URL and Claude key. The local stdio alternative remains available through coletar serve-mcp-stdio.",
    code: "Add an HTTP MCP server to Claude Code using the URL below and an Authorization: Bearer header. This uses model tool calls; it does not watch provider pages.",
    local:
      "On your machine, set COLETAR_MCP_URL and COLETAR_MCP_API_KEY, then run uv run coletar serve-proxy. Keep Ollama running locally and point your model client at that proxy.",
  };
  modal(
    `Set up ${title}`,
    `<p>${instructions[id]}</p><label>MCP URL<input readonly value="${esc(url)}"></label><label>REST base URL<input readonly value="${esc(connections.rest_url.replace(/\/v1$/, ""))}"></label><p>Use the <b>${keyName}</b> bearer key from your private deployment credentials.</p><pre class="panel" style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(`Authorization: Bearer <${keyName} key>`)}</pre><p class="caption">A successful API check verifies the server. It does not prove your provider has connected. Test a search from that provider after setup.</p>`,
    "Done",
    async () => {},
  );
}
function surfaces() {
  return shell(
    "Get set up",
    `<div class="stepper"><span class="current"><i>1</i> Connect</span><hr><span class="current"><i>2</i> Import your history</span><hr><span><i>3</i> Review what was found</span></div><div class="columns"><section><h2>Connected surfaces</h2><p class="muted">Connect supported tools to your context. Read and write capabilities depend on the surface; setup status is shown below.</p>${connections ? hostedSurfaces() : surfaceList.map(([id, title, desc]) => `<div class="panel surface ${prefs.surfaces?.[id] ? "ready" : ""}"><div><h3>${mark(id)}${title}</h3><span class="mono">${desc}</span></div><div class="row"><span class="mono ${prefs.surfaces?.[id] ? "green" : "muted"}">${prefs.surfaces?.[id] ? "setup saved · unverified" : "not configured"}</span><button class="${prefs.surfaces?.[id] ? "quiet" : ""}" data-connect="${id}">${prefs.surfaces?.[id] ? "Manage" : "Connect"}</button></div></div>`).join("")}<p class="caption">${connections ? "Endpoints are live. A provider is connected only after you configure its client and successfully use it. Account integrations are never installed automatically." : "Setup controls simulate the connection flow. A saved setup does not establish a provider connection."}</p></section><section><h2>Import your history</h2><p class="muted">Click the export button inside your provider account, then drop the file here. coleta never signs in as you and never reads your archive on its own.</p><div class="panel dropzone" id="dropzone">${icon("upload")}<b>Drop a ChatGPT or Claude export</b><p class="small muted">.zip or conversations.json · processed ${state.hosted ? "on your hosted server" : "on this machine"}</p><label class="btn" for="import-file">Choose a file</label><input id="import-file" type="file" accept=".zip,.json" hidden></div><p class="caption">Pattern extraction · no third-party model calls · ${connections?.upload_limit_mb || 20} MB upload limit. Conservative extraction may miss facts.</p>${importReport ? `<div class="panel mt"><div class="row between"><b>${esc(importReport.name)}</b><span class="mono green">${importReport.busy ? "extracting…" : "complete"}</span></div><div class="progress mt"><span style="width:${importReport.busy ? 40 : 100}%"></span></div><p class="mono muted mt">${importReport.busy ? "Reading your uploaded file…" : `${importReport.conversations} conversations · ${importReport.turns} turns read · ${importReport.memories} new memories · ${importReport.corroborated} corroborated`}</p>${importReport.busy ? "" : '<a class="btn primary" href="#/review">Review what was found</a>'}</div>` : ""}<p class="caption">New memories appear in Review. Nothing compiles until every eligible object has been reviewed; live retrieval follows its existing policy.</p></section></div>`,
  );
}
/* Named so the row reads as a routing choice. Prices stay yours to enter: this
   is a scenario calculator, not a published price list. */
const costModels = [
  ["claude-opus-5", 0],
  ["claude-sonnet-5", 1],
  ["gpt-5.6-terra", 2],
  ["qwen2.5 · local", 3],
];
function settings() {
  const usage = Object.fromEntries(
    Object.entries(state.usage).sort(([, a], [, b]) => b - a),
  );
  const total = Object.values(usage).reduce((a, b) => a + b, 0);
  const keys = prefs.keys || [];
  const rules = prefs.rules || [];
  return shell(
    "Settings",
    `<div class="columns"><section><h2>Plan & usage</h2><p class="muted">Context tokens served to your surfaces, not how many memories you store.</p><div class="panel"><div class="row between"><b class="usage-total">${number(total)}</b><span class="mono muted">recorded tokens · no billing enabled</span></div><div class="progress mt">${Object.entries(
      usage,
    )
      .filter(([, n]) => n > 0)
      .map(
        ([s, n]) =>
          `<span data-surface="${esc(markFor(s))}" style="width:${total ? (n / total) * 100 : 0}%"></span>`,
      )
      .join("")}</div><div class="legend mono muted">${
      Object.entries(usage)
        .map(
          ([s, n]) =>
            `<span data-surface="${esc(markFor(s))}">${esc(s)} ${number(n)}</span>`,
        )
        .join("") || "No recorded retrievals yet."
    }</div></div><h2 class="mt">What that context costs you</h2><p class="muted">Explore the same token volume at input prices you choose.</p><div class="panel table-wrap"><table><thead><tr><th>Model</th><th>Input / Mtok</th><th>This month</th><th>vs. row 1</th></tr></thead><tbody>${costModels
      .map(([name, i]) => {
        const rate = Number(prefs.rates?.[i] ?? 0);
        const base = Number(prefs.rates?.[0] ?? 0);
        // The comparison is the point of the table: the same graph, priced
        // against each model you could route it to.
        const delta = base ? Math.round(((rate - base) / base) * 100) : null;
        return `<tr><td>${tag(name)}</td><td>${i < 3 ? `<input type="number" min="0" step="0.01" aria-label="${esc(name)} price per million tokens" data-rate="${i}" value="${rate}">` : '<span class="mono">$0.00</span>'}</td><td class="mono" data-cost="${i}">$${((total / 1000000) * rate).toFixed(2)}</td><td class="mono ${delta === null || !i ? "muted" : delta <= 0 ? "green" : "amber"}" data-delta="${i}">${!i ? "—" : delta === null ? "—" : `${delta > 0 ? "+" : delta < 0 ? "−" : ""}${Math.abs(delta)}%`}</td></tr>`;
      })
      .join(
        "",
      )}</tbody></table></div><p class="caption">Scenario calculator, not current provider pricing or an invoice. Usage is based on the latest 2,000 events, not a billing period.</p><a class="btn" href="#/pricing">Explore proposed plans</a></section><section>${connections ? hostedKeys() : `<h2>API keys</h2><p class="muted">Try naming, scoping and revoking a key in this setup simulation.</p><div class="panel table-wrap"><table><thead><tr><th>Name</th><th>Scope</th><th>Status</th><th></th></tr></thead><tbody>${keys.map((k, i) => `<tr><td>${esc(k.name)}</td><td class="mono">${esc(k.scope)}</td><td class="mono muted">demo only</td><td><button class="quiet small" data-revoke="${i}">Revoke</button></td></tr>`).join("") || '<tr><td colspan="4" class="muted">No demo keys. These do not authenticate API requests.</td></tr>'}</tbody></table></div><button class="wide" style="margin-top:12px" data-action="new-key">${icon("plus")} New demo key</button>`}${
      isPublic()
        ? `<h2 class="mt">Workspace access</h2><div class="notice warning">${icon("info")} This workspace is served without a password. Anyone with the link can read every object — including ones marked restricted — and can add, edit, retire and import. Keep private context out of it.</div><p class="caption">Connector bearer keys are a separate authority and still gate the MCP and REST endpoints. The scheduled extraction batch still requires its own credential.</p>`
        : ""
    }<h2 class="mt">Default reach for new memories</h2><p class="muted">A browser preference for memories you add here. Existing memories and other surfaces are unaffected.</p><div class="panel"><div class="rule-row"><span class="mono">everything</span><span>${esc(prefs.defaultReach || "every surface")}</span></div>${rules.map((r, i) => `<div class="rule-row"><span class="mono">${esc(r.project)}</span><span>${esc(r.reach)} only <button class="quiet" data-remove-rule="${i}" aria-label="Remove ${esc(r.project)} rule">×</button></span></div>`).join("")}<button class="mt" data-action="new-rule">${icon("plus")} Add a rule</button></div><p class="caption">Project rules apply only to manual additions in this browser. Team policy and server-wide defaults are not configured.</p></section></div>`,
  );
}
// Synthetic examples are isolated from the Store and never call a graph-write API.
const demoFacts = [
  {
    kind: "Preference",
    title: "A little less jargon.",
    content: "Use plain language and keep explanations concise.",
    source: "I prefer plain language. Please keep your explanations concise.",
    provider: "Claude",
    reach: ["claude", "chatgpt", "local"],
    project: "Global",
    date: "04 Sep 2026",
  },
  {
    kind: "Decision",
    title: "Built for the long run.",
    content: "Use PostgreSQL for the Atlas project.",
    source:
      "For Atlas, let's use PostgreSQL. We need a relational database we can grow with.",
    provider: "Claude",
    reach: ["claude", "local"],
    project: "Project Atlas",
    date: "06 Sep 2026",
  },
  {
    kind: "Correction",
    title: "Plans change. Context follows.",
    content: "The Atlas launch is now 24 October.",
    source:
      "The launch has moved from 10 October to 24 October. Keep the previous date in the history.",
    provider: "ChatGPT",
    reach: ["claude", "chatgpt", "local"],
    project: "Project Atlas",
    date: "08 Sep 2026",
  },
  {
    kind: "Personal note",
    title: "Some things stay close.",
    content: "Keep my personal journal on my local model.",
    source: "My personal journal should only be available to my local model.",
    provider: "Local",
    reach: ["local"],
    project: "Personal",
    date: "09 Sep 2026",
  },
];
let demoSelected = 1,
  demoProvider = "claude",
  historyStep = 1,
  libraryView = "grouped";
let demoReach = demoFacts.map((f) => [...f.reach]);
const providerName = (s) =>
  ({ claude: "Claude", chatgpt: "ChatGPT", local: "Local model" })[s];
function marketingNav() {
  return horizonNav(false);
}
function marketingFooter() {
  return `<footer class="site-footer"><div><a class="brand" href="#/home">coleta</a><p class="muted mt">A place for everything<br>you bring to AI.</p></div><div class="stack"><span class="eyebrow">Explore</span><a href="#/library">Your workspace</a><a href="#/surfaces">Connections & imports</a><a href="#/migrate">Export & migrate</a></div><div class="stack"><span class="eyebrow">Built on trust</span><a href="#/security">Privacy & boundaries</a><a href="#/audit">Context history</a><a href="https://github.com/chrisdten3/coletar/blob/main/docs/CONTINUITY_SCORE.md" target="_blank" rel="noopener">Continuity Score ↗</a></div><div class="stack"><span class="eyebrow">Open by design</span><a href="https://github.com/chrisdten3/coletar" target="_blank" rel="noopener">Source & documentation ↗</a><a href="/" target="_blank" rel="noopener">Developer Inspector ↗</a><span class="muted">Independent context.<br>Human control.</span></div></footer><div class="footer-bottom"><span>coleta / a portable AI workspace</span><span>Keep what matters. Carry it forward.</span></div>`;
}
function orbitArtwork() {
  return `<svg class="orbit-art" viewBox="0 0 700 660" fill="none" aria-hidden="true"><defs><linearGradient id="ribbon" x1="100" y1="50" x2="550" y2="610" gradientUnits="userSpaceOnUse"><stop stop-color="#ecf1f7"/><stop offset=".22" stop-color="#9db8df"/><stop offset=".43" stop-color="#eef3fa"/><stop offset=".58" stop-color="#779acc"/><stop offset=".76" stop-color="#b9cdec"/><stop offset="1" stop-color="#527cb7"/></linearGradient><linearGradient id="edge" x1="0" y1="0" x2="600" y2="550" gradientUnits="userSpaceOnUse"><stop stop-color="#fff"/><stop offset=".5" stop-color="#eaf0fa"/><stop offset="1" stop-color="#7a99c9"/></linearGradient><filter id="soft"><feGaussianBlur stdDeviation="17"/></filter></defs><ellipse cx="353" cy="580" rx="200" ry="20" fill="#617899" opacity=".14" filter="url(#soft)"/><g transform="rotate(-34 350 330)"><ellipse cx="350" cy="330" rx="204" ry="270" stroke="#5577a3" stroke-width="49" opacity=".12" transform="translate(4 9)"/><ellipse cx="350" cy="330" rx="204" ry="270" stroke="url(#ribbon)" stroke-width="48"/><ellipse cx="350" cy="330" rx="226" ry="292" stroke="url(#edge)" stroke-width="2"/><ellipse cx="350" cy="330" rx="182" ry="248" stroke="#f0f5fd" stroke-width="2" opacity=".85"/></g><g transform="rotate(43 350 330)"><ellipse cx="350" cy="330" rx="215" ry="108" stroke="url(#ribbon)" stroke-width="32"/><ellipse cx="350" cy="330" rx="230" ry="123" stroke="url(#edge)" stroke-width="1.5"/><ellipse cx="350" cy="330" rx="199" ry="92" stroke="#eaf2ff" stroke-width="1.5"/></g><path d="M100 325H595M350 65V585" stroke="#47678d" stroke-dasharray="3 7" opacity=".2"/><circle cx="350" cy="330" r="73" fill="#f4f1e9" stroke="#b9cadc"/><circle cx="350" cy="330" r="64" stroke="#cfdbdf"/><path d="M370 303a32 32 0 1 0 0 54M370 316a17 17 0 1 0 0 28" stroke="#244be8" stroke-width="9"/><circle cx="375" cy="330" r="6" fill="#244be8"/></svg>`;
}
function atlasCards() {
  return demoFacts
    .slice(0, 3)
    .map(
      (f, i) =>
        `<button class="atlas-slip slip-${i} ${demoReach[i].includes(demoProvider) ? "eligible" : "withheld"}" data-source="${i}"><span class="slip-label">${String(i + 1).padStart(2, "0")} / ${f.kind} <span>${demoReach[i].includes(demoProvider) ? "↗" : "⊘"}</span></span><strong>${f.content}</strong><span class="slip-source">${mark(f.provider)} ${f.project} · ${demoReach[i].includes(demoProvider) ? "Eligible" : "Withheld"}</span></button>`,
    )
    .join("");
}
function demoPanel() {
  const f = demoFacts[demoSelected];
  return `<div class="demo-library"><span class="eyebrow">01 / Your collection</span>${demoFacts.map((f, i) => `<button class="demo-object ${i === demoSelected ? "selected" : ""}" data-demo-object="${i}" aria-pressed="${i === demoSelected}"><span class="demo-object-icon">${icon(i === 3 ? "lock" : i === 2 ? "audit" : "library")}</span><span><small>${f.kind}</small><strong>${f.title}</strong></span><span class="demo-chevron">↗</span></button>`).join("")}</div><div class="demo-policy"><span class="eyebrow">02 / Set the boundaries</span><h3>${f.content}</h3><span class="badge">${f.project}</span><fieldset><legend>Who may read this?</legend>${["claude", "chatgpt", "local"].map((p) => `<label class="demo-toggle"><span>${mark(p)} ${providerName(p)}</span><input class="switch" type="checkbox" data-demo-permission="${p}" aria-label="${providerName(p)} may read this example" ${demoReach[demoSelected].includes(p) ? "checked" : ""}></label>`).join("")}</fieldset><button class="source-link quiet" data-source="${demoSelected}">Inspect the source ${icon("arrow")}</button></div><div class="demo-result"><span class="eyebrow">03 / See the difference</span><label class="preview-label" for="demo-provider">Preview context for</label><select id="demo-provider">${["claude", "chatgpt", "local"].map((p) => `<option value="${p}" ${p === demoProvider ? "selected" : ""}>${providerName(p)}</option>`).join("")}</select><div class="eligibility" aria-live="polite"><span class="eligibility-number">${demoReach.filter((r) => r.includes(demoProvider)).length}<small> / 4</small></span><p>objects eligible for retrieval</p>${demoFacts.map((f, i) => `<div class="eligibility-row ${demoReach[i].includes(demoProvider) ? "" : "off"}">${icon(demoReach[i].includes(demoProvider) ? "check" : "lock")}<span>${f.kind}<small>${demoReach[i].includes(demoProvider) ? "Within your access policy" : "Withheld by your access policy"}</small></span></div>`).join("")}</div><p class="demo-disclaimer">Permission makes context available. The assistant’s query determines what is retrieved.</p></div>`;
}
function historyExample() {
  return `<div class="history-stamp"><span class="eyebrow">Project Atlas / Decision history</span>${icon("audit")}</div><div class="history-steps" role="group" aria-label="Example history"><button data-history-step="0" aria-pressed="${historyStep === 0}" class="${historyStep === 0 ? "active" : ""}"><span>01</span> 03 September</button><button data-history-step="1" aria-pressed="${historyStep === 1}" class="${historyStep === 1 ? "active" : ""}"><span>02</span> 08 September</button></div><div class="history-content" aria-live="polite"><span class="eyebrow">${historyStep ? "Current version" : "Previous version"}</span><p>The Atlas launch is<br><em>${historyStep ? "24" : "10"} October.</em></p>${historyStep ? '<div class="history-diff"><del>10 October</del><span>→</span><ins>24 October</ins></div>' : '<p class="small muted">Recorded on 03 September. Superseded on 08 September.</p>'}</div><div class="history-receipt">${mark(historyStep ? "chatgpt" : "claude")}<span>${historyStep ? "ChatGPT" : "Claude"}<small>Explicit statement · synthetic example</small></span><button class="quiet" data-source="${historyStep ? 2 : 4}">View source ↗</button></div>`;
}
function home() {
  return horizonHome();
}
function showDemoSource(index) {
  const f =
    index === 4
      ? {
          source: "Let's set the Atlas launch for 10 October.",
          provider: "Claude",
          project: "Project Atlas",
          date: "03 Sep 2026",
        }
      : demoFacts[index];
  modal(
    "A fact, with its source.",
    `<span class="eyebrow">Source receipt / synthetic example</span><blockquote class="source-quote">“${f.source}”</blockquote><dl class="source-details"><dt>Origin</dt><dd>${f.provider} conversation</dd><dt>Scope</dt><dd>${f.project}</dd><dt>Recorded</dt><dd>${f.date}</dd><dt>Extraction</dt><dd>Explicit statement</dd><dt>Confidence</dt><dd>1.00 · directly stated in this example</dd></dl><p class="muted small">Illustrative data only. In your workspace, every object links to its recorded provenance and events.</p>`,
    "",
    () => {},
  );
}
function bindDesign() {
  const redrawDemo = () => {
    const focused = document.activeElement;
    const permission = focused?.dataset.demoPermission;
    const selected = focused?.dataset.demoObject;
    const wasProvider = focused?.id === "demo-provider";
    $("#context-demo").innerHTML = demoPanel();
    if ($("#atlas-cards")) $("#atlas-cards").innerHTML = atlasCards();
    document.querySelectorAll("[data-atlas-provider]").forEach((b) => {
      const on = b.dataset.atlasProvider === demoProvider;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", String(on));
    });
    bindDesign();
    if (permission) $(`[data-demo-permission="${permission}"]`)?.focus();
    else if (selected) $(`[data-demo-object="${selected}"]`)?.focus();
    else if (wasProvider) $("#demo-provider")?.focus();
  };
  $(".skip").onclick = (e) => {
    e.preventDefault();
    const main = $("#content");
    main.setAttribute("tabindex", "-1");
    main.focus({ preventScroll: true });
    main.scrollIntoView({ block: "start" });
  };
  document
    .querySelectorAll("[data-source]")
    .forEach(
      (b) => (b.onclick = () => showDemoSource(Number(b.dataset.source))),
    );
  document.querySelectorAll("[data-demo-object]").forEach(
    (b) =>
      (b.onclick = () => {
        demoSelected = Number(b.dataset.demoObject);
        redrawDemo();
      }),
  );
  document.querySelectorAll("[data-demo-permission]").forEach(
    (b) =>
      (b.onchange = () => {
        const p = b.dataset.demoPermission;
        demoReach[demoSelected] = b.checked
          ? [...demoReach[demoSelected], p]
          : demoReach[demoSelected].filter((x) => x !== p);
        redrawDemo();
      }),
  );
  document.querySelectorAll("[data-atlas-provider]").forEach(
    (b) =>
      (b.onclick = () => {
        demoProvider = b.dataset.atlasProvider;
        redrawDemo();
      }),
  );
  if ($("#demo-provider"))
    $("#demo-provider").onchange = (e) => {
      demoProvider = e.target.value;
      redrawDemo();
    };
  if ($("#reset-demo"))
    $("#reset-demo").onclick = () => {
      demoReach = demoFacts.map((f) => [...f.reach]);
      demoSelected = 1;
      demoProvider = "claude";
      redrawDemo();
      toast("Example reset. Your workspace was not changed.");
    };
  document.querySelectorAll("[data-history-step]").forEach(
    (b) =>
      (b.onclick = () => {
        historyStep = Number(b.dataset.historyStep);
        $("#history-example").innerHTML = historyExample();
        bindDesign();
        $(`[data-history-step="${historyStep}"]`).focus();
      }),
  );
  document.querySelectorAll("[data-scroll]").forEach(
    (a) =>
      (a.onclick = (e) => {
        if (!$("#" + a.dataset.scroll)) return;
        e.preventDefault();
        $("#" + a.dataset.scroll).scrollIntoView({
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "instant"
            : "smooth",
        });
        $(".site-nav")?.classList.remove("menu-open");
        $(".menu-toggle")?.setAttribute("aria-expanded", "false");
      }),
  );
  if ($(".site-nav"))
    $(".site-nav").onkeydown = (e) => {
      if (e.key === "Escape") {
        $(".site-nav").classList.remove("menu-open");
        $(".menu-toggle").setAttribute("aria-expanded", "false");
        $(".menu-toggle").focus();
      }
    };
  if ($(".menu-toggle"))
    $(".menu-toggle").onclick = (e) => {
      const open = $(".site-nav").classList.toggle("menu-open");
      e.currentTarget.setAttribute("aria-expanded", String(open));
    };
  document.querySelectorAll("[data-library-view]").forEach(
    (b) =>
      (b.onclick = () => {
        libraryView = b.dataset.libraryView;
        render();
        $(`[data-library-view="${libraryView}"]`).focus();
      }),
  );
  // Same switch, reached from inside an empty state rather than from the toolbar.
  document.querySelectorAll("[data-library-view-set]").forEach(
    (b) =>
      (b.onclick = () => {
        libraryView = b.dataset.libraryViewSet;
        render();
      }),
  );
  document.querySelectorAll("[data-section]").forEach(
    (b) =>
      (b.onclick = () => {
        const key = b.dataset.section;
        if (collapsed.has(key)) collapsed.delete(key);
        else collapsed.add(key);
        render();
        $(`[data-section="${CSS.escape(key)}"]`)?.focus();
      }),
  );
  document.querySelectorAll("[data-cluster]").forEach(
    (b) =>
      (b.onclick = () => {
        const key = b.dataset.cluster;
        if (expandedClusters.has(key)) expandedClusters.delete(key);
        else expandedClusters.add(key);
        render();
        $(`[data-cluster="${CSS.escape(key)}"]`)?.focus();
      }),
  );
}
function pricing() {
  const plans = [
    [
      "Free",
      "$0",
      "2M",
      "Enough to explore your own context.",
      [
        "Every surface",
        "Import history",
        "Per-fact reach controls",
        "Compile to a destination",
      ],
    ],
    [
      "Pro",
      "To be announced",
      "25M",
      "For a working life that runs through assistants.",
      [
        "Everything in Free",
        "Two-axis audit queries",
        "Read receipts",
        "Priority extraction",
      ],
    ],
    [
      "Team",
      "To be announced",
      "100M pooled",
      "One graph, several people.",
      [
        "Everything in Pro",
        "Shared project scopes",
        "Policy rules",
        "Team administration",
      ],
    ],
    [
      "Enterprise",
      "Custom",
      "Custom",
      "For organisations with additional requirements.",
      [
        "Deployment options",
        "Retention controls",
        "Security review",
        "Contracted support",
      ],
    ],
  ];
  return `<div class="marketing">${marketingNav()}<main id="content" style="padding:0;max-width:none"><header class="pricing-header"><h1>Priced on context served,<br>not memories stored.</h1><p class="muted">Proposed plans from the product design. Billing and subscriptions are not available.</p></header><section class="pricing-grid">${plans.map(([name, price, tokens, desc, features]) => `<article class="panel"><span class="eyebrow">${name}</span><div class="price">${price}</div><p class="muted">${desc}</p><p><b>${tokens}</b> context tokens / month</p><ul>${features.map((f) => `<li>${f}</li>`).join("")}</ul><a href="#/library" class="btn ${name === "Free" ? "primary" : ""}">Explore prototype</a></article>`).join("")}</section><section class="features"><div><h2>What counts as context</h2><p>The context your graph retrieves and hands to a model. The prototype exposes recorded usage without charging for it.</p></div><div><h2>Why not per-memory</h2><p>A useful graph grows with your history. The proposed pricing model does not charge by the object.</p></div><div><h2>If you use a local model</h2><p>Your graph stays portable. Compare estimated input costs in the Settings scenario calculator.</p></div></section></main>${marketingFooter()}</div>`;
}
function security() {
  return `<div class="marketing">${marketingNav()}<main id="content" class="privacy"><h1>Your context, under your control.</h1><h2>Only what you choose to share</h2><p>Import files you export yourself. Consented extension capture is limited to submitted user turns on the active page. coleta never signs in as you, replays provider sessions, or automates a provider UI.</p><h2 class="mt">A record for every change</h2><p>Memories carry provenance. Edits and retirements append events. Retirement preserves history; raw-turn erasure uses the separate crypto-shredding workflow.</p><h2 class="mt">${state.hosted ? "Hosted preview boundaries" : "Local prototype boundaries"}</h2><p>${state.hosted ? `This single-owner workspace runs on Vercel with Supabase Postgres. ${isPublic() ? "This workspace is public: anyone with its link can read and change it." : "This workspace uses the configured hosted access gate."} Connectors use separate scoped bearer keys. Account signup and billing are not enabled. Imports run on the hosted server without third-party model calls. Captured turns require an opt-in client. When OpenAI extraction is enabled, only candidate turns are sent to OpenAI; stored memories and the rest of the graph are not sent. OpenAI is the extraction subprocessor. Batches run daily and on demand.` : "This app runs on loopback and has no account/session system. Import uses the local pattern extractor and makes no model calls. Connection and API-key screens simulate setup; their saved values do not grant access."}</p><h2 class="mt">A real way out</h2><p>The three provider compilers produce downloadable native packages after review. Destination reach is enforced by the compiler. Markdown is an owner export and includes restricted context.</p><a class="btn primary mt" href="#/library">Explore your library</a></main>${marketingFooter()}</div>`;
}
function render() {
  if (!state) return;
  const parts = (location.hash.replace(/^#\/?/, "") || "library").split("/");
  const route = parts[0];
  const pages = {
    library,
    capture,
    review,
    audit,
    migrate,
    surfaces,
    settings,
    home,
    pricing,
    security,
  };
  $("#app").innerHTML =
    route === "object"
      ? detail(decodeURIComponent(parts.slice(1).join("/")))
      : (pages[route] || library)();
  document.title = `${route === "home" ? "Your context, everywhere" : route.charAt(0).toUpperCase() + route.slice(1)} · coleta`;
  bind();
  bindDesign();
  bindHorizon();
  bindGraph();
  if (route === "home" && parts[1])
    requestAnimationFrame(() =>
      document.getElementById(parts[1])?.scrollIntoView(),
    );
}
function modal(title, body, submitText, onSubmit) {
  const d = $("#dialog");
  d.innerHTML = `<form method="dialog" id="modal-form"><header><h2 id="dialog-title">${title}</h2><button type="button" class="quiet" data-close aria-label="Close dialog">${icon("close")}</button></header>${body}<div class="error-message" role="alert"></div><footer><button type="button" data-close>${submitText ? "Cancel" : "Close"}</button>${submitText ? `<button class="primary" type="submit">${submitText}</button>` : ""}</footer></form>`;
  d.querySelectorAll("[data-close]").forEach(
    (b) => (b.onclick = () => d.close()),
  );
  $("#modal-form").onsubmit = async (e) => {
    e.preventDefault();
    const button = $("button[type=submit]", d);
    if (button) button.disabled = true;
    try {
      await onSubmit(new FormData(e.target));
      d.close();
    } catch (error) {
      $(".error-message", d).textContent = error.message;
    } finally {
      if (button) button.disabled = false;
    }
  };
  d.setAttribute("aria-labelledby", "dialog-title");
  d.showModal();
}
function memoryModal(id) {
  const o = id ? objById(id) : null;
  modal(
    o ? "Edit memory" : "Add memory",
    `<label><span class="eyebrow">Your memory</span><textarea name="content" placeholder="A fact, preference or decision worth keeping" required maxlength="20000">${esc(o?.content || "")}</textarea></label>${o ? `<div class="edit-comparison"><div><span class="eyebrow">Before</span><p>${esc(o.content)}</p></div><div><span class="eyebrow">After</span><p id="edit-preview">${esc(o.content)}</p></div></div>` : `<label><span class="eyebrow">Kind</span><select name="kind">${["fact", "preference", "instruction", "goal", "correction"].map((k) => `<option>${k}</option>`).join("")}</select></label><label><span class="eyebrow">Project · optional</span><input name="project" placeholder="e.g. proj_ledger" maxlength="200"></label>`}<p class="small muted mt">${o ? "Editing preserves the previous version and records your review." : "Explicitly added by you, with provenance. Review before compiling."}</p>`,
    o ? "Save changes" : "Add memory",
    async (form) => {
      if (o)
        await api("/objects/" + encodeURIComponent(id), {
          action: "edit",
          content: form.get("content"),
        });
      else {
        const rule = (prefs.rules || []).find(
          (r) => r.project === String(form.get("project")).trim(),
        );
        const reach = rule?.reach || prefs.defaultReach;
        await api("/memories", {
          content: form.get("content"),
          kind: form.get("kind"),
          project: form.get("project"),
          locality:
            reach && reach !== "every surface"
              ? { mode: "local_only", surfaces: [reach] }
              : { mode: "synced", surfaces: [] },
        });
      }
      await refresh();
      render();
      toast(
        o
          ? "Memory updated. Its history is preserved."
          : "Memory added to your review queue.",
      );
    },
  );
  if (o)
    $("textarea[name=content]", $("#dialog")).oninput = (e) => {
      $("#edit-preview").textContent = e.target.value;
    };
}
async function reviewOne(id) {
  const pair = conflictPairs().find((p) => p.includes(id));
  if (pair) {
    await api("/review", {
      ids: pair.filter((x) => state.unreviewed.includes(x)),
    });
    await refresh();
    render();
    toast("Both statements reviewed.");
    return;
  }
  await api("/objects/" + encodeURIComponent(id), { action: "review" });
  await refresh();
  render();
  toast("Memory reviewed.");
}
function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function runAction(action, id) {
  if (action === "process-captures") {
    const result = await api("/process-captures", {});
    await refresh();
    render();
    toast(
      result.skipped
        ? "Another worker is processing this queue."
        : `${result.extraction.processed || 0} turns processed; ${result.extraction.unavailable || 0} unavailable and pending retry.`,
    );
    return;
  }
  if (action === "add" || action === "edit") {
    memoryModal(id);
    return;
  }
  if (action === "review-one") {
    await reviewOne(id);
    return;
  }
  if (action === "clear") {
    query = "";
    filter = "all";
    surface = "all";
    render();
    return;
  }
  if (action === "sample") {
    await api("/sample", {});
    await refresh();
    render();
    toast("Design examples loaded. All are synthetic.");
    return;
  }
  if (action === "retire") {
    modal(
      "Retire this memory?",
      `<p>${esc(objById(id).content)}</p><p class="muted">It will stop appearing in retrieval and compilation. Its history stays readable.</p>`,
      "Retire memory",
      async () => {
        await api("/objects/" + encodeURIComponent(id), { action: "retire" });
        await refresh();
        render();
        toast("Memory retired. History preserved.");
      },
    );
    return;
  }
  if (action === "review-visible") {
    const ids = reviewObjects()
      .filter((o) => category(o) === reviewTab)
      .map((o) => o.id);
    await api("/review", { ids });
    await refresh();
    render();
    toast(`${ids.length} memories reviewed.`);
    return;
  }
  if (action === "preview") {
    manifest = await api("/compile/preview", { destination });
    render();
    toast("Preview calculated by the compiler.");
    return;
  }
  if (action === "download") {
    const response = await fetch("/web-api/compile/download", {
      method: "POST",
      // Not api(): the body is a ZIP, not JSON. Same authenticated route, so the
      // session has to be attached by hand here.
      headers: await coletaAuth.authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ destination }),
    });
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail);
    }
    downloadBlob(await response.blob(), `coleta-${destination}.zip`);
    toast("Package built. Your download is ready.");
    return;
  }
  if (action === "audit-export") {
    downloadBlob(
      new Blob([JSON.stringify(auditResult, null, 2)], {
        type: "application/json",
      }),
      "coleta-audit-snapshot.json",
    );
    return;
  }
  if (action === "new-key") {
    modal(
      "New demo API key",
      '<div class="notice warning">Setup simulation. This key cannot authenticate API requests.</div><label><span class="eyebrow">Name</span><input name="name" required maxlength="80" placeholder="e.g. Claude Desktop"></label><label><span class="eyebrow">Scope</span><select name="scope"><option value="read">Read only</option><option value="read, write">Read and write</option></select></label>',
      "Create demo key",
      async (form) => {
        prefs.keys = [
          ...(prefs.keys || []),
          { name: form.get("name"), scope: form.get("scope") },
        ];
        savePrefs();
        render();
        toast("Demo key entry created. No real credential was issued.");
      },
    );
    return;
  }
  if (action === "new-rule") {
    modal(
      "Default reach for a project",
      '<p class="muted">Applies to manual memories added in this browser only.</p><label><span class="eyebrow">Project ID</span><input name="project" required maxlength="200" placeholder="proj_ledger"></label><label><span class="eyebrow">May read</span><select name="reach"><option value="claude">Claude (including Code)</option><option value="chatgpt">ChatGPT</option><option value="local">Local model only</option></select></label>',
      "Save rule",
      async (form) => {
        const project = String(form.get("project")).trim();
        if (!project) throw new Error("Enter a project ID.");
        prefs.rules = [
          ...(prefs.rules || []).filter((r) => r.project !== project),
          { project, reach: form.get("reach") },
        ];
        savePrefs();
        render();
        toast("Rule saved for new manual memories in this browser.");
      },
    );
  }
}
function connect(id) {
  if (connections) return hostedConnect(id);
  const entry = surfaceList.find((s) => s[0] === id);
  const instructions = {
    claude:
      "Install the coleta browser extension manually. Enable capture only on the active site after reviewing its consent screen.",
    chatgpt:
      "Install the coleta browser extension manually. Only submitted user turns on the active tab may be captured.",
    desktop:
      "Add coletar serve-mcp-stdio to your Claude Desktop MCP configuration. The client launches a local process under your OS identity.",
    code: "Use the Claude Code transcript importer on files the application writes to your own disk. This is not background browser capture.",
    local:
      "Route your OpenAI-compatible client through the coleta local proxy, with Ollama or your chosen local runtime as its backend.",
  };
  modal(
    `Set up ${entry[1]}`,
    `<p>${instructions[id]}</p><div class="notice warning">Prototype setup simulation. This does not install software, open a provider session, or establish a connection.</div><label class="row"><input type="checkbox" name="consent" required> I understand what this setup will read.</label>`,
    "Save setup",
    async () => {
      prefs.surfaces = { ...(prefs.surfaces || {}), [id]: true };
      savePrefs();
      render();
      toast("Setup preference saved. Connection is not verified.");
    },
  );
}
async function upload(file) {
  if (!file) return;
  if (file.size > (connections?.upload_limit_mb || 20) * 1024 * 1024)
    throw new Error(
      `Choose an export under ${connections?.upload_limit_mb || 20} MB.`,
    );
  const form = new FormData();
  form.append("file", file);
  importReport = { name: file.name, busy: true };
  render();
  try {
    const response = await fetch("/web-api/import", {
      method: "POST",
      // No Content-Type: the browser sets the multipart boundary itself, and
      // naming it here produces a body the server cannot parse.
      headers: await coletaAuth.authHeaders(),
      body: form,
    });
    const data = await response.json();
    if (!response.ok)
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Could not import that file.",
      );
    importReport = { ...data, name: file.name, busy: false };
    await refresh();
    render();
    toast(`Imported ${data.memories} new memories.`);
  } catch (error) {
    importReport = null;
    render();
    throw error;
  }
}
function bind() {
  document.querySelectorAll("[data-sign-out]").forEach(
    (b) =>
      (b.onclick = async () => {
        b.disabled = true;
        // Reload rather than re-render: signing out invalidates every cached
        // answer in `state`, and the cheapest way to be certain none of it is
        // still on screen is to start the page over.
        await coletaAuth.session.signOut();
        location.reload();
      }),
  );
  document.querySelectorAll("[data-action]").forEach(
    (b) =>
      (b.onclick = async () => {
        b.disabled = true;
        try {
          await runAction(b.dataset.action, b.dataset.id);
        } catch (e) {
          toast(e.message, true);
        } finally {
          b.disabled = false;
        }
      }),
  );
  document.querySelectorAll("[data-resolve]").forEach(
    (b) =>
      (b.onclick = async () => {
        b.disabled = true;
        try {
          await api("/resolve", {
            keep: b.dataset.resolve,
            retire: b.dataset.reject,
          });
          await refresh();
          render();
          toast("Conflict resolved. The retired statement stays in history.");
        } catch (e) {
          toast(e.message, true);
        } finally {
          b.disabled = false;
        }
      }),
  );
  document.querySelectorAll("button[data-surface]").forEach(
    (b) =>
      (b.onclick = () => {
        surface = b.dataset.surface;
        render();
      }),
  );
  document.querySelectorAll("[data-filter]").forEach(
    (b) =>
      (b.onclick = () => {
        filter = b.dataset.filter;
        render();
      }),
  );
  document.querySelectorAll("[data-review-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        reviewTab = b.dataset.reviewTab;
        render();
      }),
  );
  document.querySelectorAll("[data-destination]").forEach(
    (b) =>
      (b.onclick = () => {
        destination = b.dataset.destination;
        manifest = null;
        render();
      }),
  );
  document
    .querySelectorAll("[data-connect]")
    .forEach((b) => (b.onclick = () => connect(b.dataset.connect)));
  document.querySelectorAll("[data-revoke]").forEach(
    (b) =>
      (b.onclick = () => {
        prefs.keys.splice(Number(b.dataset.revoke), 1);
        savePrefs();
        render();
        toast("Demo key entry revoked.");
      }),
  );
  document.querySelectorAll("[data-remove-rule]").forEach(
    (b) =>
      (b.onclick = () => {
        prefs.rules.splice(Number(b.dataset.removeRule), 1);
        savePrefs();
        render();
      }),
  );
  document.querySelectorAll("[data-rate]").forEach(
    (input) =>
      (input.oninput = () => {
        const rate = Math.max(0, Number(input.value) || 0);
        prefs.rates = { ...(prefs.rates || {}), [input.dataset.rate]: rate };
        savePrefs();
        const total = Object.values(state.usage).reduce((a, b) => a + b, 0);
        $(`[data-cost="${input.dataset.rate}"]`).textContent =
          "$" + ((rate * total) / 1000000).toFixed(2);
        // Every comparison is against row one, so a change to it moves all rows.
        const base = Number(prefs.rates?.[0] ?? 0);
        costModels.forEach(([, i]) => {
          const cell = $(`[data-delta="${i}"]`);
          if (!cell || !i) return;
          const other = Number(prefs.rates?.[i] ?? 0);
          const delta = base ? Math.round(((other - base) / base) * 100) : null;
          cell.textContent =
            delta === null
              ? "—"
              : `${delta > 0 ? "+" : delta < 0 ? "−" : ""}${Math.abs(delta)}%`;
          cell.className =
            "mono " +
            (delta === null ? "muted" : delta <= 0 ? "green" : "amber");
        });
      }),
  );
  if ($("#search-form"))
    $("#search-form").onsubmit = (e) => {
      e.preventDefault();
      query = $("#search").value;
      render();
    };
  if ($("#reach-form")) {
    $("#reach-form").onchange = (e) => {
      if (e.target.type !== "checkbox") return;
      const row = e.target.closest(".reach-row");
      row.classList.toggle("withheld", !e.target.checked);
      const badge = $(".badge", row);
      badge.className = "badge " + (e.target.checked ? "ok" : "reach-off");
      badge.textContent = e.target.checked ? "may read" : "withheld";
      const count = $("#reach-form").querySelectorAll("input:checked").length;
      $(".notice", $("#reach-form")).textContent =
        `Unsaved preview: ${count} of 3 provider policies may read this. Save reach to apply.`;
    };
  }
  if ($("#reach-form"))
    $("#reach-form").onsubmit = async (e) => {
      e.preventDefault();
      const values = new FormData(e.target).getAll("surfaces");
      if (!values.length) {
        toast("Keep at least one provider, or retire the memory.", true);
        return;
      }
      const b = $("button[type=submit]", e.target) || $("button", e.target);
      b.disabled = true;
      try {
        await api("/objects/" + encodeURIComponent(e.target.dataset.id), {
          action: "reach",
          locality:
            values.length === 3
              ? { mode: "synced", surfaces: [] }
              : { mode: "local_only", surfaces: values },
        });
        await refresh();
        render();
        toast("Reach saved and recorded in history.");
      } catch (error) {
        toast(error.message, true);
      } finally {
        b.disabled = false;
      }
    };
  if ($("#audit-form"))
    $("#audit-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      auditAt = String(f.get("at"));
      auditValid = String(f.get("valid"));
      const button = $("button", e.target);
      button.disabled = true;
      try {
        auditResult = await api(
          "/audit?" +
            new URLSearchParams({
              at: auditAt + "T23:59:59.999999Z",
              valid: auditValid + "T12:00:00Z",
            }),
        );
        render();
      } catch (err) {
        toast(err.message, true);
      } finally {
        button.disabled = false;
      }
    };
  if ($("#import-file"))
    $("#import-file").onchange = (e) =>
      upload(e.target.files[0]).catch((err) => toast(err.message, true));
  const drop = $("#dropzone");
  if (drop) {
    drop.ondragover = (e) => {
      e.preventDefault();
      drop.classList.add("drag");
    };
    drop.ondragleave = () => drop.classList.remove("drag");
    drop.ondrop = (e) => {
      e.preventDefault();
      upload(e.dataTransfer.files[0]).catch((err) => toast(err.message, true));
    };
  }
}
let libraryScroll = 0,
  libraryFocus = null;
window.addEventListener("hashchange", (event) => {
  const previous = new URL(event.oldURL).hash;
  if (previous === "#/library") {
    libraryScroll = window.scrollY;
    libraryFocus = document.activeElement
      ?.closest(".memory")
      ?.getAttribute("href");
  }
  render();
  if (location.hash === "#/library" && previous.startsWith("#/object/")) {
    requestAnimationFrame(() => {
      if (libraryFocus)
        document.querySelectorAll(".memory").forEach((a) => {
          if (a.getAttribute("href") === libraryFocus)
            a.focus({ preventScroll: true });
        });
      window.scrollTo(0, libraryScroll);
    });
  } else {
    window.scrollTo(0, 0);
    const main = $("#content");
    if (main) {
      main.setAttribute("tabindex", "-1");
      main.focus({ preventScroll: true });
    }
  }
});
// Sign-in first: there is no workspace to draw until we know whose it is, and
// rendering before the answer is what shows a stranger someone else's shell for a
// frame. `establishSession` returns false once it has painted the reason itself —
// a sign-in form, or a misconfiguration — and we stop.
coletaAuth
  .establishSession()
  .then(async (proceed) => {
    if (!proceed) return;
    await refresh();
    render();
  })
  .catch((error) => {
    $("#app").innerHTML =
      `<main class="loading"><h1>Couldn’t open your workspace.</h1><p>${esc(error.message)}</p><button onclick="location.reload()">Try again</button></main>`;
  });

/* --- Atlas: the library as a graph -----------------------------------------
   Entities are what a corpus of 3,818 objects is actually *about*, and the
   `mentions` edges to reach them already existed — the list view simply drew
   entities as if they were peers of the memories they describe.

   Two states, because 1,722 entities is not a picture. The constellation shows
   the hubs, sized by how many facts mention them; clicking one drops into the
   hub-and-spoke the entity deserves, its facts in a ring around it.

   Vanilla SVG and a small force loop rather than a graph library: the client is
   dependency-free by convention, and what this needs — repel, centre, settle — is
   less code than the import would be. */
let graphData = null;
let graphFocus = null;
let graphLoading = false;

function loadGraph(focus = null) {
  if (graphLoading) return;
  graphLoading = true;
  const q = focus ? `?focus=${encodeURIComponent(focus)}` : "?limit=48";
  api("/graph" + q)
    .then((data) => {
      graphData = data;
      graphFocus = focus;
    })
    .catch(() => {
      graphData = { nodes: [], edges: [], error: true };
    })
    .finally(() => {
      graphLoading = false;
      if (location.hash.startsWith("#/library")) render();
    });
}

function atlasGraph() {
  if (!graphData) {
    loadGraph();
    return '<div class="atlas-stage"><p class="atlas-status">Drawing your context…</p></div>';
  }
  if (graphData.error)
    return '<div class="atlas-stage"><p class="atlas-status">The graph could not be loaded.</p></div>';
  if (!graphData.nodes.length)
    return `<div class="atlas-stage"><p class="atlas-status">No connected entities yet. Facts link to the people and organisations they mention; import or add context and they appear here.</p></div>`;

  const focused = graphFocus
    ? graphData.nodes.find((n) => n.id === graphFocus)
    : null;
  const omitted = graphData.omitted_entities;
  const shown = Math.min(graphData.nodes.length - 1, 14);
  const caption = focused
    ? `${esc(focused.label)} · ${graphData.nodes.length - 1} connected ${graphData.nodes.length - 1 === 1 ? "fact" : "facts"}${graphData.nodes.length - 1 > shown ? ` · showing ${shown}` : ""}`
    : `${graphData.nodes.filter((n) => n.type === "entity").length} of ${graphData.total_entities} entities${omitted ? ` · ${omitted} less-connected hidden` : ""}`;

  return `<div class="atlas-stage">
    <div class="atlas-toolbar">
      <span class="mono muted">${caption}</span>
      <div class="row">
        ${focused ? '<button class="quiet small" data-graph-back>← All entities</button>' : ""}
        <button class="quiet small" data-graph-zoom="out" aria-label="Zoom out">−</button>
        <button class="quiet small" data-graph-zoom="in" aria-label="Zoom in">+</button>
      </div>
    </div>
    <svg id="atlas-svg" role="img" aria-label="Context graph"><g id="atlas-root"></g></svg>
    <div class="atlas-hint mono muted">${focused ? `Click a fact to open it · drag to pan${graphData.nodes.length - 1 > shown ? ' · <button type="button" class="linklike" data-library-view-set="grouped">see all in the list</button>' : ""}` : "Click an entity to see what mentions it · drag to pan"}</div>
  </div>`;
}

/* A few dozen iterations of repulsion and centring. Deterministic seeding, so the
   same graph lays out the same way twice — a picture that rearranges itself on
   every render is one nobody can learn the shape of. */
function layoutGraph(nodes, edges, width, height, focus) {
  const centre = { x: width / 2, y: height / 2 };
  if (focus) {
    // Hub and spoke: the entity in the middle, everything that mentions it in a
    // ring. Deterministic and legible, which a force sim is not at this size.
    const others = nodes.filter((n) => n.id !== focus);
    const outer = Math.min(width, height) / 2 - 70;
    // Facts are sentences, not filenames, so a single ring of thirty-nine of them
    // is a wall of overlapping text. Rings are added until each one holds few
    // enough that its labels have room.
    const rings = Math.max(1, Math.min(3, Math.ceil(others.length / 14)));
    const perRing = Math.ceil(others.length / rings);
    others.forEach((n, i) => {
      const ring = Math.floor(i / perRing);
      const withinRing = i % perRing;
      const count = Math.min(perRing, others.length - ring * perRing);
      // Each ring is offset half a step so nodes sit in the gaps of the one
      // outside it rather than directly along the same spokes.
      const angle =
        ((withinRing + (ring % 2) * 0.5) / count) * Math.PI * 2 - Math.PI / 2;
      const r = outer * (1 - ring * (0.3 / Math.max(1, rings - 1 || 1)));
      n.x = centre.x + Math.cos(angle) * r;
      n.y = centre.y + Math.sin(angle) * r;
      // Labels alternate above and below, which halves the collisions again.
      n.labelAbove = withinRing % 2 === 1;
    });
    const hub = nodes.find((n) => n.id === focus);
    if (hub) {
      hub.x = centre.x;
      hub.y = centre.y;
    }
    return;
  }

  let seed = 7;
  const rand = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  nodes.forEach((n, i) => {
    const angle = (i / nodes.length) * Math.PI * 2;
    const r = (0.35 + rand() * 0.5) * Math.min(width, height) * 0.42;
    n.x = centre.x + Math.cos(angle) * r;
    n.y = centre.y + Math.sin(angle) * r;
  });

  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const links = edges
    .map((e) => [index.get(e.src), index.get(e.dst)])
    .filter(([a, b]) => a !== undefined && b !== undefined);

  // Labels sit under their node and are wider than it, so spacing is driven by
  // the text, not the circle. Without this the constellation reads as a pile of
  // overlapping names however far apart the dots are.
  const spacing = (n) => 46 + Math.min(150, n.label.length * 5.5);

  for (let step = 0; step < 220; step++) {
    const cool = 1 - step / 260;
    for (let i = 0; i < nodes.length; i++) {
      let fx = 0;
      let fy = 0;
      for (let j = 0; j < nodes.length; j++) {
        if (i === j) continue;
        const dx = nodes[i].x - nodes[j].x;
        const dy = nodes[i].y - nodes[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const want = (spacing(nodes[i]) + spacing(nodes[j])) / 2;
        // Only push while crowded. A constant inverse-square repulsion is what
        // drove every node to the border and left the middle empty.
        if (dist < want) {
          const push = ((want - dist) / dist) * 0.5;
          fx += dx * push;
          fy += dy * push;
        }
      }
      fx += (centre.x - nodes[i].x) * 0.03;
      fy += (centre.y - nodes[i].y) * 0.03;
      nodes[i].vx = fx;
      nodes[i].vy = fy;
    }
    // Co-mentioned entities pull together, so the picture groups by what actually
    // appears alongside what.
    links.forEach(([a, b]) => {
      const dx = nodes[b].x - nodes[a].x;
      const dy = nodes[b].y - nodes[a].y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const pull = ((dist - 190) / dist) * 0.06;
      nodes[a].vx += dx * pull;
      nodes[a].vy += dy * pull;
      nodes[b].vx -= dx * pull;
      nodes[b].vy -= dy * pull;
    });
    nodes.forEach((n) => {
      n.x += Math.max(-18, Math.min(18, n.vx)) * cool;
      n.y += Math.max(-18, Math.min(18, n.vy)) * cool;
    });
  }
  const padX = 90;
  const padY = 60;
  nodes.forEach((n) => {
    n.x = Math.max(padX, Math.min(width - padX, n.x));
    n.y = Math.max(padY, Math.min(height - padY, n.y));
  });
}

function drawGraph() {
  const svg = $("#atlas-svg");
  const root = $("#atlas-root");
  if (!svg || !root || !graphData?.nodes?.length) return;

  const box = svg.getBoundingClientRect();
  const width = box.width || 900;
  const height = box.height || 560;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  // Focus mode draws every returned node; the constellation draws entities only,
  // because the facts are what you get *after* choosing one.
  /* A hub with thirty-nine spokes is a list wearing a picture's clothes. Cap the
     ring and say so: the remainder is one click away in the grouped Library,
     which is the view that reads a long set properly. */
  const RING_CAP = 14;
  let nodes;
  let hiddenSpokes = 0;
  if (graphFocus) {
    const hub = graphData.nodes.filter((n) => n.id === graphFocus);
    const spokes = graphData.nodes.filter((n) => n.id !== graphFocus);
    hiddenSpokes = Math.max(0, spokes.length - RING_CAP);
    nodes = [...hub, ...spokes.slice(0, RING_CAP)].map((n) => ({ ...n }));
  } else {
    nodes = graphData.nodes.filter((n) => n.type === "entity").map((n) => ({ ...n }));
  }
  window.__atlasHidden = hiddenSpokes;
  const ids = new Set(nodes.map((n) => n.id));
  const edges = graphData.edges.filter((e) => ids.has(e.src) && ids.has(e.dst));
  layoutGraph(nodes, edges, width, height, graphFocus);
  const at = new Map(nodes.map((n) => [n.id, n]));

  const maxDegree = Math.max(1, ...nodes.map((n) => n.degree || 0));
  const radiusOf = (n) =>
    n.id === graphFocus
      ? 30
      : n.type === "entity"
        ? 9 + Math.sqrt(n.degree / maxDegree) * 17
        : 5;

  const line = (e) => {
    const a = at.get(e.src);
    const b = at.get(e.dst);
    if (!a || !b) return "";
    return `<line class="atlas-edge" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"/>`;
  };

  /* Labels are the whole readability problem. Facts are sentences, not names, so
     thirty-nine of them centred on their own node produced the overlapping wall
     the focused view used to be. Three things fix it, in order of how much they
     matter:

     1. Anchor outward. A label on the left half is right-aligned and sits to the
        left of its node; the right half mirrors it. Nothing crosses the middle,
        which is where every collision used to happen.
     2. Give each one an opaque plate. Text drawn straight onto the canvas competes
        with every edge passing under it; a rounded rect in the page's own colour
        means the label occludes the line instead of fighting it.
     3. Drop what still collides. After placement, any label whose box overlaps one
        already drawn is omitted rather than layered — the node keeps its circle,
        its tooltip and its click target, and the picture stays readable. */
  const placed = [];
  const CHAR = 6.1; // ~6px per character at the label's size; measured, not exact.
  const label = (n) => {
    const r = radiusOf(n);
    const cap = n.type === "entity" ? 30 : 42;
    const text = n.label.length > cap ? n.label.slice(0, cap - 1) + "…" : n.label;
    const width = text.length * CHAR + 10;
    const hub = n.id === graphFocus;
    // The hub keeps its label underneath: it is the one node the eye starts from.
    const right = hub ? true : n.x >= width / 2 + 4;
    const x = hub ? n.x : right ? n.x + r + 7 : n.x - r - 7;
    const y = hub ? n.y + r + 16 : n.y;
    const boxX = hub ? x - width / 2 : right ? x - 5 : x - width + 5;
    const box = { x: boxX, y: y - 9, w: width, h: 18 };
    const clash = placed.some(
      (b) =>
        box.x < b.x + b.w && box.x + box.w > b.x && box.y < b.y + b.h && box.y + box.h > b.y,
    );
    if (clash && !hub) return "";
    placed.push(box);
    return `<g class="atlas-label-group"><rect class="atlas-label-plate" x="${box.x.toFixed(1)}" y="${box.y.toFixed(1)}" width="${box.w.toFixed(1)}" height="${box.h}" rx="5"/><text class="atlas-label ${n.type}${hub ? " hub" : ""}" x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="${hub ? "middle" : right ? "start" : "end"}">${esc(text)}</text></g>`;
  };

  root.innerHTML =
    edges.map(line).join("") +
    nodes
      .map(
        (n) =>
          `<g class="atlas-node ${n.type} ${n.id === graphFocus ? "focused" : ""}" data-node="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(n.label)}"><circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${radiusOf(n).toFixed(1)}"/><title>${esc(n.description || n.label)}${n.type === "entity" ? ` — mentioned by ${n.degree} ${n.degree === 1 ? "fact" : "facts"}` : ""}</title></g>`,
      )
      .join("") +
    // Hub first: it is the label that must never be the one dropped.
    [...nodes].sort((a, b) => (b.id === graphFocus) - (a.id === graphFocus)).map(label).join("");
}

function bindGraph() {
  const svg = $("#atlas-svg");
  if (!svg) return;
  drawGraph();

  document.querySelectorAll("[data-node]").forEach((el) => {
    const id = el.dataset.node;
    const open = () => {
      const node = graphData.nodes.find((n) => n.id === id);
      // An entity is a place to stand; a fact is a thing to read, so it opens in
      // the same object view the list links to.
      if (node?.type === "entity" && id !== graphFocus) loadGraph(id);
      else location.hash = `#/object/${encodeURIComponent(id)}`;
    };
    el.onclick = open;
    el.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    };
  });

  if ($("[data-graph-back]"))
    $("[data-graph-back]").onclick = () => {
      graphData = null;
      graphFocus = null;
      loadGraph();
    };

  let zoom = 1;
  let panX = 0;
  let panY = 0;
  const apply = () => {
    $("#atlas-root")?.setAttribute(
      "transform",
      `translate(${panX} ${panY}) scale(${zoom})`,
    );
  };
  document.querySelectorAll("[data-graph-zoom]").forEach((b) => {
    b.onclick = () => {
      zoom = Math.max(0.4, Math.min(2.6, zoom * (b.dataset.graphZoom === "in" ? 1.25 : 0.8)));
      apply();
    };
  });

  let dragging = false;
  let startX = 0;
  let startY = 0;
  svg.onpointerdown = (e) => {
    if (e.target.closest("[data-node]")) return;
    dragging = true;
    startX = e.clientX - panX;
    startY = e.clientY - panY;
    svg.setPointerCapture(e.pointerId);
  };
  svg.onpointermove = (e) => {
    if (!dragging) return;
    panX = e.clientX - startX;
    panY = e.clientY - startY;
    apply();
  };
  svg.onpointerup = () => (dragging = false);
}
