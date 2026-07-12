"""PDF text extraction for PDF indexing.

Uses ``pypdf`` when available. Extraction failures (encrypted PDFs, scanned
image-only PDFs, missing dependency) degrade to an empty string rather than
breaking ingestion — the document is still indexed on its metadata.
"""

from __future__ import annotations

import io
from typing import Optional

from engine.config import EngineConfig, get_config
from engine.ingest import http


def extract_text_from_bytes(data: bytes, max_pages: int = 40) -> str:
    """Extract text from PDF bytes, capped at ``max_pages`` pages."""
    try:
        from pypdf import PdfReader  # lazy import
    except Exception:
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:max_pages]
        chunks = []
        for page in pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
        return _clean("\n".join(chunks))
    except Exception:
        return ""


def extract_text_from_url(
    url: str,
    max_pages: int = 40,
    config: Optional[EngineConfig] = None,
) -> str:
    """Download a PDF and extract its text."""
    config = config or get_config()
    try:
        data = http.get_bytes(url, config=config)
    except Exception:
        return ""
    return extract_text_from_bytes(data, max_pages=max_pages)


def _clean(text: str) -> str:
    # Collapse runaway whitespace introduced by PDF layout extraction.
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
