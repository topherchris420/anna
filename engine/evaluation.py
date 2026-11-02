"""Measure ranking quality against judged queries.

A ranking change is only an improvement if it puts more of what people were
looking for nearer the top, so this module measures exactly that. A
**judgment set** is a list of topics — a query, the information need behind
it, and graded relevance for the documents a pool of independent retrievers
surfaced (2 = highly relevant, 1 = partially relevant, 0 = judged not
relevant) — over a fixed corpus. The repository ships one, with its corpus
and method, in ``test/relevance/``; ``flask engine evaluate`` runs it (or
your own) against the configured backend.

Metrics, per topic and averaged:

- **nDCG@k** — graded gain ``2^grade - 1`` discounted by ``log2(rank + 1)``,
  divided by the best possible ordering of the judged documents.
- **MRR** — reciprocal rank of the first relevant (grade ≥ 1) document.
- **P@5** — share of the top five that are relevant.
- **Recall@k** — share of the judged-relevant documents in the top ``k``.

Topics with no relevant document in the corpus ("no-answer" topics) have no
ideal ranking, so they are scored by **noise@k** instead: how many results
the engine showed for a question it cannot answer. Lower is better; zero is
honest.

Documents the judgments never saw are counted as **unjudged** and scored as
not relevant, the TREC convention. A ranker that surfaces many unjudged
documents needs its pool re-judged before its scores mean much — the report
says so instead of hiding it.

Everything here except :func:`load_corpus` is pure and runs without a
backend.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

FORMAT = "anna-relevance-judgments/v1"

# The repository's own judgment set (see test/relevance/README.md).
DEFAULT_JUDGMENTS = (
    Path(__file__).resolve().parents[1] / "test" / "relevance" / "judgments.json"
)

SearchFn = Callable[[str, str, int], Sequence[str]]


@dataclass
class Topic:
    id: str
    query: str
    grades: Dict[str, int]
    type: str = "keywords"
    need: str = ""

    @property
    def answerable(self) -> bool:
        return any(g > 0 for g in self.grades.values())


@dataclass
class JudgmentSet:
    topics: List[Topic]
    corpus: Optional[Path] = None
    about: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def load_judgments(path: Optional[Path] = None) -> JudgmentSet:
    """Read a judgment set; the corpus path is resolved next to it."""
    path = Path(path or DEFAULT_JUDGMENTS)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("format") != FORMAT:
        raise ValueError(f"{path} is not an {FORMAT} file")
    topics = [
        Topic(
            id=t["id"],
            query=t["query"],
            grades={k: int(v) for k, v in (t.get("grades") or {}).items()},
            type=t.get("type", "keywords"),
            need=t.get("need", ""),
        )
        for t in data["topics"]
    ]
    corpus = data.get("corpus")
    return JudgmentSet(
        topics=topics,
        corpus=(path.parent / corpus) if corpus else None,
        about=data.get("about", ""),
    )


# --------------------------------------------------------------------------- #
# Metrics (pure)
# --------------------------------------------------------------------------- #
def dcg(gains: Iterable[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg(ranked: Sequence[str], grades: Dict[str, int], k: int = 10) -> float:
    """Normalized DCG at ``k`` with ``2^grade - 1`` gains."""
    ideal = dcg(
        sorted(((2 ** g) - 1 for g in grades.values() if g > 0), reverse=True)[
            :k
        ]
    )
    if ideal == 0:
        return 0.0
    actual = dcg((2 ** grades.get(d, 0)) - 1 for d in ranked[:k])
    return actual / ideal


def reciprocal_rank(ranked: Sequence[str], grades: Dict[str, int]) -> float:
    for i, doc_id in enumerate(ranked):
        if grades.get(doc_id, 0) > 0:
            return 1.0 / (i + 1)
    return 0.0


def precision(ranked: Sequence[str], grades: Dict[str, int], k: int) -> float:
    return sum(1 for d in ranked[:k] if grades.get(d, 0) > 0) / float(k)


def recall(ranked: Sequence[str], grades: Dict[str, int], k: int) -> float:
    relevant = {d for d, g in grades.items() if g > 0}
    if not relevant:
        return 0.0
    return len(relevant.intersection(ranked[:k])) / float(len(relevant))


# --------------------------------------------------------------------------- #
# Running a judgment set
# --------------------------------------------------------------------------- #
def evaluate(
    search: SearchFn,
    judgments: JudgmentSet,
    mode: str = "hybrid",
    k: int = 10,
) -> Dict[str, Any]:
    """Run every topic through ``search`` and score the rankings.

    ``search(query, mode, n)`` returns ranked document ids. The report holds
    per-topic rows, means over answerable topics (overall and per query
    type), and noise over no-answer topics.
    """
    rows = []
    for topic in judgments.topics:
        ranked = list(search(topic.query, mode, max(k, 20)))
        top = ranked[:k]
        row: Dict[str, Any] = {
            "id": topic.id,
            "query": topic.query,
            "type": topic.type,
            "answerable": topic.answerable,
            "returned": len(ranked),
            "unjudged": sum(1 for d in top if d not in topic.grades),
            "top": top,
        }
        if topic.answerable:
            row.update(
                ndcg=ndcg(ranked, topic.grades, k),
                mrr=reciprocal_rank(ranked, topic.grades),
                p5=precision(ranked, topic.grades, 5),
                recall=recall(ranked, topic.grades, k),
            )
        else:
            row["noise"] = len(top)
        rows.append(row)
    return {
        "mode": mode,
        "k": k,
        "topics": rows,
        "summary": summarize(rows),
    }


def _mean(values: List[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]

    def means(subset):
        return {
            "topics": len(subset),
            "ndcg": _mean([r["ndcg"] for r in subset]),
            "mrr": _mean([r["mrr"] for r in subset]),
            "p5": _mean([r["p5"] for r in subset]),
            "recall": _mean([r["recall"] for r in subset]),
        }

    by_type = {}
    for kind in sorted({r["type"] for r in answerable}):
        by_type[kind] = means([r for r in answerable if r["type"] == kind])
    return {
        **means(answerable),
        "by_type": by_type,
        "no_answer_topics": len(unanswerable),
        "noise": _mean([r["noise"] for r in unanswerable]),
        "unjudged": sum(r["unjudged"] for r in rows),
        "failed_topics": [
            r["id"] for r in answerable if r["mrr"] == 0.0
        ],
    }


def format_report(report: Dict[str, Any]) -> str:
    """A fixed-width table: one line per topic, then the summary."""
    k = report["k"]
    lines = [
        f"mode={report['mode']}  k={k}",
        f"{'topic':<20} {'type':<9} {'nDCG':>6} {'MRR':>6} {'P@5':>5} "
        f"{'R@' + str(k):>6} {'ret':>4} {'unj':>4}",
    ]
    for r in report["topics"]:
        if r["answerable"]:
            metrics = (
                f"{r['ndcg']:>6.3f} {r['mrr']:>6.3f} {r['p5']:>5.2f} "
                f"{r['recall']:>6.2f}"
            )
        else:
            metrics = f"{'noise@' + str(k) + '=' + str(r['noise']):>26}"
        lines.append(
            f"{r['id']:<20} {r['type']:<9} {metrics} {r['returned']:>4} "
            f"{r['unjudged']:>4}"
        )
    s = report["summary"]
    lines.append(
        f"MEAN over {s['topics']} answerable topics: nDCG@{k} {s['ndcg']:.3f}"
        f"  MRR {s['mrr']:.3f}  P@5 {s['p5']:.3f}  R@{k} {s['recall']:.3f}"
    )
    for kind, m in s["by_type"].items():
        lines.append(
            f"  {kind:<9} ({m['topics']:>2}): nDCG {m['ndcg']:.3f}  "
            f"MRR {m['mrr']:.3f}  P@5 {m['p5']:.3f}"
        )
    lines.append(
        f"no-answer topics: {s['no_answer_topics']}, mean noise@{k} "
        f"{s['noise']:.2f}  |  unjudged in top {k}: {s['unjudged']}"
    )
    if s["failed_topics"]:
        lines.append("no relevant result: " + ", ".join(s["failed_topics"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The fixture corpus
# --------------------------------------------------------------------------- #
def read_corpus(path: Path) -> List[Dict[str, Any]]:
    """The corpus fixture: gzipped JSON Lines of ``Document`` fields."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_corpus(path: Path, config=None, embedder=None) -> int:
    """Index the corpus fixture into the configured backend (recreated).

    Embeds with the configured embedder (or ``embedder``), exactly as
    ingestion does, so the vectors the evaluation searches are the ones a
    deployment would hold. Returns the number of documents indexed.
    """
    from engine import backend
    from engine.documents import Document
    from engine.ingest import IngestionPipeline

    config = config or backend.get_config()
    backend.create_index(config, recreate=True)
    docs = [Document(**fields) for fields in read_corpus(path)]
    pipeline = IngestionPipeline(config=config, embedder=embedder)
    stats = pipeline.index_documents(docs, source="relevance-fixture")
    if config.backend != "postgres":
        from engine.index import get_client

        get_client(config).indices.refresh(index=config.index_name)
    return stats.indexed
