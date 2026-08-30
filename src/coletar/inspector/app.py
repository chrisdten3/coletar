"""Read-only Context Inspector (SCOPE §8.2, ROADMAP M5) — first cut.

The full Inspector reviews, edits, merges and re-scopes objects in the live store
and gates compile on every object having been shown once; none of that is built.
This is a smaller, honest slice of it: upload a store snapshot (the JSON
`coletar` already writes to `COLETAR_STORE_PATH`) and see the three boxes from
the README architecture diagram — Canonical Context Graph, Event/Revision Log,
Search Index — rendered as plain nested-list outlines. One request in, one page
out, nothing kept on the server.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from html import escape
from typing import Any

from fastapi import FastAPI, UploadFile
from fastapi.responses import HTMLResponse

from coletar.retrieval.embedding import tokenize
from coletar.schema.events import Event
from coletar.schema.objects import Edge, object_from_record

#: A record failing one of these means it's malformed, not that the process is
#: broken -- caught per-row so one bad edge doesn't blank out the objects that
#: parsed fine.
_ROW_ERRORS = (KeyError, ValueError, AttributeError, TypeError)

app = FastAPI(title="coletar context inspector", version="0.1.0")

_PREVIEW_LEN = 80

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>coletar — context inspector</title>
<style>
body {{ font-family: ui-monospace, monospace; margin: 2rem auto; max-width: 60rem; }}
h2 {{ margin-top: 2rem; }}
li {{ margin: 0.15rem 0; }}
.meta {{ color: #666; }}
.error {{ color: #b00020; }}
</style></head><body>
<h1>coletar context inspector</h1>
<p class="meta">Upload a store snapshot to see its context graph, event/revision
log and search index.</p>
<form action="/upload" method="post" enctype="multipart/form-data">
  <input type="file" name="snapshot" accept="application/json" required>
  <button type="submit">upload</button>
</form>
{body}
</body></html>"""


def _outline(items: list[str]) -> str:
    if not items:
        return '<p class="meta">(none)</p>'
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def _preview(content: str) -> str:
    flat = " ".join(content.split())
    if len(flat) > _PREVIEW_LEN:
        flat = flat[:_PREVIEW_LEN].rstrip() + "…"
    return escape(flat)


def _context_graph(objects: list[Any], edges: list[Edge]) -> str:
    edges_from: dict[str, list[Edge]] = {}
    for edge in edges:
        edges_from.setdefault(edge.src_id, []).append(edge)

    by_type: dict[str, list[Any]] = {}
    for obj in objects:
        by_type.setdefault(obj.type, []).append(obj)

    sections = []
    for object_type in sorted(by_type):
        rows = []
        for obj in by_type[object_type]:
            out_edges = edges_from.get(obj.id, [])
            edge_outline = (
                _outline([f"{e.type} → {escape(e.dst_id)}" for e in out_edges])
                if out_edges
                else ""
            )
            rows.append(
                f"<code>{escape(obj.id)}</code> [{escape(str(obj.scope))}, "
                f"confidence {obj.confidence:.2f}] {_preview(obj.content)}{edge_outline}"
            )
        sections.append(f"<h3>{escape(object_type)}</h3>{_outline(rows)}")
    return "".join(sections)


def _event_log(events: list[Event]) -> str:
    ordered = sorted(events, key=lambda e: e.at, reverse=True)
    rows = [
        f"{e.at.isoformat()}  {e.actor}  {e.type}  <code>{escape(e.object_id or '-')}</code>"
        for e in ordered
    ]
    return _outline(rows)


def _search_index(objects: list[Any]) -> str:
    rows = []
    for obj in sorted(objects, key=lambda o: o.id):
        terms = sorted(set(tokenize(obj.content)))
        rows.append(f"<code>{escape(obj.id)}</code>: {escape(', '.join(terms)) or '(no terms)'}")
    return _outline(rows)


def _parse_rows[T](records: list[Any], parse: Callable[[Any], T]) -> tuple[list[T], list[str]]:
    parsed: list[T] = []
    errors: list[str] = []
    for index, record in enumerate(records):
        try:
            parsed.append(parse(record))
        except _ROW_ERRORS as exc:
            errors.append(f"#{index}: {str(exc).splitlines()[0]}")
    return parsed, errors


def _skipped(label: str, errors: list[str]) -> str:
    if not errors:
        return ""
    return (
        f'<p class="error">{len(errors)} {label} skipped (malformed):</p>'
        + _outline([escape(e) for e in errors])
    )


def _render(snapshot: dict[str, Any]) -> str:
    objects, object_errors = _parse_rows(snapshot.get("objects", []), object_from_record)
    edges, edge_errors = _parse_rows(snapshot.get("edges", []), Edge.model_validate)
    events, event_errors = _parse_rows(snapshot.get("events", []), Event.model_validate)
    return (
        _skipped("objects", object_errors)
        + _skipped("edges", edge_errors)
        + _skipped("events", event_errors)
        + "<h2>Canonical Context Graph</h2>" + _context_graph(objects, edges)
        + "<h2>Event/Revision Log</h2>" + _event_log(events)
        + "<h2>Search Index</h2>" + _search_index(objects)
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE.format(body="")


@app.post("/upload", response_class=HTMLResponse)
async def upload(snapshot: UploadFile) -> str:
    raw = await snapshot.read()
    try:
        body = _render(json.loads(raw))
    except (json.JSONDecodeError, *_ROW_ERRORS) as exc:
        body = f'<p class="error">Could not read this snapshot: {escape(str(exc))}</p>'
    return _PAGE.format(body=body)


def run() -> None:
    import uvicorn

    from coletar.config import get_settings

    uvicorn.run(app, host="127.0.0.1", port=get_settings().inspector_port)


if __name__ == "__main__":
    run()
