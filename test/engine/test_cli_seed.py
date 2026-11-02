"""flask engine seed-corpus: a real corpus spread over every category."""

import types

import pytest

pytest.importorskip("flask")

from flask import Flask  # noqa: E402


def _seed(monkeypatch, *args, documents=25, new=lambda limit, start: limit):
    """Run the command against a fake index and arXiv.

    ``new(limit, start)`` says how many of a request's results the index did
    not hold yet: papers cross-listed in two categories come back twice.
    """
    import engine.backend as backend
    import engine.ingest as ingest
    from allthethings.engine_cli import views

    state = {"count": documents}
    calls = []
    monkeypatch.setattr(backend, "create_index", lambda *a, **k: None)
    monkeypatch.setattr(backend, "count", lambda *a, **k: state["count"])

    class _Pipeline:
        def run(self, source, *, query, limit, start, batch_size):
            assert source == "arxiv"
            calls.append((query, limit, start))
            state["count"] += new(limit, start)
            return types.SimpleNamespace(indexed=limit, errors=[])

    monkeypatch.setattr(ingest, "IngestionPipeline", _Pipeline)
    app = Flask(__name__)
    app.register_blueprint(views.engine_cli)
    result = app.test_cli_runner().invoke(args=["engine", "seed-corpus", *args])
    assert result.exit_code == 0, result.output
    return calls, views._SEED_QUERIES


def test_the_target_is_shared_between_every_category(monkeypatch):
    # 275 documents still wanted over 12 categories: 23 from each. Taking 50
    # from each in turn stopped after six and left the rest unindexed.
    calls, categories = _seed(monkeypatch)
    assert calls == [(query, 23, 0) for query in categories]


def test_a_cross_listing_shortfall_is_topped_up_from_further_pages(monkeypatch):
    # Two of every first-page 23 are already indexed (cross-listed), so the
    # even pass ends at 277; the top-up asks each category for two more,
    # starting past the 23 it already gave, until the target is met.
    calls, categories = _seed(
        monkeypatch, new=lambda limit, start: limit - 2 if start == 0 else limit
    )
    assert calls[:12] == [(query, 23, 0) for query in categories]
    assert calls[12:] == [(query, 2, 23) for query in categories]


def test_a_pass_that_adds_nothing_ends_the_seeding(monkeypatch):
    calls, _ = _seed(
        monkeypatch, new=lambda limit, start: limit - 2 if start == 0 else 0
    )
    assert len(calls) == 24  # the even pass and one fruitless top-up


def test_per_query_caps_what_each_category_gives(monkeypatch):
    calls, categories = _seed(
        monkeypatch, "--target", "1000", "--per-query", "10"
    )
    assert calls == [(query, 10, 0) for query in categories]


def test_a_corpus_at_its_target_is_left_alone(monkeypatch):
    calls, _ = _seed(monkeypatch, documents=300)
    assert calls == []


def test_force_takes_per_query_from_every_category(monkeypatch):
    calls, categories = _seed(monkeypatch, "--force", documents=300)
    assert calls == [(query, 50, 0) for query in categories]
