"""Inspect a real export without importing anything. Reads only, writes nothing.

Point it at a ChatGPT export ZIP, or at a Claude `manifest-*.json` (or the folder
holding one). For Claude it reports which of the five archives you actually have and
what is inside each, because the per-archive layout is not documented anywhere and
guessing it is how a parser silently imports less than it should.
"""

from __future__ import annotations

import contextlib
import json
import sys
import zipfile
from pathlib import Path

from coletar.acquisition import chatgpt_export, claude_export
from coletar.acquisition.watcher import detect


def describe_conversations(provider: str, path: Path) -> None:
    module = chatgpt_export if provider == "chatgpt" else claude_export
    conversations = list(module.read_export(path))
    turns = sum(len(c.messages) for c in conversations)
    skipped: dict[str, int] = {}
    for conversation in conversations:
        for reason, count in conversation.skipped.items():
            skipped[reason] = skipped.get(reason, 0) + count

    print(f"  conversations parsed: {len(conversations)}")
    print(f"  your turns found:     {turns}")
    print(f"  skipped (by reason):  {skipped}")
    print("\n  first 3 turns, so you can check they are really yours:")
    for conversation in conversations[:3]:
        if conversation.messages:
            preview = conversation.messages[0].text[:90]
            print(f"    [{conversation.title[:38]}] {preview!r}")


def main(target: Path) -> int:
    # --- a Claude manifest, or the folder holding one ---
    if target.is_dir() or target.name.startswith("manifest-"):
        bundle = claude_export.read_manifest(target)
        print(f"Claude export manifest — {len(bundle.entries)} archives listed\n")
        for entry in bundle.entries:
            archive = bundle.present.get(entry.category)
            mark = "have" if archive else "NOT DOWNLOADED"
            print(f"  {entry.category:<16} {entry.filename:<24} {mark}")
        if bundle.missing:
            print(f"\n  still to download: {', '.join(bundle.missing)}")
            print("  (each export URL works once — download all five before importing)")

        for category, archive in sorted(bundle.present.items()):
            print(f"\n--- {category} ({archive.name}) ---")
            try:
                print(json.dumps(claude_export.describe(archive), indent=2)[:1600])
            except (zipfile.BadZipFile, OSError) as exc:
                print(f"  unreadable: {exc}")
            if category == "conversations":
                try:
                    print()
                    describe_conversations("claude", archive)
                except claude_export.ClaudeExportError as exc:
                    print(f"  parser does not recognise this yet: {exc}")
        return 0

    # --- a single ZIP: ChatGPT, or a Claude archive on its own ---
    print(f"file: {target.name}  ({target.stat().st_size / 1_048_576:.1f} MB)")
    with zipfile.ZipFile(target) as bundle:
        names = sorted(bundle.namelist())
    print(f"contains: {', '.join(names[:8])}{' …' if len(names) > 8 else ''}")

    provider = detect(target)
    print(f"detected: {provider}")
    if provider is None:
        print("\n  NOT RECOGNISED. Send me the 'contains:' line above.")
        with contextlib.suppress(zipfile.BadZipFile, OSError):
            print(json.dumps(claude_export.describe(target), indent=2)[:1600])
        return 1
    describe_conversations(provider, target)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1]).expanduser()))
