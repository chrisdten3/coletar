# The look of coleta, and why it looks that way

Written 2026-09-20, while fixing an Atlas that had drifted away from it. This is
descriptive, not aspirational: it records the system the product already has so
that the next screen added to it does not invent a second one.

## The vision, in one line

**coleta is an archive you own, so it should look like an archive and not like a
dashboard.** Paper, ink, one accent, and technical annotation set in monospace —
the visual language of a well-kept record rather than of analytics software.

That is not decoration. The product's claim is provenance: every object can say
where it came from, who wrote it, and what it used to say. A page that reads like
a BI tool implies numbers refreshed from somewhere else. A page that reads like a
document implies something kept, which is the truth.

## The thematic elements

### 1. Paper ground, ink text

```
--bg      #f4f1e9   the ground: warm, off-white, never pure #fff
--paper   #fffdf7   raised surfaces — cards, sections, toolbars
--rail    #ebece5   the sidebar's own ground
--ink     #202522   text; near-black with green in it, never #000
--muted   #596158   secondary text and every annotation
--line    #d6d9cf   borders, always 1px
```

Nothing is pure white or pure black. The warmth is what separates this from a
generic SaaS surface, and it is the single easiest thing to break by adding
`#fff` to a new component.

### 2. One accent, used sparingly

```
--green   #244be8   the brand blue (the name is historical — do not "fix" it)
--amber   #8b5318   caution: restricted, withheld, needs review
--red     #a63b28   destructive only
```

`--green` is the *only* colour that carries meaning by itself: it means "this is
the thing to act on". A screen with three blue buttons has no primary action. The
per-surface colours (`--s-claude`, `--s-chatgpt`, `--s-local`) are a separate,
categorical scale and should never be used for emphasis.

**Naming caveat:** `--green` holds a blue. It was green in an earlier direction
and the variable name survived the change. Renaming it touches every stylesheet
for no user-visible gain, so it stays — but a reader should not be surprised.

### 3. Three typefaces, three jobs

| face | job |
|---|---|
| **Instrument Serif** | page headlines only — "A place for what matters." |
| **Manrope** | all interface text |
| **monospace** | anything machine-written or machine-read |

The monospace rule is the load-bearing one. Ids, counts, timestamps, confidence
scores, extraction methods, status lines: if a machine produced it, it is set in
mono and usually in `--muted`. That is how the eye learns, without being told,
which parts of a screen are assertions by the system and which are the user's own
words. It is the typographic expression of "provenance is never optional".

**Boldonse** is the wordmark alone. It is not a display face for anything else.

### 4. Quiet structure

- 1px borders in `--line`; no shadows except on genuinely floating elements
- 8–12px radii; 999px only for pills and chips
- 44px minimum control height
- Generous vertical rhythm — the archive should feel unhurried

### 5. Density is earned, not default

A card is for something you are deciding about. A row is for something you are
scanning. The Library was built from cards and met 3,818 objects, at which point
the card became the wrong container — this is why the list is now rows grouped
into sections. Use the heaviest container the content actually needs and no
heavier.

## How the Atlas follows from this

The Atlas is the part that had drifted, and it is worth writing down what going
wrong looked like, because the mistake was reasonable each step of the way.

Labels over a graph need to stay readable where edges pass beneath them. The first
fix gave every label an opaque plate. That works, and it turned the picture into a
wall of sticky notes: forty filled rectangles competing with each other and with
the nodes, in a design language whose whole premise is restraint. It also guessed
label widths from character counts, so the boxes were the wrong size and collided
anyway.

The system already had the answer. **Paint the text twice** — a thick stroke in
`--bg` beneath the glyphs, via `paint-order: stroke fill` — and the label knocks a
clean gap in whatever runs under it while drawing no shape at all. Paper ground,
ink text, nothing added.

The rest follows the same principle of removing rather than adding:

- **Nodes are filled and weightless**, not outlined bubbles. Size already encodes
  degree; a heavy stroke on every node encoded it a second time and doubled the
  noise.
- **Edges are `--ink` at 10% opacity.** They are the faintest thing on screen
  because they are the most numerous.
- **Labels anchor outward** — left half right-aligned, right half left-aligned —
  so nothing crosses the crowded middle.
- **What cannot be drawn legibly is not drawn.** Labels are placed most-connected
  first and any that still collides is dropped, not layered; the node keeps its
  circle, tooltip and click target. A focused hub draws at most fourteen spokes and
  says so: *"39 connected facts · showing 14"*, with a link to the list.

That last one is the rule worth generalising. **A view that cannot show
everything should say what it is not showing and point at where the rest lives** —
rather than truncating quietly, or rendering an unreadable pile in the name of
completeness. The same instinct is why withheld objects appear in a compile
manifest instead of vanishing.

## Where the two views sit

`List` and `Atlas` are one library seen two ways, not two features.

- **List** groups by subject — entities and projects, derived from edges the
  extractor already wrote. It is the working view, and the default.
- **Atlas** is the same graph as a picture: what the corpus is *about* and what
  sits near what. It is for orientation, not for reading.

There is deliberately no third "flat list" tab. It existed, it was a wall of
3,818 undifferentiated rows, and nobody wanted it — the sectioned view is what a
list of this corpus should always have been. The flat rendering survives only as
the fallback when the grouping request fails.

## Rules for anything new

1. No pure white, no pure black.
2. Machine-written text is monospace and `--muted`.
3. One `--green` element per screen region — the thing to do next.
4. `--amber` means withheld or unreviewed. It never means "highlight".
5. Prefer removing a border, a fill or a shadow over adding one.
6. If a view is hiding something, it says so and links to where it is.
