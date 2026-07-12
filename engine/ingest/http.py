"""Small HTTP helper shared by source plugins.

Wraps ``httpx`` with the configured user-agent, timeout, and a bounded
exponential-backoff retry. Imported lazily so that importing the engine never
requires the HTTP stack.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from engine.config import EngineConfig, get_config


class HttpError(RuntimeError):
    pass


def _client(config: EngineConfig, headers: Optional[Dict[str, str]] = None):
    import httpx  # lazy import

    base_headers = {"User-Agent": config.user_agent}
    if headers:
        base_headers.update(headers)
    return httpx.Client(
        headers=base_headers,
        timeout=config.request_timeout,
        follow_redirects=True,
    )


def get(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    config: Optional[EngineConfig] = None,
    retries: int = 3,
):
    """GET ``url`` with retries. Returns an ``httpx.Response``."""
    config = config or get_config()
    last_exc: Optional[Exception] = None
    with _client(config, headers) as client:
        for attempt in range(retries):
            try:
                resp = client.get(url, params=params)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise HttpError(f"HTTP {resp.status_code} for {url}")
                resp.raise_for_status()
                return resp
            except Exception as exc:  # network error or retriable status
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(2**attempt)
    raise HttpError(f"GET failed for {url}: {last_exc}")


def get_json(url: str, **kwargs: Any) -> Any:
    return get(url, **kwargs).json()


def get_text(url: str, **kwargs: Any) -> str:
    return get(url, **kwargs).text


def get_bytes(url: str, **kwargs: Any) -> bytes:
    return get(url, **kwargs).content
