/* Second design experiment: editorial landscapes and inspectable context.
   All demonstration state is synthetic. Workspace actions use product.js APIs. */
let horizonSlide = 0;
let flowStep = 0;
let horizonObserver;
const horizonStories = [
  {
    label: "Your context",
    title: "Your context,<br>anywhere and everywhere.",
    description:
      "Stay in context no matter the model.\nSecure, audited, and compliant wherever it goes.",
    kicker: "A portable workspace for your AI context",
    receipt: "A little more you.<br>In every conversation.",
    fact: "Use plain language. Keep explanations concise.",
    type: "Writing preference",
  },
  {
    label: "Your rules",
    title: "A little personal.<br>Entirely in your control.",
    description:
      "Choose which assistants can read each detail.\nLet the useful things travel. Keep the private things close.",
    kicker: "Selective context, down to the individual fact",
    receipt: "The right context.<br>Only in the right hands.",
    fact: "Keep my personal journal on my local model.",
    type: "Personal context",
  },
  {
    label: "Your next step",
    title: "New conversations.<br>Same understanding.",
    description:
      "Keep the thread of your work, even when your tools change.\nYour context has a history. And a way forward.",
    kicker: "Continuity without the lock-in",
    receipt: "Everything changes.<br>Keep what matters.",
    fact: "The Atlas launch is now 24 October.",
    type: "Project decision",
  },
];
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
function horizonNav(onHero = false) {
  return `<header class="horizon-nav site-nav ${onHero ? "on-hero" : ""}">
    <a class="brand" href="#/home">${wordmark}coletar</a>
    <nav class="horizon-links" aria-label="Main">
      <details class="nav-disclosure"><summary>Product ${icon("chevron")}</summary><div class="nav-popover"><a href="#/home/how-it-works" data-scroll="how-it-works">${icon("network")}<span>How it works<small>A continuous thread for your thinking</small></span></a><a href="#/home/product" data-scroll="product">${icon("shield")}<span>Selective context<small>Decide who can know what</small></span></a><a href="#/library">${icon("library")}<span>Your workspace<small>Explore your collection</small></span></a></div></details>
      <a href="#/security">Trust & privacy</a>
      <details class="nav-disclosure"><summary>Resources ${icon("chevron")}</summary><div class="nav-popover"><a href="https://github.com/chrisdten3/coletar" target="_blank" rel="noopener">${icon("code")}<span>Developers<small>API, SDKs & MCP documentation</small></span></a><a href="#/migrate">${icon("download")}<span>Export & migrate<small>Take your context with you</small></span></a><a href="#/audit">${icon("audit")}<span>Context history<small>See how your knowledge changed</small></span></a></div></details>
    </nav>
    <div class="horizon-nav-actions"><a class="nav-workspace" href="#/library">Open workspace ${icon("arrow")}</a><button class="horizon-menu" aria-label="Toggle navigation" aria-expanded="false" aria-controls="horizon-mobile-nav">${icon("menu")}</button></div>
    <nav id="horizon-mobile-nav" class="horizon-mobile-nav" aria-label="Mobile navigation" hidden><a href="#/home/how-it-works" data-scroll="how-it-works">How it works</a><a href="#/home/product" data-scroll="product">Selective context</a><a href="#/security">Trust & privacy</a><a href="https://github.com/chrisdten3/coletar" target="_blank" rel="noopener">Developers ${icon("external")}</a></nav>
  </header>`;
}
function heroReceipt() {
  const story = horizonStories[horizonSlide];
  const providers =
    horizonSlide === 1 ? ["local"] : ["claude", "chatgpt", "local"];
  return `<div class="receipt-top"><span class="receipt-orbit">${icon("library")}</span><span>YOUR CONTEXT, CONNECTED</span>${icon("external")}</div><h2>${story.receipt}</h2><button class="receipt-fact" data-source="${[0, 3, 2][horizonSlide]}"><span>${story.type} ${icon("external")}</span><p>${story.fact}</p><small>${icon("check")} Source attached · inspect this example</small></button><div class="receipt-bottom"><span>Available to</span><div>${providers.map((p) => `<span title="${providerName(p)}">${mark(p, providerName(p))}</span>`).join("")}</div><span class="receipt-note">Synthetic example</span></div>`;
}
function flowPreview() {
  const titles = [
    "Bring what matters.",
    "Give every detail a place.",
    "Carry it into the next conversation.",
  ];
  const descriptions = [
    "Start with an export you choose. Useful details keep a link to the conversation they came from.",
    "Facts, preferences, and decisions share one collection, with their own source, scope, and history.",
    "Connected assistants can retrieve eligible context. Your access policy decides what stays private.",
  ];
  return `<div class="flow-explainer"><span class="section-label">0${flowStep + 1} / THE CONTEXT LOOP</span><h3>${titles[flowStep]}</h3><p>${descriptions[flowStep]}</p><div class="flow-control" role="group" aria-label="Context workflow">${["Collect", "Organize", "Continue"].map((v, i) => `<button data-flow-step="${i}" aria-pressed="${flowStep === i}"><span>0${i + 1}</span>${v}</button>`).join("")}</div></div><div class="flow-canvas stage-${flowStep}" aria-label="Illustrative context flow">
  <div class="flow-sources"><span class="flow-token">${mark("claude")} Claude export</span><span class="flow-token">${mark("chatgpt")} ChatGPT export</span><span class="flow-token">${icon("file")} Your own notes</span></div><div class="flow-connection" aria-hidden="true"></div><div class="flow-core"><div class="flow-core-brand">${wordmark}<span>coletar</span></div><span class="flow-object">${icon("check")} Clear, concise writing</span><span class="flow-object">${icon("library")} Project Atlas</span><span class="flow-object">${icon("audit")} Launch: 24 October</span><span class="flow-core-caption">Context + source + your rules</span></div><div class="flow-connection" aria-hidden="true"></div><div class="flow-destinations">${["claude", "chatgpt", "local"].map((p) => `<span class="flow-token">${mark(p)}${providerName(p)}</span>`).join("")}</div><span class="flow-footnote">Illustrative flow · availability depends on the connection and access policy</span></div>`;
}
function horizonHome() {
  const story = horizonStories[horizonSlide];
  return `<div class="horizon">${horizonNav(true)}<main id="content" class="horizon-main">
    <section class="horizon-hero" aria-label="Introduction"><div class="horizon-reel" aria-hidden="true"><video class="horizon-clip is-active" data-hero-clip="0" src="/static/video/${heroClips[0]}" poster="/static/images/hero-poster.jpg" muted playsinline preload="auto" disablepictureinpicture disableremoteplayback></video><video class="horizon-clip" data-hero-clip="1" poster="/static/images/hero-poster.jpg" muted playsinline preload="none" disablepictureinpicture disableremoteplayback></video></div><div class="hero-shade"></div><div class="horizon-hero-grid"><div class="horizon-hero-copy"><span id="horizon-kicker" class="hero-overline">${icon("dot")} ${story.kicker}</span><h1 id="horizon-title">${story.title}</h1><p id="horizon-description">${story.description.replace("\n", "<br>")}</p><div class="horizon-hero-actions"><a class="btn horizon-primary" href="#/library">Find your continuity ${icon("arrow")}</a><a class="hero-secondary" href="#/home/how-it-works" data-scroll="how-it-works">See how it works ${icon("external")}</a></div></div><div id="hero-receipt" class="hero-receipt">${heroReceipt()}</div></div><div class="hero-story-nav" role="group" aria-label="Explore coletar"><span class="hero-story-intro">A place for your thinking.<br>A way to take it further.</span>${horizonStories.map((s, i) => `<button data-horizon-slide="${i}" aria-pressed="${horizonSlide === i}"><span>0${i + 1}</span><strong>${s.label}</strong>${icon("external")}</button>`).join("")}</div></section>
    <section class="horizon-intro"><div class="horizon-platforms"><span>Keep working where you think best.</span><div>${mark("claude")}Claude</div><div>${mark("chatgpt")}ChatGPT</div><div>${mark("ollama")}Ollama</div><a href="#/surfaces">Explore connections ${icon("external")}</a></div><div class="horizon-statement"><h2>Great work doesn’t<br>start from zero.</h2><p>Your preferences. The decisions behind your project. The details you’ve already explained. coletar brings that context to any model, and builds your knowledge base from the conversations you’ve already had.</p></div></section>
    <section id="how-it-works" class="horizon-section flow-section"><div class="horizon-section-head"><span class="section-label">01 / COLLECT. CONNECT. CONTINUE.</span><h2>One workspace.<br>Every next step.</h2></div><div id="horizon-flow" class="horizon-flow">${flowPreview()}</div></section>
    <section class="horizon-features horizon-section"><div class="horizon-section-head centered"><h2>The things that make<br>your AI <span>yours.</span></h2></div><div class="horizon-feature-grid"><a class="horizon-feature" href="#/library"><div class="feature-visual collection-visual"><span class="mini-context ctx-one">${icon("file")} A preference worth keeping</span><span class="mini-context ctx-two">${icon("library")} The thinking behind Project Atlas</span><span class="mini-context ctx-three">${icon("check")} A decision with a source</span><div class="collection-grid" aria-hidden="true"></div></div><div class="feature-copy"><span>01 / YOUR COLLECTION</span><h3>A home for useful context. ${icon("external")}</h3><p>Keep facts, preferences, and decisions together, with a source you can always inspect.</p></div></a><a class="horizon-feature" href="#/home/product" data-scroll="product"><div class="feature-visual reach-visual"><div class="reach-orbit orbit-one"></div><div class="reach-orbit orbit-two"></div><div class="reach-lock">${icon("shield")}</div><span class="floating-brand fb-one">${mark("claude")}</span><span class="floating-brand fb-two">${mark("chatgpt")}</span><span class="floating-brand fb-three">${mark("local")}</span><span class="mini-policy">Your context. Your boundaries.</span></div><div class="feature-copy"><span>02 / YOUR CONTROL</span><h3>Selective by nature. ${icon("external")}</h3><p>Set access for each detail. Your personal context doesn’t need to go everywhere.</p></div></a><a class="horizon-feature" href="#/audit"><div class="feature-visual time-visual"><div class="time-line"></div><div class="time-point"><span></span><small>03 SEP</small><p>Launch: <del>10 October</del></p></div><div class="time-point current"><span></span><small>08 SEP</small><p>Launch: 24 October</p><b>${icon("check")} Current version</b></div></div><div class="feature-copy"><span>03 / YOUR HISTORY</span><h3>Room to change your mind. ${icon("external")}</h3><p>A correction moves you forward. The previous version remains part of the story.</p></div></a></div></section>
    <section id="product" class="horizon-section horizon-access"><div class="horizon-section-head"><div><span class="section-label">02 / SEE YOUR RULES AT WORK</span><h2>Context is personal.<br>Keep it that way.</h2></div><p>Choose a detail. Change who can read it.<br>See exactly what becomes available.</p></div><div class="horizon-playground-label"><span>${icon("settings")} The context playground</span><span>Synthetic data · your workspace is untouched</span><button id="reset-demo" class="quiet">${icon("reset")} Reset example</button></div><div id="context-demo" class="context-demo">${demoPanel()}</div><div class="playground-caption"><span>${icon("shield")} Your policy is enforced when context is read.</span><a href="#/library">Make it yours ${icon("arrow")}</a></div></section>
    <section class="horizon-history horizon-section"><div><span class="section-label">03 / AN EXPLANATION FOR EVERYTHING</span><h2>Nothing lost.<br>Nothing without<br>a source.</h2><p>Follow a fact back to its origin. See how a decision changed. Ask what your workspace knew at a different moment.</p><a href="#/audit" class="btn">Explore your history ${icon("arrow")}</a></div><div id="history-example" class="history-example">${historyExample()}</div></section>
    <section class="horizon-section horizon-portable"><div class="horizon-section-head centered"><span class="section-label">04 / OPEN AT BOTH ENDS</span><h2>Bring your history.<br>Keep your freedom.</h2></div><div class="portable-grid"><article class="portable-card import"><img src="/static/images/misty-valley.jpg" alt="Misty green mountain valley at sunrise" loading="lazy" width="1800" height="1200"><div class="portable-card-shade"></div><div class="portable-paper" aria-hidden="true">${icon("file")}<span>conversations.json</span><small>Your history, ready for a new home.</small></div><div class="portable-copy"><span class="section-label">START WITH WHAT YOU KNOW</span><h3>You’re already<br>part of the way there.</h3><p>Choose a conversation export. Inspect the useful context, then decide where it belongs.</p><a class="btn" href="#/surfaces">Import your history ${icon("arrow")}</a></div></article><article class="portable-card export"><div class="export-diagram" aria-hidden="true"><div class="export-core">${wordmark}</div><div class="export-line"></div><span>${mark("claude")}</span><span>${mark("chatgpt")}</span><span>${mark("ollama")}</span></div><div class="portable-copy"><span class="section-label">YOUR RIGHT TO LEAVE</span><h3>Another tool.<br>Still your context.</h3><p>Preview the manifest and Continuity Score. Download a package you install at your next destination.</p><a class="btn" href="#/migrate">Explore migration ${icon("arrow")}</a></div></article></div></section>
    <section class="horizon-faq horizon-section"><div><span class="section-label">A FEW THINGS WORTH KNOWING</span><h2>Clarity,<br>before anything.</h2><a href="#/security">Our boundaries ${icon("external")}</a></div><div class="faq-list">${[
      [
        "Do I have to change how I use AI?",
        "Keep using your existing AI tools. coletar is a workspace for your context, with supported connectors and exports. It does not add another chat interface.",
      ],
      [
        "Can every assistant read everything?",
        "You decide which provider policies may read each object. Eligibility does not guarantee that an assistant retrieves a fact: the query still determines what is returned. Claude web, Desktop, and Code currently share one provider policy.",
      ],
      [
        "How does my existing history get here?",
        "You export your conversations from the provider and choose the downloaded file in coletar. The current web importer uses local pattern extraction. Optional browser capture requires consent and only captures submitted user text on the active supported page.",
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
    <section class="horizon-closing"><img src="/static/images/alpine-valley.webp" alt="" loading="lazy" width="2200" height="1375"><div class="closing-shade"></div><span class="section-label">THERE’S MORE AHEAD.</span><h2>Go further.<br>Bring your thinking.</h2><a class="btn" href="#/library">Open your workspace ${icon("arrow")}</a></section>
    </main><footer class="horizon-footer"><div class="horizon-footer-top"><div><a class="brand" href="#/home">${wordmark}coletar</a><p>Independent context.<br>Human control.</p></div><div><span>Product</span><a href="#/library">Workspace</a><a href="#/surfaces">Connections</a><a href="#/migrate">Export & migrate</a></div><div><span>Trust</span><a href="#/security">Privacy & boundaries</a><a href="#/audit">History</a><a href="https://github.com/chrisdten3/coletar/blob/main/docs/CONTINUITY_SCORE.md" target="_blank" rel="noopener">Continuity Score ${icon("external")}</a></div><div><span>For builders</span><a href="https://github.com/chrisdten3/coletar" target="_blank" rel="noopener">Source & docs ${icon("external")}</a><a href="/" target="_blank" rel="noopener">Developer Inspector ${icon("external")}</a><button class="photo-credits quiet">Media credits</button></div></div><div class="horizon-wordmark" aria-hidden="true">coletar<span>↗</span></div><div class="horizon-footer-bottom"><span>A portable AI workspace.</span><span>Your context. Your rules. Your next step.</span></div></footer></div>`;
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
  document.querySelectorAll("[data-horizon-slide]").forEach(
    (b) =>
      (b.onclick = () => {
        horizonSlide = Number(b.dataset.horizonSlide);
        const s = horizonStories[horizonSlide];
        $("#horizon-title").innerHTML = s.title;
        $("#horizon-description").innerHTML = s.description.replace(
          "\n",
          "<br>",
        );
        $("#horizon-kicker").innerHTML = icon("dot") + " " + s.kicker;
        // The reel runs on its own clock; changing the story does not cut the clip.
        $("#hero-receipt").innerHTML = heroReceipt();
        document
          .querySelectorAll("[data-horizon-slide]")
          .forEach((x) =>
            x.setAttribute(
              "aria-pressed",
              String(Number(x.dataset.horizonSlide) === horizonSlide),
            ),
          );
        $("#hero-receipt")
          .querySelectorAll("[data-source]")
          .forEach(
            (x) => (x.onclick = () => showDemoSource(Number(x.dataset.source))),
          );
      }),
  );
  document.querySelectorAll("[data-flow-step]").forEach(
    (b) =>
      (b.onclick = () => {
        flowStep = Number(b.dataset.flowStep);
        $("#horizon-flow").innerHTML = flowPreview();
        bindHorizon();
        $(`[data-flow-step="${flowStep}"]`).focus({ preventScroll: true });
      }),
  );
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
