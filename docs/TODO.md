# TODO — next up

A short, living list of the next concrete steps, pulled from
[ROADMAP.md](ROADMAP.md) in sequencing order. This is not a second source of
truth: when an item here lands, tick it in the roadmap and delete it here.
Full context, acceptance criteria and rationale live in the roadmap entry
each item links back to — this file only exists so the next few steps don't
require re-reading the whole thing.

M1 and M2 are done. M3 (Claude connector / Live Sync) is the milestone in
progress — tenant isolation (M3.1) and local cross-surface propagation (M3.2)
are proven with no infrastructure. What's left is the deployed leg:

- [ ] **Run `fly deploy`** (M3.3). Deployment artifacts (`Dockerfile`,
      `fly.toml`, boot-time guards) are built and verified against Postgres
      over real HTTP. What's missing is Fly credentials, which are the
      user's to enter — this is a manual step, not an engineering one.

- [ ] **Register the deployed server as a Claude Custom Connector** (M3.3),
      completed from Claude's own settings once the endpoint is live. Also
      manual, and blocked on the item above.

- [ ] **Wire the simulated OAuth handshake** so it issues a token scoped to
      one tenant (M3.3) — the last piece of the auth path between
      "deployed" and "usable by a real Claude account."

- [ ] **Ship the `CONNECTORS.md` instruction snippets as a copy-paste flow**
      (M3.3), so setup doesn't require reading the whole doc to find the
      snippet that actually goes in the Project.

- [ ] **Run the cross-conversation propagation harness against the live
      connector** (M3.3): a fact written in Claude conversation A must be
      retrievable in a fresh conversation B, under 1s at p95. M3.2 proved
      the mechanism locally; the build plan is explicit that this step is
      **not optional** before M3 can be called done.

- [ ] **Build the tool-use reliability harness** (M3.5): scripted
      conversations driven through the Messages API against the deployed
      connector, measuring `write_memory` fire rate (≥85% on clear
      preference statements), `search_context` call-within-first-two-turns
      (≥80%) and spurious writes (<10%). This is the first bar in the whole
      roadmap that measures a model's *choice* rather than deterministic
      behavior, and it needs its own Anthropic API key — separate from the
      coletar token, pointing the other way. M3.3's finding that Claude
      prefers its own native memory unless the tool description explains
      *why coletar* should inform the versioned instruction snippet this
      harness measures.

The first three boxes are mostly blocked on credentials/manual setup rather
than code; the last three are the engineering work that can proceed in the
meantime.
