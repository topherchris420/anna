"""Render every slide of the deck to a PNG for the top-level README.

Serves this directory over a local HTTP server, loads the deck in headless
Chromium, steps through the slides with deck-stage's number-key navigation,
suppresses the thumbnail rail and controls overlay, waits for fonts and the
entrance animation, and screenshots each slide at 1280x720 into slides/.

Usage:
    pip install playwright
    python docs/deck/render_slides.py

Uses the Playwright-managed Chromium if installed; otherwise set
CHROMIUM_PATH to a Chromium/Chrome executable (Claude Code web sessions
pre-install one at /opt/pw-browsers/chromium).
"""
import http.server
import os
import socketserver
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

DECK = Path(__file__).resolve().parent
OUT = DECK / "slides"
N_SLIDES = 8

handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(DECK), **kw)
socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

chromium = os.environ.get("CHROMIUM_PATH") or (
    "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
)

OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=chromium)
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto(f"http://127.0.0.1:{port}/index.html?_snthumb=1")  # _snthumb suppresses the rail
    page.wait_for_selector("deck-stage")
    page.evaluate(
        """async () => {
            await customElements.whenDefined('deck-stage');
            await document.fonts.ready;
            const stage = document.querySelector('deck-stage');
            // no chrome in the captures: drop the controls overlay + rail
            for (const sel of ['.overlay', '.rail', '.rail-resize'])
                stage.shadowRoot.querySelector(sel)?.remove();
        }"""
    )
    for i in range(1, N_SLIDES + 1):
        page.keyboard.press(str(i))  # deck-stage number-key navigation
        page.wait_for_timeout(900)  # entrance animation (0.45s) + settle
        page.screenshot(path=str(OUT / f"{i:02d}.png"))
        print("rendered", i)
    browser.close()
httpd.shutdown()
print("done ->", OUT)
