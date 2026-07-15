"""The ``snippet`` template filter (highlight rendering). Skipped without Flask.

Highlight fragments are raw document text with ``<em>`` markers inserted by the
retrieval layer. The server-rendered UI must escape the document text (crawled
content can carry hostile markup) while keeping the markers — the same policy
the static frontend applies client-side in ``highlight()``.
"""

import pytest

pytest.importorskip("flask")

from allthethings.engine_web.views import snippet_filter  # noqa: E402


class TestSnippetFilter:
    def test_keeps_engine_em_markers(self):
        out = str(snippet_filter("a <em>kalman</em> filter"))
        assert out == "a <em>kalman</em> filter"

    def test_escapes_document_markup(self):
        out = str(snippet_filter("x <script>alert(1)</script> <em>y</em>"))
        assert "<script>" not in out
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
        assert "<em>y</em>" in out

    def test_escapes_attributes_and_entities(self):
        out = str(snippet_filter('<img src=x onerror=alert(1)> & <em>dma</em>'))
        assert "<img" not in out
        assert "&amp;" in out
        assert "<em>dma</em>" in out

    def test_result_is_markup_safe(self):
        # Jinja must not re-escape the filter's output (or the <em> markers
        # would render as literal text).
        from markupsafe import Markup, escape

        out = snippet_filter("plain")
        assert isinstance(out, Markup)
        assert str(escape(out)) == "plain"

    def test_handles_empty_input(self):
        assert str(snippet_filter("")) == ""
        assert str(snippet_filter(None)) == ""
