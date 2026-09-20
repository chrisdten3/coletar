#!/usr/bin/env python3
"""Render the extension's PNG icons from the same mark the web app uses.

The Chrome Web Store requires raster icons and will not take the SVG that
`static/favicon.svg` already holds, so something has to convert. Every obvious
converter here wanted a system library or a browser: cairosvg needs libcairo,
which is not installed and is not worth installing on a contributor's machine to
draw four small squares.

So this draws the mark analytically instead. That is only reasonable because the
mark *is* analytic — a rounded rectangle, two arcs of an annulus, and a dot — and
because keeping it in the repository means the icons can be regenerated from the
same numbers as the SVG rather than being four opaque binaries nobody can edit.

Geometry is taken from `favicon.svg` and its 40x40 viewBox. The two arcs there are
SVG elliptical-arc commands; the centre and angular extent of each are derived in
`_ARCS` below rather than eyeballed, so a change to the SVG can be followed here.

Usage:
    python scripts/build_extension_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

#: The viewBox the geometry below is expressed in.
VIEW = 40.0

#: Straight from favicon.svg.
BACKGROUND = (0x24, 0x4B, 0xE8)
FOREGROUND = (0xFF, 0xFD, 0xF7)
CORNER_RADIUS = 9.0

#: Supersampling factor per axis. 4 means 16 samples a pixel, which is enough to
#: keep the 16px icon's arc from looking chewed and is instant at these sizes.
SAMPLES = 4


def _arc(start: tuple[float, float], end: tuple[float, float], radius: float, width: float):
    """Centre, radii and kept angular range for an SVG arc with large-arc=1, sweep=0.

    Both arcs in the mark share a vertical chord, so the centre sits on the
    perpendicular bisector at a distance set by the chord half-length. large-arc=1
    with sweep=0 keeps the long way round — the side away from the chord — which is
    what opens the C to the right.
    """
    (x0, y0), (x1, y1) = start, end
    # The derivation below puts the centre on a horizontal line through the chord's
    # midpoint, which is only correct for a vertical chord. Both arcs in this mark
    # have one. Asserted rather than assumed: an edited SVG with a tilted chord
    # would otherwise render a subtly wrong icon and nothing would say so.
    if not math.isclose(x0, x1):
        raise ValueError(f"arc chord is not vertical: {start} -> {end}")
    midpoint_y = (y0 + y1) / 2
    half_chord = abs(y1 - y0) / 2
    offset = math.sqrt(max(radius * radius - half_chord * half_chord, 0.0))
    centre = (x0 - offset, midpoint_y)
    # The gap spans the angles between the two endpoints, measured from the centre.
    cutoff = abs(math.atan2(y0 - centre[1], x0 - centre[0]))
    return {
        "centre": centre,
        "inner": radius - width / 2,
        "outer": radius + width / 2,
        # Points whose angle falls inside this cutoff are in the opening.
        "cutoff": cutoff,
    }


_ARCS = (
    # <path d="M28 9a13 13 0 1 0 0 22" stroke-width="4"/>
    _arc((28.0, 9.0), (28.0, 31.0), radius=13.0, width=4.0),
    # <path d="M28 15a7 7 0 1 0 0 10" stroke-width="4"/>
    _arc((28.0, 15.0), (28.0, 25.0), radius=7.0, width=4.0),
)

#: <circle cx="30" cy="20" r="2.5"/>
_DOT = ((30.0, 20.0), 2.5)


def _inside_rounded_rect(x: float, y: float) -> bool:
    r = CORNER_RADIUS
    if r <= x <= VIEW - r or r <= y <= VIEW - r:
        return 0.0 <= x <= VIEW and 0.0 <= y <= VIEW
    cx = r if x < r else VIEW - r
    cy = r if y < r else VIEW - r
    return math.hypot(x - cx, y - cy) <= r


def _inside_mark(x: float, y: float) -> bool:
    dx, dy = x - _DOT[0][0], y - _DOT[0][1]
    if dx * dx + dy * dy <= _DOT[1] * _DOT[1]:
        return True
    for arc in _ARCS:
        cx, cy = arc["centre"]
        distance = math.hypot(x - cx, y - cy)
        if not (arc["inner"] <= distance <= arc["outer"]):
            continue
        if abs(math.atan2(y - cy, x - cx)) >= arc["cutoff"]:
            return True
    return False


def render(size: int) -> bytes:
    """RGBA rows for one icon, supersampled."""
    scale = VIEW / size
    step = scale / SAMPLES
    origin = step / 2
    rows = bytearray()
    total = SAMPLES * SAMPLES
    for py in range(size):
        rows.append(0)  # PNG per-scanline filter: none
        for px in range(size):
            covered = 0
            marked = 0
            for sy in range(SAMPLES):
                y = py * scale + origin + sy * step
                for sx in range(SAMPLES):
                    x = px * scale + origin + sx * step
                    if not _inside_rounded_rect(x, y):
                        continue
                    covered += 1
                    if _inside_mark(x, y):
                        marked += 1
            if covered == 0:
                rows.extend((0, 0, 0, 0))
                continue
            # Blend the mark over the background by its share of the covered
            # samples, then let overall coverage drive alpha so the rounded corners
            # stay smooth instead of stair-stepping.
            share = marked / covered
            pixel = tuple(
                round(BACKGROUND[i] * (1 - share) + FOREGROUND[i] * share) for i in range(3)
            )
            rows.extend((*pixel, round(255 * covered / total)))
    return bytes(rows)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def png(size: int) -> bytes:
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(render(size), 9))
        + _chunk(b"IEND", b"")
    )


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "extension" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    # 16 toolbar, 32 Windows, 48 the extensions page, 128 the store listing.
    for size in (16, 32, 48, 128):
        target = out / f"icon{size}.png"
        target.write_bytes(png(size))
        print(f"{target.relative_to(out.parent.parent)}  {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
