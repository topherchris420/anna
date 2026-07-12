"""arXiv source plugin (Atom API).

Uses the public arXiv API (``export.arxiv.org/api/query``), which returns an
Atom feed parsed here with the standard library — no extra dependency. Records
carry rich metadata (authors, categories, DOI, PDF link) and frequently contain
LaTeX in the abstract, which the equation detector picks up automatically.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterator, Optional

from engine.documents import DocumentKind
from engine.ingest import http
from engine.ingest.base import SourcePlugin, register

_API = "http://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_PAGE = 100


@register
class ArxivSource(SourcePlugin):
    name = "arxiv"
    display_name = "arXiv"
    default_kind = DocumentKind.PAPER
    description = "Open-access preprints in physics, CS, EE, math and more."

    def fetch(
        self, *, query: Optional[str] = None, limit: int = 100, **kwargs: Any
    ) -> Iterator[Dict[str, Any]]:
        search_query = query or "cat:eess.SY OR cat:cs.SY"
        start = 0
        fetched = 0
        while fetched < limit:
            page = min(_PAGE, limit - fetched)
            xml = http.get_text(
                _API,
                params={
                    "search_query": search_query,
                    "start": start,
                    "max_results": page,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
                config=self.config,
            )
            try:
                root = ET.fromstring(xml)
            except ET.ParseError:
                break
            entries = root.findall(f"{_ATOM}entry")
            if not entries:
                break
            for entry in entries:
                yield {"entry": entry}
                fetched += 1
            start += page
            if len(entries) < page:
                break
            time.sleep(3)  # arXiv asks for ~1 request / 3s

    def normalize(self, raw: Dict[str, Any]):
        entry = raw["entry"]
        arxiv_url = _text(entry, f"{_ATOM}id")
        if not arxiv_url:
            return None
        arxiv_id = arxiv_url.rsplit("/abs/", 1)[-1]

        authors = [
            _text(a, f"{_ATOM}name")
            for a in entry.findall(f"{_ATOM}author")
            if _text(a, f"{_ATOM}name")
        ]
        categories = [
            c.get("term")
            for c in entry.findall(f"{_ATOM}category")
            if c.get("term")
        ]
        pdf_url = ""
        for link in entry.findall(f"{_ATOM}link"):
            if (
                link.get("title") == "pdf"
                or link.get("type") == "application/pdf"
            ):
                pdf_url = link.get("href", "")

        identifiers = {"arxiv_id": arxiv_id}
        doi = _text(entry, f"{_ARXIV}doi")
        if doi:
            identifiers["doi"] = doi

        return self.make_document(
            arxiv_id,
            title=" ".join((_text(entry, f"{_ATOM}title") or "").split()),
            abstract=(_text(entry, f"{_ATOM}summary") or "").strip(),
            url=arxiv_url,
            pdf_url=pdf_url,
            authors=authors,
            published=_text(entry, f"{_ATOM}published"),
            updated=_text(entry, f"{_ATOM}updated"),
            categories=categories,
            identifiers=identifiers,
            language="en",
        )


def _text(el, tag: str) -> str:
    child = el.find(tag)
    return (
        (child.text or "").strip() if child is not None and child.text else ""
    )
