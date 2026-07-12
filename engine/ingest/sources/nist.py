"""NIST publications source plugin.

Crawls the public NIST publications listing (``nist.gov/publications``) and the
Computer Security Resource Center (``csrc.nist.gov``). NIST technical-series
documents (SP, FIPS, IR) are treated as ``STANDARD`` kind. Individual
publication PDFs can be added as seeds to index their extracted text.
"""

from __future__ import annotations

from engine.documents import DocumentKind
from engine.ingest.base import register
from engine.ingest.crawler import DocsCrawler


@register
class NistSource(DocsCrawler):
    name = "nist"
    display_name = "NIST Publications"
    default_kind = DocumentKind.STANDARD
    description = "NIST standards, special publications, and technical reports."

    seeds = [
        "https://www.nist.gov/publications",
        "https://csrc.nist.gov/publications/sp",
        "https://csrc.nist.gov/publications/fips",
    ]
    allowed_domains = ["www.nist.gov", "csrc.nist.gov"]
    path_includes = ["/publications", "/pubs/"]
    version = "current"

    def _tags_for(self, url):
        tags = ["nist"]
        if "csrc" in url:
            tags.append("cybersecurity")
        return tags
