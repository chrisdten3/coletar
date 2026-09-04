"""The library: every object in the graph, seen from one surface's point of view.

This is the demo. Locality is the product's differentiator and it has, until now,
only ever been an API argument — provable in a test, invisible in a room. The
switcher makes it something you perform: change the surface, watch a memory leave
the list.

**Nothing here filters.** The store does. `list_objects` already takes
`caller_surface` and applies `Locality.visible_to` inside the query, the same
predicate the compiler enforces at its own boundary. A view that did its own
filtering would be a second implementation of the product's central rule, free to
disagree with the one that matters — so this page asks the store twice, once as the
owner and once as the surface, and the difference between the two answers is the
withheld count. That difference is real evidence rather than a rendering choice.

`surface=None` is the owner's own view, matching the convention the Store protocol
already uses: a trusted internal caller sees everything.
"""

from __future__ import annotations

from html import escape

from coletar.schema.objects import ContextObject, LocalityMode, Provider, ScopeType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Surfaces offered in the switcher. `COLETAR` and `GEMINI` are omitted: the first
#: is our own writes rather than a destination a user reads from, and the second has
#: no confirmed connector (AGENTS.md keeps it out of scope until one exists).
SWITCHABLE: tuple[Provider, ...] = (Provider.CLAUDE, Provider.CHATGPT, Provider.LOCAL)

#: What each surface is called in the room, rather than in the enum.
SURFACE_LABELS: dict[Provider, str] = {
    Provider.CLAUDE: "Claude",
    Provider.CHATGPT: "ChatGPT",
    Provider.LOCAL: "the local model",
}

LIST_LIMIT = 500


def parse_surface(raw: str | None) -> Provider | None:
    """The requested surface, or None for the owner's view.

    Raises `ValueError` on an unknown one rather than silently falling back to the
    owner view — a typo that quietly shows *more* than was asked for is the wrong
    direction to fail in a page about withholding.
    """
    if raw is None or raw == "" or raw == "owner":
        return None
    try:
        surface = Provider(raw)
    except ValueError:
        legal = ", ".join(["owner", *(str(s) for s in SWITCHABLE)])
        raise ValueError(f"unknown surface {raw!r}; have {legal}") from None
    if surface not in SWITCHABLE:
        legal = ", ".join(["owner", *(str(s) for s in SWITCHABLE)])
        raise ValueError(f"{surface} is not a switchable surface; have {legal}")
    return surface


def _switcher(current: Provider | None) -> str:
    def link(target: Provider | None, label: str) -> str:
        href = "/" if target is None else f"/?surface={escape(str(target))}"
        mark = ' aria-current="page"' if target == current else ""
        return f'<a href="{href}"{mark}>{escape(label)}</a>'

    options = [link(None, "you")] + [
        link(surface, str(surface)) for surface in SWITCHABLE
    ]
    return (
        '<div class="viewing"><span>Viewing as</span>'
        f'<div class="seg" role="group" aria-label="Viewing as surface">{"".join(options)}</div>'
        "</div>"
    )


def _locality_chip(obj: ContextObject) -> str:
    if obj.locality.mode is LocalityMode.SYNCED:
        return '<span class="chip synced">every surface</span>'
    named = ", ".join(sorted(str(s) for s in obj.locality.surfaces))
    return f'<span class="chip local">{escape(named)} only</span>'


def _row(obj: ContextObject, *, viewing: Provider | None) -> str:
    """One object. Marked `restricted` only in the owner's view.

    A surface never sees a restricted object at all, so styling one as withheld
    while viewing *as* that surface could not happen — and if it ever did, it would
    mean the store had handed over something it should not have.
    """
    restricted = obj.locality.mode is LocalityMode.LOCAL_ONLY
    classes = "row restricted" if (restricted and viewing is None) else "row"
    kind = getattr(obj, "kind", obj.type)
    scope = "global" if obj.scope.type is ScopeType.GLOBAL else str(obj.scope)
    return (
        f'<div class="{classes}">'
        f'<div class="row-text"><a href="/object/{escape(obj.id)}">'
        f"{escape(obj.content)}</a></div>"
        '<div class="row-meta">'
        f'<span class="chip kind">{escape(str(kind))}</span>'
        f"{_locality_chip(obj)}"
        f"<span>{escape(obj.id)} · v{obj.version} · {obj.confidence:.2f}</span>"
        f"<span>{escape(scope)}</span>"
        f"<span>via {escape(str(obj.provenance.provider))}"
        f" · {escape(str(obj.extraction_method))}</span>"
        "</div></div>"
    )


async def render_library(
    store: Store, tenant: TenantId, *, surface: Provider | None
) -> str:
    """The whole page body for one surface's view of the graph."""
    owned = await store.list_objects(tenant, limit=LIST_LIMIT)
    visible = (
        owned
        if surface is None
        else await store.list_objects(tenant, caller_surface=surface, limit=LIST_LIMIT)
    )
    withheld = len(owned) - len(visible)

    noun = "memory" if len(visible) == 1 else "objects"
    right = (
        "your own view — nothing withheld" if surface is None else f"{withheld} withheld"
    )

    rows = "".join(_row(obj, viewing=surface) for obj in visible)
    if not rows:
        rows = (
            '<p class="empty">Nothing here yet. Run <code>coletar demo-seed</code> for a '
            "worked example, or import a real export with "
            "<code>coletar import-claude ~/Downloads</code>.</p>"
        )

    note = ""
    if surface is not None and withheld:
        plural = "" if withheld == 1 else "s"
        note = (
            f'<p class="withheld-note">{withheld} object{plural} withheld from '
            f"{escape(SURFACE_LABELS[surface])} by locality. "
            "Withheld objects are recorded in a compile manifest, never dropped.</p>"
        )

    return (
        f"{_switcher(surface)}"
        f'<div class="count-line"><span>{len(visible)} {noun} · '
        f"{escape(tenant)}</span><span>{escape(right)}</span></div>"
        f"{note}"
        f'<div class="rows">{rows}</div>'
    )
