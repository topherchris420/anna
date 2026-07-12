"""ARM developer documentation source plugin.

Crawls ``developer.arm.com/documentation`` — architecture reference manuals,
Cortex-M/A technical reference manuals, and toolchain guides.
"""

from __future__ import annotations

from engine.documents import DocumentKind
from engine.ingest.base import register
from engine.ingest.crawler import DocsCrawler


@register
class ArmSource(DocsCrawler):
    name = "arm"
    display_name = "ARM Developer Documentation"
    default_kind = DocumentKind.DOCUMENTATION
    description = "ARM architecture and processor technical reference manuals."

    seeds = [
        "https://developer.arm.com/documentation",
        "https://developer.arm.com/documentation/ddi0487/latest/",
    ]
    allowed_domains = ["developer.arm.com"]
    path_includes = ["/documentation/"]
    version = "latest"

    def _tags_for(self, url):
        return ["arm", "cortex", "architecture"]
