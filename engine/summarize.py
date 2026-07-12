"""Citation-first AI summaries and document comparison.

By design, every generated answer is grounded in retrieved documents and cites
them with bracketed markers ``[1]``, ``[2]`` … that map to a citation list. The
default summarizer is *extractive* (no model required): it selects the most
query-relevant sentences from the top hits and attaches their sources. When a
local LLM is configured (Ollama-compatible endpoint) it is used instead, but
the prompt still forces citation-grounded output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from engine.config import EngineConfig, get_config
from engine.documents import Document

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class Citation:
    n: int
    id: str
    title: str
    url: str
    source: str


@dataclass
class Summary:
    query: str
    answer: str
    citations: List[Citation] = field(default_factory=list)
    generator: str = "extractive"  # or "llm"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.answer,
            "generator": self.generator,
            "citations": [
                {"n": c.n, "id": c.id, "title": c.title, "url": c.url, "source": c.source}
                for c in self.citations
            ],
        }


def _tokens(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower())


def _sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_RE.split(text) if len(s.strip()) > 20]


class Summarizer:
    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or get_config()

    # ------------------------------------------------------------------ #
    def summarize(
        self,
        query: str,
        documents: Sequence[Document],
        max_sentences: int = 5,
    ) -> Summary:
        """Produce a citation-first answer from the top documents."""
        documents = list(documents)[:8]
        if not documents:
            return Summary(query=query, answer="No relevant documents were found.")

        citations = [
            Citation(
                n=i + 1,
                id=doc.id,
                title=doc.title,
                url=doc.url or doc.pdf_url,
                source=doc.source,
            )
            for i, doc in enumerate(documents)
        ]

        if self.config.llm_enabled:
            answer = self._llm_answer(query, documents)
            if answer:
                return Summary(query, answer, citations, generator="llm")

        answer = self._extractive_answer(query, documents, max_sentences)
        return Summary(query, answer, citations, generator="extractive")

    # ------------------------------------------------------------------ #
    def _extractive_answer(
        self, query: str, documents: Sequence[Document], max_sentences: int
    ) -> str:
        query_terms = set(_tokens(query))
        scored: List[tuple] = []
        for idx, doc in enumerate(documents):
            text = doc.abstract or doc.body[:1500]
            for sent in _sentences(text):
                sent_terms = _tokens(sent)
                if not sent_terms:
                    continue
                overlap = sum(1 for t in sent_terms if t in query_terms)
                # Normalize by length; small boost for earlier documents.
                score = overlap / (len(sent_terms) ** 0.5) + (1.0 / (idx + 1)) * 0.25
                scored.append((score, idx, sent))

        scored.sort(key=lambda x: x[0], reverse=True)
        chosen: List[tuple] = []
        seen = set()
        for score, idx, sent in scored:
            key = sent[:80].lower()
            if key in seen or score <= 0:
                continue
            seen.add(key)
            chosen.append((idx, sent))
            if len(chosen) >= max_sentences:
                break

        if not chosen:
            # Fall back to the leading sentences of the top document.
            lead = _sentences(documents[0].abstract or documents[0].body)[:2]
            return " ".join(f"{s} [1]" for s in lead) or documents[0].title

        # Preserve document order for readability, attach citation markers.
        chosen.sort(key=lambda x: x[0])
        return " ".join(f"{sent} [{idx + 1}]" for idx, sent in chosen)

    # ------------------------------------------------------------------ #
    def _llm_answer(self, query: str, documents: Sequence[Document]) -> Optional[str]:
        """Query a local Ollama-compatible LLM. Returns None on any failure."""
        context_blocks = []
        for i, doc in enumerate(documents):
            snippet = (doc.abstract or doc.body[:800]).strip()
            context_blocks.append(f"[{i + 1}] {doc.title}\n{snippet}")
        context = "\n\n".join(context_blocks)
        prompt = (
            "You are an engineering research assistant. Answer the question using "
            "ONLY the numbered sources below. Cite every claim with bracketed "
            "markers like [1]. If the sources do not answer the question, say so.\n\n"
            f"Question: {query}\n\nSources:\n{context}\n\nAnswer:"
        )
        try:
            import httpx  # lazy import

            resp = httpx.post(
                f"{self.config.llm_base_url}/api/generate",
                json={"model": self.config.llm_model, "prompt": prompt, "stream": False},
                timeout=self.config.request_timeout,
            )
            resp.raise_for_status()
            return (resp.json().get("response") or "").strip() or None
        except Exception:
            return None


def compare_documents(doc_a: Document, doc_b: Document) -> Dict[str, Any]:
    """Structured side-by-side comparison of two documents."""

    def _facts(doc: Document) -> Dict[str, Any]:
        return {
            "id": doc.id,
            "title": doc.title,
            "source": doc.source,
            "kind": str(doc.kind),
            "authors": doc.authors,
            "published": doc.published,
            "version": doc.version,
            "categories": doc.categories,
            "url": doc.url or doc.pdf_url,
            "has_code": doc.has_code,
            "has_equations": doc.has_equations,
        }

    terms_a = set(_tokens(f"{doc_a.title} {doc_a.abstract}"))
    terms_b = set(_tokens(f"{doc_b.title} {doc_b.abstract}"))
    shared = sorted(terms_a & terms_b)
    jaccard = len(terms_a & terms_b) / len(terms_a | terms_b) if (terms_a | terms_b) else 0.0

    return {
        "a": _facts(doc_a),
        "b": _facts(doc_b),
        "shared_categories": sorted(set(doc_a.categories) & set(doc_b.categories)),
        "only_a_categories": sorted(set(doc_a.categories) - set(doc_b.categories)),
        "only_b_categories": sorted(set(doc_b.categories) - set(doc_a.categories)),
        "shared_terms": shared[:40],
        "text_similarity": round(jaccard, 4),
    }
