"""An idea over time, as cluster mass per bucket (SCOPE §6).

The question behind this module is "is this topic growing or fading in my
context" -- and the tempting way to answer it is to ask a model to score each
memory for sentiment or theme and chart the scores. That is exactly the wrong
trade for this product. Extraction here is precision-over-recall because a wrong
memory costs trust; a wrong *chart* costs more, because it carries the authority
of a number and nobody audits an axis. A model-scored sentiment series is a
picture of model noise with a trend line drawn through it.

So the theme signal is derived, not judged. Objects are clustered by the same
embeddings retrieval already uses, and the series is the honest, checkable
quantity: how many active objects sat in each cluster at the end of each bucket.
Every point is a set of object ids the user can open. Nothing is inferred that
cannot be pointed at.

**Cluster quality tracks embedder quality, and that limit is real.** With
`OllamaEmbedder` these are topics. With the `HashingEmbedder` fallback the
vectors are hashed lexical features, so the clusters are closer to shared
vocabulary than shared meaning. The UI says which embedder produced a view for
that reason -- a chart whose meaning depends on a config value has to disclose it.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from coletar.history.query import Bucket
from coletar.retrieval.embedding import build_embedder, cosine, l2_normalize, stem, tokenize
from coletar.schema.objects import ContextObject, ObjectType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Where in the pairwise-similarity distribution the join threshold sits. A
#: *percentile*, not a cosine, because the absolute numbers are a property of
#: the embedder and not of the graph: on the same 128-object workspace the
#: hashing fallback puts the median pair at 0.009 and its 98th percentile at
#: 0.244, while a real sentence embedder would put both an order of magnitude
#: higher. A hard-coded cosine tuned against one of them silently produces a
#: single cluster of everything, or 128 clusters of one, against the other --
#: and `embedding_backend` is a config value that can change under this module.
DEFAULT_PERCENTILE = 0.975

#: Below this, "similar" stops meaning anything and the clustering is drawing
#: shapes in noise. A workspace whose distribution never gets here has no topics
#: worth charting, and says so rather than inventing some.
MIN_THRESHOLD = 0.12

#: And a ceiling, for the opposite failure. A percentile is a measure of spread,
#: so on a graph that is *all one topic* the 97.5th percentile lands above almost
#: every real pair and the clustering splits it into singletons -- reporting no
#: topics for a workspace that has exactly one. Capping the threshold means a
#: homogeneous graph reports the one topic it has.
MAX_THRESHOLD = 0.8

#: A cluster smaller than this is a coincidence, not an idea.
MIN_CLUSTER_SIZE = 3

#: Objects that are context *about* the graph rather than content in it. An
#: entity row ("Maya") would otherwise anchor a cluster that means nothing.
_EXCLUDED = frozenset({ObjectType.EPISODE, ObjectType.ENTITY})


def _utc(when: datetime) -> datetime:
    return when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC)


@dataclass
class _Cluster:
    centroid: list[float]
    members: list[ContextObject] = field(default_factory=list)

    def add(self, obj: ContextObject, vector: list[float]) -> None:
        n = len(self.members)
        self.centroid = l2_normalize(
            [(c * n + v) / (n + 1) for c, v in zip(self.centroid, vector, strict=True)]
        )
        self.members.append(obj)


@dataclass(frozen=True)
class IdeaCluster:
    """One topic, with the objects that constitute it."""

    id: str
    label: str
    terms: list[str]
    size: int
    exemplar: str
    object_ids: list[str]
    first_seen: datetime
    last_seen: datetime
    #: Mean pairwise-to-centroid similarity. Low means "these were swept together
    #: by a threshold", and the UI greys those rather than hiding them.
    coherence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "terms": self.terms,
            "size": self.size,
            "exemplar": self.exemplar,
            "object_ids": self.object_ids,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "coherence": round(self.coherence, 4),
        }


@dataclass(frozen=True)
class ClusterSeries:
    cluster_id: str
    label: str
    #: (bucket start, active members at bucket end, object ids, added in bucket).
    points: list[tuple[datetime, int, list[str], int]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label,
            "points": [
                {"at": at.isoformat(), "value": value, "object_ids": ids, "added": added}
                for at, value, ids, added in self.points
            ],
        }


def _label_for(members: list[ContextObject], corpus: Counter[str]) -> tuple[str, list[str]]:
    """Distinctive terms, by lift against the whole graph.

    Raw frequency labels every cluster with the user's own name and the word
    "project". Lift -- how much more this cluster says a word than the graph does
    -- is what separates one topic from its neighbours.
    """
    local: Counter[str] = Counter()
    for obj in members:
        local.update({stem(t) for t in tokenize(obj.content)})

    total_corpus = sum(corpus.values()) or 1
    scored: list[tuple[float, str]] = []
    for term, count in local.items():
        if len(term) < 3 or count < 2:
            continue
        share_local = count / max(len(members), 1)
        share_global = corpus.get(term, 0) / total_corpus
        lift = share_local / (share_global + 1e-6)
        scored.append((lift * math.log1p(count), term))

    scored.sort(reverse=True)
    terms = [term for _, term in scored[:4]]
    label = " · ".join(terms[:3]) if terms else "unlabelled"
    return label, terms


def _auto_threshold(vectors: list[list[float]], *, sample: int = 6000) -> float:
    """Pick the join threshold from this graph's own similarity distribution.

    Sampled rather than exhaustive: the full pairwise matrix is O(n^2) and the
    percentile is stable well before that. Seeded, because a dashboard whose
    clusters regroup on refresh is one nobody trusts twice.
    """
    count = len(vectors)
    if count < 4:
        return MIN_THRESHOLD
    rng = random.Random(1729)
    scores: list[float] = []
    for _ in range(min(sample, count * (count - 1) // 2)):
        i, j = rng.randrange(count), rng.randrange(count)
        if i != j:
            scores.append(cosine(vectors[i], vectors[j]))
    if not scores:
        return MIN_THRESHOLD
    scores.sort()
    index = min(len(scores) - 1, int(DEFAULT_PERCENTILE * (len(scores) - 1)))
    return min(MAX_THRESHOLD, max(MIN_THRESHOLD, scores[index]))


def _active_at(obj: ContextObject, edge: datetime, superseded: dict[str, datetime]) -> bool:
    """Was this object live at `edge`?

    Deliberately read from the object's own timestamps rather than replayed from
    the log. Cluster mass is a shape, not an audit figure, and the extra pass
    would buy precision the chart does not claim. `rollup` is where a number has
    to be defensible to the event.
    """
    if _utc(obj.created_at) > edge:
        return False
    if obj.retired_at is not None and _utc(obj.retired_at) <= edge:
        return False
    gone = superseded.get(obj.id)
    return not (gone is not None and gone <= edge)


async def cluster_mass(
    store: Store,
    tenant_id: TenantId,
    *,
    bucket: Bucket = Bucket.WEEK,
    window_days: int = 180,
    threshold: float | None = None,
    min_size: int = MIN_CLUSTER_SIZE,
    now: datetime | None = None,
    limit: int = 1500,
) -> dict[str, Any]:
    """Cluster the graph, then chart each cluster's active membership per bucket."""
    end = _utc(now or datetime.now(UTC))
    start = end - timedelta(days=window_days)

    objects = [
        obj
        for obj in await store.list_objects(
            tenant_id, limit=limit, include_retired=True, include_superseded=True
        )
        if obj.type not in _EXCLUDED and obj.content.strip()
    ]
    if not objects:
        return {
            "clusters": [],
            "series": [],
            "embedder": store.embedder_model,
            "bucket": str(bucket),
            "start": start.isoformat(),
            "end": end.isoformat(),
        }

    superseded: dict[str, datetime] = {
        obj.supersedes: _utc(obj.created_at) for obj in objects if obj.supersedes
    }

    embedder = build_embedder()
    vectors = [l2_normalize(v) for v in await embedder.embed([o.content for o in objects])]

    resolved = threshold if threshold is not None else _auto_threshold(vectors)

    # Oldest first, so a cluster is anchored by the object that started the topic
    # and its label reads like the origin rather than the latest addition.
    order = sorted(range(len(objects)), key=lambda i: _utc(objects[i].created_at))

    clusters: list[_Cluster] = []
    for index in order:
        vector, obj = vectors[index], objects[index]
        best, best_score = None, resolved
        for candidate in clusters:
            score = cosine(candidate.centroid, vector)
            if score >= best_score:
                best, best_score = candidate, score
        if best is None:
            clusters.append(_Cluster(centroid=list(vector), members=[obj]))
        else:
            best.add(obj, vector)

    corpus: Counter[str] = Counter()
    for obj in objects:
        corpus.update({stem(t) for t in tokenize(obj.content)})

    by_id = {obj.id: index for index, obj in enumerate(objects)}
    kept: list[tuple[IdeaCluster, _Cluster]] = []
    for position, cluster in enumerate(clusters):
        if len(cluster.members) < min_size:
            continue
        label, terms = _label_for(cluster.members, corpus)
        sims = [cosine(cluster.centroid, vectors[by_id[m.id]]) for m in cluster.members]
        exemplar = max(
            cluster.members, key=lambda m: cosine(cluster.centroid, vectors[by_id[m.id]])
        )
        stamps = [_utc(m.created_at) for m in cluster.members]
        kept.append(
            (
                IdeaCluster(
                    id=f"idea_{position:02d}",
                    label=label,
                    terms=terms,
                    size=len(cluster.members),
                    exemplar=exemplar.content,
                    object_ids=[m.id for m in cluster.members],
                    first_seen=min(stamps),
                    last_seen=max(stamps),
                    coherence=sum(sims) / len(sims),
                ),
                cluster,
            )
        )

    kept.sort(key=lambda pair: -pair[0].size)

    boundaries: list[datetime] = []
    cursor = bucket.floor(start)
    while cursor <= end:
        boundaries.append(cursor)
        cursor = (
            (cursor + timedelta(days=32)).replace(day=1)
            if bucket is Bucket.MONTH
            else cursor + bucket.delta
        )

    series: list[ClusterSeries] = []
    for idea, cluster in kept:
        points: list[tuple[datetime, int, list[str], int]] = []
        for boundary in boundaries:
            edge = min(
                end,
                (boundary + timedelta(days=32)).replace(day=1) - timedelta(microseconds=1)
                if bucket is Bucket.MONTH
                else boundary + bucket.delta - timedelta(microseconds=1),
            )
            live = [m.id for m in cluster.members if _active_at(m, edge, superseded)]
            # Mass is a stock and only ever grows while nothing is retired, so
            # a chart of it alone cannot show a topic cooling off. `added` is
            # the flow behind it: new objects in this bucket, which is what
            # "is this idea still going" actually asks.
            added = [
                m.id
                for m in cluster.members
                if boundary <= _utc(m.created_at) <= edge
            ]
            points.append((boundary, len(live), live[:12], len(added)))
        series.append(ClusterSeries(cluster_id=idea.id, label=idea.label, points=points))

    return {
        "clusters": [idea.as_dict() for idea, _ in kept],
        "series": [s.as_dict() for s in series],
        "embedder": store.embedder_model,
        "bucket": str(bucket),
        "threshold": round(resolved, 4),
        "threshold_source": "auto" if threshold is None else "explicit",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "clustered": sum(idea.size for idea, _ in kept),
        "total": len(objects),
    }


def movers(payload: dict[str, Any], *, top: int = 3) -> list[dict[str, Any]]:
    """Which ideas grew and which faded over the charted window.

    The headline a user actually wants, computed from the series rather than
    asserted: last bucket minus the bucket a third of the way in, so a topic that
    appeared mid-window is not counted as flat.
    """
    out: list[dict[str, Any]] = []
    for entry in payload.get("series", []):
        points = entry.get("points") or []
        if len(points) < 3:
            continue
        pivot = points[len(points) // 3]["value"]
        latest = points[-1]["value"]
        out.append(
            {
                "cluster_id": entry["cluster_id"],
                "label": entry["label"],
                "from": pivot,
                "to": latest,
                "delta": latest - pivot,
            }
        )
    out.sort(key=lambda row: -abs(row["delta"]))
    return out[:top]
