"""In-process vector index.

`InMemoryStore` has to answer a `search_context` call over ten thousand objects
inside the same latency budget the Postgres backend meets, because it is the store
the local-model wedge dogfoods against (§10 step 1) -- a zero-infrastructure path
that is too slow to actually use is not a path.

Cosine similarity against every stored vector is a dense matrix-vector product. Done
in interpreter loops that is ~250ms at 10k objects and 768 dimensions; done in numpy
it is single-digit milliseconds. This class is that product plus the bookkeeping to
grow the matrix without rebuilding it on every write.

Postgres does the equivalent work with an HNSW index instead, and both paths hand
their similarities to the same `rank_score` blend -- so which memory a model sees
does not depend on which backend is configured.
"""

from __future__ import annotations

import numpy as np

_INITIAL_CAPACITY = 256


class VectorIndex:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._matrix: np.ndarray = np.zeros((_INITIAL_CAPACITY, dim), dtype=np.float32)
        self._row_of: dict[str, int] = {}
        self._id_of: list[str] = []

    def __len__(self) -> int:
        return len(self._id_of)

    def put(self, object_id: str, vector: list[float]) -> None:
        """Insert or overwrite one object's vector. Capacity doubles rather than
        the matrix being rebuilt, so a run of writes stays amortized O(1) each."""
        if len(vector) != self.dim:
            raise ValueError(f"expected {self.dim} dimensions, got {len(vector)}")
        row = self._row_of.get(object_id)
        if row is None:
            if len(self._id_of) == self._matrix.shape[0]:
                grown = np.zeros((self._matrix.shape[0] * 2, self.dim), dtype=np.float32)
                grown[: self._matrix.shape[0]] = self._matrix
                self._matrix = grown
            row = len(self._id_of)
            self._row_of[object_id] = row
            self._id_of.append(object_id)
        self._matrix[row] = np.asarray(vector, dtype=np.float32)

    def put_many(self, pairs: list[tuple[str, list[float]]]) -> None:
        for object_id, vector in pairs:
            self.put(object_id, vector)

    def similarities(self, query_vector: list[float]) -> dict[str, float]:
        """Cosine similarity to every indexed object, by id.

        Everything stored here is already L2-normalized by the embedder, so the
        dot product *is* the cosine and no per-row division is needed.
        """
        count = len(self._id_of)
        if count == 0:
            return {}
        query = np.asarray(query_vector, dtype=np.float32)
        if query.shape != (self.dim,):
            return {}
        scores = self._matrix[:count] @ query
        return dict(zip(self._id_of, scores.tolist(), strict=True))
