"""One stylesheet for every Inspector page.

The Inspector was a developer tool in monospace, which was right while its only
reader was the person who wrote it. It is now the demo surface, so it inherits the
preview site's identity — warm paper, deep green, amber — and the product and the
pitch stop looking like two companies.

Amber is reserved for **withheld**, and nothing else. Locality is the thing this
product sells; if the colour that means "this is being kept from a surface" is also
the colour of a warning banner somewhere, the demo's central signal is diluted.

Every colour is a token declared in the bare `:root`, then redefined for dark. A
colour whose only definition sits inside a media query renders one theme's text on
the other theme's ground, which is the classic way a page becomes unreadable for
half its readers.
"""

from __future__ import annotations

#: Legacy class names (`.card`, `.stat`, `.gate`) are styled here too, so the
#: dashboard and agentic views inherit the identity without being rewritten in the
#: same change as the library.
STYLES = """
:root {
  --paper: #f7f6f2;
  --surface: #fffefb;
  --sunken: #f1efe8;
  --ink: #1c1b19;
  --ink-soft: #56534c;
  --ink-faint: #86827a;
  --line: #e4e1d8;
  --line-strong: #d3cfc3;
  --accent: #2f6f5e;
  --accent-soft: #e4efe9;
  --accent-ink: #1c463b;
  --withheld: #a15c1f;
  --withheld-soft: #f7ecdd;
  --danger: #8f2a2a;
  --on-accent: #fffefb;
  --sans: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  --serif: "IBM Plex Serif", Georgia, serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #171614;
    --surface: #201e1b;
    --sunken: #14130f;
    --ink: #f0ede5;
    --ink-soft: #b5b0a4;
    --ink-faint: #837e73;
    --line: #322f2a;
    --line-strong: #423e37;
    --accent: #6fb79f;
    --accent-soft: #1d332c;
    --accent-ink: #a8d8c6;
    --withheld: #d9954e;
    --withheld-soft: #33261a;
    --danger: #e08585;
    --on-accent: #14130f;
  }
}
* { box-sizing: border-box; }
body {
  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  line-height: 1.55;
  margin: 0;
  -webkit-font-smoothing: antialiased;
}
.top {
  border-bottom: 1px solid var(--line);
  background: var(--sunken);
}
.top-inner {
  max-width: 62rem;
  margin: 0 auto;
  padding: .7rem 1.25rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
}
.brand { display: flex; align-items: center; gap: .5rem; font-weight: 600; font-size: .95rem; }
.brand .mark {
  width: 1.4rem; height: 1.4rem; border-radius: 5px;
  background: var(--accent); color: var(--on-accent);
  display: grid; place-items: center;
  font-family: var(--serif); font-size: .9rem; line-height: 1;
}
.wrap {
  max-width: 62rem;
  margin: 0 auto;
  padding: 1.5rem 1.25rem 4rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}
nav.views { display: flex; gap: .9rem; font-size: .85rem; }
nav.views a { color: var(--ink-soft); text-decoration: none; }
nav.views a:hover,
nav.views a:focus-visible { color: var(--accent); text-decoration: underline; }
nav.views a[aria-current="page"] { color: var(--ink); font-weight: 600; }
h1 { font-family: var(--serif); font-weight: 600; font-size: 1.5rem; margin: 0; }
h2 {
  font-family: var(--serif); font-weight: 600; font-size: 1.2rem;
  margin: 1.5rem 0 0; padding-bottom: .3rem; border-bottom: 1px solid var(--line);
}
p { margin: 0; }
a { color: var(--accent); }
code, .mono { font-family: var(--mono); font-size: .88em; }
code { background: var(--sunken); border-radius: 4px; padding: .08em .32em; }
.meta { color: var(--ink-faint); font-size: .85rem; }
.error { color: var(--danger); font-size: .9rem; }

/* -- viewing-as switcher -------------------------------------------------- */
.viewing {
  display: flex; align-items: center; gap: .55rem;
  font-size: .82rem; color: var(--ink-soft);
}
.seg {
  display: flex; border: 1px solid var(--line-strong);
  border-radius: 7px; overflow: hidden; background: var(--surface);
}
.seg a {
  font-family: var(--mono); font-size: .78rem;
  color: var(--ink-soft); text-decoration: none;
  padding: .32rem .7rem; border-right: 1px solid var(--line);
}
.seg a:last-child { border-right: 0; }
.seg a[aria-current="page"] {
  background: var(--accent); color: var(--on-accent); font-weight: 500;
}
.seg a:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }

/* -- library rows --------------------------------------------------------- */
.count-line {
  display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
  font-family: var(--mono); font-size: .78rem; color: var(--ink-faint);
}
.rows { display: flex; flex-direction: column; gap: .55rem; }
.row {
  border: 1px solid var(--line); border-radius: 8px;
  padding: .75rem .85rem; background: var(--surface);
  display: flex; flex-direction: column; gap: .45rem;
}
.row.restricted { border-color: var(--withheld); background: var(--withheld-soft); }
.row-text { font-size: .93rem; line-height: 1.45; }
.row-text a { color: inherit; text-decoration: none; }
.row-text a:hover { text-decoration: underline; }
.row-meta {
  display: flex; flex-wrap: wrap; align-items: center; gap: .35rem .7rem;
  font-family: var(--mono); font-size: .72rem; color: var(--ink-faint);
}
.chip {
  display: inline-flex; align-items: center; border-radius: 999px;
  padding: .1rem .5rem; font-family: var(--mono); font-size: .7rem;
  border: 1px solid transparent; white-space: nowrap;
}
.chip.kind { background: var(--sunken); color: var(--ink-soft); border-color: var(--line); }
.chip.synced { background: var(--accent-soft); color: var(--accent-ink); }
.chip.local { background: var(--withheld); color: var(--on-accent); }
.withheld-note {
  font-family: var(--mono); font-size: .78rem; color: var(--withheld);
  border-left: 3px solid var(--withheld); background: var(--withheld-soft);
  padding: .6rem .8rem; border-radius: 0 7px 7px 0;
}
.empty { color: var(--ink-faint); font-size: .9rem; padding: 1.5rem 0; }

/* -- legacy views (dashboard, agentic) ------------------------------------ */
.card {
  border: 1px solid var(--line); border-radius: 8px;
  padding: .7rem 1rem; margin: .6rem 0; background: var(--surface);
}
.unreviewed { border-left: 4px solid var(--withheld); }
.reviewed { border-left: 4px solid var(--accent); }
.local-only {
  color: var(--on-accent); background: var(--withheld);
  padding: .05rem .45rem; border-radius: 999px; font-size: .72rem;
  font-family: var(--mono);
}
.gate { padding: .8rem 1rem; border-radius: 8px; margin: 1rem 0; font-size: .9rem; }
.blocked { background: var(--withheld-soft); border: 1px solid var(--withheld); }
.open { background: var(--accent-soft); border: 1px solid var(--accent); }
input[type=text] {
  font-family: var(--sans); font-size: .88rem; padding: .3rem .5rem;
  border: 1px solid var(--line-strong); border-radius: 6px;
  background: var(--surface); color: var(--ink); max-width: 100%;
}
button {
  font-family: var(--sans); font-size: .82rem; padding: .3rem .7rem;
  border: 1px solid var(--line-strong); border-radius: 6px;
  background: var(--surface); color: var(--ink); cursor: pointer;
}
button:hover { border-color: var(--accent); color: var(--accent); }
form.inline { display: inline; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: .6rem 0; font-size: .87rem; }
th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid var(--line); }
th { color: var(--ink-faint); font-weight: 400; font-family: var(--mono); font-size: .74rem;
     text-transform: uppercase; letter-spacing: .07em; }
td { font-variant-numeric: tabular-nums; }
.stat { display: inline-block; margin: 0 1.6rem .6rem 0; }
.stat b { display: block; font-size: 1.5rem; font-variant-numeric: tabular-nums; }
.cold { color: var(--withheld); }
"""

#: `{...}` placeholders are filled with `str.format`, so this must not be an
#: f-string and the CSS must not be interpolated through one.
PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?\
family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600\
&family=IBM+Plex+Serif:wght@600&display=swap">
<style>{styles}</style></head><body>
<header class="top"><div class="top-inner">
<span class="brand"><span class="mark">c</span> Context Inspector</span>
<nav class="views">{nav}</nav>
</div></header>
<div class="wrap">{flash}{body}</div>
</body></html>"""


def render_page(*, title: str, nav: str, body: str, flash: str = "") -> str:
    return PAGE.format(title=title, styles=STYLES, nav=nav, body=body, flash=flash)
