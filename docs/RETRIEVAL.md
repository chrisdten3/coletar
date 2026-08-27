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
| `ollama` | `nomic-embed-text` (or any embedding model) on the user's own server. | Real deployments. §4 and §11: typed extraction and embedding at consumer scale is genuine inference spend, and on the local leg that spend is zero. |

Embedding happens **on the write path**, so an object is searchable on the very next
call. The bound on "when does a write become visible" is one embed call, not an
unspecified background window.

## Measured

Against [`tests/fixtures/relevance_set.json`](../tests/fixtures/relevance_set.json) —
30 objects with deliberate near-misses, 20 queries phrased the way a model phrases
them when calling `search_context`:

| | `hashing` (default) |
|---|---|
| top-5 hit rate | **95%** (19/20), against a 90% bar |
| p95 search latency, 10,000 objects | **~21ms**, against a 300ms bar |
| write path | ~0.2ms/object |

The single miss is *"is it ok to book a meeting at 9am"* against *"Do not schedule
anything before 10am"*. That needs synonymy, and there is no model behind a hash. It
is kept in the set on purpose: it is the query that should start passing the day
`COLETAR_EMBEDDING_BACKEND=ollama` becomes the default.

## Backend parity

Postgres narrows candidates with an HNSW cosine scan unioned with a trigram match —
the part a database is genuinely better at — and the in-process store does one
matrix-vector product over its whole index. Both then hand their similarities to the
same `rank_score`. `test_ranking_matches_the_in_process_store` pins it: swapping the
backend changes performance, not which memory a model sees.
