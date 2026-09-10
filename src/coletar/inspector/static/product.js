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
const paths = {
  library: "m3 7 9-5 9 5-9 5-9-5Zm0 5 9 5 9-5M3 17l9 5 9-5",
  capture: "M4 4h16l2 14H2L4 4Zm-2 9h6l2 3h4l2-3h6",
  review:
    "M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm2 9 3 3 7-7",
  audit: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 4v5l4 3",
  migrate: "M3 12h13m-5-5 5 5-5 5M19 3h3v18h-3",
  surfaces: "M3 3h7v7H3V3Zm11 0h7v7h-7V3ZM3 14h7v7H3v-7Zm11 0h7v7h-7v-7Z",
  settings: "M3 6h6m4 0h8M3 18h12m4 0h2M9 3v6m6 6v6",
  search: "m16 16 5 5M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z",
  plus: "M12 4v16M4 12h16",
  arrow: "M5 12h14m-6-6 6 6-6 6",
  back: "M19 12H5m6-6-6 6 6 6",
  check: "m4 12 5 5L20 6",
  lock: "M6 10h12v11H6V10Zm3 0V6a3 3 0 0 1 6 0v4",
  upload: "M12 16V3m-5 5 5-5 5 5M4 18v3h16v-3",
  download: "M12 3v13m-5-5 5 5 5-5M4 19v2h16v-2",
  close: "m5 5 14 14M5 19 19 5",
  info: "M12 10v7m0-11v1M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Z",
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
/* Surface marks. Deliberately geometric rather than reproductions of anyone's
   logo: they identify a destination in this UI, they do not claim a trademark. */
const marks = {
  claude:
    "M12 3.2v5m0 7.6v5M3.2 12h5m7.6 0h5M5.8 5.8l3.5 3.5m5.4 5.4 3.5 3.5m0-12.4-3.5 3.5m-5.4 5.4-3.5 3.5",
  claude_code: "m8.5 8-4 4 4 4m7-8 4 4-4 4m-2-11.5-3 15",
  chatgpt:
    "M12 2.8 20 7.4v9.2L12 21.2 4 16.6V7.4l8-4.6Zm0 5.3 3.4 2v3.8l-3.4 2-3.4-2v-3.8l3.4-2Z",
  local:
    "M8.5 8.5h7v7h-7v-7ZM5.5 5.5h13v13h-13v-13ZM9.5 2.5v3m5-3v3m-5 13v3m5-3v3M2.5 9.5h3m-3 5h3m13-5h3m-3 5h3",
  markdown: "M12 2.5 21 8v8l-9 5.5L3 16V8l9-5.5Zm0 5.2L16.6 10v4L12 16.4 7.4 14v-4L12 7.7Z",
  coletar: "M15.5 8.5a5 5 0 1 0 0 7",
};
marks.desktop = marks.claude;
marks.code = marks.claude_code;
marks.ollama = marks.local;
marks.qwen = marks.local;
marks.gpt = marks.chatgpt;
marks.openai = marks.chatgpt;
marks.all = marks.coletar;
/* Which mark stands for a surface, model or destination id. */
const markFor = (name) => {
  const key = String(name || "").toLowerCase();
  if (marks[key]) return key;
  if (key.includes("claude_code") || key.includes("claude-code")) return "claude_code";
  if (key.includes("claude") || key.includes("opus") || key.includes("sonnet")) return "claude";
  if (key.includes("gpt") || key.includes("openai")) return "chatgpt";
  if (key.includes("markdown") || key.includes("obsidian")) return "markdown";
  if (key.includes("local") || key.includes("ollama") || key.includes("qwen")) return "local";
  return "coletar";
};
const mark = (name, label = "") => {
  const key = markFor(name);
  return `<svg class="mark mark-${key}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" ${label ? `role="img" aria-label="${esc(label)}"` : 'aria-hidden="true"'}><path d="${marks[key]}"/></svg>`;
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
  reads = null,
  auditResult = null;
let auditAt = "2026-03-03",
  auditValid = "2026-01-01",
  importReport = null,
  toastTimer;
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
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    let problem;
    try {
      problem = await response.json();
    } catch {
      problem = { detail: "Request failed. Please try again." };
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
  '<a class="brand" href="#/home"><span class="brand-mark">c</span> coletar</a>';
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
    ["audit", "Audit"],
    ["migrate", "Migrate"],
    ["surfaces", "Surfaces"],
    ["settings", "Settings"],
  ];
  const total = Object.values(state.usage).reduce((a, b) => a + b, 0);
  return `<aside class="sidebar">${brand}<nav aria-label="Workspace">${links.map(([route, label]) => `<a class="nav-link ${title === label || (title === "Object" && label === "Library") || (title === "Get set up" && label === "Surfaces") ? "active" : ""}" ${title === label ? 'aria-current="page"' : ""} href="#/${route}">${icon(route)}<span>${label}</span>${counts[label] !== undefined ? `<span class="count">${counts[label]}</span>` : ""}</a>`).join("")}</nav><div class="sidebar-bottom"><div class="row between"><span>Context served</span><span class="mono">${number(total)} / 2M</span></div><div class="progress"><span style="width:${Math.min((total / 2000000) * 100, 100)}%"></span></div><a class="account" href="#/settings"><span class="avatar">${state.sample ? "DS" : state.hosted ? "PW" : "LW"}</span>${state.sample ? "Design sample workspace" : isPublic() ? "Public workspace" : state.hosted ? "Hosted workspace" : "Local workspace"}</a><span class="prototype-label">${isPublic() ? "Anyone with the link can read and change this" : state.hosted ? "Hosted preview" : "Local prototype"} · ${state.sample ? "synthetic examples" : "your configured store"}</span></div></aside><div class="workspace"><header class="topbar"><h1>${title}</h1><div class="top-actions">${actions}</div></header><main id="content">${body}</main></div>`;
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
    `<form id="search-form" class="search-row"><div class="search-box">${icon("search")}<input id="search" name="q" type="search" aria-label="Search your context" placeholder="Search your context" value="${esc(query)}"></div><button class="quiet" type="submit">Search</button><button type="button" data-action="add">${icon("plus")} Add memory</button></form><div class="chips">${chips.map(([v, l]) => `<button class="chip ${filter === v ? "active" : ""}" data-filter="${esc(v)}" aria-pressed="${filter === v}">${esc(l)}</button>`).join("")}</div><div class="list-summary"><span>${filtered.length} objects · ${restricted} restricted · ${state.unreviewed.length} awaiting review${surface !== "all" ? ` · ${objects.filter((o) => !canRead(o, surface)).length} withheld from this preview` : ""}</span><span>sorted by last written</span></div>${
      filtered
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
    }<p class="caption">Withheld objects are recorded in a compile manifest, never dropped.</p>`,
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
/* Read receipts for one object, fetched on demand: the workspace snapshot carries
   the whole event log already, but a receipt is a containment query over it, which
   belongs on the server where it is indexed. */
function loadReads(id) {
  if (reads?.object_id === id || reads === "loading") return;
  reads = "loading";
  api(`/objects/${encodeURIComponent(id)}/reads`)
    .then((result) => {
      reads = result;
      if (location.hash.includes(encodeURIComponent(id))) render();
    })
    .catch(() => {
      reads = { object_id: id, reads: [], error: true };
    });
}
function readReceipts(o) {
  if (reads === "loading" || reads?.object_id !== o.id)
    return '<p class="mono muted">Looking up who has been served this…</p>';
  if (reads.error)
    return '<p class="mono muted">Read history could not be loaded.</p>';
  if (!reads.reads.length)
    return `<p class="muted">No surface has been served this object.${isRestricted(o) ? " Its reach is restricted, and the empty list is the evidence that the restriction held." : ""}</p>`;
  return `<table class="reads"><thead><tr><th>Surface</th><th>Served</th><th>In answer to</th></tr></thead><tbody>${reads.reads
    .map(
      (r) =>
        `<tr><td>${r.provider ? tag(r.provider) : '<span class="mono muted">unattributed</span>'}</td><td class="mono">${time(r.at)}</td><td>${r.query_text ? `<span class="quoted">${esc(r.query_text)}</span>` : `<span class="mono muted">digest ${esc(r.query_digest)}</span>`} <span class="mono muted">· via ${esc(r.surface)}</span></td></tr>`,
    )
    .join("")}</tbody></table>`;
}
function detail(id) {
  const o = objById(id);
  if (o) loadReads(id);
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
    `<div class="row mono muted mb"><a href="#/library">← Library</a> ${esc(o.id)}</div><article class="panel flush"><div class="panel-head"><p>${esc(o.content)}</p>${meta(o)}<div class="row wrap small muted mt"><span class="eyebrow">In force · UTC</span><span class="badge">${date(o.valid_from)}</span> → <span class="badge">${date(o.valid_until)}</span><span>${o.valid_until ? "after which this stops being retrieved" : "No end date set"}</span></div></div><div class="object-grid"><section><div class="eyebrow"><span class="green">←</span> Lineage · read-only</div><ol class="timeline">${events.map((e) => `<li><strong>${humanEvent(e)}</strong><span class="mono">${time(e.at)} · ${esc(e.actor)}${e.detail?.design_sample ? " · design example" : ""}${e.detail?.field === "locality" ? ` · ${esc(e.detail.to)}` : ""}</span></li>`).join("") || "<li>No recorded events in this window.</li>"}</ol><p class="mono muted">Origin: ${esc(o.provenance.origin_type)} · confidence ${o.provenance.confidence.toFixed(2)}</p>${o.provenance.note ? `<p class="small muted">${esc(o.provenance.note)}</p>` : ""}${sourceIds.length ? `<h3>Source objects</h3>${sourceIds.map((s) => (objById(s) ? `<a class="mono" href="#/object/${encodeURIComponent(s)}">${esc(s)}</a>` : `<p class="mono muted">${esc(s)} · external source ID</p>`)).join("")}` : ""}<p class="caption">Oldest first, because a history read newest-first is a list of surprises.</p><div class="eyebrow mt"><span class="green">→</span> Who has been served this</div><p class="small muted">Reach decides who <em>may</em> read this fact. This is who actually has — so a restriction is provable after the fact, not merely configured.</p>${readReceipts(o)}</section><section><div class="eyebrow"><span class="green">→</span> Reach · editable</div><form id="reach-form" data-id="${esc(id)}" class="mt">${[
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
    `<div class="row between mb"><div><h2>Captured, not yet remembered.</h2><p class="muted">Your submitted turns are kept as encrypted source material until extraction runs.</p></div><span class="badge">${episodes.length} pending</span></div>${episodes.map((o) => `<article class="panel mb"><div class="row between"><h3>${tag(o.provenance.provider)} <span class="badge">awaiting extraction</span></h3><span class="mono muted">${time(o.created_at)}</span></div><p class="capture-content">${esc(o.content)}</p><div class="row between wrap"><span class="capture-lock">${icon("lock")} Encrypted at rest · only your submitted turn</span><a class="btn" href="#/object/${encodeURIComponent(o.id)}">Inspect source</a></div></article>`).join("") || empty("The capture queue is clear", "Submitted turns will appear here when consented capture is enabled.", '<a class="btn" href="#/surfaces">Set up a surface</a>')}<div class="columns mt"><section class="panel"><span class="eyebrow">What happens next</span><h3 class="mt">Capture now. Judge later.</h3><p class="muted">The configured background worker extracts durable context, grounds it in the source turn, and records its provenance. ${connections?.capture_enabled ? "OpenAI receives candidate turns only; stored memories are not sent. Run a batch below or wait for the daily schedule." : "Extraction does not run in this page."}</p>${connections?.capture_enabled ? '<button class="primary mt" data-action="process-captures">Process pending turns with OpenAI</button>' : ""}<a href="#/review">Open the review queue →</a></section><section class="panel"><span class="eyebrow">Your control</span><h3 class="mt">Only turns you submit.</h3><p class="muted">coletar does not read assistant replies, other conversations, or background tabs. Capture requires explicit consent in the extension.</p><a href="#/surfaces">Manage surfaces →</a></section></div>`,
  );
}
/* The strip is a fixed five-stage legend, so the marker is placed by where the
   record time falls between the earliest event and now — not by pixel guesswork. */
function asOfOffset() {
  const events = state.events.map((e) => Date.parse(e.at)).filter(Number.isFinite);
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
    `<form id="audit-form" class="panel date-form"><label><span class="eyebrow">As of — record time</span><input aria-label="Record time" name="at" type="date" value="${auditAt}" required><p class="small">when the graph was asked</p></label><label><span class="eyebrow">In force — valid time</span><input aria-label="Valid time" name="valid" type="date" value="${auditValid}" required><p class="small">when the fact itself applied</p></label><button class="primary">${icon("audit")} Run query</button></form><div class="audit-heading">${auditResult ? `What the graph believed on ${date(auditResult.at)}, about facts in force on ${date(auditResult.valid)}. ${changed.length} ${changed.length === 1 ? "object differs" : "objects differ"} from today.` : "Two dates. One explainable history."}</div><div class="panel event-strip">${auditResult ? `<span class="as-of" style="left:${asOfOffset()}%"><i>as of ${esc(date(auditResult.at))}</i></span>` : ""}${[["written", "green"], ["reviewed", "green"], ["edited", "green"], ["reach restricted", "amber"], ["superseded", "green"]].map(([x, tone]) => `<span class="${tone}-dot">${x}</span>`).join("")}</div><div class="columns asymmetric"><section><h2>What changed since</h2>${
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
  if (manifest || previewing || !state.can_compile || destination === "markdown") return;
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
      "runs independently of coletar",
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
      )}</div><p class="caption">${score ? "Weights are published. Fresh for one day; staleness then decays over 30 days." : destination === "markdown" ? "Markdown is an owner export and has no compiler continuity score." : "Review your context, then preview to calculate the score from the actual compiler."}</p></div></div><div class="columns asymmetric"><section><div class="list-summary"><span>Migration manifest · ${entries.length} objects</span><span>${entries.filter((e) => e.status === "withheld").length} withheld</span></div>${entries.map((e) => `<div class="manifest-row ${e.status === "withheld" ? "withheld" : ""}"><span>${esc(objById(e.source_id)?.content || e.source_id)}</span><span class="mono ${e.status === "withheld" ? "amber" : "green"}">${esc(e.status)}</span></div>`).join("") || empty("No context to migrate", "Add or import memories to start.")}</section><section>${statusNotice()}<div class="panel"><span class="eyebrow">What leaves</span><p class="mt">${destination === "markdown" ? "All reviewed objects, including restricted ones, are included in your owner export." : `${entries.filter((e) => e.status !== "withheld").length} objects may compile to ${destinations.find((d) => d[0] === destination)[1]}. Withheld objects appear in the manifest, not in the destination’s context.`}</p><p class="muted">Afterwards you can disconnect coletar entirely. That is the point of compiling rather than syncing.</p></div><button class="primary wide mt" data-action="download" ${state.can_compile && entries.length ? "" : "disabled"}>${icon("download")} Build package</button><button class="wide" style="margin-top:10px" data-action="preview" ${state.can_compile && entries.length ? "" : "disabled"}>Preview what ${destinations.find((d) => d[0] === destination)[1]} will see</button>${manifest ? `<details class="panel mt"><summary>Installation instructions</summary><p class="small mt" style="white-space:pre-wrap">${esc(manifest.instructions)}</p></details>` : ""}</section></div>`,
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
      "Configure the remote MCP URL in ChatGPT's supported developer connector settings. If bearer authentication is unavailable in your account, use the coletar browser extension with the REST base URL. Provider-side setup must be done by you.",
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
    `<div class="stepper"><span class="current"><i>1</i> Connect</span><hr><span class="current"><i>2</i> Import your history</span><hr><span><i>3</i> Review what was found</span></div><div class="columns"><section><h2>Connected surfaces</h2><p class="muted">Every surface reads and writes the same graph. coletar has no chat interface of its own.</p>${connections ? hostedSurfaces() : surfaceList.map(([id, title, desc]) => `<div class="panel surface ${prefs.surfaces?.[id] ? "ready" : ""}"><div><h3>${mark(id)}${title}</h3><span class="mono">${desc}</span></div><div class="row"><span class="mono ${prefs.surfaces?.[id] ? "green" : "muted"}">${prefs.surfaces?.[id] ? "setup saved · unverified" : "not configured"}</span><button class="${prefs.surfaces?.[id] ? "quiet" : ""}" data-connect="${id}">${prefs.surfaces?.[id] ? "Manage" : "Connect"}</button></div></div>`).join("")}<p class="caption">${connections ? "Endpoints are live. A provider is connected only after you configure its client and successfully use it. Account integrations are never installed automatically." : "Setup controls simulate the connection flow. A saved setup does not establish a provider connection."}</p></section><section><h2>Import your history</h2><p class="muted">Click the export button inside your provider account, then drop the file here. coletar never signs in as you and never reads your archive on its own.</p><div class="panel dropzone" id="dropzone">${icon("upload")}<b>Drop a ChatGPT or Claude export</b><p class="small muted">.zip or conversations.json · processed ${state.hosted ? "on your hosted server" : "on this machine"}</p><label class="btn" for="import-file">Choose a file</label><input id="import-file" type="file" accept=".zip,.json" hidden></div><p class="caption">Pattern extraction · no third-party model calls · ${connections?.upload_limit_mb || 20} MB upload limit. Conservative extraction may miss facts.</p>${importReport ? `<div class="panel mt"><div class="row between"><b>${esc(importReport.name)}</b><span class="mono green">${importReport.busy ? "extracting…" : "complete"}</span></div><div class="progress mt"><span style="width:${importReport.busy ? 40 : 100}%"></span></div><p class="mono muted mt">${importReport.busy ? "Reading your uploaded file…" : `${importReport.conversations} conversations · ${importReport.turns} turns read · ${importReport.memories} new memories · ${importReport.corroborated} corroborated`}</p>${importReport.busy ? "" : '<a class="btn primary" href="#/review">Review what was found</a>'}</div>` : ""}<p class="caption">New memories appear in Review. Nothing compiles until every eligible object has been reviewed; live retrieval follows its existing policy.</p></section></div>`,
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
    `<div class="columns"><section><h2>Plan & usage</h2><p class="muted">Context tokens served to your surfaces, not how many memories you store.</p><div class="panel"><div class="row between"><b class="usage-total">${number(total)}</b><span class="mono muted">recorded tokens · no billing enabled</span></div><div class="progress mt">${Object.entries(usage)
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
      .join("")}</tbody></table></div><p class="caption">Scenario calculator, not current provider pricing or an invoice. Usage is based on the latest 2,000 events, not a billing period.</p><a class="btn" href="#/pricing">Explore proposed plans</a></section><section>${connections ? hostedKeys() : `<h2>API keys</h2><p class="muted">Try naming, scoping and revoking a key in this setup simulation.</p><div class="panel table-wrap"><table><thead><tr><th>Name</th><th>Scope</th><th>Status</th><th></th></tr></thead><tbody>${keys.map((k, i) => `<tr><td>${esc(k.name)}</td><td class="mono">${esc(k.scope)}</td><td class="mono muted">demo only</td><td><button class="quiet small" data-revoke="${i}">Revoke</button></td></tr>`).join("") || '<tr><td colspan="4" class="muted">No demo keys. These do not authenticate API requests.</td></tr>'}</tbody></table></div><button class="wide" style="margin-top:12px" data-action="new-key">${icon("plus")} New demo key</button>`}${
      isPublic()
        ? `<h2 class="mt">Workspace access</h2><div class="notice warning">${icon("info")} This workspace is served without a password. Anyone with the link can read every object — including ones marked restricted — and can add, edit, retire and import. Keep private context out of it.</div><p class="caption">Connector bearer keys are a separate authority and still gate the MCP and REST endpoints. The scheduled extraction batch still requires its own credential.</p>`
        : ""
    }<h2 class="mt">Default reach for new memories</h2><p class="muted">A browser preference for memories you add here. Existing memories and other surfaces are unaffected.</p><div class="panel"><div class="rule-row"><span class="mono">everything</span><span>${esc(prefs.defaultReach || "every surface")}</span></div>${rules.map((r, i) => `<div class="rule-row"><span class="mono">${esc(r.project)}</span><span>${esc(r.reach)} only <button class="quiet" data-remove-rule="${i}" aria-label="Remove ${esc(r.project)} rule">×</button></span></div>`).join("")}<button class="mt" data-action="new-rule">${icon("plus")} Add a rule</button></div><p class="caption">Project rules apply only to manual additions in this browser. Team policy and server-wide defaults are not configured.</p></section></div>`,
  );
}
function marketingNav() {
  return `<nav>${brand}<div class="row"><a class="hide-mobile" href="#/home">How it works</a><a class="hide-mobile" href="#/security">Security</a><a href="#/pricing">Pricing</a><a class="btn" href="#/library">Open prototype</a><a class="btn primary" href="#/surfaces">Get started</a></div></nav>`;
}
function marketingFooter() {
  return `<footer><div>${brand}<p class="muted mt">A portable AI workspace.<br>Memory as a first-class object.</p></div><div class="stack"><span class="eyebrow">Product</span><a href="#/surfaces">Surfaces</a><a href="#/pricing">Pricing</a><a href="#/library">Library</a></div><div class="stack"><span class="eyebrow">Trust</span><a href="#/security">Security & boundaries</a><a href="#/audit">Audit your context</a><a href="#/migrate">Take your context with you</a></div><div class="stack"><span class="eyebrow">Prototype</span><span class="muted">Local development preview<br>No signup or billing<br>No provider sign-in automation</span><a href="/" target="_blank" rel="noopener">Developer Inspector ↗</a></div></footer>`;
}
function home() {
  return `<div class="marketing">${marketingNav()}<main id="content" style="padding:0;max-width:none"><section class="hero"><div><h1>Your context should<br>outlive your model.</h1><p>Your facts, preferences and project state live in one typed graph that you own. You decide, fact by fact, which assistants may see what.</p><div class="row"><a class="btn primary" href="#/surfaces">Import your history ${icon("arrow")}</a><a class="btn" href="#/library">Explore the prototype</a></div><p class="small muted mt">No chat interface of our own — you stay where you already work.</p></div><div class="panel"><div class="eyebrow">One fact. Multiple surfaces. Your rules.</div><p>Handling the Northwind litigation matter; filings are due 14 November.</p><span class="mono muted">Design example · Claude-only reach</span><div class="mt">${[
    ["claude", true],
    ["chatgpt", false],
    ["local", false],
    ["claude_code", false],
  ]
    .map(
      ([s, allowed]) =>
        `<div class="reach-row ${allowed ? "" : "withheld"}"><span class="mono">${tag(s)}</span><span class="badge ${allowed ? "ok" : "reach-off"}">${allowed ? icon("check") : icon("lock")} ${allowed ? "may read" : "withheld"}</span></div>`,
    )
    .join(
      "",
    )}</div><span class="small muted">Withheld at read time, and recorded in the migration manifest.</span></div></section><div class="logo-strip">${[
    ["claude", "claude.ai"],
    ["claude", "Claude Desktop"],
    ["claude_code", "Claude Code"],
    ["chatgpt", "chatgpt.com"],
    ["local", "Ollama & local models"],
  ]
    .map(([m, label]) => `<span>${mark(m)}${label}</span>`)
    .join("")}<span>MCP · REST · Python · JavaScript</span></div><section class="features"><div><span class="feature-chip">${icon("lock")}</span><h2>Selective context</h2><p>Not every assistant should know everything. Choose reach per fact. The policy is enforced when context is read.</p></div><div><span class="feature-chip">${icon("audit")}</span><h2>Provenance you can prove</h2><p>Every object records where it came from, what changed it, and when. Ask what the graph believed on a past date.</p></div><div><span class="feature-chip">${icon("migrate")}</span><h2>An exit that works</h2><p>Compile context into a Claude import, a Custom GPT package or an Ollama Modelfile. Then disconnect us entirely.</p></div></section><section class="story"><div><h2>Every fact can<br>explain itself.</h2><p>Your library is more than a collection of sentences. Each memory carries its source, confidence, scope and full revision history.</p><a href="#/library">Explore the Context Inspector →</a></div><div class="panel"><span class="eyebrow">Lineage · design example</span><ol class="timeline">${["Turn captured on claude.ai", "Extracted and grounded in the source", "Created with provenance", "Reviewed by you", "Reach changed"].map((x) => `<li><strong>${x}</strong><span class="mono">An explainable step in the same history</span></li>`).join("")}</ol></div></section><section class="hero"><div class="panel"><div class="eyebrow">Continuity score · published weights</div>${[
    ["Object coverage", "40%"],
    ["Fidelity", "30%"],
    ["Scope preservation", "20%"],
    ["Staleness", "10%"],
  ]
    .map(([l, v]) => `<div class="rule-row"><span>${l}</span><b>${v}</b></div>`)
    .join(
      "",
    )}<p class="small muted mt">Calculated from the actual compiler manifest.</p></div><div><h2 style="font-size:38px">Leaving is a feature,<br>not a support ticket.</h2><p>A package the destination understands, a manifest of what made it across, and a score that explains what was preserved.</p><a href="#/migrate">Try a migration →</a></div></section><section class="cta"><h2>Bring your history.<br>Keep it when you switch.</h2><p class="muted">Start with your own export or explore the design examples.</p><a class="btn primary" href="#/surfaces">Import your history ${icon("arrow")}</a></section></main>${marketingFooter()}</div>`;
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
  return `<div class="marketing">${marketingNav()}<main id="content" class="privacy"><h1>Your context, under your control.</h1><h2>Only what you choose to share</h2><p>Import files you export yourself. Consented extension capture is limited to submitted user turns on the active page. coletar never signs in as you, replays provider sessions, or automates a provider UI.</p><h2 class="mt">A record for every change</h2><p>Memories carry provenance. Edits and retirements append events. Retirement preserves history; raw-turn erasure uses the separate crypto-shredding workflow.</p><h2 class="mt">${state.hosted ? "Hosted preview boundaries" : "Local prototype boundaries"}</h2><p>${state.hosted ? "This single-owner workspace runs on Vercel with Supabase Postgres. Workspace pages require a password; connectors use separate scoped bearer keys. Account signup and billing are not enabled. Imports run on the hosted server without third-party model calls. Captured turns require an opt-in client. When OpenAI extraction is enabled, only candidate turns are sent to OpenAI; stored memories and the rest of the graph are not sent. OpenAI is the extraction subprocessor. Batches run daily and on demand." : "This app runs on loopback and has no account/session system. Import uses the local pattern extractor and makes no model calls. Connection and API-key screens simulate setup; their saved values do not grant access."}</p><h2 class="mt">A real way out</h2><p>The three provider compilers produce downloadable native packages after review. Destination reach is enforced by the compiler. Markdown is an owner export and includes restricted context.</p><a class="btn primary mt" href="#/library">Explore your library</a></main>${marketingFooter()}</div>`;
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
  document.title = `${route === "home" ? "Your context, everywhere" : route.charAt(0).toUpperCase() + route.slice(1)} · coletar`;
  bind();
}
function modal(title, body, submitText, onSubmit) {
  const d = $("#dialog");
  d.innerHTML = `<form method="dialog" id="modal-form"><header><h2>${title}</h2><button type="button" class="quiet" data-close aria-label="Close dialog">${icon("close")}</button></header>${body}<div class="error-message" role="alert"></div><footer><button type="button" data-close>Cancel</button>${submitText ? `<button class="primary" type="submit">${submitText}</button>` : ""}</footer></form>`;
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
  d.showModal();
}
function memoryModal(id) {
  const o = id ? objById(id) : null;
  modal(
    o ? "Edit memory" : "Add memory",
    `<label><span class="eyebrow">Your memory</span><textarea name="content" placeholder="A fact, preference or decision worth keeping" required maxlength="20000">${esc(o?.content || "")}</textarea></label>${o ? "" : `<label><span class="eyebrow">Kind</span><select name="kind">${["fact", "preference", "instruction", "goal", "correction"].map((k) => `<option>${k}</option>`).join("")}</select></label><label><span class="eyebrow">Project · optional</span><input name="project" placeholder="e.g. proj_ledger" maxlength="200"></label>`}<p class="small muted mt">${o ? "Editing preserves the previous version and records your review." : "Explicitly added by you, with provenance. Review before compiling."}</p>`,
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
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination }),
    });
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail);
    }
    downloadBlob(await response.blob(), `coletar-${destination}.zip`);
    toast("Package built. Your download is ready.");
    return;
  }
  if (action === "audit-export") {
    downloadBlob(
      new Blob([JSON.stringify(auditResult, null, 2)], {
        type: "application/json",
      }),
      "coletar-audit-snapshot.json",
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
      "Install the coletar browser extension manually. Enable capture only on the active site after reviewing its consent screen.",
    chatgpt:
      "Install the coletar browser extension manually. Only submitted user turns on the active tab may be captured.",
    desktop:
      "Add coletar serve-mcp-stdio to your Claude Desktop MCP configuration. The client launches a local process under your OS identity.",
    code: "Use the Claude Code transcript importer on files the application writes to your own disk. This is not background browser capture.",
    local:
      "Route your OpenAI-compatible client through the coletar local proxy, with Ollama or your chosen local runtime as its backend.",
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
  document.querySelectorAll("[data-surface]").forEach(
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
            "mono " + (delta === null ? "muted" : delta <= 0 ? "green" : "amber");
        });
      }),
  );
  if ($("#search-form"))
    $("#search-form").onsubmit = (e) => {
      e.preventDefault();
      query = $("#search").value;
      render();
    };
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
window.addEventListener("hashchange", () => {
  render();
  window.scrollTo(0, 0);
});
refresh()
  .then(render)
  .catch((error) => {
    $("#app").innerHTML =
      `<main class="loading"><h1>Couldn’t open your workspace.</h1><p>${esc(error.message)}</p><button onclick="location.reload()">Try again</button></main>`;
  });
