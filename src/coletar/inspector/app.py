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

from coletar.capture import is_pending
from coletar.config import get_settings
from coletar.inspector.capture_view import render_capture
from coletar.inspector.detail import render_detail, surfaces_from_form
from coletar.inspector.library import parse_surface, render_library
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
    set_locality,
)
from coletar.inspector.theme import render_page
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

#: Which view is current, for the nav's `aria-current`.
_VIEWS: tuple[tuple[str, str], ...] = (
    ("/", "library"),
    ("/capture", "capture"),
    ("/review", "review"),
    ("/dashboard", "dashboard"),
    ("/agentic", "entity / fact / episode"),
)


def _nav(current: str) -> str:
    return "".join(
        f'<a href="{href}"{' aria-current="page"' if href == current else ""}>'
        f"{escape(label)}</a>"
        for href, label in _VIEWS
    )


def _shell(*, title: str, current: str, body: str, error: str = "") -> str:
    flash = f'<p class="error">{escape(error)}</p>' if error else ""
    return render_page(title=title, nav=_nav(current), body=body, flash=flash)


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


@app.get("/", response_class=HTMLResponse)
async def index(surface: str | None = None, error: str = "") -> str:
    """The library, seen from one surface. `?surface=` drives the whole page."""
    tenant = _tenant()
    try:
        chosen = parse_surface(surface)
    except ValueError as exc:
        chosen, error = None, str(exc)
    body = await render_library(build_store(), tenant, surface=chosen)
    return _shell(title="Library — coletar", current="/", body=body, error=error)


@app.get("/review", response_class=HTMLResponse)
async def review(error: str = "") -> str:
    """The compile gate. Unchanged behaviour; it moved off `/` to make room."""
    tenant = _tenant()
    body = await _render(build_store(), tenant)
    return _shell(title="Review — coletar", current="/review", body=body, error=error)


async def _act(action: str, **kwargs: object) -> RedirectResponse:
    """Every mutation redirects to the review view, so a refresh cannot repeat it."""
    store, tenant = build_store(), _tenant()
    operations = {"review": mark_reviewed, "edit": edit, "rescope": rescope, "merge": merge}
    try:
        await operations[action](store, tenant, **kwargs)  # type: ignore[operator]
    except InspectorError as exc:
        return RedirectResponse(f"/review?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/review", status_code=303)


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
        "episode are three object types, not a second store. "
        f"Pending extraction: {view.pending_episodes}.</p>"
        + "".join(sections)
        + "<h2>Episode lineage</h2>"
        '<p class="meta">Which objects an episode produced. §6 requires this to '
        "survive; losing it would make the view pretty and unfalsifiable.</p>"
        f"{lineage}"
    )


@app.get("/object/{object_id}", response_class=HTMLResponse)
async def object_detail(object_id: str, error: str = "") -> HTMLResponse:
    """One object: lineage above, reach below."""
    tenant = _tenant()
    try:
        body = await render_detail(build_store(), tenant, object_id)
    except InspectorError as exc:
        return HTMLResponse(
            _shell(
                title="Not found — coletar",
                current="/",
                body='<p class="empty">That object is not in this tenant. '
                '<a href="/">Back to the library</a>.</p>',
                error=str(exc),
            ),
            status_code=404,
        )
    return HTMLResponse(
        _shell(title="Object — coletar", current="/", body=body, error=error)
    )


@app.post("/locality")
async def post_locality(
    object_id: Annotated[str, Form()],
    surfaces: Annotated[list[str], Form()] = [],  # noqa: B006 - FastAPI reads the default
) -> RedirectResponse:
    """Apply a whole reach decision at once.

    An unchecked-everything post arrives here as an empty list, which
    `set_locality` refuses: restricting a memory to nobody is retirement wearing
    the wrong control, and it should say so rather than silently succeed.
    """
    try:
        await set_locality(
            build_store(),
            _tenant(),
            object_id,
            surfaces=surfaces_from_form(surfaces),
        )
    except InspectorError as exc:
        return RedirectResponse(
            f"/object/{quote(object_id)}?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(f"/object/{quote(object_id)}", status_code=303)


@app.get("/capture", response_class=HTMLResponse)
async def capture(error: str = "") -> str:
    """What has arrived, what has been judged, and whether the queue is moving."""
    tenant = _tenant()
    body = await render_capture(build_store(), tenant)
    return _shell(title="Capture — coletar", current="/capture", body=body, error=error)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(error: str = "") -> str:
    tenant = _tenant()
    board = await build_dashboard(build_store(), tenant)
    return _shell(
        title="Dashboard — coletar", current="/dashboard",
        body=_render_dashboard(board), error=error,
    )


@app.get("/agentic", response_class=HTMLResponse)
async def agentic(error: str = "") -> str:
    tenant = _tenant()
    view = await build_agentic_view(build_store(), tenant)
    return _shell(
        title="Entity / fact / episode — coletar", current="/agentic",
        body=_render_agentic(view), error=error,
    )


@app.post("/erase-episode")
async def post_erase_episode(
    object_id: Annotated[str, Form()],
) -> RedirectResponse:
    try:
        await erase_episode(build_store(), _tenant(), object_id)
    except InspectorError as exc:
        return RedirectResponse(f"/capture?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/capture", status_code=303)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=get_settings().inspector_port)


if __name__ == "__main__":
    run()
