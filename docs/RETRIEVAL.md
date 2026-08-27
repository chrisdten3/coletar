# Retrieval

*What `search_context` actually returns, and why.*

Retrieval is the surface every connected model touches. Whatever ranks first here is
what Claude, ChatGPT and a local model each see at the top of their next conversation
— so the blend below is a product decision, not a tuning detail, and it lives in one
module rather than in each backend.

## The formula

[`coletar/retrieval/ranking.py`](../src/coletar/retrieval/ranking.py):

```
relevance = 0.55 · vector_similarity + 0.45 · lexical_coverage
score     = relevance · (0.5 + 0.5 · confidence) · recency_factor
```

| Term | Definition | Why |
|---|---|---|
| `vector_similarity` | cosine against the object's embedding, clamped at zero | Finds a memory the query paraphrases rather than repeats. |
| `lexical_coverage` | fraction of the query's content tokens the object contains | Keeps an exact identifier — a project name, a library, a person — from being smeared into approximate neighbours. Recall-shaped, so a short exact match does not lose to a long loose one. |
| `confidence` | scales relevance between 0.5× and 1.0× | §2 and §3.1: a typed connector write should outrank a line recovered from a raw export. It scales rather than gates — a low-confidence memory is still worth showing if it is the only thing that matches. |
| `recency_factor` | 0.85–1.0, 90-day half life | A tiebreaker only. A fact from a year ago that answers the question beats a fresh one that does not. |

Both halves of `relevance` run over the same vocabulary: stopwords dropped, lightly
stemmed. That is not cosmetic. With stopwords left in, the query *"what should I do
when a function fails"* ranks an object that merely shares "what / should / does"
above the one that actually answers it.

## Active objects only

An object is returned only when nothing has retired it **and** nothing newer
supersedes it. The second half matters: compression (§6) retires superseded objects
eventually, but retrieval must not serve a stale fact in the window before the job
next runs. Writing the correction is what hides the old fact, not the job.

## Scope

`search` takes the scope the *conversation* is happening in:

| Query scope | Returns |
|---|---|
| project `p` | objects scoped to `p`, plus everything global |
| global | global objects only |
| none | everything |

A user's global preferences do not stop applying because they opened a project, and
another project's context never leaks in. `list_objects` is deliberately the
opposite — an exact filter, because `get_project_state` has to answer "what is in
this container".

## Embedders

| Backend | What it is | When |
|---|---|---|
| `hashing` (default) | Signed hashing of word unigrams and character 4-grams into the same 768-dim space, L2-normalized. Reaches morphological variants (`money` ~ `monetary`), **cannot** reach synonymy. | A fresh clone, with nothing installed. The in-process store has to work with no infrastructure, so the default embedder cannot require a model server. |
| `ollama` | `nomic-embed-text` (or any embedding model) on the user's own server. Verified against a live Ollama in `tests/test_embedding_live.py`, gated so the suite stays green without one. | Real deployments. §4 and §11: typed extraction and embedding at consumer scale is genuine inference spend, and on the local leg that spend is zero. |

Embedding happens **on the write path**, so an object is searchable on the very next
call. The bound on "when does a write become visible" is one embed call, not an
unspecified background window.

## Measured

Against [`tests/fixtures/relevance_set.json`](../tests/fixtures/relevance_set.json) —
30 objects with deliberate near-misses, 20 queries phrased the way a model phrases
them when calling `search_context`. **Both backends are measured, because both ship:**
`hashing` is what a fresh clone gets with nothing installed, `ollama` is what a real
deployment runs.

| | `hashing` (default) | `ollama` / `nomic-embed-text` |
|---|---|---|
| top-5 hit rate (bar: 90%) | **95%** (19/20) | **100%** (20/20) |
| hit@1 | 80% | 90% |
| MRR@5 | 0.858 | 0.933 |
| search latency, p50 | 0.2ms | 23.5ms |
| write path, per object | ~0.2ms | ~34ms (one HTTP round trip) |

The numbers live in
[`tests/fixtures/relevance_baselines.json`](../tests/fixtures/relevance_baselines.json)
and are asserted by `test_the_published_numbers_still_hold`. A documented figure that
has drifted from the implementation is worse than no figure, so the table and the test
move together.

**The hashing default's one miss** is *"is it ok to book a meeting at 9am"* against
*"Do not schedule anything before 10am"*. That needs synonymy, and there is no model
behind a hash. It was left in the set deliberately as the canary for this backend —
and it resolves under `nomic-embed-text`, which is what took 95% to 100%.

**The trade is latency, not accuracy.** Real embeddings cost roughly 100× on search
(0.2ms → 23.5ms, still an order of magnitude inside the 300ms bar) and roughly 170× on
write, because every write is an HTTP round trip to the model server. That write cost
is the one to watch: at 34ms per object, parsing a 500-conversation export (M6.2) would
spend minutes in the embedder alone. The `Embedder` protocol is batch-shaped for
exactly this reason, but `put_object` currently embeds one object per call, so bulk
ingest paths should batch before that milestone.

At 10,000 objects the in-process index answers in **~21ms p95** with the hashing
backend, against a 300ms bar.

## The evaluation suite

The 20-query relevance set answers *is retrieval working*. It cannot answer *where is
it failing*, which is what you need before changing a ranker. So M2.3 expands it to
**106 labelled queries over 58 objects**, in
[`tests/fixtures/retrieval_eval.json`](../tests/fixtures/retrieval_eval.json), across
the eight categories §5.1 names. The original 20 are carried verbatim and tagged, so
the headline number stays comparable across the expansion.

Measured at **two boundaries**, because they fail differently:

- **Candidate recall@50** — did narrowing keep the relevant object at all? A reranker
  cannot repair an object that candidate generation discarded, so this says whether a
  fix belongs in the retriever or the ranker.
- **Final ranking** — did it land in the context the model actually saw?

Reproduce with `uv run coletar evaluate` (add `--ollama` for the real embedder).

| | `hashing` | `nomic-embed-text` |
|---|---|---|
| candidate recall@50 | 91.5% | **100%** |
| hit@1 | 55.7% | **67.0%** |
| hit@5 | 85.8% | **92.5%** |
| MRR@5 | 0.676 | **0.768** |
| mean injected tokens | 80.2 | 82.4 |
| latency p50 / p95 | 0.5 / 0.9ms | 32.0 / 76.7ms |
| **leaks** | **0** | **0** |

`leaks` is a hard zero, not a target. A superseded or cross-scope object surfacing at
all is a correctness failure, and hit rate bought by *also* returning the stale answer
is not a retrieval win. Injected tokens sit next to accuracy for the same reason —
so a future change cannot buy hit rate by flooding the context.

### Where it fails, by category

| Category | `hashing` | `nomic-embed-text` |
|---|---|---|
| exact_id | 100% | 93.8% |
| near_miss | 100% | 100% |
| paraphrase | 93.3% | 96.7% |
| negation | 81.8% | 90.9% |
| temporal | 80.0% | 90.0% |
| multi_hop | 77.8% | 88.9% |
| scope_isolation | 77.8% | 77.8% |
| **correction** | **50.0%** | 90.0% |

Three things fall out of that table that the single headline number hid.

**Corrections are the weak leg, and it is structural.** When a fact is superseded, the
old object is correctly excluded from retrieval — but the *correction* often does not
mention the old value. "Chris is independent and consults through his own studio"
contains no "Acme", so a user asking *"is Chris still at Acme?"* matches nothing, and
the honest answer ("no, he moved") is unreachable. A real embedder papers over much of
this (50% → 90%) without fixing it. The fix is graph-shaped, not ranking-shaped:
match the superseded object for *recall*, then follow its `supersedes` edge and return
the replacement. That is a candidate-generation strategy, so it belongs at M4 behind
the strategy interface, evaluated against this suite — which is exactly what having
the suite is for.

**A better embedder is not uniformly better.** `nomic-embed-text` is *worse* at exact
identifiers (100% → 93.8%). This is the concrete case for the hybrid: the vector term
finds paraphrase, and the lexical term stops a project name or a port number being
smeared into semantic neighbours. Dropping either half would cost real accuracy.

**`scope_isolation` is 77.8% on both backends**, which means it is not a semantic
problem — no embedder will move it. Isolation itself is intact (zero leaks in both);
what fails is *ranking within* the correct scope when global and project objects
compete. Also an M4 concern.

## Backend parity

Postgres narrows candidates with an HNSW cosine scan unioned with a trigram match —
the part a database is genuinely better at — and the in-process store does one
matrix-vector product over its whole index. Both then hand their similarities to the
same `rank_score`. `test_ranking_matches_the_in_process_store` pins it: swapping the
backend changes performance, not which memory a model sees.
