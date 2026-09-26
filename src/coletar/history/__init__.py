"""Context as an observability surface (SCOPE §6).

The graph already records everything a metrics backend records. Every revision
event carries a full `after` snapshot, so the Event/Revision Log is not merely an
audit trail -- it is a materialised time series that nobody was reading as one.
This package reads it as one.

Three ideas, in dependency order:

  * `query` is a **typed grammar**. A question about history compiles to a
    `HistoryQuery` before anything executes it. That is what keeps constraint #4
    true: the Context Inspector can explain any answer, because the answer came
    from a query a user can read, edit and re-run.
  * `rollup` executes that grammar against the log by replaying it, so a metric
    about object *state* ("how many active facts did I have in March") is answered
    from the same pass as a metric about object *events* ("how many did I write").
  * `nl` is the only part a model touches, and it does not touch the graph: it
    emits a `HistoryQuery` and stops. The model never sees a memory, and the
    numbers in an answer are never a model's arithmetic.
"""

from coletar.history.clusters import ClusterSeries, IdeaCluster, cluster_mass
from coletar.history.query import (
    Bucket,
    GroupBy,
    HistoryQuery,
    Metric,
    ObjectFilter,
    QueryResult,
    Series,
    SeriesPoint,
)
from coletar.history.rollup import object_lifetime, reach_report, run_query

__all__ = [
    "Bucket",
    "ClusterSeries",
    "GroupBy",
    "HistoryQuery",
    "IdeaCluster",
    "Metric",
    "ObjectFilter",
    "QueryResult",
    "Series",
    "SeriesPoint",
    "cluster_mass",
    "object_lifetime",
    "reach_report",
    "run_query",
]
