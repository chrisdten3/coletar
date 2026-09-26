#!/usr/bin/env python3
"""Show the same queries reranked three ways, side by side.

The point is a decision, not a demo. §5.1 names a bounded local cross-encoder as
the reranking strategy, and a real one means `sentence-transformers` and therefore
torch — about two gigabytes on a machine the test suite has already OOM-killed.
That is not a dependency to take on a hunch, so this makes the hunch checkable:
run it, read the three columns, and see whether joint (query, document) scoring
actually moves the answer a bi-encoder buried.

The case that motivated it, measured on the real corpus: asked "what are my coding
preferences", the bi-encoder ranked "Prefers fixed-point integers over floating
point" *eleventh* at 0.278, below "I got into coding by building foodme" at 0.459.
Query and document are embedded independently, so nothing ever compares them to
each other.

    python scripts/compare_rerankers.py \
        --dsn postgresql://coletar@localhost:5434/coletar --tenant tenant_v1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time

#: Broad enough to need judgement, specific enough to have a right answer. A set
#: where the bi-encoder already wins would measure nothing.
QUERIES = [
    "what are my coding preferences",
    "where do I work",
    "what do I want to do after graduation",
    "what am I studying",
    "what projects have I built",
]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--model", default="llama3.1", help="local reranking model")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--query", action="append", help="override the built-in set")
    args = parser.parse_args()

    os.environ.update(
        COLETAR_STORE_BACKEND="postgres",
        COLETAR_DATABASE_URL=args.dsn,
        # The corpus was embedded with this; a mismatch makes the whole comparison
        # meaningless and is the bug that started this work.
        COLETAR_EMBEDDING_BACKEND="ollama",
        COLETAR_EMBEDDING_MODEL="nomic-embed-text",
    )
    from coletar.config import get_settings

    get_settings.cache_clear()

    from coletar.retrieval.context import retrieve
    from coletar.retrieval.strategy import (
        LocalModelReranker,
        MaximalMarginalRelevance,
        PublishedOrder,
    )
    from coletar.schema.tenancy import tenant_id
    from coletar.store import build_store

    store = build_store()
    owner = tenant_id(args.tenant)
    strategies = [
        ("published", PublishedOrder()),
        ("mmr", MaximalMarginalRelevance(0.7)),
        ("local-model", LocalModelReranker(model=args.model)),
    ]

    for query in args.query or QUERIES:
        print(f"\n{'=' * 78}\n{query}\n{'=' * 78}")
        for label, strategy in strategies:
            started = time.perf_counter()
            result = await retrieve(
                store, owner, query, top_k=args.top_k, reranker=strategy, trace=False
            )
            elapsed = (time.perf_counter() - started) * 1000
            print(f"\n  --- {label}  ({elapsed:.0f} ms)")
            if not result.objects:
                print("      (nothing above the relevance floor)")
            for i, obj in enumerate(result.objects, 1):
                print(f"      {i}. {obj.content[:88]}")
    print(
        "\nRead the columns, not the timings: a local model is slower than a real "
        "cross-encoder\nwould be, and the question here is whether the ordering is "
        "better, not how fast it was."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
