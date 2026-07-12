"""Espressif (ESP-IDF) documentation source plugin.

Crawls ``docs.espressif.com`` (Sphinx-generated). ESP-IDF publishes a sitemap
and versioned URLs like ``/projects/esp-idf/en/v5.1/esp32/…`` — the version is
captured for version-aware documentation search.
"""

from __future__ import annotations

import re
from typing import Optional

from engine.documents import DocumentKind
from engine.ingest.base import register
from engine.ingest.crawler import DocsCrawler

_VERSION_RE = re.compile(r"/en/([^/]+)/")


@register
class EspressifSource(DocsCrawler):
    name = "espressif"
    display_name = "Espressif ESP-IDF Documentation"
    default_kind = DocumentKind.DOCUMENTATION
    description = "ESP32 / ESP-IDF SDK and hardware documentation."

    seeds = [
        "https://docs.espressif.com/projects/esp-idf/en/latest/esp32/index.html",
        "https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/index.html",
    ]
    sitemaps = [
        "https://docs.espressif.com/projects/esp-idf/en/latest/esp32/sitemap.xml",
    ]
    allowed_domains = ["docs.espressif.com"]
    path_includes = ["/projects/esp-idf/"]

    def detect_version(self, url: str) -> Optional[str]:
        match = _VERSION_RE.search(url)
        return match.group(1) if match else "latest"

    def _tags_for(self, url):
        return ["espressif", "esp32", "esp-idf"]
