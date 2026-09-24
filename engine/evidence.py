"""Deterministic source excerpts; lexical relevance is not factual entailment.

Offsets are Python Unicode code-point offsets into the original document field.
The selected text is never rewritten, so callers can verify each excerpt by
slicing ``getattr(document, field)[start:end]``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from engine.documents import Document

WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
STOP_WORDS = frozenset(
    "a an and are as at be been by can could do does for from has have how "
    "i in is it its of on or should that the their these this to was were "
    "what when where which who why will with would you your".split()
)
REFUSAL = "The retrieved documents do not contain query-matching evidence. Try a more specific query or different sources."


def content_terms(text: str) -> set:
    return set(WORD_RE.findall(text.lower())) - STOP_WORDS


def select_evidence(
    query: str, documents: Sequence[Document], limit: int = 5
) -> List[Dict[str, Any]]:
    """Pick relevant, distinct excerpts, with at most two per source.

    Scan both abstract and body (up to 24,000 characters each). Do not let a
    leading-document bonus turn zero lexical overlap into positive evidence.
    This is a bounded excerpt selector, not a semantic entailment classifier.
    """
    terms = content_terms(query)
    if not terms:
        return []
    candidates = []
    for index, doc in enumerate(documents):
        for field in ("abstract", "body"):
            text = getattr(doc, field) or ""
            for match in re.finditer(
                r"\S[^\n]*?(?:[.!?](?=\s|$)|$|(?=\n))", text[:24000]
            ):
                quote = match.group().rstrip()
                if not 20 < len(quote) <= 1000:
                    continue
                matched = sorted(terms & content_terms(quote))
                if not matched:
                    continue
                score = (
                    len(matched) / max(1, len(WORD_RE.findall(quote))) ** 0.5
                )
                candidates.append(
                    (
                        score,
                        index,
                        {
                            "document_id": doc.id,
                            "field": field,
                            "offset_unit": "unicode-code-points",
                            "start": match.start(),
                            "end": match.start() + len(quote),
                            "quote": quote,
                            "matched_terms": matched,
                        },
                    )
                )
    candidates.sort(
        key=lambda row: (-row[0], row[1], row[2]["field"], row[2]["start"])
    )
    selected = []
    seen = set()
    counts: Dict[str, int] = {}
    # First include the best excerpt per source, then fill remaining slots.
    for per_source in (1, 2):
        for _, _, excerpt in candidates:
            key = " ".join(excerpt["quote"].lower().split())
            doc_id = excerpt["document_id"]
            if key in seen or counts.get(doc_id, 0) >= per_source:
                continue
            selected.append(excerpt)
            seen.add(key)
            counts[doc_id] = counts.get(doc_id, 0) + 1
            if len(selected) >= limit:
                return selected
    return selected


def citation_references_valid(answer: str, count: int) -> bool:
    """Check reference syntax/coverage only, never claim factual verification.

    Every prose sentence must terminate with one or more known citations.
    Lists are accepted; uncited headings, unknown markers and empty output
    deliberately fail closed to the deterministic source excerpts.
    """
    if not answer or not answer.strip():
        return False
    markers = re.findall(r"\[(\d+)\]", answer)
    if not markers or any(not 1 <= int(n) <= count for n in markers):
        return False
    # Strip valid cited sentences. Any remaining prose is unreferenced. The
    # conservative sentence boundary may reject abbreviations; fallback is safe.
    remaining = re.sub(r"[^.!?\n]+[.!?]?\s*(?:\[\d+\]\s*)+[.!?]?", "", answer)
    return not remaining.strip()
