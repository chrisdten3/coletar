# Continuity Score

*How much of your context actually survived the move.*

Nothing in the memory-infra space uses this framing, so coletar gets to define it —
which means it has to survive scrutiny. A black-box percentage is a badge, not a
differentiator, and the people who would pay for this before it is mainstream will see
through one in about five minutes.

So: the weighting is a public constant in
[`coletar/compiler/continuity.py`](../src/coletar/compiler/continuity.py), every term
is computed from Migration Manifest facts rather than estimated, and any score can
print its own arithmetic.

## Definition

```
continuity_score = 0.40 · object_coverage
                 + 0.30 · fidelity
                 + 0.20 · scope_preservation
                 + 0.10 · staleness
```

| Term | Definition | Why this weight |
|---|---|---|
| `object_coverage` | mapped objects ÷ objects the compile was **asked** to move | Dropping context is the worst failure. The denominator is the ask, so objects that never reached the manifest count as lost instead of vanishing from the math. |
| `fidelity` | `native` ÷ total manifest entries | A fact flattened into a prose blob technically moved, but the destination can no longer act on it as a fact. |
| `scope_preservation` | project-scoped facts landing in the right destination container ÷ project-scoped source objects | Flattening project scope to global is the most common silent corruption in every competitor's export. |
| `staleness` | `1.0` for 24h, then linear decay to `0.0` over 30 days | Real, but the smallest term: a slightly old compile is still a working compile. |

Fidelity categories are the Appendix D manifest categories: **native** (a real
container of the right type), **reconstructed** (preserved, but flattened), and
**unsupported** (no destination representation exists).

## Reading a score

```python
from coletar.compiler import Fidelity, ManifestEntry, MigrationManifest, score

result = score(manifest, source_object_count=42)
print(result.explain())
```

```
Continuity Score
  object_coverage      0.905 x 0.40 = 0.362
  fidelity             0.711 x 0.30 = 0.213
  scope_preservation   1.000 x 0.20 = 0.200
  staleness            1.000 x 0.10 = 0.100
  total                0.875
```

An honest score for the ChatGPT destination will be *lower* than for Claude, because
OpenAI offers no native container for most of what coletar holds. That is the correct
outcome and it should be shown to the user, not smoothed away. A compiler that scores
itself well by relabelling `reconstructed` as `native` has broken the only number in
the product anyone should trust.

## Changing the weights

If you change `WEIGHTS`, change this file in the same commit. A published weighting
that drifts from the implementation is worse than no published weighting at all.
