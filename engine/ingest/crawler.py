"""Shared documentation-crawler base for HTML/PDF documentation sources.

Vendor documentation (STM32, Espressif, ARM, RISC-V), the Linux kernel docs,
and standards listings (NIST) don't expose search APIs, so they are ingested by
crawling. :class:`DocsCrawler` discovers page URLs from ``sitemap.xml`` and/or a
small breadth-first crawl of seed pages, then extracts a title and readable text
from each page with a dependency-free HTML→text converter.

Subclasses just declare ``seeds`` / ``sitemaps`` and metadata; they rarely need
to override behavior. Version-aware sources override :meth:`detect_version`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from engine.documents import Document, DocumentKind
from engine.ingest import http
from engine.ingest.base import SourcePlugin

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|nav|footer|header|svg|noscript)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'href=["\']([^"\'#?]+)', re.IGNORECASE)
_WS_RE = re.compile(r"[ \t]+")
_MULTINL_RE = re.compile(r"\n{3,}")


def html_to_text(html: str) -> Tuple[str, str]:
    """Return ``(title, body_text)`` extracted from an HTML page."""
    if not html:
        return "", ""
    title_match = _TITLE_RE.search(html) or _H1_RE.search(html)
    title = ""
    if title_match:
        title = unescape(_TAG_RE.sub(" ", title_match.group(1))).strip()

    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    # Turn block-level tags into newlines so text stays readable.
    cleaned = re.sub(r"</(p|div|li|h[1-6]|section|article|tr)>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", cleaned)
    text = unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(ln.strip() for ln in text.splitlines())
    text = _MULTINL_RE.sub("\n\n", text).strip()
    return title, text


class DocsCrawler(SourcePlugin):
    default_kind = DocumentKind.DOCUMENTATION
    supports_query = False

    #: Content pages / index pages to crawl (one level of link expansion).
    seeds: List[str] = []
    #: ``sitemap.xml`` URLs to enumerate.
    sitemaps: List[str] = []
    #: URL substring filter; only URLs containing one of these are kept.
    path_includes: List[str] = []
    #: Restrict crawling to these domains (defaults to the seeds' domains).
    allowed_domains: List[str] = []
    #: Fixed version label, or ``None`` to detect per-URL.
    version: Optional[str] = None

    # ------------------------------------------------------------------ #
    def fetch(
        self,
        *,
        query: Optional[str] = None,
        limit: int = 100,
        seeds: Optional[List[str]] = None,
        sitemaps: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        seeds = seeds or list(self.seeds)
        sitemaps = sitemaps or list(self.sitemaps)
        urls = self._discover_urls(seeds, sitemaps, limit)
        seen = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            try:
                html = http.get_text(url, config=self.config)
            except Exception:
                continue
            yield {"url": url, "html": html}

    def normalize(self, raw: Dict[str, Any]) -> Optional[Document]:
        url = raw.get("url", "")
        title, text = html_to_text(raw.get("html", ""))
        if len(text) < 120:  # skip near-empty / redirect shells
            return None
        return self.make_document(
            url,
            title=title or url,
            abstract=text[:500],
            body=text[:20000],
            url=url,
            version=self.detect_version(url),
            categories=[self.name],
            tags=self._tags_for(url),
        )

    # ------------------------------------------------------------------ #
    def detect_version(self, url: str) -> Optional[str]:
        """Version-aware sources override this. Default: fixed ``version``."""
        return self.version

    def _tags_for(self, url: str) -> List[str]:
        return []

    def _same_domain(self, url: str) -> bool:
        domains = self.allowed_domains or [urlparse(s).netloc for s in self.seeds]
        return urlparse(url).netloc in domains if domains else True

    def _wanted(self, url: str) -> bool:
        if not url.startswith("http"):
            return False
        if not self._same_domain(url):
            return False
        if self.path_includes and not any(p in url for p in self.path_includes):
            return False
        return True

    def _discover_urls(
        self, seeds: List[str], sitemaps: List[str], limit: int
    ) -> List[str]:
        urls: List[str] = []
        for sm in sitemaps:
            urls.extend(self._parse_sitemap(sm, limit * 2))
            if len(urls) >= limit * 2:
                break
        # One level of link expansion from seed index pages.
        for seed in seeds:
            if len(urls) >= limit * 2:
                break
            urls.append(seed)
            try:
                html = http.get_text(seed, config=self.config)
            except Exception:
                continue
            for href in _HREF_RE.findall(html):
                absolute = urljoin(seed, href)
                if self._wanted(absolute):
                    urls.append(absolute)
        # De-dup, keep order, filter.
        out: List[str] = []
        seen = set()
        for u in urls:
            u = u.split("#")[0]
            if u in seen:
                continue
            seen.add(u)
            if self._wanted(u) or u in seeds:
                out.append(u)
            if len(out) >= limit:
                break
        return out

    def _parse_sitemap(self, sitemap_url: str, cap: int) -> List[str]:
        try:
            xml = http.get_text(sitemap_url, config=self.config)
        except Exception:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [el.text for el in root.findall(".//sm:loc", ns) if el.text]
        if not locs:  # sitemaps without the standard namespace
            locs = [el.text for el in root.iter() if el.tag.endswith("loc") and el.text]
        # A sitemap index points at more sitemaps.
        if locs and all(loc.endswith(".xml") for loc in locs[:3]):
            expanded: List[str] = []
            for child in locs:
                expanded.extend(self._parse_sitemap(child, cap))
                if len(expanded) >= cap:
                    break
            locs = expanded
        return [u for u in locs if self._wanted(u)][:cap]
