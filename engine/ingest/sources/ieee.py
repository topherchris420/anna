"""IEEE Open Access source plugin (IEEE Xplore Metadata API).

The IEEE Xplore API requires a free API key. Set ``IEEE_API_KEY`` in the
environment to enable it; the plugin filters to open-access articles. Without a
key, the plugin registers but yields nothing (and logs a hint), so the rest of
the platform is unaffected.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterator, List, Optional

from engine.documents import DocumentKind
from engine.ingest import http
from engine.ingest.base import SourcePlugin, register

_API = "https://ieeexploreapi.ieee.org/api/v1/search/articles"


@register
class IeeeSource(SourcePlugin):
    name = "ieee"
    display_name = "IEEE Open Access"
    default_kind = DocumentKind.PAPER
    description = "IEEE open-access journal and conference articles."

    def fetch(
        self, *, query: Optional[str] = None, limit: int = 100, **kwargs: Any
    ) -> Iterator[Dict[str, Any]]:
        api_key = os.getenv("IEEE_API_KEY")
        if not api_key:
            print(
                "[ieee] IEEE_API_KEY is not set; skipping. "
                "Get a free key at https://developer.ieee.org/ to enable IEEE ingestion."
            )
            return
        per_page = min(200, limit)
        start = 1
        fetched = 0
        while fetched < limit:
            data = http.get_json(
                _API,
                params={
                    "apikey": api_key,
                    "querytext": query or "engineering",
                    "open_access": "True",
                    "max_records": per_page,
                    "start_record": start,
                    "sort_order": "desc",
                    "sort_field": "article_number",
                },
                config=self.config,
            )
            articles = (
                data.get("articles", []) if isinstance(data, dict) else []
            )
            if not articles:
                break
            for art in articles:
                yield art
                fetched += 1
                if fetched >= limit:
                    break
            if len(articles) < per_page:
                break
            start += per_page

    def normalize(self, raw: Dict[str, Any]):
        native = str(raw.get("article_number") or "")
        if not native or not raw.get("title"):
            return None

        authors: List[str] = []
        author_block = raw.get("authors") or {}
        for a in (
            author_block.get("authors", [])
            if isinstance(author_block, dict)
            else []
        ):
            if a.get("full_name"):
                authors.append(a["full_name"])

        identifiers: Dict[str, str] = {"ieee_article": native}
        if raw.get("doi"):
            identifiers["doi"] = raw["doi"]

        return self.make_document(
            native,
            title=raw.get("title", "").strip(),
            abstract=(raw.get("abstract") or "").strip(),
            url=raw.get("html_url") or raw.get("abstract_url") or "",
            pdf_url=raw.get("pdf_url") or "",
            authors=authors,
            published=str(
                raw.get("publication_date") or raw.get("publication_year") or ""
            ),
            categories=[raw.get("content_type")]
            if raw.get("content_type")
            else [],
            tags=_index_terms(raw.get("index_terms")),
            language="en",
            identifiers=identifiers,
        )


def _index_terms(terms: Any) -> List[str]:
    out: List[str] = []
    if isinstance(terms, dict):
        for group in terms.values():
            if isinstance(group, dict):
                out.extend(group.get("terms", []) or [])
    return out[:30]
