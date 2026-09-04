"""One object, asked two opposite questions.

Lineage points backward: where did this come from, what has happened to it. It is a
history, so it is read-only — you cannot edit the past, and a control that appeared
to would be lying about an append-only log.

Reach points forward: where may this go. It is a policy, so it is editable, and it
is the only place in the product where locality stops being an API argument.

They share a screen and nothing else. Drawn as one graph, an edge to `chatgpt`
would read as *this went to ChatGPT* when it means *this may go to ChatGPT* — a
confusion that is expensive in a product whose pitch is data provenance.

The lineage is drawn as a vertical chain rather than a node cloud. A real object
has a handful of ancestors and a handful of revisions; a force-directed view of 205
objects is decorative, and it cannot answer either of the two questions anyone
actually arrives with.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from coletar.inspector.library import SURFACE_LABELS, SWITCHABLE
from coletar.inspector.review import InspectorError
from coletar.schema.events import Event, EventType
from coletar.schema.objects import ContextObject, LocalityMode, Provider, ScopeType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Enough to cover any object a human has actually touched. An object with more
#: revisions than this has a different problem than a truncated timeline.
EVENT_LIMIT = 200

#: What each event *did*, in the user's words rather than the enum's. Anything not
#: named here still renders, using its raw type — a timeline that silently dropped
#: unknown events would hide exactly the ones worth asking about.
_TITLES: dict[EventType, str] = {
    EventType.OBJECT_CREATED: "Created",
    EventType.OBJECT_UPDATED: "Edited",
    EventType.OBJECT_REVIEWED: "Reviewed",
    EventType.OBJECT_RESCOPED: "Moved to another project",
    EventType.OBJECT_LOCALITY_CHANGED: "Reach changed",
    EventType.OBJECT_MERGED: "Absorbed another object",
    EventType.OBJECT_SUPERSEDED: "Superseded",
    EventType.OBJECT_RETIRED: "Retired",
    EventType.OBJECT_CORROBORATED: "Restated elsewhere",
    EventType.CONNECTOR_WRITE: "Written by a connector",
    EventType.OBJECT_SHREDDED: "Content key destroyed",
    EventType.EXTRACTION_UNAVAILABLE: "Extraction failed",
}


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _detail_line(event: Event) -> str:
    """The event's own `detail`, rendered as the sentence it stands for.

    Locality changes get their own phrasing because `local_only:claude` is the
    log's vocabulary, not a reader's, and this is the timeline where a user goes
    looking for when a fact was restricted.
    """
    detail = event.detail or {}
    if event.type is EventType.OBJECT_LOCALITY_CHANGED:
        return f"{detail.get('from', '?')} → {detail.get('to', '?')}"
    if event.type is EventType.OBJECT_RESCOPED:
        return f"{detail.get('from', '?')} → {detail.get('to', '?')}"
    if event.type is EventType.OBJECT_MERGED:
        return f"absorbed {detail.get('absorbed', '?')}"
    if event.type is EventType.OBJECT_CREATED and detail.get("extraction_model"):
        return (
            f"extracted by {detail.get('extraction_model')}"
            f" · from episode {detail.get('episode', '?')}"
        )
    if event.type is EventType.OBJECT_CREATED and detail.get("demo_seed"):
        return "example data from coletar demo-seed"
    parts = [f"{key} {value}" for key, value in sorted(detail.items())]
    return " · ".join(parts)


def _node(
    *, title: str, sub: str, last: bool, hollow: bool = False, href: str | None = None
) -> str:
    dot = "dot hollow" if hollow else "dot"
    stem = "" if last else '<span class="stem"></span>'
    body = escape(title)
    if href is not None:
        body = f'<a href="{escape(href)}">{body}</a>'
    sub_html = f'<span class="node-sub">{escape(sub)}</span>' if sub else ""
    return (
        '<div class="node">'
        f'<div class="rail"><span class="{dot}"></span>{stem}</div>'
        f'<div class="node-body"><span class="node-title">{body}</span>'
        f"{sub_html}</div></div>"
    )


def _lineage(obj: ContextObject, events: list[Event]) -> str:
    """Oldest first, because a history read newest-first is a list of surprises."""
    ordered = sorted(events, key=lambda e: e.at)
    nodes: list[str] = []

    for source_id in obj.provenance.source_object_ids:
        nodes.append(
            _node(
                title="Derived from a captured turn",
                sub=f"{source_id} · raw evidence, kept encrypted",
                last=False,
                hollow=True,
                href=f"/object/{source_id}",
            )
        )
    if obj.supersedes:
        nodes.append(
            _node(
                title="Replaced an earlier statement",
                sub=f"supersedes {obj.supersedes}",
                last=False,
                hollow=True,
            )
        )

    for index, event in enumerate(ordered):
        title = _TITLES.get(event.type, str(event.type))
        by = f"by {event.actor}" if event.actor else ""
        line = _detail_line(event)
        sub = " · ".join(part for part in (_when(event.at), by, line) if part)
        nodes.append(_node(title=title, sub=sub, last=index == len(ordered) - 1))

    if not nodes:
        return '<p class="empty">No recorded history for this object.</p>'
    return f'<div class="lineage">{"".join(nodes)}</div>'


def _reach(obj: ContextObject) -> str:
    """A checkbox per surface, posted as one decision.

    Rendering from `Locality.visible_to` rather than from the surface set means the
    checkboxes agree with the predicate the store actually filters on, instead of
    re-deriving the same answer a second way and being free to get it wrong.
    """
    rows = []
    for surface in SWITCHABLE:
        allowed = obj.locality.visible_to(surface)
        state = "on" if allowed else "off"
        word = "can read" if allowed else "withheld"
        checked = " checked" if allowed else ""
        rows.append(
            '<label class="reach-row">'
            f'<span class="reach-name">{escape(str(surface))}</span>'
            f'<span class="reach-controls"><span class="toggle {state}">{word}</span>'
            f'<input type="checkbox" name="surfaces" value="{escape(str(surface))}"'
            f"{checked}></span></label>"
        )
    return (
        '<form method="post" action="/locality" class="reach-form">'
        f'<input type="hidden" name="object_id" value="{escape(obj.id)}">'
        f'<div class="reach">{"".join(rows)}</div>'
        '<div class="reach-actions"><button type="submit">Save reach</button>'
        '<span class="meta">Withheld objects are recorded in a compile manifest, '
        "never dropped.</span></div>"
        "</form>"
    )


def _body_text(obj: ContextObject) -> str:
    """What the object says — unless saying it would print ciphertext.

    A captured turn is stored encrypted under a key that TTL expiry destroys, so
    its `content` is base64 and its plaintext is deliberately not reachable from a
    read-only page. Showing the ciphertext would be noise; saying what it is, and
    when its key dies, is the part that makes the retention promise legible.
    """
    if obj.payload.get("content_encryption"):
        ttl = f"key destroyed after {obj.ttl_days} days" if obj.ttl_days else "no expiry set"
        pending = " · awaiting extraction" if obj.payload.get("needs_model_extraction") else ""
        return (
            '<div class="row-text encrypted">Encrypted captured turn — content is not '
            f"shown here.<br><span class=\"meta\">{escape(ttl)}{escape(pending)}</span></div>"
        )
    return f'<div class="row-text">{escape(obj.content)}</div>'


def _head(obj: ContextObject) -> str:
    kind = getattr(obj, "kind", obj.type)
    scope = "global" if obj.scope.type is ScopeType.GLOBAL else str(obj.scope)
    if obj.locality.mode is LocalityMode.SYNCED:
        locality_chip = '<span class="chip synced">every surface</span>'
    else:
        named = ", ".join(sorted(str(s) for s in obj.locality.surfaces))
        locality_chip = f'<span class="chip local">{escape(named)} only</span>'

    in_force = ""
    if obj.valid_from or obj.valid_until:
        start = _when(obj.valid_from) if obj.valid_from else "always"
        end = _when(obj.valid_until) if obj.valid_until else "still true"
        in_force = f"<span>in force {escape(start)} → {escape(end)}</span>"

    return (
        '<div class="detail-head">'
        f"{_body_text(obj)}"
        '<div class="row-meta">'
        f'<span class="chip kind">{escape(str(kind))}</span>{locality_chip}'
        f"<span>{escape(obj.id)} · v{obj.version} · confidence {obj.confidence:.2f}</span>"
        f"<span>{escape(scope)}</span>"
        f"<span>via {escape(str(obj.provenance.provider))}"
        f" · {escape(str(obj.extraction_method))}</span>"
        f"{in_force}"
        "</div></div>"
    )


async def render_detail(store: Store, tenant: TenantId, object_id: str) -> str:
    """The whole page body for one object.

    Loaded with no `caller_surface`: the Inspector is the owner's own view, and an
    owner who cannot open an object they restricted could never lift the
    restriction — the control would be a one-way door.
    """
    obj = await store.get_object(tenant, object_id)
    if obj is None:
        raise InspectorError(f"no object {object_id!r} in this tenant")

    events = await store.list_events(tenant, object_id=object_id, limit=EVENT_LIMIT)
    surfaces_seeing = [s for s in SWITCHABLE if obj.locality.visible_to(s)]
    withheld_from = [
        SURFACE_LABELS[s] for s in SWITCHABLE if not obj.locality.visible_to(s)
    ]

    summary = f"{len(surfaces_seeing)} of {len(SWITCHABLE)} surfaces can read this"
    if withheld_from:
        summary += f" · withheld from {', '.join(withheld_from)}"

    return (
        '<p class="meta"><a href="/">&larr; Library</a></p>'
        f'<div class="mock detail">{_head(obj)}'
        '<div class="half"><div class="half-label"><span class="dir">&larr;</span>'
        "Lineage · read-only</div>"
        f"{_lineage(obj, events)}</div>"
        '<div class="half"><div class="half-label"><span class="dir">&rarr;</span>'
        "Reach · editable</div>"
        f'<p class="meta">{escape(summary)}</p>'
        f"{_reach(obj)}</div></div>"
    )


def surfaces_from_form(values: list[str]) -> frozenset[Provider]:
    """Parse posted checkbox values into the intended surface set.

    Unknown values are refused rather than ignored. A silently dropped surface name
    would apply a *narrower* policy than the user selected, and quietly withholding
    more than was asked for is the failure this whole screen exists to prevent.
    """
    surfaces = set()
    for raw in values:
        try:
            surface = Provider(raw)
        except ValueError:
            raise InspectorError(f"unknown surface {raw!r}") from None
        if surface not in SWITCHABLE:
            raise InspectorError(f"{surface} is not a surface you can grant")
        surfaces.add(surface)
    return frozenset(surfaces)
