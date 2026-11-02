"""Security headers: on every response, and a strict CSP on the engine's pages.

The engine shows text it did not write — titles, abstracts and passages
ingested from arXiv, GitHub, vendor documentation — so escaping is the first
defence and these headers are the second:

- every response: ``X-Content-Type-Options: nosniff`` (a JSON body or a
  ``.bib`` download is never sniffed into HTML), ``Referrer-Policy:
  strict-origin-when-cross-origin`` (a search URL, query included, never
  leaves in a Referer to an outbound source link), ``X-Frame-Options:
  SAMEORIGIN`` (no clickjacking frame; the deck frames its own slides);
- every page rendered from the engine's templates: a Content-Security-
  Policy that runs no script without this response's nonce, loads nothing
  from another origin, and allows no plugins, base rewrites or foreign form
  targets. An injected ``<script>`` or ``onclick`` would not run.

A page opts in by rendering ``csp_nonce()`` (``base.html`` does, so every
engine page does) and puts the nonce on its own ``<script>`` tags. Static
files — the blueprint animation, the deck — carry inline scripts of their
own and get the response-wide headers only.
"""

from __future__ import annotations

import secrets

from flask import g

_NONCE_KEY = "engine_csp_nonce"

RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "SAMEORIGIN",
}

PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


def csp_nonce() -> str:
    """This response's script nonce (created on first use, then reused)."""
    nonce = g.get(_NONCE_KEY)
    if nonce is None:
        nonce = secrets.token_urlsafe(18)
        setattr(g, _NONCE_KEY, nonce)
    return nonce


def content_security_policy(nonce: str) -> str:
    """The policy for a page whose scripts carry ``nonce``."""
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            # Inline <style> blocks and style attributes are how the pages
            # are styled; style is not a script-execution vector.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'self'",
        ]
    )


def apply_security_headers(response):
    """``after_app_request`` hook: the headers above, never overriding a
    header a view set itself."""
    for name, value in RESPONSE_HEADERS.items():
        response.headers.setdefault(name, value)
    nonce = g.get(_NONCE_KEY)
    if nonce is not None and response.mimetype == "text/html":
        response.headers.setdefault(
            "Content-Security-Policy", content_security_policy(nonce)
        )
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
    return response
