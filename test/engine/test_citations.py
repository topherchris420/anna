"""Citations: deterministic BibTeX keys and entries, plain-text references,
byte-level parity with ``frontend/citations.js`` (executed under Node), and
the API and CLI surfaces that expose them."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engine import citations
from engine.documents import Document, DocumentKind

REPO = Path(__file__).resolve().parents[2]
CITATIONS_JS = REPO / "frontend" / "citations.js"

try:
    from flask import Flask
except ImportError:  # pragma: no cover - exercised by the skip itself
    Flask = None

requires_flask = pytest.mark.skipif(Flask is None, reason="flask not installed")
requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _paper(**overrides):
    fields = dict(
        id="arxiv:8bbe0f7de8c6440b",
        source="arxiv",
        kind=DocumentKind.PAPER,
        title="Zero-Copy Circular DMA Buffering for Sensor Acquisition",
        abstract="We present a zero-copy ping-pong DMA scheme.",
        authors=["M. Devlin", "R. Okafor", "S. Nakamura"],
        published="2024-03-11T00:00:00Z",
        url="https://arxiv.org/abs/2403.00001",
        pdf_url="https://arxiv.org/pdf/2403.00001",
        identifiers={"arxiv_id": "2403.00001"},
    )
    fields.update(overrides)
    return Document(**fields)


class TestKeys:
    def test_surname_year_word(self):
        assert citations.bibtex_key(_paper()) == "devlin2024zero"

    def test_last_comma_first_and_initials(self):
        assert citations.surname("Smith, John") == "smith"
        assert citations.surname("J. R. Smith") == "smith"
        assert citations.surname("Müller-Lüdenscheidt, K.") == (
            "mullerludenscheidt"
        )

    def test_without_authors_the_source_stands_in(self):
        doc = _paper(authors=[], source="espressif", published=None)
        assert citations.bibtex_key(doc) == "espressifndzero"

    def test_title_word_skips_stop_words_and_short_tokens(self):
        assert citations.title_word("The Art of Electronics") == "art"
        assert citations.title_word("A DMA ring") == "dma"
        assert citations.title_word("Of the") == "of"
        assert citations.title_word("") == "untitled"
        assert citations.title_word("ESP32 DMA and Circular Buffers") == (
            "esp32"
        )

    def test_year_comes_from_the_start_of_the_date(self):
        assert citations.year_of("2023-05-01") == "2023"
        assert citations.year_of(" 1999") == "1999"
        assert citations.year_of("May 2023") == ""
        assert citations.year_of(None) == ""

    def test_duplicate_keys_get_letter_suffixes_in_order(self):
        docs = [_paper(id=f"arxiv:{i}") for i in range(3)]
        keys = [c["key"] for c in citations.citations(docs)]
        assert keys == ["devlin2024zero", "devlin2024zerob", "devlin2024zeroc"]
        assert citations._unique_key("k", 26) == "kz"
        assert citations._unique_key("k", 27) == "k-27"


class TestBibtex:
    def test_arxiv_paper_entry(self):
        assert citations.bibtex(_paper()) == "\n".join(
            [
                "@article{devlin2024zero,",
                "  author = {M. Devlin and R. Okafor and S. Nakamura},",
                "  title = {{Zero-Copy Circular DMA Buffering for Sensor "
                "Acquisition}},",
                "  journal = {arXiv preprint arXiv:2403.00001},",
                "  year = {2024},",
                "  eprint = {2403.00001},",
                "  archivePrefix = {arXiv},",
                "  url = {https://arxiv.org/abs/2403.00001},",
                "  annaid = {arxiv:8bbe0f7de8c6440b}",
                "}",
            ]
        )

    @pytest.mark.parametrize(
        "kind, entry_type, venue_field",
        [
            ("paper", "article", None),
            ("report", "techreport", "institution"),
            ("standard", "techreport", "institution"),
            ("documentation", "manual", "organization"),
            ("datasheet", "manual", "organization"),
            ("repository", "misc", "howpublished"),
            ("code", "misc", "howpublished"),
            ("library", "misc", "howpublished"),
            ("other", "misc", "howpublished"),
        ],
    )
    def test_entry_type_follows_the_document_kind(
        self, kind, entry_type, venue_field
    ):
        doc = _paper(kind=kind, source="nasa", identifiers={})
        entry = citations.bibtex(doc)
        assert entry.startswith("@" + entry_type + "{")
        if venue_field:
            assert (
                f"  {venue_field} = {{NASA Technical Reports (NTRS)}}," in entry
            )
        else:
            assert "institution" not in entry and "journal" not in entry

    def test_special_characters_are_escaped_once(self):
        doc = _paper(
            title="100% of C&C {systems} cost $5 #1 a_b x^2 ~ \\ok",
            authors=["Research and Development Corp"],
        )
        entry = citations.bibtex(doc)
        assert (
            "  title = {{100\\% of C\\&C \\{systems\\} cost \\$5 \\#1 a\\_b "
            "x\\^{}2 \\~{} \\textbackslash{}ok}}," in entry
        )
        # An organization containing "and" is braced so BibTeX keeps it whole.
        assert "  author = {{Research and Development Corp}}," in entry
        assert citations.escape("a  \n b") == "a b"

    def test_links_must_be_http_and_brace_free(self):
        assert citations.link({"url": "javascript:alert(1)"}) == ""
        assert citations.link({"url": "", "pdf_url": "https://x.org/p.pdf"}) == (
            "https://x.org/p.pdf"
        )
        assert citations.link({"url": "https://x.org/a {b}\\c"}) == (
            "https://x.org/a%7Bb%7D%5Cc"
        )
        entry = citations.bibtex(_paper(url="ftp://x", pdf_url=""))
        assert "url =" not in entry

    def test_version_doi_and_missing_fields(self):
        doc = Document(
            id="espressif:1020b2afe9f87462",
            source="espressif",
            kind=DocumentKind.DOCUMENTATION,
            title="ESP32 DMA and Circular Buffers",
            version="v5.1",
            url="https://docs.espressif.com/",
            identifiers={"doi": "10.1000/xyz", "repo": "ignored"},
        )
        assert citations.bibtex(doc) == "\n".join(
            [
                "@manual{espressifndesp32,",
                "  title = {{ESP32 DMA and Circular Buffers}},",
                "  organization = {Espressif ESP-IDF Documentation},",
                "  version = {v5.1},",
                "  doi = {10.1000/xyz},",
                "  url = {https://docs.espressif.com/},",
                "  annaid = {espressif:1020b2afe9f87462}",
                "}",
            ]
        )

    def test_an_unknown_source_keeps_its_name_and_an_empty_title_is_named(self):
        entry = citations.bibtex({"id": "x", "source": "mysource", "kind": "x"})
        assert "@misc{mysourcenduntitled," in entry
        assert "  title = {{Untitled}}," in entry
        assert "  howpublished = {mysource}," in entry

    def test_a_mapping_and_a_document_cite_identically(self):
        doc = _paper()
        mapping = {
            "id": doc.id,
            "source": doc.source,
            "kind": doc.kind,
            "title": doc.title,
            "authors": doc.authors,
            "published": doc.published,
            "url": doc.url,
            "pdf_url": doc.pdf_url,
            "identifiers": doc.identifiers,
        }
        assert citations.citation(mapping) == citations.citation(doc)

    def test_hostile_field_types_are_ignored(self):
        doc = {
            "id": "x",
            "title": None,
            "authors": "not a list",
            "identifiers": ["not", "a", "dict"],
            "published": 2024,
            "url": 42,
        }
        assert citations.bibtex(doc) == "\n".join(
            ["@misc{annanduntitled,", "  title = {{Untitled}},", "  annaid = {x}", "}"]
        )


class TestPlainText:
    def test_authors_year_title_source_link(self):
        assert citations.plain(_paper()) == (
            "M. Devlin, R. Okafor, and S. Nakamura (2024). Zero-Copy Circular "
            "DMA Buffering for Sensor Acquisition. arXiv. "
            "https://arxiv.org/abs/2403.00001"
        )
        two = _paper(authors=["A. One", "B. Two"], url="", pdf_url="")
        assert citations.plain(two).startswith("A. One and B. Two (2024). ")

    def test_without_authors_the_title_leads(self):
        doc = {
            "title": "ESP32 DMA and Circular Buffers?",
            "source": "espressif",
            "version": "v5.1",
            "url": "https://docs.espressif.com/",
        }
        assert citations.plain(doc) == (
            "ESP32 DMA and Circular Buffers? (n.d.). Espressif ESP-IDF "
            "Documentation, version v5.1. https://docs.espressif.com/"
        )
        # A title that already ends a sentence is not given a second period.
        assert citations.plain({"title": "Why?", "authors": ["A. B"]}) == (
            "A. B (n.d.). Why?"
        )


class TestBibliography:
    def test_entries_are_blank_line_separated(self):
        docs = [_paper(), _paper(id="arxiv:2", title="Another DMA paper")]
        text = citations.bibliography(docs)
        assert text.count("@article{") == 2
        assert "}\n\n@article{devlin2024another," in text
        assert text.endswith("}\n")
        assert citations.bibliography([]) == ""


class TestSourceLabels:
    def test_labels_match_the_plugin_registry(self):
        from engine.ingest import all_plugins

        registry = {p.name: p.info()["display_name"] for p in all_plugins()}
        for name, label in citations.SOURCE_LABELS.items():
            assert registry.get(name) == label, name


# Documents whose fields exercise every branch both twins have to agree on.
PARITY_DOCS = [
    {
        "id": "arxiv:8bbe0f7de8c6440b",
        "source": "arxiv",
        "kind": "paper",
        "title": "  Zero-Copy  Circular DMA\tBuffering: 100% faster & cheaper ",
        "authors": ["M. Devlin", "Okafor, R.", "Research and Development Corp"],
        "published": "2024-03-11T00:00:00Z",
        "url": "https://arxiv.org/abs/2403.00001?x=1&y={2}",
        "identifiers": {"arxiv_id": "2403.00001", "doi": "10.1000/a_b"},
    },
    {
        "id": "espressif:1020b2afe9f87462",
        "source": "espressif",
        "kind": "documentation",
        "title": "ESP32 DMA and Circular Buffers",
        "authors": [],
        "published": None,
        "version": "v5.1",
        "url": "javascript:alert(1)",
        "pdf_url": "https://docs.espressif.com/esp32.pdf",
        "identifiers": {},
    },
    {
        "id": "github:dd47b427bee571ef",
        "source": "github",
        "kind": "repository",
        "title": "demo/tiny-rtos",
        "authors": ["Müller-Lüdenscheidt, Kåre", "Ünal Çelik"],
        "published": "2019",
        "url": "https://github.com/demo/tiny-rtos",
        "identifiers": {"repo": "demo/tiny-rtos"},
    },
    {
        "id": "nasa:1",
        "source": "nasa",
        "kind": "report",
        "title": "The Art of #Testing $x^2$ ~ \\LaTeX",
        "authors": ["NASA Langley Research Center"],
        "published": "1999-01",
        "identifiers": {"ntrs_id": "1"},
    },
    {"id": "nasa:2", "source": "nasa", "kind": "report", "title": "The Art of Flight"},
    {"id": "nasa:3", "source": "nasa", "kind": "report", "title": "The Art of Flight"},
    {"id": "", "source": "", "kind": "", "title": "", "authors": None},
    {
        "id": "shadowlibraries:x",
        "source": "shadowlibraries",
        "kind": "library",
        "title": "Anna’s Archive — “the” mirror (ру́сский текст)",
        "authors": ["李, 明"],
        "published": "2020-06-30",
        "url": "https://annas-archive.example/ ",
    },
]

NODE_SCRIPT = """
const citations = require(process.argv[1]);
let input = "";
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const docs = JSON.parse(input);
  process.stdout.write(JSON.stringify({
    citations: citations.citations(docs),
    bibliography: citations.bibliography(docs),
  }));
});
"""


def _node(docs):
    completed = subprocess.run(
        ["node", "-e", NODE_SCRIPT, str(CITATIONS_JS)],
        input=json.dumps(docs),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(completed.stdout)


@requires_node
class TestBrowserParity:
    def test_keys_entries_and_references_match_citations_js(self):
        browser = _node(PARITY_DOCS)
        assert citations.citations(PARITY_DOCS) == browser["citations"]
        assert citations.bibliography(PARITY_DOCS) == browser["bibliography"]

    def test_the_demo_corpus_cites_the_same_in_both(self):
        corpus = json.loads(
            subprocess.run(
                [
                    "node",
                    "-e",
                    "process.stdout.write(JSON.stringify(require(process.argv[1])))",
                    str(REPO / "frontend" / "demo-corpus.js"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            ).stdout
        )
        assert citations.citations(corpus) == _node(corpus)["citations"]


# --------------------------------------------------------------------------- #
# API and CLI
# --------------------------------------------------------------------------- #
def _app():
    from allthethings.engine_api.views import engine_api

    app = Flask(__name__)
    app.register_blueprint(engine_api)
    return app


@requires_flask
class TestCitationEndpoint:
    def test_json_by_default(self, monkeypatch):
        from allthethings.engine_api import views

        doc = _paper()
        monkeypatch.setattr(views.backend, "get_document", lambda doc_id: doc)
        app = _app()
        response = app.test_client().get(
            "/api/v1/document/arxiv%3A8bbe0f7de8c6440b/citation"
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body == citations.citation(doc)
        assert body["key"] == "devlin2024zero"

    @pytest.mark.parametrize(
        "fmt, content_type, field",
        [
            ("bibtex", "application/x-bibtex", "bibtex"),
            ("text", "text/plain", "text"),
        ],
    )
    def test_plain_formats_are_served_as_text(
        self, monkeypatch, fmt, content_type, field
    ):
        from allthethings.engine_api import views

        doc = _paper()
        monkeypatch.setattr(views.backend, "get_document", lambda doc_id: doc)
        app = _app()
        response = app.test_client().get(
            f"/api/v1/document/{doc.id}/citation?format={fmt}"
        )
        assert response.status_code == 200
        assert response.content_type == content_type + "; charset=utf-8"
        assert response.get_data(as_text=True) == (
            citations.citation(doc)[field] + "\n"
        )
        if fmt == "bibtex":
            assert response.headers["Content-Disposition"] == (
                'inline; filename="devlin2024zero.bib"'
            )

    def test_unknown_format_is_400_before_any_lookup(self, monkeypatch):
        from allthethings.engine_api import views

        def forbidden(doc_id):
            raise AssertionError("validation must precede retrieval")

        monkeypatch.setattr(views.backend, "get_document", forbidden)
        app = _app()  # keep the app alive while the response is decoded
        response = app.test_client().get(
            "/api/v1/document/x/citation?format=ris"
        )
        assert response.status_code == 400
        assert "format" in response.get_json()["error"]

    def test_missing_document_is_404_and_outage_is_503(self, monkeypatch):
        from allthethings.engine_api import views

        monkeypatch.setattr(views.backend, "get_document", lambda doc_id: None)
        app = _app()
        assert (
            app.test_client().get("/api/v1/document/x/citation").status_code
            == 404
        )

        def down(doc_id):
            raise RuntimeError("connection refused to db-host")

        monkeypatch.setattr(views.backend, "get_document", down)
        response = app.test_client().get("/api/v1/document/x/citation")
        assert response.status_code == 503
        assert "db-host" not in response.get_data(as_text=True)

    def test_overlong_id_is_400(self):
        response = _app().test_client().get(
            "/api/v1/document/" + "x" * 513 + "/citation"
        )
        assert response.status_code == 400


@requires_flask
class TestCiteCommand:
    def _runner(self, monkeypatch, lookup):
        from allthethings.engine_cli.views import engine_cli
        from engine import backend

        monkeypatch.setattr(backend, "get_document", lookup)
        app = Flask(__name__)
        app.register_blueprint(engine_cli)
        return app.test_cli_runner()

    def test_prints_bibtex_by_default(self, monkeypatch):
        doc = _paper()
        runner = self._runner(monkeypatch, lambda doc_id: doc)
        result = runner.invoke(args=["engine", "cite", doc.id])
        assert result.exit_code == 0, result.output
        assert result.output == citations.bibtex(doc) + "\n"

    def test_text_and_json_formats(self, monkeypatch):
        doc = _paper()
        runner = self._runner(monkeypatch, lambda doc_id: doc)
        text = runner.invoke(args=["engine", "cite", doc.id, "--format", "text"])
        assert text.output == citations.plain(doc) + "\n"
        as_json = runner.invoke(
            args=["engine", "cite", doc.id, "--format", "json"]
        )
        assert json.loads(as_json.output) == citations.citation(doc)

    def test_exit_codes_distinguish_missing_from_unreachable(self, monkeypatch):
        runner = self._runner(monkeypatch, lambda doc_id: None)
        missing = runner.invoke(args=["engine", "cite", "nope"])
        assert missing.exit_code == 1
        assert "not in the index" in missing.output

        def down(doc_id):
            raise RuntimeError("refused")

        runner = self._runner(monkeypatch, down)
        unreachable = runner.invoke(args=["engine", "cite", "nope"])
        assert unreachable.exit_code == 2
        assert "index unavailable" in unreachable.output
