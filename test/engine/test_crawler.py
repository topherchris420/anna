"""Unit tests for the dependency-free HTML→text extraction."""

from engine.ingest.crawler import DocsCrawler, html_to_text


class TestHtmlToText:
    def test_extracts_title(self):
        title, _ = html_to_text(
            "<html><head><title>DMA Guide</title></head><body>x</body></html>"
        )
        assert title == "DMA Guide"

    def test_falls_back_to_h1(self):
        title, _ = html_to_text(
            "<body><h1>Circular Buffers</h1><p>text</p></body>"
        )
        assert title == "Circular Buffers"

    def test_strips_scripts_and_styles(self):
        html = "<body><script>var x=1;evil()</script><style>.a{}</style><p>Real content here.</p></body>"
        _, text = html_to_text(html)
        assert "Real content here." in text
        assert "evil" not in text
        assert ".a{" not in text

    def test_block_tags_become_newlines(self):
        _, text = html_to_text("<p>one</p><p>two</p>")
        assert "one" in text and "two" in text

    def test_unescapes_entities(self):
        _, text = html_to_text("<p>a &amp; b &lt; c</p>")
        assert "a & b < c" in text

    def test_empty_input(self):
        assert html_to_text("") == ("", "")


class _Crawler(DocsCrawler):
    name = "_crawler_test"
    seeds = ["https://example.com/docs/"]
    allowed_domains = ["example.com"]
    path_includes = ["/docs/"]
    version = "1.0"


class TestDocsCrawlerNormalize:
    def test_normalize_builds_document(self):
        c = _Crawler()
        raw = {
            "url": "https://example.com/docs/dma.html",
            "html": "<title>DMA</title><body><p>"
            + ("word " * 60)
            + "</p></body>",
        }
        doc = c.normalize(raw)
        assert doc is not None
        assert doc.source == "_crawler_test"
        assert doc.version == "1.0"
        assert doc.url == raw["url"]
        assert "_crawler_test" in doc.categories

    def test_normalize_skips_thin_pages(self):
        c = _Crawler()
        doc = c.normalize(
            {"url": "https://example.com/docs/x", "html": "<p>tiny</p>"}
        )
        assert doc is None

    def test_wanted_filters_domain_and_path(self):
        c = _Crawler()
        assert c._wanted("https://example.com/docs/page") is True
        assert c._wanted("https://evil.com/docs/page") is False
        assert c._wanted("https://example.com/other/page") is False
