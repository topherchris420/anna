"""U.S. Department of Energy (DOE OSTI) source plugin.

Uses the public OSTI records API (``osti.gov/api/v1/records``), which returns
scientific and technical reports funded by the DOE.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from engine.documents import DocumentKind
from engine.ingest import http
from engine.ingest.base import SourcePlugin, register

_API = "https://www.osti.gov/api/v1/records"


@register
class DoeSource(SourcePlugin):
    name = "doe"
    display_name = "DOE OSTI"
    default_kind = DocumentKind.REPORT
    description = "Department of Energy scientific and technical reports."

    def fetch(
        self, *, query: Optional[str] = None, limit: int = 100, **kwargs: Any
    ) -> Iterator[Dict[str, Any]]:
        rows = min(100, limit)
        page = 1
        fetched = 0
        while fetched < limit:
            data = http.get_json(
                _API,
                params={"q": query or "", "rows": rows, "page": page},
                config=self.config,
            )
            records = (
                data if isinstance(data, list) else data.get("records", [])
            )
            if not records:
                break
            for rec in records:
                yield rec
                fetched += 1
                if fetched >= limit:
                    break
            if len(records) < rows:
                break
            page += 1

    def normalize(self, raw: Dict[str, Any]):
        native = str(raw.get("osti_id") or raw.get("id") or "")
        if not native or not raw.get("title"):
            return None

        authors = raw.get("authors") or []
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(";") if a.strip()]

        pdf_url = ""
        url = ""
        for link in raw.get("links", []) or []:
            if not isinstance(link, dict):
                continue
            rel, href = link.get("rel"), link.get("href")
            if rel == "fulltext" and href:
                pdf_url = href
            elif rel == "citation" and href:
                url = href

        identifiers: Dict[str, str] = {"osti_id": native}
        if raw.get("doi"):
            identifiers["doi"] = raw["doi"]
        if raw.get("report_number"):
            identifiers["report_number"] = str(raw["report_number"])

        return self.make_document(
            native,
            title=raw.get("title", "").strip(),
            abstract=(
                raw.get("description") or raw.get("abstract") or ""
            ).strip(),
            url=url or f"https://www.osti.gov/biblio/{native}",
            pdf_url=pdf_url,
            authors=[a for a in authors if a],
            published=raw.get("publication_date"),
            categories=_as_list(raw.get("research_orgs")),
            tags=_as_list(raw.get("subjects")),
            language=(raw.get("language") or "en").lower()[:2] or "en",
            identifiers=identifiers,
        )


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [s.strip() for s in str(value).split(";") if s.strip()]
