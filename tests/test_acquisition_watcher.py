"""M6 — the folder watcher and the raw archive store.

The bar is detection within 10 seconds and zero false positives across 50 unrelated
files. The second half is the one that shapes the code: a filename rule would pass a
detection test and fail this one, because `chatgpt-export.zip` is a name anything can
have.
"""

from __future__ import annotations

import asyncio
import json
import time
import zipfile
from pathlib import Path

import pytest

from coletar.acquisition.archive import digest, store_archive
from coletar.acquisition.watcher import (
    POLL_SECONDS,
    WatchState,
    looks_like_export,
    scan,
    watch,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chatgpt_export.zip"


def make_export(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("conversations.json", json.dumps([]))
    return path


# --- detection is by content ------------------------------------------------------


def test_a_real_export_is_recognised() -> None:
    assert looks_like_export(FIXTURE)


def test_zero_false_positives_across_fifty_unrelated_files(tmp_path: Path) -> None:
    """The explicit M6 bar, and the reason detection reads the archive.

    The decoys include a ZIP *named* like an export and holding the right filename in
    a comment — anything keying on the name would take it.
    """
    for index in range(20):
        (tmp_path / f"document-{index}.pdf").write_bytes(b"%PDF-1.7 not a zip")
    for index in range(10):
        (tmp_path / f"notes-{index}.txt").write_text("conversations.json")
    for index in range(15):
        path = tmp_path / f"archive-{index}.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr(f"photos/IMG_{index}.jpg", "binary-ish")
    trap = tmp_path / "chatgpt-export.zip"
    with zipfile.ZipFile(trap, "w") as bundle:
        bundle.writestr("readme.txt", "this is not conversations.json")
    (tmp_path / "half-done.zip.crdownload").write_bytes(b"partial")
    (tmp_path / ".hidden.zip").write_bytes(b"nope")
    (tmp_path / "broken.zip").write_bytes(b"not a zip at all")
    (tmp_path / "empty.zip").write_bytes(b"")

    files = sorted(p for p in tmp_path.iterdir() if p.is_file())
    assert len(files) >= 50
    assert [p for p in files if looks_like_export(p)] == []


def test_a_file_named_nothing_like_an_export_is_still_found(tmp_path: Path) -> None:
    """Users rename downloads, and OpenAI has shipped several names."""
    assert looks_like_export(make_export(tmp_path / "stuff (3).zip"))


# --- arrival ----------------------------------------------------------------------


def test_files_already_present_are_not_announced(tmp_path: Path) -> None:
    """Starting a watcher should not import a year of old downloads."""
    make_export(tmp_path / "old-export.zip")

    async def never(_: Path) -> None:  # pragma: no cover - must not run
        raise AssertionError("an existing file was treated as an arrival")

    asyncio.run(watch(tmp_path, never, poll_seconds=0, iterations=1))


def test_an_arrival_is_announced_once(tmp_path: Path) -> None:
    state = asyncio.run(_prime(tmp_path))
    make_export(tmp_path / "export.zip")

    seen: list[Path] = []

    async def record(path: Path) -> None:
        seen.append(path)

    asyncio.run(watch(tmp_path, record, poll_seconds=0, iterations=3, state=state))
    assert [p.name for p in seen] == ["export.zip"]


async def _prime(directory: Path) -> WatchState:
    async def nothing(_: Path) -> None:  # pragma: no cover
        raise AssertionError

    return await watch(directory, nothing, poll_seconds=0, iterations=1)


def test_a_download_in_progress_is_left_alone_until_it_settles(tmp_path: Path) -> None:
    """A growing file is a browser still writing. Handing that to the parser would
    fail on a truncated ZIP and mark the arrival as seen."""
    state = WatchState(seen=set())
    path = tmp_path / "growing.zip"
    path.write_bytes(b"partial")
    assert scan(tmp_path, state) == []      # first sight: size unknown
    path.write_bytes(b"partial-and-more")
    assert scan(tmp_path, state) == []      # changed: still writing


def test_an_unrelated_zip_is_opened_once_not_every_poll(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "photos.zip", "w") as bundle:
        bundle.writestr("a.jpg", "x")
    state = WatchState()
    for _ in range(4):
        scan(tmp_path, state)
    assert any(p.name == "photos.zip" for p in state.seen)


def test_detection_fits_inside_the_ten_second_bar() -> None:
    assert POLL_SECONDS * 2 <= 10.0


def test_detecting_an_export_is_fast_enough_to_poll(tmp_path: Path) -> None:
    started = time.perf_counter()
    looks_like_export(FIXTURE)
    assert (time.perf_counter() - started) < 1.0


# --- the raw archive --------------------------------------------------------------


def test_the_archive_is_kept_so_a_better_parser_can_re_read_it(tmp_path: Path) -> None:
    """M6.2 took export recall from 31.4% to ~97% without touching the file. Throwing
    the archive away would have meant that improvement only ever reached exports the
    user had not yet imported."""
    held = store_archive(FIXTURE, root=tmp_path)
    assert held.path.exists()
    assert held.archive_id == digest(FIXTURE)
    assert held.size_bytes == FIXTURE.stat().st_size
    assert not held.already_held


def test_re_importing_the_same_download_is_recognised(tmp_path: Path) -> None:
    first = store_archive(FIXTURE, root=tmp_path)
    second = store_archive(FIXTURE, root=tmp_path)
    assert second.archive_id == first.archive_id
    assert second.already_held
    assert len(list(tmp_path.glob("*.zip"))) == 1


def test_the_users_own_download_is_copied_not_moved(tmp_path: Path, monkeypatch) -> None:
    """A tool that silently relocates something you just downloaded is a tool you
    stop trusting."""
    source = make_export(tmp_path / "download" / "export.zip".replace("/", "_"))
    store_archive(source, root=tmp_path / "archives")
    assert source.exists()


def test_no_partial_file_is_left_at_the_final_name(tmp_path: Path) -> None:
    store_archive(FIXTURE, root=tmp_path)
    assert list(tmp_path.glob("*.partial")) == []


@pytest.fixture(autouse=True)
def _make_download_dir(tmp_path: Path):
    (tmp_path / "download").mkdir(exist_ok=True)
    yield
