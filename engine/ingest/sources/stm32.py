"""STM32 documentation source plugin.

Crawls the STMicroelectronics community wiki (``wiki.st.com``), which hosts
HTML documentation for STM32 microcontrollers. STM32 reference manuals and
datasheets are largely PDFs on ``st.com``; those can additionally be ingested by
passing PDF URLs as seeds (the crawler indexes their extracted text).
"""

from __future__ import annotations

from engine.documents import DocumentKind
from engine.ingest.base import register
from engine.ingest.crawler import DocsCrawler


@register
class Stm32Source(DocsCrawler):
    name = "stm32"
    display_name = "STM32 Documentation"
    default_kind = DocumentKind.DOCUMENTATION
    description = "STM32 microcontroller reference documentation and guides."

    seeds = [
        "https://wiki.st.com/stm32mcu/wiki/Main_Page",
        "https://wiki.st.com/stm32mcu/wiki/Category:Getting_started_with_STM32_hardware_features",
    ]
    allowed_domains = ["wiki.st.com"]
    path_includes = ["/stm32mcu/wiki/"]
    version = "latest"

    def _tags_for(self, url):
        return ["stm32", "stmicroelectronics", "arm-cortex-m"]
