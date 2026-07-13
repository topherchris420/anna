"""Built-in source plugins.

Importing this package registers every source with the plugin registry via the
``@register`` decorator on each plugin class.
"""

from engine.ingest.sources import arxiv  # noqa: F401
from engine.ingest.sources import github  # noqa: F401
from engine.ingest.sources import nasa  # noqa: F401
from engine.ingest.sources import doe  # noqa: F401
from engine.ingest.sources import ieee  # noqa: F401
from engine.ingest.sources import nist  # noqa: F401
from engine.ingest.sources import linux_kernel  # noqa: F401
from engine.ingest.sources import stm32  # noqa: F401
from engine.ingest.sources import espressif  # noqa: F401
from engine.ingest.sources import arm  # noqa: F401
from engine.ingest.sources import riscv  # noqa: F401
from engine.ingest.sources import shadowlibraries  # noqa: F401
