# Horizon — second UI design direction

Implemented on `codex/product-ui-primefold`. The first direction is preserved on
`codex/product-ui-testing` at `bacfe1d` and pushed to origin.

## Message and art direction

**Your thinking. Without boundaries.** A portable AI workspace should feel like
forward movement: useful context accompanies the person, while that person chooses
its destinations. Landscape photography gives this idea scale and breathing room.
Actual context cards, source receipts, and permission controls make it concrete.

Use monumental Manrope headings, muted forest and sage, warm white surfaces,
thin rules, rounded navigation, and restrained translucent panels. Alternate open
editorial sections with denser functional demonstrations. Product controls remain
clear and quiet; expressive scale belongs to the website.

## Reference exploration

On 10 September 2026 the reference sites were explored interactively in the browser:

- [Primefold](https://primefold.ai/): selected a hero carousel item, scrolled the
  connected-input diagram and product cards, followed Experience Intelligence,
  switched its audience selector to HR / People manager, and opened Security.
  The direction borrows photographic scale, floating navigation, generous white
  space, and demonstrations that change with the visitor's choice.
- [Amca](https://www.amca.com/): scrolled its aviation timeline and wireframe reveal,
  opened the main menu, followed About, opened the table of contents, and selected
  Our Technology. The direction borrows editorial pacing, strong section contrast,
  fine technical rules, and a physical sense of scale.

The layout, product diagrams, copy, and visual system are original to coletar.
Photographs come from separately credited licensed sources, not those websites.

## Page and interaction specification

1. Full-height photographic introduction with three selectable stories: context,
   access rules, and continuity. Each changes the copy, receipt, and destination
   marks. Selection is deliberate; there is no timed carousel.
2. An open introduction explains the human benefit before the architecture.
3. Collect / Organize / Continue controls change the explanation and highlight the
   corresponding part of the context-flow diagram.
4. Three illustrated cards explain collection, access, and history.
5. A synthetic permission playground demonstrates per-object eligibility. Switching
   provider changes the result count; source receipts explain each example.
6. A dark history section includes a two-date comparison and inspectable source.
7. Paired import and migration cards describe human-initiated operations.
8. Native FAQ disclosures, a photographic closing invitation, and an oversized
   wordmark conclude the page.

The workspace uses a dark forest navigation rail, white content surfaces, clearer
sans-serif hierarchy, and a search dialog available from Quick search or Cmd/Ctrl-K.
Search filters existing active context; arrow keys browse results and Enter opens
an object. The library's list/atlas modes and existing object, review, import,
compiler, settings, and history flows remain connected to their existing APIs.

## Implementation boundaries

`horizon.js` and `horizon.css` supply this branch's visual layer over the vanilla
app. No React runtime or new production dependency is introduced. Icons are the
Lucide set commonly used with shadcn/ui; provider marks use actual brand assets.
See the static `ICONS.md` for versions and licenses. Images and fonts are local.

All landing examples are synthetic and do not mutate the graph. Read eligibility
is not a promise of retrieval. Provider policies retain their actual granularity;
Claude web, Desktop, and Code share the Claude provider policy. Existing simulated
connection setup remains labeled. This design does not add billing, authentication,
provider acquisition, or a new connector.

Mobile stacks the hero and receipt, reduces display sizes, folds marketing links
into a menu, and stacks the diagram and playground. Native buttons/disclosures,
visible focus, labeled controls, Escape dismissal, and reduced-motion styling
support keyboard and motion-sensitive use.

## Validation

The 35 Inspector tests pass, as do Ruff, strict mypy, and JavaScript syntax checks.
Browser checks cover the story/source interaction, workflow selector, eligibility
switches, history dates, FAQ, mobile menu, and search-to-object flow. Desktop and
mobile layouts were visually inspected; the local preview uses a separate synthetic
Store so UI testing does not alter the configured production graph.
