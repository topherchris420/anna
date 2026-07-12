"""RISC-V specifications & documentation source plugin.

Crawls the RISC-V International specifications listing and linked technical
material. RISC-V ratified specs are versioned documents, so records are treated
as ``STANDARD`` kind.
"""

from __future__ import annotations

from engine.documents import DocumentKind
from engine.ingest.base import register
from engine.ingest.crawler import DocsCrawler


@register
class RiscvSource(DocsCrawler):
    name = "riscv"
    display_name = "RISC-V Specifications"
    default_kind = DocumentKind.STANDARD
    description = "RISC-V ISA specifications and technical documentation."

    seeds = [
        "https://riscv.org/technical/specifications/",
        "https://riscv.org/specifications/ratified/",
    ]
    allowed_domains = ["riscv.org"]
    path_includes = ["/specifications", "/technical/"]
    version = "ratified"

    def _tags_for(self, url):
        return ["risc-v", "isa", "specification"]
