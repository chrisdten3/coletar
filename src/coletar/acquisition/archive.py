"""Keeping the raw export beside the objects derived from it (ROADMAP M6).

The derived objects are a lossy reading of the archive, and the reading will get
better — M6.2 already took export recall from 31.4% to ~97% by changing nothing about
the file. Discarding the archive after a parse would mean every future improvement
applied only to exports the user had not yet imported, which is exactly backwards.

Stored under a content hash rather than a filename, so re-importing the same download
is recognised as the same archive rather than kept twice under `(1)`.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Read in blocks: an export is routinely hundreds of megabytes and there is no
#: reason to hold one in memory to hash it.
_BLOCK = 1024 * 1024


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_BLOCK):
            sha.update(block)
    return sha.hexdigest()


@dataclass(frozen=True)
class StoredArchive:
    """Where the raw file went, and the id every derived object points back to."""

    archive_id: str
    path: Path
    source_name: str
    size_bytes: int
    stored_at: datetime
    already_held: bool

    @property
    def short_id(self) -> str:
        return self.archive_id[:12]


def default_root() -> Path:
    return Path.home() / ".coletar" / "archives"


def store_archive(source: Path, *, root: Path | None = None) -> StoredArchive:
    """Copy an export into coletar's own storage, keyed by content.

    Copied rather than moved. The file is in the user's Downloads folder because they
    put it there, and a tool that silently relocates something you just downloaded is
    a tool you stop trusting.
    """
    root = root or default_root()
    root.mkdir(parents=True, exist_ok=True)
    archive_id = digest(source)
    destination = root / f"{archive_id}.zip"
    already = destination.exists()
    if not already:
        # Write beside, then rename: a crash mid-copy must not leave a truncated file
        # sitting at the name that says "this archive is held".
        staging = destination.with_suffix(".partial")
        shutil.copy2(source, staging)
        staging.replace(destination)
    return StoredArchive(
        archive_id=archive_id,
        path=destination,
        source_name=source.name,
        size_bytes=destination.stat().st_size,
        stored_at=datetime.now(UTC),
        already_held=already,
    )
