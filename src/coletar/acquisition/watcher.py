"""Watching for an export the user asked for (SCOPE §4.1, ROADMAP M6).

The acquisition boundary decides this module's shape. coletar never asks a provider
for anything: the user clicks their own export button, OpenAI emails them a link, and
they download it. All this does is notice the file landing so the import does not
have to be a second manual step (§8.1, §11).

**Detection is by content, never by filename.** OpenAI has shipped exports under
several names and users rename downloads. More importantly, a filename rule is a
false-positive engine — anything called `chatgpt-export.zip` would qualify, including
a file that is not an export at all. So a candidate is a ZIP that actually contains
`conversations.json`, which is the same question the parser asks.

Polling rather than a filesystem-event library: 5 seconds clears the 10-second bar
with margin, and a dependency has to survive being said out loud (AGENTS.md).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from coletar.acquisition.chatgpt_export import CONVERSATIONS, ChatGPTExportError, read_export

#: Half the 10-second detection bar, so a file landing just after a poll is still
#: reported inside it.
POLL_SECONDS = 5.0

#: A download in progress grows between polls. Two identical sizes means the browser
#: has finished writing, which avoids handing the parser a truncated ZIP.
SETTLE_POLLS = 2

_SUFFIXES = frozenset({".zip"})
#: Browsers name in-progress downloads this way; opening one is guaranteed to fail.
_PARTIAL = (".crdownload", ".part", ".partial", ".download", ".tmp")


def looks_like_export(path: Path) -> bool:
    """Content, not name. Cheap enough to run on every ZIP that appears."""
    if path.suffix.lower() not in _SUFFIXES or path.name.startswith("."):
        return False
    if path.name.endswith(_PARTIAL):
        return False
    try:
        # `read_export` raises unless the archive really holds conversations.json.
        next(iter(read_export(path)), None)
    except (ChatGPTExportError, OSError):
        return False
    return True


@dataclass
class WatchState:
    """What has been seen, so one landing is announced once."""

    seen: set[Path] = field(default_factory=set)
    sizes: dict[Path, tuple[int, int]] = field(default_factory=dict)
    #: Tracked explicitly rather than inferred from `seen` being empty. An empty
    #: Downloads folder is a perfectly ordinary starting state, and inferring from
    #: emptiness re-primes on the next pass — swallowing the first real arrival.
    primed: bool = False

    def settled(self, path: Path) -> bool:
        """True once a file's size has stopped changing across polls."""
        try:
            size = path.stat().st_size
        except OSError:
            self.sizes.pop(path, None)
            return False
        previous, stable_for = self.sizes.get(path, (-1, 0))
        stable_for = stable_for + 1 if size == previous else 0
        self.sizes[path] = (size, stable_for)
        return stable_for >= SETTLE_POLLS - 1


def scan(directory: Path, state: WatchState) -> list[Path]:
    """Exports that have appeared and finished downloading since the last scan."""
    found: list[Path] = []
    try:
        entries = sorted(p for p in directory.iterdir() if p.is_file())
    except OSError:
        return found
    for path in entries:
        if path in state.seen or path.suffix.lower() not in _SUFFIXES:
            continue
        if not state.settled(path):
            continue
        if looks_like_export(path):
            state.seen.add(path)
            found.append(path)
        else:
            # Not an export. Remembered so a folder full of unrelated ZIPs is opened
            # once each rather than on every poll forever.
            state.seen.add(path)
    return found


async def watch(
    directory: Path,
    on_export: Callable[[Path], Awaitable[None]],
    *,
    poll_seconds: float = POLL_SECONDS,
    state: WatchState | None = None,
    iterations: int | None = None,
) -> WatchState:
    """Call `on_export` once per export that lands. Runs until cancelled.

    Files already present when the watch starts are *not* announced — starting a
    watcher should not import a year of old downloads. `iterations` bounds the loop
    for tests; production passes nothing and cancels the task.
    """
    state = state or WatchState()
    if not state.primed:
        # Prime: everything already here is history, not an arrival.
        with contextlib.suppress(OSError):
            state.seen.update(p for p in directory.iterdir() if p.is_file())
        state.primed = True

    count = 0
    while iterations is None or count < iterations:
        count += 1
        for path in scan(directory, state):
            await on_export(path)
        if iterations is not None and count >= iterations:
            break
        await asyncio.sleep(poll_seconds)
    return state


__all__ = ["CONVERSATIONS", "POLL_SECONDS", "WatchState", "looks_like_export", "scan", "watch"]
