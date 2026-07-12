"""GitHub source plugin (REST API).

Ingests repositories (and, optionally, individual code files) via the public
GitHub REST API. A ``GITHUB_TOKEN`` in the environment lifts the rate limit and
is used automatically. Repositories become ``REPOSITORY`` documents; when
``code=True`` is passed, matching source files become ``CODE`` documents that
power code search.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, Optional

from engine.documents import DocumentKind
from engine.ingest import http
from engine.ingest.base import SourcePlugin, register

_SEARCH_REPOS = "https://api.github.com/search/repositories"
_SEARCH_CODE = "https://api.github.com/search/code"


@register
class GithubSource(SourcePlugin):
    name = "github"
    display_name = "GitHub"
    default_kind = DocumentKind.REPOSITORY
    description = "Open-source engineering repositories and source code."

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token}"
        return headers

    def fetch(
        self,
        *,
        query: Optional[str] = None,
        limit: int = 100,
        code: bool = False,
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        query = query or "topic:embedded stars:>500"
        endpoint = _SEARCH_CODE if code else _SEARCH_REPOS
        per_page = min(100, limit)
        page = 1
        fetched = 0
        while fetched < limit:
            data = http.get_json(
                endpoint,
                params={"q": query, "per_page": per_page, "page": page},
                headers=self._headers(),
                config=self.config,
            )
            items = data.get("items", [])
            if not items:
                break
            for item in items:
                yield {"item": item, "code": code}
                fetched += 1
                if fetched >= limit:
                    break
            if len(items) < per_page:
                break
            page += 1

    def normalize(self, raw: Dict[str, Any]):
        item = raw["item"]
        if raw.get("code"):
            return self._normalize_code(item)
        return self._normalize_repo(item)

    def _normalize_repo(self, item: Dict[str, Any]):
        full_name = item.get("full_name")
        if not full_name:
            return None
        topics = item.get("topics", []) or []
        return self.make_document(
            full_name,
            kind=DocumentKind.REPOSITORY,
            title=full_name,
            abstract=item.get("description") or "",
            body=item.get("description") or "",
            url=item.get("html_url", ""),
            authors=[item.get("owner", {}).get("login", "")],
            published=item.get("created_at"),
            updated=item.get("updated_at"),
            categories=[item.get("language")] if item.get("language") else [],
            tags=topics,
            language=item.get("language"),
            popularity=float(item.get("stargazers_count", 0)),
            has_code=True,
            identifiers={"repo": full_name},
            extra={
                "stars": item.get("stargazers_count", 0),
                "forks": item.get("forks_count", 0),
                "license": (item.get("license") or {}).get("spdx_id"),
            },
        )

    def _normalize_code(self, item: Dict[str, Any]):
        repo = item.get("repository", {}).get("full_name", "")
        path = item.get("path", "")
        native = f"{repo}/{path}"
        return self.make_document(
            native,
            kind=DocumentKind.CODE,
            title=f"{path} — {repo}",
            abstract=f"Source file {path} in {repo}",
            url=item.get("html_url", ""),
            categories=[repo],
            tags=[path.rsplit(".", 1)[-1]] if "." in path else [],
            has_code=True,
            identifiers={"repo": repo, "path": path},
        )
