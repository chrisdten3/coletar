"""Context Inspector (SCOPE §8.2, ROADMAP M5.3).

Bound to the live store, not to an uploaded snapshot. That change is what makes the
milestone's actual requirement possible — review, edit, merge, re-scope, and no
compile until every eligible object has been shown at least once — and it also
removes the snapshot viewer's one real defect for free: a page reading the live
store always knows which tenant it is showing, where a JSON file did not carry one.

The operations live in `review.py`. This module is the rendering of them, so the
rules the gate enforces cannot drift between the browser and the CLI.

Binds loopback only. It performs authenticated-user actions with no auth of its own,
which is exactly as far as it should be trusted until there is a session model.
"""

from __future__ import annotations

from html import escape
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from coletar.config import get_settings
from coletar.inspector.metrics import (
    AgenticView,
    Dashboard,
    build_agentic_view,
    build_dashboard,
)
from coletar.inspector.review import (
    InspectorError,
    ReviewStatus,
    edit,
    mark_reviewed,
    merge,
    rescope,
    review_status,
)
from coletar.schema.events import Event
from coletar.schema.objects import (
    GLOBAL_SCOPE,
    ContextObject,
    LocalityMode,
    ObjectType,
    Scope,
    ScopeType,
)
from coletar.schema.tenancy import TenantId
from coletar.schema.tenancy import tenant_id as parse_tenant_id
from coletar.store import build_store
from coletar.store.base import Store

app = FastAPI(title="coletar context inspector", version="0.2.0")

_PREVIEW_LEN = 120

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>coletar — context inspector</title>
<style>
body {{ font-family: ui-monospace, monospace; margin: 2rem auto; max-width: 64rem;
       line-height: 1.45; }}
h2 {{ margin-top: 2.5rem; border-bottom: 1px solid #ddd; padding-bottom: .3rem; }}
.meta {{ color: #666; }}
.error {{ color: #b00020; }}
.gate {{ padding: .8rem 1rem; border-radius: 6px; margin: 1rem 0; }}
.blocked {{ background: #fff4f4; border: 1px solid #e0b4b4; }}
.open {{ background: #f2fbf2; border: 1px solid #b4e0b4; }}
.card {{ border: 1px solid #ddd; border-radius: 6px; padding: .7rem 1rem; margin: .6rem 0; }}
.unreviewed {{ border-left: 4px solid #d08a00; }}
.local-only {{ color: #8a4a00; background: #fff6e8; padding: 0 .35rem;
              border-radius: 3px; font-weight: 600; }}
.reviewed {{ border-left: 4px solid #4a9a4a; }}
input[type=text] {{ width: 28rem; font-family: inherit; }}
form.inline {{ display: inline; }}
code {{ background: #f5f5f5; padding: 0 .2rem; }}
nav {{ margin: .4rem 0 1rem; }}
nav a {{ margin-right: .4rem; }}
table {{ border-collapse: collapse; width: 100%; margin: .6rem 0; }}
th, td {{ text-align: left; padding: .25rem .5rem; border-bottom: 1px solid #eee; }}
th {{ color: #666; font-weight: normal; }}
.stat {{ display: inline-block; margin: 0 1.6rem .6rem 0; }}
.stat b {{ display: block; font-size: 1.5rem; }}
.cold {{ color: #8a4a00; }}
</style></head><body>
<h1>coletar context inspector</h1>
<nav><a href="/">graph</a> · <a href="/dashboard">dashboard</a>
 · <a href="/agentic">entity / fact / episode</a></nav>
<p class="meta">tenant <code>{tenant}</code> — everything below is the live store.</p>
{flash}
{body}
</body></html>"""


def _tenant() -> TenantId:
    return parse_tenant_id(get_settings().default_tenant_id)


def _preview(content: str) -> str:
    flat = " ".join(content.split())
    if len(flat) > _PREVIEW_LEN:
        flat = flat[:_PREVIEW_LEN].rstrip() + "…"
    return escape(flat)


def _gate(status: ReviewStatus) -> str:
    """The M5 requirement, stated to the user rather than hidden in a disabled button."""
    if status.can_compile:
        return (
            f'<div class="gate open"><strong>Compile is available.</strong> '
            f"All {len(status.eligible)} eligible objects have been reviewed.</div>"
        )
    return (
        f'<div class="gate blocked"><strong>Compile is blocked.</strong> '
        f"{len(status.unreviewed)} of {len(status.eligible)} eligible objects have not "
        f"been reviewed since they last changed. Nothing leaves for another product "
        f"until you have seen what it says.</div>"
    )


def _locality(obj: ContextObject) -> str:
    """Shown on every card, because the reviewer is the last check before a compile.

    An object marked local to one surface is withheld from every other destination's
    compile — a real difference in where it can end up, so a page whose whole job is
    "see what this says before it leaves" has to say which products can receive it.
    """
    if obj.locality.mode is LocalityMode.SYNCED:
        return '<span class="meta">every surface</span>'
    allowed = ", ".join(sorted(escape(str(s)) for s in obj.locality.surfaces))
    return f'<span class="local-only">local to {allowed}</span>'


def _object_card(obj: ContextObject, *, reviewed: bool) -> str:
    kind = getattr(obj, "kind", obj.type)
    scope_value = "" if obj.scope.type is ScopeType.GLOBAL else escape(obj.scope.id or "")
    state = "reviewed" if reviewed else "unreviewed"
    return f"""<div class="card {state}">
<div><code>{escape(obj.id)}</code>
 <span class="meta">{escape(str(kind))} · {escape(str(obj.scope))}
 · confidence {obj.confidence:.2f} · v{obj.version}
 · via {escape(str(obj.provenance.provider))} · {escape(str(obj.extraction_method))}</span>
 · {_locality(obj)}</div>
<form action="/edit" method="post">
  <input type="hidden" name="object_id" value="{escape(obj.id)}">
  <input type="text" name="content" value="{escape(obj.content)}">
  <button type="submit">save</button>
</form>
<form action="/rescope" method="post" class="inline">
  <input type="hidden" name="object_id" value="{escape(obj.id)}">
  <input type="text" name="project" value="{scope_value}" placeholder="(blank = global)"
         size="18">
  <button type="submit">re-scope</button>
</form>
<form action="/merge" method="post" class="inline">
  <input type="hidden" name="survivor_id" value="{escape(obj.id)}">
  <input type="text" name="absorbed_id" placeholder="absorb object id" size="24">
  <button type="submit">merge in</button>
</form>
<form action="/review" method="post" class="inline">
  <input type="hidden" name="object_id" value="{escape(obj.id)}">
  <button type="submit">{"reviewed ✓" if reviewed else "mark reviewed"}</button>
</form>
</div>"""


def _event_log(events: list[Event]) -> str:
    rows = [
        f"<li>{e.at.isoformat()} <span class=\"meta\">{escape(str(e.actor))}</span> "
        f"{escape(str(e.type))} <code>{escape(e.object_id or '-')}</code></li>"
        for e in events
    ]
    return "<ul>" + "".join(rows) + "</ul>" if rows else '<p class="meta">(none)</p>'


async def _render(store: Store, tenant: TenantId) -> str:
    status = await review_status(store, tenant)
    unreviewed_ids = {o.id for o in status.unreviewed}
    # Unreviewed first: the page's job is to get the gate open, so the objects
    # standing between the user and a compile belong at the top.
    ordered = sorted(
        status.eligible, key=lambda o: (o.id not in unreviewed_ids, str(o.scope), o.id)
    )
    cards = "".join(
        _object_card(obj, reviewed=obj.id not in unreviewed_ids) for obj in ordered
    )
    events = await store.list_events(tenant, limit=40)
    return (
        _gate(status)
        + f"<h2>Canonical Context Graph <span class='meta'>"
        f"({status.reviewed_count}/{len(status.eligible)} reviewed)</span></h2>"
        + (cards or '<p class="meta">(no compile-eligible objects)</p>')
        + "<h2>Event/Revision Log</h2>"
        + _event_log(events)
    )


async def _page(error: str = "") -> str:
    tenant = _tenant()
    flash = f'<p class="error">{escape(error)}</p>' if error else ""
    return _PAGE.format(
        tenant=escape(tenant), flash=flash, body=await _render(build_store(), tenant)
    )


@app.get("/", response_class=HTMLResponse)
async def index(error: str = "") -> str:
    return await _page(error)


async def _act(action: str, **kwargs: object) -> RedirectResponse:
    """Every mutation redirects home, so a refresh cannot repeat it."""
    store, tenant = build_store(), _tenant()
    operations = {"review": mark_reviewed, "edit": edit, "rescope": rescope, "merge": merge}
    try:
        await operations[action](store, tenant, **kwargs)  # type: ignore[operator]
    except InspectorError as exc:
        return RedirectResponse(f"/?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/review")
async def post_review(object_id: Annotated[str, Form()]) -> RedirectResponse:
    return await _act("review", object_id=object_id)


@app.post("/edit")
async def post_edit(
    object_id: Annotated[str, Form()], content: Annotated[str, Form()]
) -> RedirectResponse:
    return await _act("edit", object_id=object_id, content=content)


@app.post("/rescope")
async def post_rescope(
    object_id: Annotated[str, Form()], project: Annotated[str, Form()] = ""
) -> RedirectResponse:
    project = project.strip()
    scope = Scope(type=ScopeType.PROJECT, id=project) if project else GLOBAL_SCOPE
    return await _act("rescope", object_id=object_id, scope=scope)


@app.post("/merge")
async def post_merge(
    survivor_id: Annotated[str, Form()], absorbed_id: Annotated[str, Form()]
) -> RedirectResponse:
    return await _act("merge", survivor_id=survivor_id, absorbed_id=absorbed_id.strip())


def _stat(label: str, value: object) -> str:
    return f'<span class="stat"><b>{escape(str(value))}</b>{escape(label)}</span>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<p class="meta">(none)</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _render_dashboard(board: Dashboard) -> str:
    kb = board.total_bytes / 1024
    stats = (
        _stat("objects", board.total_objects)
        + _stat("retired", board.retired)
        + _stat("superseded", board.superseded)
        + _stat("KB of content", f"{kb:.1f}")
        + _stat("never read", board.never_read)
        + _stat("past TTL", board.expired)
    )

    usage = _table(
        ["surface", "searches", "mean tokens", "p50", "p95", "truncated", "deduped"],
        [
            [
                f"<code>{escape(u.surface)}</code>",
                str(u.searches),
                f"{u.mean_tokens:.0f}",
                f"{u.p50_ms:.1f}ms",
                f"{u.p95_ms:.1f}ms",
                str(u.truncated),
                str(u.deduplicated),
            ]
            for u in board.usage
        ],
    )

    # Coldest and largest first: the objects worth retiring are the ones costing
    # tokens without ever having been read.
    health = _table(
        ["object", "type", "scope", "confidence", "bytes", "last read", "TTL"],
        [
            [
                f"<code>{escape(row.object_id)}</code>",
                escape(row.type),
                escape(row.scope),
                f"{row.confidence:.2f}",
                str(row.size_bytes),
                '<span class="cold">never</span>'
                if row.never_read
                else escape(row.last_access.isoformat()),  # type: ignore[union-attr]
                '<span class="cold">expired</span>'
                if row.expired
                else (escape(row.expires_at.date().isoformat()) if row.expires_at else "—"),
            ]
            for row in board.health[:40]
        ],
    )

    explanation = _table(
        ["component", "value"],
        [[escape(k), escape(f"{v}")] for k, v in (board.last_explanation[0] or {}).items()]
        if board.last_explanation
        else [],
    )

    return (
        f"<h2>Graph</h2>{stats}"
        f"<h2>Retrieval by surface</h2>{usage}"
        "<h2>Why the last search returned what it did</h2>"
        '<p class="meta">Component scores from the most recent retrieval trace.</p>'
        f"{explanation}"
        "<h2>Object health</h2>"
        '<p class="meta">Never-read and largest first — the objects costing tokens '
        "without earning them.</p>"
        f"{health}"
        "<h2>Activity</h2>"
        f"{_event_log(board.feed)}"
    )


def _mentions(view: AgenticView, object_id: str) -> str:
    """Why this entity is in the graph, in the user's own words.

    Constraint 4: an object the Inspector cannot explain to a user should not
    exist. A bare entity row fails that — "Amanda, Walleye Business Development" is
    a name with no answer to "who is this and why do you know about her?". The facts
    that mention her are the answer, and they are the user's own sentences.
    """
    facts = view.mentioned_by.get(object_id, [])
    if not facts:
        # Said plainly rather than left blank. An entity nothing mentions is a
        # person we cannot justify holding, and the user should be able to see that
        # and delete them.
        return "<span class='meta'>nothing mentions this</span>"
    return "<br>".join(_preview(fact.content) for fact in facts)


def _render_agentic(view: AgenticView) -> str:
    sections = []
    for object_type, rows in view.by_type.items():
        is_entity = object_type == str(ObjectType.ENTITY)
        columns = ["id", "scope", "confidence", "content"]
        if is_entity:
            columns.append("mentioned by")
        listed = _table(
            columns,
            [
                [
                    f"<code>{escape(o.id)}</code>",
                    escape(str(o.scope)),
                    f"{o.confidence:.2f}",
                    _preview(o.content),
                    *([_mentions(view, o.id)] if is_entity else []),
                ]
                for o in rows
            ],
        )
        sections.append(
            f"<h2>{escape(object_type)} "
            f"<span class='meta'>({len(rows)})</span></h2>{listed}"
        )

    lineage = _table(
        ["episode", "produced"],
        [
            [
                f"<code>{escape(episode)}</code>",
                ", ".join(f"<code>{escape(o.id)}</code>" for o in derived),
            ]
            for episode, derived in sorted(view.derived_from.items())
        ],
    )
    return (
        '<p class="meta">A filtered rendering of the same graph — entity, fact and '
        "episode are three object types, not a second store.</p>"
        + "".join(sections)
        + "<h2>Episode lineage</h2>"
        '<p class="meta">Which objects an episode produced. §6 requires this to '
        "survive; losing it would make the view pretty and unfalsifiable.</p>"
        f"{lineage}"
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(error: str = "") -> str:
    tenant = _tenant()
    board = await build_dashboard(build_store(), tenant)
    flash = f'<p class="error">{escape(error)}</p>' if error else ""
    return _PAGE.format(tenant=escape(tenant), flash=flash, body=_render_dashboard(board))


@app.get("/agentic", response_class=HTMLResponse)
async def agentic() -> str:
    tenant = _tenant()
    view = await build_agentic_view(build_store(), tenant)
    return _PAGE.format(tenant=escape(tenant), flash="", body=_render_agentic(view))


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=get_settings().inspector_port)


if __name__ == "__main__":
    run()
