#!/usr/bin/env bash
# Build the Chrome Web Store upload zip.
#
# The store takes a zip of the extension directory itself — no build step, because
# this extension has no build. What it does need is the icons, which are generated
# rather than committed-by-hand, so regenerate them first: a listing rejected for a
# missing 128px icon is a slow round trip.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

python3 scripts/build_extension_icons.py

version="$(python3 -c "import json;print(json.load(open('extension/manifest.json'))['version'])")"
out="dist/coleta-extension-${version}.zip"
mkdir -p dist
rm -f "$out"

# Explicit file list rather than `zip -r`: the store rejects unexpected files, and
# a stray .DS_Store or an editor backup is exactly what ends up in a recursive zip.
( cd extension && zip -q "../$out" \
    manifest.json \
    background.js \
    content.js \
    options.html \
    options.js \
    icons/icon16.png icons/icon32.png icons/icon48.png icons/icon128.png )

echo "$out"
unzip -l "$out"
