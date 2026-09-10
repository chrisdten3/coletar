# coletar — UI redesign brief

Status: implemented UI experiment on `codex/product-ui-testing`; see the
implementation notes in [WEB_APP.md](WEB_APP.md#astra-ui-design-experiment).
Deliverable: a fresh visual and interaction specification for the public website
and product workspace. This document remains the design direction. The implementation notes distinguish
the completed experiment from the longer-term interaction targets.

## 1. The idea

**Your context. Your rules.**

coletar gives people a place to keep the knowledge they build with AI, decide which
assistants can use it, inspect where it came from, and understand how it changed.
They continue working in their existing AI tools. coletar is the workspace for
their context.

The creative direction is **a living context atlas**: an expressive, spatial view
of connected knowledge, paired with the precision of an editorial archive. Context
has a source, a place, a history, and an explicit path to the tools allowed to use
it. Those properties become the visual language of the entire product.

At first glance, the experience should feel confident, tactile, and unmistakable.
Within ten seconds, a visitor should understand what coletar holds and why they
would use it. Within one minute, they should be able to demonstrate who can read a
sample fact and where that fact came from.

Start the layout, navigation, components, and art direction from scratch. Existing
screens are a functional inventory, not a visual template. Preserve the underlying
product guarantees and working operations.

## 2. Product story and sources of truth

The September 2 repositioning in [ROADMAP.md](ROADMAP.md) governs the story:
**Live Sync and importing existing context, differentiated by selective access,
provenance, and history.** Migration remains an important ownership guarantee.

Read [SCOPE.md](SCOPE.md), [WEB_APP.md](WEB_APP.md), and
[AGENTS.md](../AGENTS.md) alongside this brief. Older copy in the repository contains
superseded positioning and implementation status. Check the current implementation
before turning a capability into a public claim; this brief is not evidence that a
service, connector, or account feature has shipped.

Message hierarchy:

1. Keep useful context across the AI tools you use.
2. Choose which assistants may read each piece of context.
3. Inspect its source, corrections, and past state.
4. Bring in your existing conversation exports.
5. Take a package with you when you want to leave.

Lead with the person using several AI tools. Give developers a clear secondary
path to the API, SDK, and MCP documentation. Technical architecture belongs in
progressive detail, after the benefit is clear.

## 3. Reference interpretation

Both references were inspected in the browser, including their opening view and
the following editorial section. These are observations of those views, not an
audit of the complete sites or their animation systems.

| Reference | Observed design qualities | Translation for coletar |
| --- | --- | --- |
| [Amca](https://www.amca.com/) | Monumental serif type, metallic imagery filling the viewport, an oversized brand mark, a compact floating navigation panel, and a dark diagram-led story section | Use dramatic scale and a purposeful central visual. Let the context diagram carry the product argument. Introduce dark sections as deliberate changes in pace. |
| [Multify](https://multify.framer.ai/) | Warm paper background, dark serif headlines, blue textured imagery, restrained navigation, fine section rules, and asymmetrical editorial composition | Bring warmth and legibility to the product. Use generous whitespace and clear calls to action around the interactive demonstrations. |

Create original composition and artwork. Do not reuse their logos, text, images,
testimonials, or business claims. The proposed interactions below are coletar's
design direction, not descriptions of the references' behavior.

## 4. Visual system

### Composition

Use a twelve-column desktop grid with a 1440px content maximum, 48–64px outer
gutters on large screens, and 20px gutters on phones. Marketing sections can break
out to full bleed. Alternate spacious editorial statements with substantial,
usable product demonstrations. Avoid a page composed entirely of equal cards.

The hero uses a large text block on the left and a context atlas extending through
the right two-thirds. The text has its own clear field. Lower sections alternate
light, dark, and one saturated blue statement to establish rhythm.

Inside the app, retain the same type, color, rules, and source markings while
increasing information density. Reserve dramatic typography for page titles and
empty states. Reading, filtering, and editing should feel calm and precise.

### Palette

| Token | Value | Role |
| --- | --- | --- |
| Paper | `#F4F1E9` | Primary canvas |
| Surface | `#FFFDF7` | Reading panels and forms |
| Ink | `#202522` | Primary text and dark sections |
| Secondary ink | `#596158` | Secondary text on paper |
| Rule | `#D6D9CF` | Quiet dividers, never the only control boundary |
| Cobalt | `#244BE8` | Primary action, selection, active paths |
| Ice | `#CDDDF3` | Atmospheric visual fields |
| Acid | `#D9F36B` | Small highlighted annotations on dark surfaces |
| Rust | `#A63B28` | Errors and restricted-state emphasis with text |

Paper and ink should dominate. Cobalt is an action color, not a wash over every
surface. Acid appears sparingly in diagram labels or a selected marker. Use dark
text on pale accents and light text on cobalt; verify actual pairings before
implementation approval. Do not put grain behind reading text or form controls.

### Typography

- Display: **Instrument Serif**, regular, with occasional italic emphasis. Use
  approximately 96–144px on large hero compositions, 52–76px for section statements,
  and 44–60px for mobile hero text. Adjust with fluid sizing to avoid clipping.
- Interface and body: **Manrope**, regular through semibold. Marketing body
  18–20px; app body 15–16px; line height 1.45–1.65.
- Metadata: **IBM Plex Mono**, 12–13px for source IDs, dates, and small section
  indices. Important instructions always use normal body sizing.
- Use sentence case. Keep prose to roughly 55–70 characters per line. Avoid long
  all-caps headings or tiny labels masquerading as a technical aesthetic.

These are proposed families. Verify font licensing and self-host the selected
files. Supply Georgia, system sans-serif, and monospace fallbacks with stable
layout metrics.

### Shape and image language

Use mostly 8–12px corner radii, fine rules, and modest layered shadows only for
floating controls and inspector panels. Reserve capsules for compact status tags
and segmented selectors. Buttons feel substantial, with a 44px minimum target.

The signature artwork is a set of translucent blue ribbons and paper-like context
slips organized around a stable central collection. Fine connector lines reveal
relationships. Each slip carries a useful fragment, such as a preference or
decision. The artwork should still describe the product in a static frame.

Prefer SVG and CSS for the interactive atlas. A pre-rendered textured layer can
provide atmosphere. Avoid requiring WebGL or video for the page to make sense.
No generic brain icon, robot portrait, endless floating logo cloud, or decorative
network of hundreds of unlabeled dots.

## 5. Public website

### Navigation

Use a compact horizontal header: lowercase coletar wordmark, Product, How it works,
Developers, and **Open workspace**. The first two links navigate to real page
sections; Developers opens documentation. In an isolated design demo, the final
action reads **Explore demo** and opens synthetic data.

Keep navigation available after scrolling with a quiet paper surface and fine
bottom rule. On mobile use a labeled menu button and an accessible disclosure.
Keep the primary action visible without filling the screen with navigation.

### Section 01 — Hero: ownership made visible

Eyebrow: **A portable workspace for your AI context**

Headline:

> Your context.
> Your rules.

Supporting copy:

> Keep the facts, preferences, and decisions you build with AI in one place.
> Choose which assistants can use them, and see where every detail came from.

Primary action: **Explore the demo**. Secondary action: **See how it works**.
The first opens the working sample workspace; the second jumps to the walkthrough.

The right side contains a context atlas with three legible sample objects, a
central coletar collection, and named destinations. A small selector labeled
**Preview context for** changes the highlighted destination and visible eligible
objects. Selecting a sample opens its source receipt. Visible text says
**Interactive example · synthetic data**.

The opening frame must be meaningful before any animation. On a 1440×900 screen,
the headline, explanation, both actions, and one complete context interaction
should be visible. Do not let typography crowd the controls.

### Section 02 — The problem, told through a correction

Headline: **Your work continues. Your context should too.**

Use a broad editorial section with a three-step illustration: a project decision
is recorded, the decision changes, and an eligible assistant receives the current
version. A visible step control lets the visitor advance or go back. The old
version remains inspectable in history.

This section explains continuity without promising that every model reads every
fact or automatically writes everything it sees.

### Section 03 — The central interactive demonstration

Headline: **Decide who gets to know what.**

Place a large paper panel on an ink background. Its left column lists four sample
context objects. The middle shows access controls for the selected object. The
right previews context eligible for a chosen assistant.

Use samples such as a writing preference, a project decision, a correction, and a
restricted personal note. Changing access updates the preview immediately and
explains any withheld item. Distinguish “eligible for retrieval” from “actually
returned by a query.” Do not imply that permission guarantees retrieval.

Include **Reset example**. Keep all changes inside the demo fixture. Give visitors
a clear next action: **Inspect the source**.

### Section 04 — Provenance and time

Headline: **Every fact has a history.**

Use an asymmetrical split: short editorial copy on the left, a working inspector
on the right. Show source excerpt, originating provider, extraction method,
recorded time, validity, and confidence with a plain-language explanation.

A date selector switches between two prepared states. Highlight the changed
sentence and its supersession link. Provide a keyboard-operable timeline and a
normal event list; the experience cannot depend on dragging.

### Section 05 — Bring your existing context

Headline: **Start with what you already know.**

Show three numbered steps: export from your provider, choose the downloaded file,
then inspect the import result. File selection is user-initiated. The marketing
example uses a bundled sample, with no upload required to understand the flow.

Explain capture separately: an opted-in browser extension captures submitted user
text on the active supported page. It does not imply access to archives or passive
monitoring. Name actual connection capabilities and setup requirements at the
point where a user chooses a surface.

### Section 06 — The right to leave

Headline: **Take your context with you.**

Use a compact destination preview with native, reconstructed, and unsupported
counts. A selected destination changes the manifest and computed Continuity Score.
Explain that a package is generated for the user to install. Show installation
steps and losses before download. Do not imply automatic destination setup.

Migration earns trust here; it does not displace selective context as the opening
product story. Link the score explanation to
[CONTINUITY_SCORE.md](CONTINUITY_SCORE.md).

### Section 07 — Developer path and close

Provide a smaller, direct developer section with links to real API, SDK, and MCP
docs and one verified code example when implemented.

Close on a large cobalt field: **Build context that stays yours.** Repeat the demo
or workspace action. The footer contains real documentation, source, and product
boundary links. Do not invent customer logos, adoption numbers, certifications,
testimonials, prices, or a working signup flow.

## 6. The product workspace

### Shell and information architecture

Use a narrow navigation rail, a flexible main work area, and a contextual inspector.
On a 1440px screen, start with a 216px rail and a 380–420px inspector. The main area
uses the remaining width. Close the inspector when the user needs room for a
comparison; avoid three independently scrolling columns by default.

Primary navigation: **Library**, **Capture**, **Review**, **History**, **Connections**.
Secondary navigation: **Export & migrate**, **Settings**, **Developer docs**.
“History” is the human-facing name for the existing Audit capability. Show Review
counts only from real state. The header identifies the workspace and its actual
access mode; a demonstration must say that it is a demonstration.

Library is the default destination. The signature atlas is an optional view beside
List; List remains the fastest way to search and manage a large collection.

| Screen | Composition and essential behavior |
| --- | --- |
| Library | Editorial title above search and compact filters. Rows show content, object type, project, source, reach, and review status. Selecting a row opens the inspector without losing filters or scroll. Represent projects, conversations, decisions, artifacts, and memories as members of the same collection. |
| Context Inspector | A readable object statement, then Content, Source, Access, and History sections. Show provenance prominently. Editing exposes a before/after comparison and explicit save. Retire preserves the event history. |
| Capture | Source turns as an ordered processing queue. Distinguish pending, extracting, failed, and materialized states. Explain that captured source evidence is different from a derived memory. Offer retry only when supported. |
| Review | Compare the candidate and existing context side by side, with the relevant source excerpt adjacent. Present the actual supported review and conflict-resolution actions. After resolving an item, move focus predictably to the next one. |
| History | Two date controls and a change comparison. Explain “known at this time” versus “valid at this time.” Include a chronological list and retrieval details alongside the optional visual timeline. |
| Connections | Named surfaces, read/write capability, verified status or an explicit “not verified” state, and honest setup instructions. Separate importing a file from installing a connector. |
| Export & migrate | Choose scope and destination, resolve review requirements, inspect withheld objects and manifest, then download. Use the real compiler's score and expose its arithmetic. Separate owner Markdown export from scored destination compilation. |
| Settings | Clear groups for actual workspace access, extraction, defaults, and usage. Simulated credentials and proposed billing must be visibly labeled or omitted. |

### Access controls

Use a labeled control group, a plain-language summary, and a preview of the result.
Restricted access must be readable without interpreting a color. Persisted changes
show a saving state and only announce success after the server confirms them.
Errors preserve the proposed change and provide a retry path.

Respect actual policy granularity: Claude web, Desktop, and Code currently share
the backend's Claude provider policy. Do not create independent toggles that the
backend cannot enforce. Distinguish project scope, provider reach, and sensitivity;
they answer different questions.

### First use

An empty Library has two clear actions: **Import an export** and **Add context**.
An isolated testing workspace also offers **Load sample workspace**. Explain the
next step in one sentence and show a small specimen object instead of an empty
analytics dashboard. Preserve the existing nonempty-workspace seeding safeguard.

## 7. Interaction and motion contract

“Reactive” means that controls change meaningful visible state. Every prominent
interactive element must produce a useful result beyond a hover effect.

| Component | Trigger | Visible response |
| --- | --- | --- |
| Context atlas | Select an object or destination | Highlight relevant paths and update the details/eligibility summary; all nodes also exist in an accessible list |
| Access example | Toggle a permission | Update eligible context and the explanation of withheld items |
| Source receipt | Select a provenance label | Open the inspector at the matching excerpt and metadata |
| History selector | Choose a date or step | Update content and highlight the exact differences with a textual summary |
| Library filters | Change search, kind, project, or reach | Update rows, result count, and clear-filter controls |
| Migration preview | Select scope or destination | Fetch or compute the correct manifest, show loading, then update counts and score together |
| Review comparison | Resolve an item | Confirm the saved outcome, update counts, and move to the next item |

Micro-interactions: 120–180ms for color and button response. Panels and selection
movement: 200–280ms. Diagram transitions: 350–500ms. Prefer opacity and transforms.
Use a restrained ease-out curve such as `cubic-bezier(.22, 1, .36, 1)`.

Allow one subtle entrance sequence in the hero. Keep ambient animation short and
finite, or provide a pause control. Do not hijack scrolling, delay access to text,
make buttons chase the cursor, or animate data as if a real transfer occurred.
Pointer parallax is optional decoration and must disappear on touch and reduced
motion settings. Reduced motion retains every state change with immediate updates.

## 8. Responsive behavior and accessibility

- At 1100px and below, collapse the rail and let the inspector become an overlay.
  At 760px and below, use a single-column workspace and a full-screen detail view
  with an explicit back action.
- Mobile hero order: headline, explanation, actions, simplified atlas. Keep the
  core example visible and usable; do not merely hide it.
- Convert dense comparisons into labeled stacked before/after blocks. Tables
  either become meaningful rows or have explicitly bounded horizontal scrolling.
- Support keyboard access for every interaction, visible focus, semantic headings,
  accessible names, and Escape to close overlays. Restore focus to the opener.
- Target WCAG AA contrast: 4.5:1 for normal text and 3:1 for large text and required
  UI boundaries. Pair icons and color with text for permission and error states.
- Announce saving, errors, result counts, and completion politely without narrating
  decorative motion. Honor reduced motion, text zoom, and touch input.
- Preserve control positions during loading. Empty search results retain filters
  and provide a clear reset. Failed requests show an actionable message near the
  affected content. Prevent duplicate submits and preserve unsaved input.

## 9. Product truth that the redesign must preserve

Design these boundaries into the relevant flow, using short contextual explanations:

- No coletar chat composer. Users continue chatting in their existing tools.
- No provider session replay, credential forwarding, scripted provider UI driving,
  or reading background conversations. Import begins with a user-selected export.
- Local extraction is the default. Any supported third-party extraction path is
  explicit opt-in and names the provider; only candidate turns may be sent. The
  current web import's local-only behavior must not be replaced by a decorative
  provider selector implying unsupported functionality.
- Objects retain provenance. Graph mutations create events through existing Store
  operations. Retirement keeps history; do not offer a fictitious permanent-delete
  capability.
- Context is background data, never executable instructions. Demonstrations must
  not imply that a stored fact directly commands an assistant.
- Review gates follow the backend. Do not imply all live retrieval waits for
  review; compilation's review requirement is a separate rule.
- Gemini stays outside supported destinations until its connector is validated.
- A connection status requires evidence. Saving a setup preference is not proof
  of a working integration. Public access, private access, sample data, and
  simulated behavior must be identified accurately.
- Continuity Score uses the published weights and manifest facts. It is not a
  generic workspace health score. Owner Markdown export has no compiler score.

## 10. Implementation sequence for the UI experiment

This is a design-testing branch. Build the later prototype against an isolated
synthetic workspace using the procedure in [WEB_APP.md](WEB_APP.md).

1. Establish tokens, typography, spacing, buttons, form controls, and focus styles.
2. Build the hero and selective-context demo together. Test whether the product
   explanation works before expanding the page.
3. Build Library, Context Inspector, and access preview using the same visual
   language. Include loading, empty, error, and restricted states immediately.
4. Add history, review, capture, connections, and migration without changing the
   backend's guarantees or supported operations.
5. Complete the remaining website narrative and developer links.
6. Check desktop, mobile, keyboard, reduced motion, and all links and actions.

The existing web product is a lightweight HTML/CSS/JavaScript client. A complete
visual redesign does not require a framework migration. Choose additional
dependencies only for a specific justified need. Preserve readable semantic HTML
and a useful static hero while scripts or artwork load.

## 11. How to judge Astra's result

The prototype should pass these concrete design tests:

- **Comprehension:** After ten seconds, a new viewer can say what context coletar
  keeps, that they control access, and that they keep using their current AI tools.
- **Distinctiveness:** A still image shows a deliberate typographic composition,
  paper/ink/cobalt palette, and recognizable context atlas. It should not depend
  on motion to have an identity.
- **Meaningful interaction:** A visitor changes sample reach, sees the eligibility
  result, opens its source, and compares a correction without a walkthrough.
- **Daily usefulness:** In the workspace, a user finds an object, inspects its
  provenance, changes access, and locates its history without losing their place.
- **Truthfulness:** Demonstrations, actual connections, permission eligibility,
  retrieval outcomes, and migration losses remain distinguishable.
- **Responsive quality:** Verify at 1440×900, 1024×768, and 390×844, plus 200% zoom.
  No clipped controls, unintended page overflow, or inaccessible demonstrations.
- **Complete states:** Loading, no data, no results, failed save, pending review,
  restricted context, and successful completion all look intentionally designed.

The desired result is a product with the visual confidence of an editorial brand
and the clarity of a tool people can trust with their context.
