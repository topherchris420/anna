"""The blueprint animation: its self-contained contract, and the route that
serves it. Only the route tests need Flask — what the page *is* stays under
test in environments without the web stack."""

import re
from pathlib import Path

import pytest

BLUEPRINT_DIR = Path(__file__).resolve().parents[2] / "docs" / "blueprint"

try:
    from flask import Flask
except ImportError:  # pragma: no cover - exercised by the skip itself
    Flask = None

requires_flask = pytest.mark.skipif(Flask is None, reason="flask not installed")


def _client():
    from allthethings.engine_web.views import engine_web

    app = Flask(__name__)
    app.register_blueprint(engine_web)
    return app.test_client()


def _index() -> str:
    return (BLUEPRINT_DIR / "index.html").read_text(encoding="utf-8")


@requires_flask
class TestBlueprintRoute:
    def test_route_serves_the_directory_in_the_repo(self):
        from allthethings.engine_web.views import _BLUEPRINT_DIR

        assert _BLUEPRINT_DIR == BLUEPRINT_DIR
        assert (_BLUEPRINT_DIR / "index.html").is_file()

    def test_index_served_at_trailing_slash(self):
        resp = _client().get("/blueprint/")
        assert resp.status_code == 200
        assert b'id="rain-root"' in resp.data

    def test_bare_path_redirects_so_relative_assets_resolve(self):
        resp = _client().get("/blueprint")
        assert resp.status_code in (301, 308)
        assert resp.headers["Location"].endswith("/blueprint/")

    def test_selfhosted_fonts_are_served(self):
        client = _client()
        for path in (
            "/blueprint/fonts/fonts.css",
            "/blueprint/fonts/archivo-700-latin.woff2",
            "/blueprint/fonts/ibm-plex-mono-400-latin.woff2",
        ):
            assert client.get(path).status_code == 200, path

    def test_missing_file_is_404(self):
        assert _client().get("/blueprint/nope.js").status_code == 404

    def test_path_traversal_is_blocked(self):
        assert _client().get("/blueprint/../../README.md").status_code == 404


class TestSelfContained:
    """The air-gapped promise: the page may not reach the network at all."""

    def test_no_outbound_references(self):
        # SVG namespace URIs are declarations, not fetches; everything else
        # with a scheme would be a request the page must not make.
        outbound = [
            url
            for url in re.findall(r"https?://[^\s\"')]+", _index())
            if not url.startswith("http://www.w3.org/")
        ]
        assert outbound == []

    def test_fonts_are_local(self):
        css = (BLUEPRINT_DIR / "fonts" / "fonts.css").read_text()
        assert "fonts.gstatic.com" not in css
        assert "fonts.googleapis.com" not in css
        for face in re.findall(r"url\(([^)]+)\)", css):
            assert (BLUEPRINT_DIR / "fonts" / face).is_file(), face

    def test_design_tool_runtime_is_not_vendored(self):
        # The authored source ran inside a design-tool harness (<x-dc>,
        # support.js). The port must not carry it — it pulls React off a CDN.
        html = _index()
        for marker in ("<x-dc", "<helmet", "support.js", "unpkg.com"):
            assert marker not in html, marker


class TestAnimationContract:
    """The clock architecture is the piece — guard its load-bearing parts."""

    def test_timeline_is_driven_by_one_custom_property(self):
        html = _index()
        # Every timeline animation is paused and positioned by --tt; a stray
        # unpaused animation would advance on its own and desync from the
        # transport, scrubbing and video export.
        assert html.count("var(--tt)") > 100
        assert "setProperty(\"--tt\"" in html

    def test_ambient_loops_are_the_only_unpaused_animations(self):
        # Decoration (blinking caret, pulsing LEDs) may run free; anything
        # keyed to a scene must not.
        unpaused = [
            decl
            for decl in re.findall(r"animation:\s*k-[\w-]+[^;\"]*", _index())
            if "paused" not in decl
        ]
        assert all(
            re.search(r"k-(blink|pulse|scan)", decl) or "infinite" in decl
            for decl in unpaused
        ), unpaused

    def test_scene_count_matches_the_timeline(self):
        html = _index()
        scenes = re.findall(r"\[(\d+), \"(\d\d) · ", html)
        assert [n for _, n in scenes] == [
            "01", "02", "03", "04", "05", "06", "07", "08", "09"
        ]
        # Scene starts are ascending and inside the 62-second clock.
        starts = [int(t) for t, _ in scenes]
        assert starts == sorted(starts) and starts[0] == 0 and starts[-1] < 62
        assert "var DUR = 62;" in html

    def test_video_export_hooks_are_present(self):
        html = _index()
        assert 'data-om-exportable-video-with-duration-secs="62"' in html
        assert "data-om-seek-to-time-frame" in html

    def test_reduced_motion_steps_scenes_instead_of_travelling(self):
        html = _index()
        assert "prefers-reduced-motion: reduce" in html
        assert "reduced" in html

    def test_content_claims_stay_grounded(self):
        # The piece states engine facts verbatim; a drifting index name or
        # fusion constant would make the animation lie about the product.
        html = _index()
        for fact in (
            "engineering_docs",
            "all-MiniLM-L6-v2",
            "384",
            "k = 60",
            "DIRECTORY METADATA ONLY",
        ):
            assert fact in html, fact
