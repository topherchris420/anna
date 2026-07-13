"""The /deck route serves the self-contained project deck. Skipped when
Flask is not installed."""

import pytest

pytest.importorskip("flask")

from flask import Flask  # noqa: E402

from allthethings.engine_web.views import _DECK_DIR, engine_web  # noqa: E402


def _client():
    app = Flask(__name__)
    app.register_blueprint(engine_web)
    return app.test_client()


class TestDeck:
    def test_deck_dir_is_in_the_repo(self):
        assert (_DECK_DIR / "index.html").is_file()

    def test_index_served_at_trailing_slash(self):
        resp = _client().get("/deck/")
        assert resp.status_code == 200
        assert b"deck-stage" in resp.data

    def test_bare_path_redirects_so_relative_assets_resolve(self):
        resp = _client().get("/deck")
        assert resp.status_code in (301, 308)
        assert resp.headers["Location"].endswith("/deck/")

    def test_assets_and_selfhosted_fonts_served(self):
        client = _client()
        for path in (
            "/deck/deck-stage.js",
            "/deck/_ds/organic-b5c8af1b-d438-4186-a607-4f630c701b41/styles.css",
            "/deck/_ds/organic-b5c8af1b-d438-4186-a607-4f630c701b41/fonts/fonts.css",
            "/deck/_ds/organic-b5c8af1b-d438-4186-a607-4f630c701b41/fonts/caprasimo-latin.woff2",
        ):
            assert client.get(path).status_code == 200, path

    def test_deck_makes_no_outbound_font_calls(self):
        css = _client().get(
            "/deck/_ds/organic-b5c8af1b-d438-4186-a607-4f630c701b41/styles.css"
        )
        assert b"fonts.googleapis.com" not in css.data

    def test_missing_file_is_404(self):
        assert _client().get("/deck/nope.js").status_code == 404

    def test_path_traversal_is_blocked(self):
        assert _client().get("/deck/../../README.md").status_code == 404
