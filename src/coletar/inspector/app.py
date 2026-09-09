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
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from coletar.capture import is_pending
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
    erase_episode,
    mark_reviewed,
    merge,
    rescope,
    review_status,
)
from coletar.inspector.web import router as web_router
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

app.include_router(web_router)

_PREVIEW_LEN = 120

app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · coletar</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<meta name="theme-color" content="#2d6e5d">
<link rel="stylesheet" href="/static/app.css">
</head><body><a class="skip" href="#main">Skip to content</a>
<aside class="sidebar"><a class="brand" href="/"><span class="mark">c</span> coletar</a>
<nav aria-label="Workspace">{navigation}</nav>
<div class="workspace"><span class="status-dot">●</span> Local workspace<br>
<span class="meta">Tenant <code>{tenant}</code><br>Stored on your configured backend</span></div>
</aside><div class="main"><header class="topbar"><h1>{title}</h1>
<span class="meta">Your context. Your rules.</span></header>
<main id="main" class="content">{flash}{body}
<p class="footer">Local preview · Accounts and hosted access are not configured.</p>
</main></div></body></html>"""


def _shell(tenant: TenantId, body: str, *, title: str = "Library", error: str = "") -> str:
    links = [
        ("/", "Library"),
        ("/review", "Review"),
        ("/dashboard", "Activity"),
        ("/agentic", "Capture & graph"),
    ]
    navigation = "".join(
        f'<a href="{url}"' + (' aria-current="page"' if label == title else "") + f">{label}</a>"
        for url, label in links
    )
    return _PAGE.format(
        title=escape(title),
        tenant=escape(tenant),
        navigation=navigation,
        flash=f'<p class="error" role="alert">{escape(error)}</p>' if error else "",
        body=body,
    )


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
  <label>Memory content<textarea name="content">{escape(obj.content)}</textarea></label>
  <button type="submit">save</button>
</form>
<form action="/rescope" method="post" class="inline">
  <input type="hidden" name="object_id" value="{escape(obj.id)}">
  <input type="text" aria-label="Project scope" name="project" value="{scope_value}"
         placeholder="(blank = global)"
         size="18">
  <button type="submit">re-scope</button>
</form>
<form action="/merge" method="post" class="inline">
  <input type="hidden" name="survivor_id" value="{escape(obj.id)}">
  <input type="text" aria-label="Object to merge" name="absorbed_id" placeholder="absorb object id"
         size="24">
  <button type="submit">merge in</button>
</form>
<form action="/review" method="post" class="inline">
  <input type="hidden" name="object_id" value="{escape(obj.id)}">
  <button type="submit">{"reviewed ✓" if reviewed else "mark reviewed"}</button>
</form>
</div>"""


def _event_log(events: list[Event]) -> str:
    rows = [
        f'<li>{e.at.isoformat()} <span class="meta">{escape(str(e.actor))}</span> '
        f"{escape(str(e.type))} <code>{escape(e.object_id or '-')}</code></li>"
        for e in events
    ]
    return "<ul>" + "".join(rows) + "</ul>" if rows else '<p class="meta">(none)</p>'


def _library_card(obj: ContextObject, *, reviewed: bool) -> str:
    restricted = obj.locality.mode is not LocalityMode.SYNCED
    kind = escape(str(getattr(obj, "kind", obj.type)))
    return (
        f'<article class="memory {"restricted" if restricted else ""}">'
        f'<a class="memory-title" href="/objects/{quote(obj.id, safe="")}">'
        f'{escape(obj.content)}</a><div class="memory-meta">'
        f'<span class="badge">{kind}</span><span>{escape(str(obj.scope))}</span>'
        f"{_locality(obj)}<span>via {escape(str(obj.provenance.provider))}</span>"
        f"<span>{escape(str(obj.extraction_method))}</span>"
        f"<span>confidence {obj.confidence:.2f}</span>"
        f"<span>{'Reviewed' if reviewed else 'Awaiting review'}</span>"
        "</div></article>"
    )


async def _render(store: Store, tenant: TenantId, *, q: str = "", view: str = "all") -> str:
    status = await review_status(store, tenant)
    unreviewed_ids = {o.id for o in status.unreviewed}
    ordered = sorted(status.eligible, key=lambda o: (o.updated_at, o.id), reverse=True)
    shown = [
        o
        for o in ordered
        if q.casefold() in o.content.casefold()
        and (
            view == "all"
            or (view == "unreviewed" and o.id in unreviewed_ids)
            or (view == "restricted" and o.locality.mode is not LocalityMode.SYNCED)
            or view == str(getattr(o, "kind", o.type))
        )
    ]
    filters = "".join(
        f'<a class="chip" href="/?{escape(urlencode({"view": value, "q": q}))}"'
        + (' aria-current="page"' if value == view else "")
        + f">{label}</a>"
        for value, label in [
            ("all", "All context"),
            ("preference", "Preferences"),
            ("fact", "Facts"),
            ("decision", "Decisions"),
            ("restricted", "Restricted"),
            ("unreviewed", "Unreviewed"),
        ]
    )
    cards = "".join(_library_card(o, reviewed=o.id not in unreviewed_ids) for o in shown)
    empty = (
        '<div class="empty"><h2>No matching context</h2>'
        '<p>Try another search or return to all context.</p><a href="/">Clear filters</a></div>'
        if status.eligible
        else '<div class="empty"><span class="eyebrow">A place for what matters</span>'
        "<h2>Your library starts here.</h2><p>Memories from your imports and connected "
        "tools will appear here, with their source and reach.</p>"
        '<p class="meta">Import and connection setup are currently available through '
        "the coletar CLI. Web onboarding is the next step.</p></div>"
    )
    return (
        '<span class="eyebrow">One library, across your tools</span>'
        '<p class="meta">Explore what your assistants know, where it came from, '
        "and which surfaces may read it.</p>"
        '<form class="toolbar" method="get" role="search">'
        f'<input type="hidden" name="view" value="{escape(view)}">'
        f'<input type="search" name="q" value="{escape(q)}" '
        'aria-label="Search your context" placeholder="Search your context">'
        '<button type="submit">Search</button></form>'
        f'<nav class="filters" aria-label="Filter context">{filters}</nav>'
        f'<div class="summary"><span>{len(shown)} objects · '
        f"{len(status.unreviewed)} awaiting review</span><span>Last written first</span></div>"
        + (cards or empty)
        + _gate(status)
        + '<p class="meta">Withheld objects are recorded in a compile manifest, never dropped.</p>'
    )


@app.get("/", response_class=HTMLResponse)
async def index(error: str = "", q: str = "", view: str = "all") -> str:
    tenant = _tenant()
    return _shell(tenant, await _render(build_store(), tenant, q=q, view=view), error=error)


@app.get("/review", response_class=HTMLResponse)
async def review_page() -> str:
    tenant, store = _tenant(), build_store()
    status = await review_status(store, tenant)
    cards = "".join(_object_card(o, reviewed=False) for o in status.unreviewed)
    return _shell(
        tenant,
        _gate(status)
        + (
            cards
            or '<div class="empty"><h2>You’re all caught up.</h2>'
            "<p>New and changed context will appear here for review.</p></div>"
        ),
        title="Review",
    )


@app.get("/objects/{object_id}", response_class=HTMLResponse)
async def object_page(object_id: str) -> str:
    tenant, store = _tenant(), build_store()
    obj = await store.get_object(tenant, object_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Object not found in this workspace")
    status = await review_status(store, tenant)
    events = await store.list_events(tenant, object_id=object_id, limit=1000)
    timeline = "".join(
        f"<li><strong>{escape(str(e.type))}</strong><time>{e.at.isoformat()}</time>"
        f" · {escape(str(e.actor))}</li>"
        for e in sorted(events, key=lambda e: e.at)
    )
    sources = ", ".join(escape(x) for x in obj.provenance.source_object_ids) or "No source objects"
    body = (
        '<a href="/">← Library</a>'
        + _object_card(obj, reviewed=obj.id not in {o.id for o in status.unreviewed})
        + '<div class="columns"><section><h2>History</h2>'
        '<p class="meta">Recorded events, oldest first. Opening this page does not mark '
        "an object reviewed. Showing up to 1,000 recent events.</p>"
        f'<ol class="timeline">{timeline}</ol></section><section><h2>Provenance & reach</h2>'
        f"<p>Origin: {escape(str(obj.provenance.origin_type))}</p>"
        f"<p>Source objects: <code>{sources}</code></p><p>{_locality(obj)}</p>"
        f"<p>In force from: {escape(str(obj.valid_from or 'Not specified'))}<br>"
        f"Until: {escape(str(obj.valid_until or 'Not specified'))}</p>"
        '<p class="meta">Reach is shown from the stored policy. Reach editing is not '
        "available in this first web slice.</p></section></div>"
    )
    return _shell(tenant, body, title="Object")


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
        is_episode = object_type == str(ObjectType.EPISODE)
        columns = ["id", "scope", "confidence", "content"]
        if is_entity:
            columns.append("mentioned by")
        if is_episode:
            columns.extend(["extraction", "control"])
        listed = _table(
            columns,
            [
                [
                    f"<code>{escape(o.id)}</code>",
                    escape(str(o.scope)),
                    f"{o.confidence:.2f}",
                    _preview(o.content),
                    *([_mentions(view, o.id)] if is_entity else []),
                    *(["pending" if is_pending(o) else "complete"] if is_episode else []),
                    *(
                        [
                            '<form action="/erase-episode" method="post">'
                            f'<input type="hidden" name="object_id" value="{escape(o.id)}">'
                            '<button type="submit">erase raw turn</button></form>'
                        ]
                        if is_episode
                        else []
                    ),
                ]
                for o in rows
            ],
        )
        sections.append(
            f"<h2>{escape(object_type)} <span class='meta'>({len(rows)})</span></h2>{listed}"
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
        "episode are three object types, not a second store. "
        f"Pending extraction: {view.pending_episodes}.</p>"
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
    return _shell(tenant, _render_dashboard(board), title="Activity", error=error)


@app.get("/agentic", response_class=HTMLResponse)
async def agentic(error: str = "") -> str:
    tenant = _tenant()
    view = await build_agentic_view(build_store(), tenant)
    return _shell(tenant, _render_agentic(view), title="Capture & graph", error=error)


@app.post("/erase-episode")
async def post_erase_episode(
    object_id: Annotated[str, Form()],
) -> RedirectResponse:
    try:
        await erase_episode(build_store(), _tenant(), object_id)
    except InspectorError as exc:
        return RedirectResponse(f"/agentic?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/agentic", status_code=303)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=get_settings().inspector_port)


if __name__ == "__main__":
    run()
