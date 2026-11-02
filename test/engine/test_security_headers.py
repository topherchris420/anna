"""Security headers: on every response, and a nonce-based CSP on the pages."""

import re
from pathlib import Path

import pytest

pytest.importorskip("flask")

from flask import Flask, Response  # noqa: E402

from engine.documents import Document  # noqa: E402
from engine.search import SearchHit, SearchResults  # noqa: E402

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "allthethings" / "engine_web" / "templates" / "engine"
)
NONCE = re.compile(r"script-src 'self' 'nonce-([A-Za-z0-9_-]+)'")


def _app(monkeypatch):
    from allthethings.engine_api.views import engine_api
    from allthethings.engine_web import views

    class _Service:
        def search(self, query, **kwargs):
            doc = Document(id="arxiv:a", source="arxiv", kind="paper", title="DMA <script>x</script>")
            return SearchResults(
                query=query, mode="hybrid", total=1, facets={},
                hits=[SearchHit(document=doc, score=1.0)],
            )

    monkeypatch.setattr(views, "_service", _Service())
    app = Flask(__name__)
    app.register_blueprint(views.engine_web)
    app.register_blueprint(engine_api)
    return app


class TestEnginePages:
    def test_scripts_run_only_with_this_responses_nonce(self, monkeypatch):
        app = _app(monkeypatch)  # held: the test client keeps a weak reference
        response = app.test_client().get("/search?q=dma")
        policy = response.headers["Content-Security-Policy"]
        nonce = NONCE.search(policy).group(1)
        page = response.get_data(as_text=True)
        scripts = re.findall(r"<script\b[^>]*>", page)
        assert scripts and all(f'nonce="{nonce}"' in tag for tag in scripts)
        # The hostile title is escaped text, and would not run anyway.
        assert "<script>x</script>" not in page
        for directive in ("default-src 'self'", "object-src 'none'",
                          "base-uri 'self'", "form-action 'self'",
                          "frame-ancestors 'self'", "connect-src 'self'"):
            assert directive in policy
        assert "camera=()" in response.headers["Permissions-Policy"]

    def test_each_response_gets_its_own_nonce(self, monkeypatch):
        app = _app(monkeypatch)
        client = app.test_client()
        first, second = (
            NONCE.search(client.get(path).headers["Content-Security-Policy"]).group(1)
            for path in ("/search?q=dma", "/about")
        )
        assert first != second

    def test_every_response_is_unsniffable_unframable_and_keeps_its_referrer(self, monkeypatch):
        app = _app(monkeypatch)
        api = app.test_client().get("/api/v1/search?q=dma")
        assert api.headers["X-Content-Type-Options"] == "nosniff"
        assert api.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert api.headers["X-Frame-Options"] == "SAMEORIGIN"
        # JSON runs no script: no CSP to send.
        assert "Content-Security-Policy" not in api.headers

    def test_a_header_a_view_set_itself_is_kept(self):
        from allthethings.engine_web.security import apply_security_headers

        app = Flask(__name__)
        with app.test_request_context("/"):
            response = apply_security_headers(Response(headers={"X-Frame-Options": "DENY"}))
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"


class TestTemplates:
    @pytest.mark.parametrize("template", sorted(p.name for p in TEMPLATES.glob("*.html")))
    def test_no_inline_handler_and_no_script_without_the_nonce(self, template):
        # The CSP refuses both, so either would be a feature that silently
        # stops working.
        text = (TEMPLATES / template).read_text(encoding="utf-8")
        assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", text, re.I), template
        assert not re.search(r"<script(?![^>]*nonce=)", text), template
