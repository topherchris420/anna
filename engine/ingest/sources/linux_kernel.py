"""Linux kernel documentation source plugin.

Crawls the official kernel documentation at ``kernel.org/doc/html``. The version
segment in the URL (``latest``, ``v6.6`` …) is captured for version-aware search.
"""

from __future__ import annotations

import re
from typing import Optional

from engine.documents import DocumentKind
from engine.ingest.base import register
from engine.ingest.crawler import DocsCrawler

_VERSION_RE = re.compile(r"/doc/html/([^/]+)/")


@register
class LinuxKernelSource(DocsCrawler):
    name = "linux_kernel"
    display_name = "Linux Kernel Documentation"
    default_kind = DocumentKind.DOCUMENTATION
    description = "The official Linux kernel documentation tree."

    seeds = [
        "https://www.kernel.org/doc/html/latest/",
        "https://www.kernel.org/doc/html/latest/driver-api/index.html",
        "https://www.kernel.org/doc/html/latest/core-api/index.html",
        "https://www.kernel.org/doc/html/latest/admin-guide/index.html",
    ]
    allowed_domains = ["www.kernel.org"]
    path_includes = ["/doc/html/"]

    def detect_version(self, url: str) -> Optional[str]:
        match = _VERSION_RE.search(url)
        return match.group(1) if match else "latest"

    def _tags_for(self, url):
        return ["linux", "kernel"]
