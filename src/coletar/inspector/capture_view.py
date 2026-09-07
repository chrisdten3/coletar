"""Capture: what has arrived, what has been judged, and what is stuck.

The library shows memories and the detail page shows where one may go. Neither
shows anything *arriving*, which leaves the demo telling the middle and the end of
a sentence whose beginning is invisible. This is the beginning: a turn was
captured, it is encrypted, a model has not looked at it yet, and here is the button
that destroys it.

It doubles as the operational view, because those turn out to be the same page.
"Is the queue draining?" and "has my turn been judged yet?" are one question asked
by two people, and answering it twice in two places is how the two answers start
disagreeing. `queue_health` is the same function `coletar queue-health` exits
non-zero on, so the page and the cron line cannot drift.

Captured text is shown, not hidden. The owner reviewing what was kept is the
consent mechanism — encryption at rest protects the turn from everyone who is not
this user, and was never meant to protect it from them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import escape

from coletar.capture import is_pending
from coletar.episode_crypto import PREFIX, EpisodeKeyUnavailable, decrypt_episode
from coletar.jobs.health import QueueHealth, queue_health
from coletar.schema.objects import ContextObject, ObjectType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

EPISODE_LIMIT = 200
#: Enough of a turn to recognise it. The whole thing is on the object's own page.
PREVIEW_CHARS = 220


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _age(moment: datetime) -> str:
    """Relative, because "4 hours ago" is the question and a timestamp is homework."""
    delta = datetime.now(UTC) - moment.astimezone(UTC)
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)}m ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


def _key_dies(episode: ContextObject) -> str:
    if not episode.ttl_days:
        return "no expiry set"
    deadline = episode.created_at + timedelta(days=episode.ttl_days)
    remaining = (deadline - datetime.now(UTC)).days
    return f"key destroyed {_when(deadline)} ({remaining}d)"


def _health_strip(health: QueueHealth) -> str:
    """Numbers first, then anything that needs acting on.

    A stalled queue and a quiet user look identical from outside; these three
    figures are the only place the difference shows.
    """
    oldest = (
        f"{health.oldest_pending_hours}h" if health.oldest_pending_hours is not None else "—"
    )
    worker = escape(health.lease_owner) if health.lease_owner else "idle"
    if health.lease_expired:
        worker += " (expired)"

    stats = (
        '<div class="stats">'
        f'<div class="stat"><b>{health.pending}</b>awaiting extraction</div>'
        f'<div class="stat"><b>{escape(oldest)}</b>oldest in queue</div>'
        f'<div class="stat"><b>{health.recent_failures}</b>failures, 24h</div>'
        f'<div class="stat mono-stat"><b>{worker}</b>worker</div>'
        "</div>"
    )
    if health.ok:
        return stats + '<p class="gate open">The queue is draining normally.</p>'
    alerts = "".join(f"<li>{escape(alert)}</li>" for alert in health.alerts)
    return stats + f'<div class="gate blocked"><ul class="alerts">{alerts}</ul></div>'


def _episode_row(episode: ContextObject, text: str, derived: list[ContextObject]) -> str:
    erased = not episode.is_active
    pending = is_pending(episode) and not erased
    if erased:
        state = '<span class="chip erased">erased</span>'
    elif pending:
        state = '<span class="chip local">awaiting extraction</span>'
    else:
        state = '<span class="chip synced">extracted</span>'
    preview = text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "…"

    if derived:
        links = ", ".join(
            f'<a href="/object/{escape(obj.id)}">{escape(obj.id)}</a>' for obj in derived
        )
        produced = f'<div class="produced">Produced {links}</div>'
    elif pending:
        produced = ""
    else:
        # Not a failure. Precision over recall means most turns are worth keeping
        # and contain nothing durable, and a queue that only showed the productive
        # ones would misrepresent how often that is true.
        produced = '<div class="produced meta">Judged; nothing durable in it.</div>'

    classes = "row" if pending else "row settled"
    if erased:
        classes = "row erased"
    return (
        f'<div class="{classes}">'
        f'<div class="row-text"><a href="/object/{escape(episode.id)}">'
        f"{escape(preview)}</a></div>"
        f"{produced}"
        '<div class="row-meta">'
        f"{state}"
        f"<span>{escape(episode.id)}</span>"
        f"<span>via {escape(str(episode.provenance.provider))}</span>"
        f"<span>captured {escape(_age(episode.created_at))}</span>"
        f"<span>{escape(_key_dies(episode))}</span>"
        "</div>"
        f"{_erase_control(episode, erased=erased)}"
        "</div>"
    )


def _erase_control(episode: ContextObject, *, erased: bool) -> str:
    """The button, or the record that it was already pressed."""
    if erased:
        return (
            '<p class="meta">Key destroyed. The object and its history remain; the '
            "text does not.</p>"
        )
    return (
        '<form method="post" action="/erase-episode" class="inline">'
        f'<input type="hidden" name="object_id" value="{escape(episode.id)}">'
        '<button type="submit">Erase this turn</button>'
        '<span class="meta">Destroys the key. The turn becomes unreadable to '
        "everyone, including us.</span></form>"
    )


async def render_capture(store: Store, tenant: TenantId) -> str:
    """Queue health, then every captured turn, pending first."""
    health = await queue_health(store, tenant)
    # Retired episodes included on purpose. Erasure destroys the content and keeps
    # the record that a turn existed and was erased (constraint 6) — and a row that
    # simply vanished would make the strongest guarantee in the product invisible
    # at the exact moment a user exercised it.
    episodes = await store.list_objects(
        tenant, type=ObjectType.EPISODE, include_retired=True, limit=EPISODE_LIMIT
    )

    # Which memories each turn produced. Read from provenance rather than from
    # edges: `source_object_ids` is what the extraction pass actually writes, and
    # §6 requires that link to survive.
    everything = await store.list_objects(tenant, limit=EPISODE_LIMIT * 4)
    derived: dict[str, list[ContextObject]] = {}
    for obj in everything:
        for source in obj.provenance.source_object_ids:
            derived.setdefault(source, []).append(obj)

    # Pending first, then newest: the queue is a worklist before it is a history.
    ordered = sorted(
        episodes,
        key=lambda e: (
            not e.is_active,
            not is_pending(e),
            -e.created_at.timestamp(),
        ),
    )

    rows = []
    for episode in ordered:
        text = episode.content
        if episode.content.startswith(PREFIX):
            try:
                text = await decrypt_episode(store, tenant, episode)
            except EpisodeKeyUnavailable:
                text = "[content erased — the key that could read this was destroyed]"
        rows.append(_episode_row(episode, text, derived.get(episode.id, [])))

    live_count = sum(1 for e in episodes if e.is_active)
    erased_count = len(episodes) - live_count
    body = (
        "".join(rows)
        or '<p class="empty">No turns captured yet. The browser extension writes '
        "here, and so does <code>coletar remember</code>.</p>"
    )
    return (
        f"{_health_strip(health)}"
        f'<h2>Captured turns <span class="meta">({live_count} live'
        f'{f", {erased_count} erased" if erased_count else ""})</span></h2>'
        '<p class="meta">Stored encrypted before anything judges them. Extraction '
        "runs later, on a schedule — nothing here has been sent to a model yet "
        "unless it says extracted.</p>"
        f'<div class="rows">{body}</div>'
    )
