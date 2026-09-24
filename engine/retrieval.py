"""Explain executed retrieval paths without changing the ranking contract."""

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


def retrieval_report(
    mode: str,
    rankings: Dict[str, List[str]],
    *,
    backend: str,
    candidate_count: int,
    unavailable: Optional[List[str]] = None,
    embedding: Optional[str] = None,
    browse: bool = False,
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
        "score_meaning": "ranking-score-not-probability",
    }
