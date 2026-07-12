"""NASA Technical Reports Server (NTRS) source plugin.

Uses the public NTRS citations API
(``ntrs.nasa.gov/api/citations/search``). Field names are read defensively
because NTRS occasionally reshapes its payloads.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, Optional

from engine.documents import DocumentKind
from engine.ingest import http
from engine.ingest.base import SourcePlugin, register

_API = "https://ntrs.nasa.gov/api/citations/search"
_BASE = "https://ntrs.nasa.gov"


@register
class NasaSource(SourcePlugin):
    name = "nasa"
    display_name = "NASA Technical Reports (NTRS)"
    default_kind = DocumentKind.REPORT
    description = "NASA technical reports, papers, and mission documentation."

    def fetch(
        self, *, query: Optional[str] = None, limit: int = 100, **kwargs: Any
    ) -> Iterator[Dict[str, Any]]:
        page_size = min(100, limit)
        offset = 0
        fetched = 0
        while fetched < limit:
            data = http.get_json(
                _API,
                params={
                    "q": query or "",
                    "page.size": page_size,
                    "page.from": offset,
                },
                config=self.config,
            )
            results = data.get("results", []) if isinstance(data, dict) else []
            if not results:
                break
            for item in results:
                yield item
                fetched += 1
                if fetched >= limit:
                    break
            if len(results) < page_size:
                break
            offset += page_size

    def normalize(self, raw: Dict[str, Any]):
        native = str(raw.get("id") or raw.get("_id") or "")
        if not native or not raw.get("title"):
            return None

        authors = []
        for aff in raw.get("authorAffiliations", []) or []:
            meta = aff.get("meta", {}) if isinstance(aff, dict) else {}
            name = (meta.get("author") or {}).get("name")
            if name:
                authors.append(name)

        pdf_url = ""
        for dl in raw.get("downloads", []) or []:
            links = dl.get("links", {}) if isinstance(dl, dict) else {}
            rel = links.get("pdf") or links.get("original") or links.get("fulltext")
            if rel:
                pdf_url = rel if rel.startswith("http") else _BASE + rel
                break

        return self.make_document(
            native,
            title=raw.get("title", "").strip(),
            abstract=(raw.get("abstract") or "").strip(),
            url=f"{_BASE}/citations/{native}",
            pdf_url=pdf_url,
            authors=authors,
            published=raw.get("distributionDate") or raw.get("created"),
            categories=[c for c in (raw.get("subjectCategories") or []) if c],
            tags=[k for k in (raw.get("keywords") or []) if k],
            language="en",
            identifiers={"ntrs_id": native},
        )
