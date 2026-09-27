"""Decision capture, synthesis and automated resolution (M11).

Three tiers, each usable without the next:

  * **Raw capture** (`raw_capture`) -- a tenant's own backend posts what one of its
    tools was asked and what it answered. Opt-in per tool, encrypted at rest, and
    short-lived. This is the richer sibling of the label-only `/v1/decisions` path,
    which stays the default because it never holds a customer's real data.
  * **Synthesis** (`coletar.jobs.decision_synthesis`) -- a batch pass distils those
    raw traces into structured samples and shreds the raw content behind itself.
  * **Resolution** (`backend`, `promotion`) -- for a pattern a human has explicitly
    approved, a fast decision model answers instead of an LLM. Nothing is ever
    automated without that approval, and a backend that cannot answer confidently
    always hands the call back rather than guessing.
"""
