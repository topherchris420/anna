"""Explain executed retrieval paths without changing the ranking contract.

Everything here is a pure function over ranked id lists, so provenance and
the shared relevance scale are unit-tested without a backend.
"""

from typing import Any, Dict, List, Optional


def explain_rankings(
    rankings: Dict[str, List[str]], k: int
) -> Dict[str, Dict[str, Any]]:
    """Build all per-hit rank contributions in one pass over the candidates."""
    result: Dict[str, Dict[str, Any]] = {}
    hybrid = len(rankings) > 1
    for name, ids in rankings.items():
        for rank, doc_id in enumerate(ids, 1):
            explanation = result.setdefault(
                doc_id,
                {
                    "method": "rrf" if hybrid else "reciprocal-rank",
                    "ranks": {},
                    "contributions": {},
                },
            )
            explanation["ranks"][name] = rank
            explanation["contributions"][name] = 1.0 / (
                (k if hybrid else 0) + rank
            )
    return result


def normalize_relevance(score: float, ceiling: float) -> float:
    """Map a fused score onto 0–1 given the result set's score ceiling.

    The ceiling is the highest score the retriever mix that ran can assign
    (see :func:`engine.search.fused_score_ceiling`), so ``1.0`` means
    "ranked first by every retriever that executed". The human search API
    and the agent endpoint share this one definition. It is a share of the
    best possible *ranking* score, not a probability that the document
    answers the query.
    """
    if ceiling <= 0:
        return 0.0
    return round(min(score / ceiling, 1.0), 4)


def retrieval_report(
    mode: str,
    rankings: Dict[str, List[str]],
    *,
    backend: str,
    candidate_count: int,
    unavailable: Optional[List[str]] = None,
    embedding: Optional[str] = None,
    browse: bool = False,
    score_ceiling: float = 0.0,
) -> Dict[str, Any]:
    return {
        "backend": backend,
        "requested_mode": mode,
        "executed": list(rankings),
        "unavailable": unavailable or [],
        "degraded": bool(unavailable),
        "embedding": embedding,
        "fusion": "rrf" if len(rankings) > 1 else "reciprocal-rank",
        "candidate_count": candidate_count,
        "total_scope": "corpus" if browse else "retrieved-candidates",
        "score_ceiling": score_ceiling,
        "score_meaning": "ranking-score-not-probability",
    }
