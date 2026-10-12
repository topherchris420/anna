"""The server-rendered pages keep the workbench's contract: an outage is an
outage (never "no results"), relevance is the shared 0–1 scale, mode labels
say what the backend runs, every hit explains its rank, and every document
can be cited. Label helpers are pure; the page tests need Flask."""

import pytest

from engine.documents import Document
from engine.search import SearchHit, SearchResults, fused_score_ceiling

try:
    from flask import Flask
except ImportError:  # pragma: no cover - exercised by the skip itself
    Flask = None

requires_flask = pytest.mark.skipif(Flask is None, reason="flask not installed")


def _views():
    from allthethings.engine_web import views

    return views


# --------------------------------------------------------------------------- #
# Pure label helpers
# --------------------------------------------------------------------------- #
class TestModeLabels:
    def test_postgres_with_hashing_vectors_names_what_runs(self):
        modes = _views().mode_labels(
            {"backend": "postgres", "vector_search": True, "embedding": "hashing"}
        )
        assert modes["bm25"]["label"] == "Lexical (full-text)"
        assert modes["hybrid"]["label"] == "Hybrid (full-text + term vectors)"
        assert modes["semantic"]["label"] == "Term vectors (not semantic)"
        assert all(m["available"] for m in modes.values())

    def test_elasticsearch_with_a_model_is_semantic(self):
        modes = _views().mode_labels(
            {
                "backend": "elasticsearch",
                "vector_search": True,
                "embedding": "sentence-transformer",
            }
        )
        assert modes["bm25"]["label"] == "Lexical (BM25)"
        assert modes["hybrid"]["label"] == "Hybrid (BM25 + Vector)"
        assert modes["semantic"]["label"] == "Semantic (Vector)"

    def test_without_vectors_the_vector_modes_are_not_offered(self):
        modes = _views().mode_labels(
            {"backend": "postgres", "vector_search": False, "embedding": None}
        )
        assert modes["bm25"]["available"] is True
        assert modes["hybrid"]["available"] is False
        assert modes["semantic"]["available"] is False

    def test_unknown_vectors_are_not_downgraded_on_a_guess(self):
        modes = _views().mode_labels({"backend": "elasticsearch"})
        assert modes["hybrid"]["available"] is True
        assert modes["hybrid"]["label"] == "Hybrid (BM25 + Vector)"


class TestStatusLines:
    def test_retrieval_status_mirrors_the_workbench(self):
        status = _views().retrieval_status
        assert status(None) == "Retrieval details unavailable"
        assert status({"executed": []}) == "Retrieval details unavailable"
        assert status(
            {
                "executed": ["fts", "knn"],
                "candidate_count": 42,
                "embedding": "hashing",
                "degraded": False,
            }
        ) == "fts + knn · 42 candidates · term-hashing vectors (no semantic model)"
        assert status(
            {
                "executed": ["fts"],
                "candidate_count": 3,
                "degraded": True,
                "unavailable": ["knn"],
            }
        ) == "fts · 3 candidates · unavailable: knn"

    def test_vectors_label(self):
        label = _views().vectors_label
        assert label({"vector_search": False}) == "none (full-text only)"
        assert label({"vector_search": True, "embedding": "hashing"}) == (
            "term-hashing (not semantic)"
        )
        assert label({"embedding": "sentence-transformer"}) == (
            "semantic (sentence-embedding model)"
        )
        assert label({"vector_search": True, "embedding": None}) == (
            "not loaded yet"
        )


class TestCapabilities:
    def test_a_retrieval_report_is_believed_before_any_probe(self, monkeypatch):
        views = _views()

        def forbidden(*args, **kwargs):
            raise AssertionError("no probe when the report already says")

        monkeypatch.setattr("engine.pg.store.get_store", forbidden)
        monkeypatch.setattr("engine.embeddings.get_embedder", forbidden)
        results = SearchResults(
            "q",
            "hybrid",
            0,
            [],
            {},
            retrieval={
                "backend": "postgres",
                "executed": ["fts", "knn"],
                "embedding": "hashing",
            },
        )
        caps = views.capabilities(results)
        assert caps == {
            "backend": "postgres",
            "vector_search": True,
            "embedding": "hashing",
        }
        degraded = SearchResults(
            "q", "hybrid", 0, [], {},
            retrieval={"backend": "postgres", "executed": ["fts"], "unavailable": ["knn"]},
        )
        monkeypatch.setattr(
            "engine.embeddings.get_embedder",
            lambda: type("E", (), {"semantic_if_known": False})(),
        )
        assert views.capabilities(degraded)["vector_search"] is False

    def test_probe_failures_leave_the_answer_unknown(self, monkeypatch):
        views = _views()
        from engine.config import EngineConfig

        monkeypatch.setattr(views, "get_config", lambda: EngineConfig(backend="postgres"))

        def down(config):
            raise RuntimeError("refused")

        monkeypatch.setattr("engine.pg.store.get_store", down)
        monkeypatch.setattr(
            "engine.embeddings.get_embedder",
            lambda: type("E", (), {"semantic_if_known": None})(),
        )
        assert views.capabilities(None) == {
            "backend": "postgres",
            "vector_search": None,
            "embedding": None,
        }
        assert views.capabilities(None, probe=False)["vector_search"] is None


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def _app():
    from allthethings.engine_api.views import engine_api
    from allthethings.engine_web.views import engine_web

    app = Flask(__name__)
    app.register_blueprint(engine_web)
    app.register_blueprint(engine_api)
    return app


def _doc(i="a", **overrides):
    fields = dict(
        id=f"arxiv:{i}",
        source="arxiv",
        kind="paper",
        title=f"DMA paper {i}",
        abstract="Circular DMA buffers stream samples without CPU load.",
        authors=["M. Devlin"],
        published="2024-03-11",
        url=f"https://arxiv.org/abs/{i}",
        identifiers={"arxiv_id": i},
    )
    fields.update(overrides)
    return Document(**fields)


class _Service:
    def __init__(self, results=None, error=None):
        self.results = results
        self.error = error

    def search(self, query, **kwargs):
        if self.error is not None:
            raise self.error
        return self.results

    def related(self, doc_id, size=8):
        if self.error is not None:
            raise self.error
        return []


def _quiet_capabilities(monkeypatch, caps=None):
    views = _views()
    monkeypatch.setattr(
        views,
        "capabilities",
        lambda results=None, probe=True: caps
        or {"backend": "postgres", "vector_search": True, "embedding": "hashing"},
    )
    return views


@requires_flask
class TestSearchPage:
    def test_an_outage_is_reported_as_an_outage_not_as_no_results(
        self, monkeypatch
    ):
        views = _quiet_capabilities(monkeypatch)
        monkeypatch.setattr(
            views,
            "_service",
            _Service(
                error=RuntimeError(
                    'connection to "db-host" failed for user "owner"'
                )
            ),
        )
        app = _app()
        response = app.test_client().get("/search?q=dma")
        page = response.get_data(as_text=True)
        assert response.status_code == 503
        assert "Anna's research backend isn't reachable." in page
        assert "This is not an empty result." in page
        assert "search index unavailable (RuntimeError)" in page
        assert "No results" not in page
        assert "Source report" not in page
        # Driver messages name hosts and users; only the class is shown.
        assert "db-host" not in page and "owner" not in page
        assert 'href="/search?q=dma">Retry</a>' in page

    def test_hits_show_relevance_and_explain_their_rank(self, monkeypatch):
        views = _quiet_capabilities(monkeypatch)
        ceiling = fused_score_ceiling(2, 60)
        results = SearchResults(
            "dma",
            "hybrid",
            2,
            [
                SearchHit(
                    _doc("a"),
                    ceiling,
                    highlights=["<em>DMA</em> buffers <script>x</script>"],
                    explanation={
                        "method": "rrf",
                        "ranks": {"fts": 1, "knn": 1},
                        "contributions": {"fts": 1 / 61, "knn": 1 / 61},
                    },
                ),
                SearchHit(_doc("b"), ceiling / 2),
            ],
            {},
            score_ceiling=ceiling,
            retrieval={
                "backend": "postgres",
                "executed": ["fts", "knn"],
                "candidate_count": 2,
                "embedding": "hashing",
            },
        )
        monkeypatch.setattr(views, "_service", _Service(results))
        app = _app()
        response = app.test_client().get("/search?q=dma")
        page = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "relevance 1.00" in page and "relevance 0.50" in page
        assert "rrf 0." not in page
        assert "Why this result?" in page
        assert "fts rank <b>1</b>" in page
        assert "fts + knn · 2 candidates · term-hashing vectors" in page
        assert "Source report" in page
        assert "AI answer" not in page
        # Highlights keep the engine's <em> marks and escape document markup.
        assert "<em>DMA</em>" in page and "<script>x</script>" not in page
        assert 'href="/document/arxiv%3Aa#cite">Cite</a>' in page or (
            'href="/document/arxiv:a#cite">Cite</a>' in page
        )
        # Labels name what this backend runs.
        assert "Hybrid (full-text + term vectors)" in page
        assert "Term vectors (not semantic)" in page
        assert "Lexical (full-text)" in page

    def test_a_hit_without_a_ceiling_shows_its_raw_rank_score(self, monkeypatch):
        views = _quiet_capabilities(monkeypatch)
        results = SearchResults("dma", "bm25", 1, [SearchHit(_doc(), 0.75)], {})
        monkeypatch.setattr(views, "_service", _Service(results))
        app = _app()
        page = app.test_client().get("/search?q=dma&mode=bm25").get_data(as_text=True)
        assert "rank score 0.7500" in page
        assert "relevance" not in page.split("Why this result?")[0]

    def test_vector_modes_are_not_offered_when_the_backend_has_none(
        self, monkeypatch
    ):
        views = _quiet_capabilities(
            monkeypatch,
            {"backend": "postgres", "vector_search": False, "embedding": None},
        )
        results = SearchResults("dma", "bm25", 0, [], {})
        monkeypatch.setattr(views, "_service", _Service(results))
        app = _app()
        page = app.test_client().get("/search?q=dma&mode=bm25").get_data(as_text=True)
        assert 'class="off"' in page and "Hybrid (full-text + Vector)" in page
        assert 'href="/search?q=dma&amp;mode=hybrid"' not in page
        assert "Lexical matching needs the query's terms" in page
        assert "No results for “dma”" in page

    def test_no_results_on_lexical_offers_hybrid_when_vectors_exist(
        self, monkeypatch
    ):
        views = _quiet_capabilities(monkeypatch)
        results = SearchResults("dma", "bm25", 0, [], {})
        monkeypatch.setattr(views, "_service", _Service(results))
        app = _app()
        page = app.test_client().get("/search?q=dma&mode=bm25").get_data(as_text=True)
        assert "Or switch to" in page
        assert "Hybrid (full-text + term vectors)</a> retrieval" in page


@requires_flask
class TestOtherPages:
    def test_home_names_the_product_and_the_vectors_it_runs(self, monkeypatch):
        views = _quiet_capabilities(monkeypatch)
        monkeypatch.setattr(views.backend, "index_exists", lambda config: True)
        monkeypatch.setattr(views.backend, "count", lambda config: 342)
        app = _app()
        page = app.test_client().get("/").get_data(as_text=True)
        assert "Anna · R.A.I.N. DataMatrix Engine" in page
        assert "R.A.I.N. DataMatrix Engine</small>" in page
        assert "Vers3Dynamics Engineering Intelligence" not in page
        assert "<b>342</b> documents indexed" in page
        assert "vectors: <b>term-hashing (not semantic)</b>" in page
        assert "Citations for every document" in page

    def test_home_survives_an_unreachable_index(self, monkeypatch):
        views = _quiet_capabilities(monkeypatch)

        def down(config):
            raise RuntimeError("refused")

        monkeypatch.setattr(views.backend, "index_exists", down)
        app = _app()
        response = app.test_client().get("/")
        assert response.status_code == 200
        assert "<b>offline</b> index" in response.get_data(as_text=True)

    def test_document_page_cites_the_document(self, monkeypatch):
        views = _quiet_capabilities(monkeypatch)
        doc = _doc("8bbe0f7de8c6440b")
        monkeypatch.setattr(views.backend, "get_document", lambda doc_id: doc)
        monkeypatch.setattr(views, "_service", _Service(SearchResults("", "", 0, [], {})))
        app = _app()
        page = app.test_client().get("/document/" + doc.id).get_data(as_text=True)
        assert '<div class="cite panel" id="cite">' in page
        assert "@article{devlin2024dma," in page
        assert "annaid = {arxiv:8bbe0f7de8c6440b}" in page
        assert "M. Devlin (2024). DMA paper 8bbe0f7de8c6440b. arXiv." in page
        assert "/citation?format=bibtex" in page
        assert "Download .bib" in page
        assert "No related documents share" in page

    def test_document_page_reports_a_related_outage(self, monkeypatch):
        views = _quiet_capabilities(monkeypatch)
        doc = _doc()
        monkeypatch.setattr(views.backend, "get_document", lambda doc_id: doc)
        monkeypatch.setattr(views, "_service", _Service(error=RuntimeError("down")))
        app = _app()
        page = app.test_client().get("/document/" + doc.id).get_data(as_text=True)
        assert "Related documents unavailable" in page
        assert "No related documents" not in page

    def test_about_describes_one_product_with_two_faces(self, monkeypatch):
        _quiet_capabilities(monkeypatch)
        app = _app()
        page = app.test_client().get("/about").get_data(as_text=True)
        assert "<h1>Anna · R.A.I.N. DataMatrix Engine</h1>" in page
        assert "research workbench" in page
        assert "/citation" in page and "/evidence/verify" in page
        assert "term-hashing fallback" in page
        assert 'href="/deck/"' in page and 'href="/blueprint/"' in page
