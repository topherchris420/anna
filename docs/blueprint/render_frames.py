"""Render the blueprint animation to still frames and a looping preview.

Serves this directory over a local HTTP server, loads the animation in
headless Chromium, and seeks the timeline with the same
``data-om-seek-to-time-frame`` event the piece exposes for video export —
so every frame is rendered at an exact timestamp rather than captured off a
running clock.

Two outputs:

- ``frames/NN.png`` — one settled still per scene, for the README.
- ``preview.gif``   — a short loop of the whole sequence (README video).

Usage:
    pip install playwright
    python docs/blueprint/render_frames.py

Uses the Playwright-managed Chromium if installed; otherwise set
CHROMIUM_PATH to a Chromium/Chrome executable (Claude Code web sessions
pre-install one at /opt/pw-browsers/chromium).
"""
import http.server
import os
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
FRAMES = ROOT / "frames"
WIDTH, HEIGHT = 1920, 1080

# One settled still per scene: each scene's start plus enough time for its
# content to finish arriving (the animation holds the settled state until the
# camera moves on).
SCENE_STILLS = [
    (1, 4.6, "System initialization"),
    (2, 11.4, "Knowledge sources"),
    (3, 19.4, "Modular ingestion"),
    (4, 26.4, "Dual index construction"),
    (5, 34.4, "Query split"),
    (6, 41.4, "Reciprocal Rank Fusion"),
    (7, 49.4, "Citation-first answer"),
    (8, 55.0, "Graceful degradation"),
    (9, 60.4, "Complete exploded view"),
]

# The README preview. 12fps reads fine — this is drafting motion, not video.
# The GIF keeps every third frame, so the full 62 seconds plays in about 21 at
# 3x speed and lands near 7 MB, which a README can carry inline; the MP4 keeps
# every frame at real time for anyone who wants to actually watch it.
PREVIEW_FPS = 12
PREVIEW_DURATION = 62.0
STILL_WIDTH = 1280
GIF_WIDTH = 720
GIF_FRAME_STEP = 3
MP4_WIDTH = 1280


def _ffmpeg() -> str:
    """An ffmpeg that can write GIF and H.264.

    Playwright ships its own ffmpeg, but it is a `--disable-everything` build
    for webm capture: no gif encoder, no libx264, and it cannot even demux a
    numbered PNG sequence. Prefer an explicit path, then imageio-ffmpeg's
    full static build, then whatever is on PATH.
    """
    explicit = os.environ.get("FFMPEG_PATH")
    if explicit:
        return explicit
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg") or ""


def _serve(directory: Path) -> int:
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(directory), **kw
    )
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


def _seek(page, seconds: float) -> None:
    """Render the document at ``seconds`` and wait for the style recalc.

    The whole timeline is CSS animations driven by one custom property, so a
    seek is a single style recalculation — but the screenshot must not race
    it, hence the explicit frame wait.
    """
    page.evaluate(
        """(t) => {
            const root = document.getElementById('rain-root');
            root.dispatchEvent(new CustomEvent('data-om-seek-to-time-frame',
              { detail: { time: t } }));
        }""",
        seconds,
    )
    page.evaluate(
        "() => new Promise(r => requestAnimationFrame(() => "
        "requestAnimationFrame(r)))"
    )


def main() -> int:
    chromium = os.environ.get("CHROMIUM_PATH") or (
        "/opt/pw-browsers/chromium"
        if Path("/opt/pw-browsers/chromium").exists()
        else None
    )
    port = _serve(ROOT)
    FRAMES.mkdir(exist_ok=True)
    raw = ROOT / ".preview-frames"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium)
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait_for_function("() => document.fonts.status === 'loaded'")
        # Sound is off by default; pause the clock so only explicit seeks move it.
        page.evaluate("() => document.getElementById('btn-play').click()")

        stills = ROOT / ".stills"
        if stills.exists():
            shutil.rmtree(stills)
        stills.mkdir()
        for number, seconds, label in SCENE_STILLS:
            _seek(page, seconds)
            page.screenshot(path=str(stills / f"{number:02d}.png"))
            print(f"  {number:02d}.png  t={seconds:>5.1f}s  {label}")

        total = int(PREVIEW_DURATION * PREVIEW_FPS)
        print(f"rendering {total} preview frames at {PREVIEW_FPS}fps…")
        for i in range(total):
            _seek(page, i / PREVIEW_FPS)
            page.screenshot(path=str(raw / f"{i:05d}.png"))
        browser.close()

    ffmpeg = _ffmpeg()
    if not ffmpeg:
        print("no ffmpeg with gif/h264 support — stills written, preview skipped")
        print("  pip install imageio-ffmpeg, or set FFMPEG_PATH")
        shutil.rmtree(raw)
        return 0

    def run(args):
        subprocess.run([ffmpeg, "-y", *args], check=True, capture_output=True)

    for still in sorted(stills.glob("*.png")):
        run(["-i", str(still), "-vf", f"scale={STILL_WIDTH}:-2",
             str(FRAMES / still.name)])
    shutil.rmtree(stills)

    frames_in = ["-framerate", str(PREVIEW_FPS), "-i", str(raw / "%05d.png")]
    keep = f"select=not(mod(n\\,{GIF_FRAME_STEP}))"
    scale = f"scale={GIF_WIDTH}:-1:flags=lanczos"
    # Two-pass palette: this is flat, high-contrast line art, and a naive
    # 256-color quantization turns the fine drafting grid and the small
    # readouts into mush. `stats_mode=diff` weights the palette toward what
    # actually changes between frames.
    palette = raw / "palette.png"
    run([*frames_in, "-vf", f"{keep},{scale},palettegen=stats_mode=diff",
         "-vsync", "0", str(palette)])
    run([*frames_in, "-i", str(palette), "-lavfi",
         f"[0:v]{keep},{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3",
         "-vsync", "0", "-loop", "0", str(ROOT / "preview.gif")])
    run([*frames_in, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "21",
         "-vf", f"scale={MP4_WIDTH}:-2", str(ROOT / "preview.mp4")])

    shutil.rmtree(raw)
    print(f"wrote {ROOT / 'preview.gif'} and {ROOT / 'preview.mp4'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
