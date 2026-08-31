"""Inspect a real export without importing anything. Reads only."""
import sys, zipfile, json
from pathlib import Path
from coletar.acquisition.watcher import detect
from coletar.acquisition import chatgpt_export, claude_export

path = Path(sys.argv[1]).expanduser()
print(f"file: {path.name}  ({path.stat().st_size / 1_048_576:.1f} MB)")

with zipfile.ZipFile(path) as z:
    print(f"contains: {', '.join(sorted(n for n in z.namelist())[:8])}")

provider = detect(path)
print(f"detected: {provider}")
if provider is None:
    print("  -> NOT RECOGNISED. Paste the file list above and I'll look.")
    raise SystemExit(1)

mod = chatgpt_export if provider == "chatgpt" else claude_export
convs = list(mod.read_export(path))
turns = sum(len(c.messages) for c in convs)
skipped: dict[str, int] = {}
for c in convs:
    for k, v in c.skipped.items():
        skipped[k] = skipped.get(k, 0) + v

print(f"conversations parsed: {len(convs)}")
print(f"your turns found:     {turns}")
print(f"skipped (by reason):  {skipped}")
print("\nfirst 3 turns, so you can eyeball that they're really yours:")
for c in convs[:3]:
    if c.messages:
        print(f"  [{c.title[:40]}] {c.messages[0].text[:90]!r}")
