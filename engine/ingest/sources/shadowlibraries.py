"""Shadow Libraries source plugin.

Integrates the `ShadowLibraries <https://shadowlibraries.github.io/>`_ directory —
a curated catalog of shadow (pirate) libraries organized by access method — into
Anna's hybrid search. Each catalog entry (Anna's Archive, LibGen, Sci-Hub,
Internet Archive, IRC channels, Telegram bots, reading-online sites, ...) becomes
a first-class :class:`~engine.documents.Document` of kind ``library``, so the
directory is searchable, faceted, and comparable alongside every other source.

Design notes
------------
* **Offline-first.** ``fetch`` reads a bundled, curated catalog
  (``data/shadowlibraries.json``) and needs no network — consistent with Anna's
  air-gapped promise and the ``flask engine demo`` flow.
* **Query-aware.** A free-text query filters the catalog by name, description,
  category, access method, tags and formats.
* **Optional live refresh.** ``fetch(live=True)`` additionally crawls the public
  ShadowLibraries site to discover entries added upstream since the bundle was
  captured, merging them onto the curated catalog (deduplicated by URL). Any
  network failure degrades silently to the bundled catalog.

Anna indexes these as browsable *access points*; it hosts none of the underlying
content.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urljoin, urlparse

from engine.documents import Document, DocumentKind
from engine.ingest import http
from engine.ingest.base import SourcePlugin, register
from engine.ingest.crawler import html_to_text

_DATA_FILE = Path(__file__).resolve().parent / "data" / "shadowlibraries.json"
_SITE = "https://shadowlibraries.github.io/"
_SITE_HOST = "shadowlibraries.github.io"

# Section index page -> (human category, access method) for live discovery.
_SECTIONS: Dict[str, tuple] = {
    "DirectDownloads": ("Direct Downloads", "Direct Download"),
    "Torrents": ("Torrents", "Torrent"),
    "IRCChannels": ("IRC Channels", "IRC"),
    "TelegramBots": ("Telegram Bots", "Telegram Bot"),
    "ReadingOnline": ("Reading Online", "Read Online"),
}

_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def load_catalog() -> Dict[str, Any]:
    """Load the bundled ShadowLibraries catalog (offline)."""
    with _DATA_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


@register
class ShadowLibrariesSource(SourcePlugin):
    name = "shadowlibraries"
    display_name = "Shadow Libraries"
    default_kind = DocumentKind.LIBRARY
    description = (
        "Curated directory of shadow libraries — Anna's Archive, LibGen, "
        "Sci-Hub, Internet Archive, IRC/Telegram and more — by access method."
    )
    supports_query = True

    # ------------------------------------------------------------------ #
    def fetch(
        self,
        *,
        query: Optional[str] = None,
        limit: int = 100,
        live: bool = False,
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = list(load_catalog().get("entries", []))
        if live:
            entries = self._merge_live(entries)
        needle = (query or "").strip().lower()
        produced = 0
        for entry in entries:
            if produced >= limit:
                break
            if needle and not self._matches(entry, needle):
                continue
            yield entry
            produced += 1

    def normalize(self, raw: Dict[str, Any]) -> Optional[Document]:
        name = (raw.get("name") or "").strip()
        if not name:
            return None
        languages = list(raw.get("languages") or [])
        access = (raw.get("access_method") or "").strip()
        category = (raw.get("category") or "Shadow Libraries").strip()

        tags = list(raw.get("tags") or [])
        for extra_tag in ("shadow-library", access):
            slug = extra_tag.strip().lower().replace(" ", "-")
            if slug and slug not in tags:
                tags.append(slug)

        return self.make_document(
            raw.get("id") or name,
            title=name,
            abstract=(raw.get("description") or "").strip(),
            url=(raw.get("url") or "").strip(),
            categories=[category],
            tags=tags,
            language=languages[0] if languages else None,
            extra={
                "access_method": access,
                "formats": list(raw.get("formats") or []),
                "languages": languages,
                "directory": _SITE_HOST,
            },
        )

    # ------------------------------------------------------------------ #
    # Query filtering
    # ------------------------------------------------------------------ #
    @staticmethod
    def _matches(entry: Dict[str, Any], needle: str) -> bool:
        parts = [
            str(entry.get(k, ""))
            for k in ("name", "description", "category", "access_method")
        ]
        parts += list(entry.get("tags") or [])
        parts += list(entry.get("formats") or [])
        return needle in " ".join(parts).lower()

    # ------------------------------------------------------------------ #
    # Optional live refresh (best-effort; never required)
    # ------------------------------------------------------------------ #
    def _merge_live(
        self, entries: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        try:
            discovered = self._crawl_site()
        except Exception:
            return entries
        seen = {self._key(e) for e in entries}
        merged = list(entries)
        for entry in discovered:
            key = self._key(entry)
            if key and key not in seen:
                seen.add(key)
                merged.append(entry)
        return merged

    @staticmethod
    def _key(entry: Dict[str, Any]) -> str:
        url = (entry.get("url") or "").strip().lower().rstrip("/")
        if url:
            return url
        return (entry.get("id") or entry.get("name") or "").strip().lower()

    def _crawl_site(self) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        for section, (category, access) in _SECTIONS.items():
            index_url = urljoin(_SITE, f"{section}/")
            try:
                index_html = http.get_text(index_url, config=self.config)
            except Exception:
                continue
            for entry_url in self._entry_links(index_html, index_url, section):
                try:
                    page = http.get_text(entry_url, config=self.config)
                except Exception:
                    continue
                title, text = html_to_text(page)
                if not title:
                    continue
                found.append(
                    {
                        "id": urlparse(entry_url)
                        .path.strip("/")
                        .replace("/", "-"),
                        "name": title,
                        "access_method": access,
                        "category": category,
                        "url": self._external_link(page) or entry_url,
                        "description": text[:400].strip(),
                        "formats": [],
                        "languages": [],
                        "tags": ["live"],
                    }
                )
        return found

    def _entry_links(self, html: str, base_url: str, section: str) -> List[str]:
        prefix = f"/{section}/"
        out: List[str] = []
        seen = set()
        for href in _HREF_RE.findall(html):
            absolute = urljoin(base_url, href.split("#")[0])
            path = urlparse(absolute).path
            # Keep only deeper entry pages within this section, not the index.
            if (
                urlparse(absolute).netloc == _SITE_HOST
                and path.startswith(prefix)
                and path.rstrip("/") != prefix.rstrip("/")
                and absolute not in seen
            ):
                seen.add(absolute)
                out.append(absolute)
        return out

    @staticmethod
    def _external_link(html: str) -> str:
        for href in _HREF_RE.findall(html):
            if not href.startswith(("http://", "https://")):
                continue
            host = urlparse(href).netloc
            if host and host != _SITE_HOST:
                return href.split("#")[0]
        return ""
