/* Second design experiment: editorial landscapes and inspectable context.
   All demonstration state is synthetic. Workspace actions use product.js APIs. */
let horizonObserver;
const horizonStory = {
  title: "Your context,<br>anywhere and everywhere.",
  description: "Stay in context no matter the model.\nSecure, audited, and compliant wherever it goes.",
};
/* The hero background is a looping reel rather than one still. Clips are held at
   720p because the shade gradients sit on top of them and nothing in the frame is
   ever read for detail; the weight saved matters more than the resolution lost.
   Only the first clip is fetched up front — the rest are given a src as they come
   up in the rotation, so opening the page costs one clip, not the whole reel. */
const heroClips = [
  "drift.mp4",
  "current.mp4",
  "ridge.mp4",
  "tide.mp4",
  "canopy.mp4",
];
let heroClip = 0;
// Longest a single clip holds the hero before the reel moves on, in seconds.
const HERO_DWELL = 12;
/* Every call to action on this page points at one of two places, and which one
   depends on whether the reader already has a workspace. Sending a signed-out
   visitor to `#/library` used to work only because the whole app was a login wall;
   now that the marketing pages render without an account, an unguarded link there
   is a button that silently turns into a sign-in form. These two helpers are the
   single place that decision is made.

   Read through `window.coletaAuth` rather than captured at load: these are called
   during render, by which point auth.js has resolved the session, and a value
   captured at parse time would be stale for the whole page. */
const signedIn = () => Boolean(window.coletaAuth?.session?.signedIn);
const canSignUp = () => Boolean(window.coletaAuth?.session?.canSignUp);
/** Where "start using this" goes. */
const ctaHref = () =>
  signedIn() ? "#/library" : canSignUp() ? "#/sign-up" : "#/sign-in";
const ctaLabel = () =>
  signedIn() ? "Open your workspace" : canSignUp() ? "Get started" : "Sign in";
function horizonNav(onHero = false) {
  return `<header class="horizon-nav site-nav ${onHero ? "on-hero" : ""}">
    <a class="brand" href="#/home">coleta</a>
    <nav class="horizon-links" aria-label="Main">
      <details class="nav-disclosure"><summary>Product ${icon("chevron")}</summary><div class="nav-popover"><a href="#/home/how-it-works" data-scroll="how-it-works">${icon("network")}<span>How it works<small>A continuous thread for your thinking</small></span></a><a href="#/home/product" data-scroll="product">${icon("shield")}<span>Selective context<small>Decide who can know what</small></span></a><a href="${ctaHref()}">${icon("library")}<span>Your workspace<small>Explore your collection</small></span></a></div></details>
      <a href="#/security">Trust & privacy</a>
      <details class="nav-disclosure"><summary>Resources ${icon("chevron")}</summary><div class="nav-popover"><a href="https://github.com/chrisdten3/coletar" target="_blank" rel="noopener">${icon("code")}<span>Developers<small>API, SDKs & MCP documentation</small></span></a><a href="#/migrate">${icon("download")}<span>Export & migrate<small>Take your context with you</small></span></a><a href="#/audit">${icon("audit")}<span>Context history<small>See how your knowledge changed</small></span></a></div></details>
    </nav>
    <div class="horizon-nav-actions">${
      signedIn()
        ? `<a class="nav-workspace" href="#/library">Open your workspace ${icon("arrow")}</a>`
        : `<a class="nav-signin" href="#/sign-in">Sign in</a><a class="nav-workspace" href="${ctaHref()}">${ctaLabel()} ${icon("arrow")}</a>`
    }<button class="horizon-menu" aria-label="Toggle navigation" aria-expanded="false" aria-controls="horizon-mobile-nav">${icon("menu")}</button></div>
    <nav id="horizon-mobile-nav" class="horizon-mobile-nav" aria-label="Mobile navigation" hidden><a href="#/home/how-it-works" data-scroll="how-it-works">How it works</a><a href="#/home/product" data-scroll="product">Selective context</a><a href="#/security">Trust & privacy</a><a href="https://github.com/chrisdten3/coletar" target="_blank" rel="noopener">Developers ${icon("external")}</a>${
      signedIn()
        ? `<a href="#/library">Open your workspace</a>`
        : `<a href="#/sign-in">Sign in</a><a href="${ctaHref()}">${ctaLabel()}</a>`
    }</nav>
  </header>`;
}
function productOfferings() {
  return `<section id="product" class="horizon-features horizon-section">
    <div class="horizon-section-head centered"><h2>Coleta keeps your context<br><span>up to date, mobile, and secure.</span></h2></div>
    <div class="horizon-feature-grid">
      <a class="horizon-feature" href="#/surfaces">
        <div class="feature-visual offering-visual sync-visual"><div class="offering-window">
          <div class="offering-toolbar"><span>${icon("network")} Live sync</span><small class="status-pill">Connected</small></div>
          <div class="sync-update"><small>PROJECT ATLAS · JUST UPDATED</small><p>Launch moved to 24 October.</p><span>${icon("check")} Saved with its source</span></div>
          <div class="offering-row"><span>${mark("claude")} Claude</span><small>Available ${icon("check")}</small></div>
          <div class="offering-row"><span>${mark("ollama")} Ollama</span><small>Available ${icon("check")}</small></div>
        </div><span class="offering-example">Example connection activity</span></div>
        <div class="feature-copy"><span>01 / UP TO DATE</span><h3>Live sync ${icon("external")}</h3><p>Keep useful context current across supported connections. Save a detail once so connected assistants can find it when it’s relevant.</p></div>
      </a>
      <a class="horizon-feature" href="#/migrate">
        <div class="feature-visual offering-visual migration-visual"><div class="offering-window">
          <div class="offering-toolbar"><span>${icon("download")} Migration preview</span><small>01 → 02</small></div>
          <div class="migration-route"><span>${mark("chatgpt")} ChatGPT</span>${icon("arrow")}<span>${mark("claude")} Claude</span></div>
          <div class="offering-row"><span>Project instructions</span><small>Native ${icon("check")}</small></div>
          <div class="offering-row"><span>Saved context</span><small>Reconstructed</small></div>
          <div class="preview-action">Download your package ${icon("download")}</div>
        </div><span class="offering-example">Example destination package</span></div>
        <div class="feature-copy"><span>02 / MOBILE</span><h3>True migration ${icon("external")}</h3><p>Move your work into another tool’s own format. Preview what carries over, see any gaps, then download a package you install yourself.</p></div>
      </a>
      <a class="horizon-feature" href="${ctaHref()}">
        <div class="feature-visual offering-visual selective-visual"><div class="offering-window">
          <div class="offering-toolbar"><span>${icon("shield")} Context access</span><small>Per detail</small></div>
          <div class="access-detail"><small>PERSONAL CONTEXT</small><p>My private journal</p></div>
          <div class="offering-row"><span>${mark("claude")} Claude</span><span class="preview-switch"><i></i><small>Off</small></span></div>
          <div class="offering-row"><span>${mark("local")} Local model</span><span class="preview-switch on"><i></i><small>On</small></span></div>
          <div class="access-note">${icon("shield")} Stays with your local model</div>
        </div><span class="offering-example">Example access policy</span></div>
        <div class="feature-copy"><span>03 / SECURE</span><h3>Selective context ${icon("external")}</h3><p>Choose which providers can read each detail. Share useful project context while keeping personal information within the boundaries you set.</p></div>
      </a>
    </div>
  </section>`;
}
function horizonHome() {
  const story = horizonStory;
  return `<div class="horizon">${horizonNav(true)}<main id="content" class="horizon-main">
    <section class="horizon-hero" aria-label="Introduction"><div class="horizon-reel" aria-hidden="true"><video class="horizon-clip is-active" data-hero-clip="0" src="/static/video/${heroClips[0]}" poster="/static/images/hero-poster.jpg" muted playsinline preload="auto" disablepictureinpicture disableremoteplayback></video><video class="horizon-clip" data-hero-clip="1" poster="/static/images/hero-poster.jpg" muted playsinline preload="none" disablepictureinpicture disableremoteplayback></video></div><div class="hero-shade"></div><div class="horizon-hero-grid"><div class="horizon-hero-copy"><h1 id="horizon-title">${story.title}</h1><p id="horizon-description">${story.description.replace("\n", "<br> ")}</p><div class="horizon-hero-actions"><a class="btn horizon-primary" href="${ctaHref()}">${ctaLabel()} ${icon("arrow")}</a><a class="hero-secondary" href="#/home/how-it-works" data-scroll="how-it-works">See how it works ${icon("external")}</a></div></div></div></section>
    <section class="horizon-intro"><div class="horizon-platforms"><span>Keep working where you think best.</span><div>${mark("claude")}Claude</div><div>${mark("chatgpt")}ChatGPT</div><div>${mark("ollama")}Ollama</div><a href="#/surfaces">Explore connections ${icon("external")}</a></div><div class="horizon-statement"><h2>Great work doesn’t<br>start from zero.</h2><p>Your preferences. The decisions behind your project. The details you’ve already explained. Coleta brings that context to any model, and builds your knowledge base from the conversations you’ve already had.</p></div></section>
    ${productOfferings()}
    <section id="how-it-works" class="horizon-section truth-section" aria-label="Your AI’s source of truth"><picture><source media="(max-width: 760px)" srcset="/static/images/context-flow-mobile.svg" width="600" height="800"><img class="truth-diagram" src="/static/images/context-flow.svg" alt="Your AI’s source of truth: your exports and notes flow into Coleta, where context keeps its source, history, and access rules. Eligible context is then available to connected assistants." width="1200" height="560" loading="lazy"></picture></section>
    <section class="horizon-section horizon-portable"><div class="horizon-section-head centered"><span class="section-label">OPEN AT BOTH ENDS</span><h2>Bring your history.<br>Keep your freedom.</h2></div><div class="portable-grid"><article class="portable-card import"><img src="/static/images/misty-valley.jpg" alt="Misty green mountain valley at sunrise" loading="lazy" width="1800" height="1200"><div class="portable-card-shade"></div><div class="portable-paper" aria-hidden="true">${icon("file")}<span>conversations.json</span><small>Your history, ready for a new home.</small></div><div class="portable-copy"><span class="section-label">START WITH WHAT YOU KNOW</span><h3>You’re already<br>part of the way there.</h3><p>Choose a conversation export. Inspect the useful context, then decide where it belongs.</p><a class="btn" href="#/surfaces">Import your history ${icon("arrow")}</a></div></article><article class="portable-card export"><div class="export-diagram" aria-hidden="true"><div class="export-core">coleta</div><div class="export-line"></div><span>${mark("claude")}</span><span>${mark("chatgpt")}</span><span>${mark("ollama")}</span></div><div class="portable-copy"><span class="section-label">YOUR RIGHT TO LEAVE</span><h3>Another tool.<br>Still your context.</h3><p>Preview the manifest and Continuity Score. Download a package you install at your next destination.</p><a class="btn" href="#/migrate">Explore migration ${icon("arrow")}</a></div></article></div></section>
    <section class="horizon-faq horizon-section"><div><span class="section-label">A FEW THINGS WORTH KNOWING</span><h2>Clarity,<br>before anything.</h2><a href="#/security">Our boundaries ${icon("external")}</a></div><div class="faq-list">${[
      [
        "Do I have to change how I use AI?",
        "Keep using your existing AI tools. coleta is a workspace for your context, with supported connectors and exports. It does not add another chat interface.",
      ],
      [
        "Can every assistant read everything?",
        "You decide which provider policies may read each object. Eligibility does not guarantee that an assistant retrieves a fact: the query still determines what is returned. Claude web, Desktop, and Code currently share one provider policy.",
      ],
      [
        "How does my existing history get here?",
        "You export your conversations from the provider and choose the downloaded file in coleta. The current web importer uses local pattern extraction. Optional automatic browser mode requires separate consent and captures your prompt and its completed reply on the active, visible supported page. Manual capture reads only your submitted text.",
      ],
      [
        "What happens when a detail changes?",
        "Corrections and edits preserve a history of what changed. Retired objects stop participating in active context, while their provenance and events remain inspectable.",
      ],
      [
        "Can I take my context somewhere else?",
        "Yes. Review your context, preview a destination package, then download it and follow the installation steps. The manifest distinguishes native, reconstructed, unsupported, and withheld records. Owner Markdown export is a separate option.",
      ],
    ]
      .map(
        ([q, a]) =>
          `<details><summary>${q}${icon("plus")}</summary><p>${a}</p></details>`,
      )
      .join("")}</div></section>
    <section class="horizon-closing"><img src="/static/images/alpine-valley.webp" alt="" loading="lazy" width="2200" height="1375"><div class="closing-shade"></div><span class="section-label">THERE’S MORE AHEAD.</span><h2>Go further.<br>Bring your thinking.</h2><a class="btn" href="${ctaHref()}">${ctaLabel()} ${icon("arrow")}</a></section>
    </main><footer class="horizon-footer"><div class="horizon-footer-top"><div><a class="brand" href="#/home">coleta</a><p>Independent context.<br>Human control.</p></div><div><span>Product</span><a href="#/library">Workspace</a><a href="#/surfaces">Connections</a><a href="#/migrate">Export & migrate</a></div><div><span>Trust</span><a href="#/security">Privacy & boundaries</a><a href="#/audit">History</a><a href="https://github.com/chrisdten3/coletar/blob/main/docs/CONTINUITY_SCORE.md" target="_blank" rel="noopener">Continuity Score ${icon("external")}</a></div><div><span>For builders</span><a href="https://github.com/chrisdten3/coletar" target="_blank" rel="noopener">Source & docs ${icon("external")}</a><a href="/" target="_blank" rel="noopener">Developer Inspector ${icon("external")}</a><button class="photo-credits quiet">Media credits</button></div></div><div class="horizon-wordmark" aria-hidden="true">coleta<span>↗</span></div><div class="horizon-footer-bottom"><span>A portable AI workspace.</span><span>Your context. Your rules. Your next step.</span></div></footer></div>`;
}
function openWorkspaceSearch() {
  modal(
    "Find your context",
    `<label class="command-input"><span class="sr-only">Search context</span>${icon("search")}<input id="command-search" type="search" placeholder="Search facts, preferences, decisions…" autocomplete="off"></label><div id="command-results" class="command-results" aria-live="polite"></div><p class="command-hint">↓ to browse results · Enter to open · Esc to close</p>`,
    "",
    () => {},
  );
  const update = () => {
    const q = $("#command-search").value.toLowerCase();
    const matches = activeObjects()
      .filter((o) => o.content.toLowerCase().includes(q))
      .slice(0, 7);
    $("#command-results").innerHTML = matches.length
      ? matches
          .map(
            (o) =>
              `<a href="#/object/${encodeURIComponent(o.id)}">${icon("file")}<span>${esc(o.content)}<small>${esc(o.kind || o.type)} · ${esc(o.provenance.provider)}</small></span>${icon("arrow")}</a>`,
          )
          .join("")
      : '<p class="command-empty">No matching context. Try another phrase.</p>';
    $("#command-results")
      .querySelectorAll("a")
      .forEach((a) => (a.onclick = () => $("#dialog").close()));
  };
  $("#command-search").oninput = update;
  $("#command-search").onkeydown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      $("#command-results a")?.focus();
    }
    if (e.key === "Enter") {
      e.preventDefault();
      $("#command-results a")?.click();
    }
  };
  $("#command-results").onkeydown = (e) => {
    const links = [...$("#command-results").querySelectorAll("a")];
    const i = links.indexOf(document.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      links[Math.min(i + 1, links.length - 1)]?.focus();
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (i <= 0) $("#command-search").focus();
      else links[i - 1].focus();
    }
  };
  update();
  $("#command-search").focus();
}
/* The reel plays one clip at a time across two stacked <video> elements: while one
   is on screen the other already holds the next clip, so the handover is a fade
   instead of a stall. Motion here is decoration. If it cannot run — autoplay
   refused, reduced motion asked for, the fetch failed — the poster frame is the
   design rather than a broken state, so every failure path ends quietly. */
function bindHeroReel() {
  const reel = $(".horizon-reel");
  if (!reel || reel.dataset.heroBound) return;
  reel.dataset.heroBound = "true";
  const clips = [...reel.querySelectorAll(".horizon-clip")];
  heroClip = 0;
  clips[0].dataset.clipIndex = "0";

  const load = (el, index) => {
    const src = "/static/video/" + heroClips[index];
    el.dataset.clipIndex = String(index);
    if (el.getAttribute("src") === src) return;
    el.setAttribute("src", src);
    el.preload = "auto";
    el.load();
  };

  // Returns false when the next clip has not buffered enough to cut to yet.
  const advance = () => {
    const current = clips.find((c) => c.classList.contains("is-active"));
    const next = clips.find((c) => c !== current);
    if (!current || next.readyState < 2) return false;
    next.currentTime = 0;
    next.play().catch(() => {});
    next.classList.add("is-active");
    current.classList.remove("is-active");
    current.pause();
    heroClip = Number(next.dataset.clipIndex);
    load(current, (heroClip + 1) % heroClips.length);
    return true;
  };

  clips.forEach((clip) => {
    clip.addEventListener("timeupdate", () => {
      if (!clip.classList.contains("is-active")) return;
      const left = clip.duration - clip.currentTime;
      // The clips run from 8 to 42 seconds. Left alone, the long one would hold
      // the hero five times longer than its neighbours and read as a stall
      // rather than a reel, so a clip is cut short once it has had its turn.
      if (
        clip.currentTime >= HERO_DWELL ||
        (Number.isFinite(left) && left <= 0.9)
      )
        advance();
    });
    clip.addEventListener("ended", () => {
      // Whatever is next is not ready; replaying beats holding a frozen frame.
      if (clip.classList.contains("is-active") && !advance()) {
        clip.currentTime = 0;
        clip.play().catch(() => {});
      }
    });
  });

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    clips.forEach((c) => c.pause());
    return;
  }
  load(clips[1], 1);
  clips[0].play().catch(() => {});
}
function bindHorizon() {
  bindHeroReel();
  if ($(".horizon-menu"))
    $(".horizon-menu").onclick = () => {
      const open = $(".horizon-menu").getAttribute("aria-expanded") !== "true";
      $(".horizon-menu").setAttribute("aria-expanded", String(open));
      $("#horizon-mobile-nav").hidden = !open;
    };
  document.querySelectorAll(".horizon-nav").forEach(
    (nav) =>
      (nav.onkeydown = (e) => {
        if (e.key === "Escape") {
          const activeSummary = nav.querySelector("details[open] summary");
          nav.querySelectorAll("details").forEach((d) => (d.open = false));
          const menu = $(".horizon-menu");
          menu?.setAttribute("aria-expanded", "false");
          if ($("#horizon-mobile-nav")) $("#horizon-mobile-nav").hidden = true;
          if (activeSummary) activeSummary.focus();
          else menu?.focus();
        }
      }),
  );
  document.querySelectorAll(".nav-disclosure").forEach(
    (d) =>
      (d.ontoggle = () => {
        if (d.open)
          document.querySelectorAll(".nav-disclosure").forEach((other) => {
            if (other !== d) other.open = false;
          });
      }),
  );
  document.querySelectorAll("[data-scroll]").forEach((a) => {
    if (a.dataset.horizonBound) return;
    a.dataset.horizonBound = "true";
    a.addEventListener("click", () => {
      document
        .querySelectorAll(".nav-disclosure")
        .forEach((d) => (d.open = false));
      if ($("#horizon-mobile-nav")) $("#horizon-mobile-nav").hidden = true;
      $(".horizon-menu")?.setAttribute("aria-expanded", "false");
    });
  });
  document
    .querySelectorAll("[data-command]")
    .forEach((b) => (b.onclick = openWorkspaceSearch));
  if ($(".photo-credits"))
    $(".photo-credits").onclick = () =>
      modal(
        "Media",
        `<p>Alpine valley photograph by <a href="https://unsplash.com/photos/ahsuhZiBAAY" target="_blank" rel="noopener">Thierry Lemaitre / Unsplash</a>, used under the Unsplash License.</p><p>Misty valley photograph by <a href="https://www.pexels.com/photo/4542933/" target="_blank" rel="noopener">Quang Nguyen Vinh / Pexels</a>, used under the Pexels License.</p><p>Hero reel: five stock clips supplied for this design experiment. <b>Attribution is outstanding</b> — see <span class="mono">static/video/CREDITS.md</span>. They should not ship to a public deployment until each clip names its source and licence.</p><p class="muted small">Media is served locally. Nothing was taken from the design-reference websites.</p>`,
        "",
        () => {},
      );
  horizonObserver?.disconnect();
  if ($(".horizon-hero")) {
    horizonObserver = new IntersectionObserver(
      ([entry]) =>
        $(".horizon-nav")?.classList.toggle("scrolled", !entry.isIntersecting),
      { rootMargin: "-90px 0px 0px 0px" },
    );
    horizonObserver.observe($(".horizon-hero"));
  }
}
document.addEventListener("keydown", (e) => {
  if (
    (e.metaKey || e.ctrlKey) &&
    e.key.toLowerCase() === "k" &&
    document.querySelector(".workspace")
  ) {
    e.preventDefault();
    if (document.querySelector("#dialog").open)
      document.querySelector("#dialog").close();
    else openWorkspaceSearch();
  }
});
