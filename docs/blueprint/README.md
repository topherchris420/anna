# The blueprint animation

A self-contained, 62-second cinematic explanation of how the R.A.I.N. DataMatrix
Engine turns technical sources into a citable answer — nine scenes over one
continuous 4000×2100 drafting canvas — which then becomes an interactive system
map you can inspect.

Open [`index.html`](index.html) in any browser, or visit
[`/blueprint`](http://localhost:8000/blueprint) on a running instance. Like the
[deck](../deck), it vendors its own fonts and makes **no outbound requests**, so
it works air-gapped — the same promise the platform makes.

```bash
python -m http.server -d docs/blueprint    # or just open index.html
```

## Controls

| Control | Does |
|---|---|
| `PLAY` / `PAUSE` | Toggles the clock. Keyboard: <kbd>Space</kbd> or <kbd>K</kbd>. |
| `RESTART` | Back to `t = 0` and play. Keyboard: <kbd>R</kbd>. |
| Timeline | Click or drag to scrub. Focus it and use <kbd>←</kbd>/<kbd>→</kbd> (1s), <kbd>PgUp</kbd>/<kbd>PgDn</kbd> (6s), <kbd>Home</kbd>/<kbd>End</kbd>. |
| `SOUND OFF` / `ON` | WebAudio blips on scene changes and presses. Off by default; no audio assets, and no `AudioContext` is constructed until you switch it on. |
| `EXPLORE SYSTEM` | Pauses at the settled blueprint and enables the system map. Keyboard: <kbd>E</kbd>. |

In Explore mode, `BM25` / `SEMANTIC` / `HYBRID` dim the canvas to one retrieval
route, hovering any module lights its complete data path, and clicking a source
terminal traces that source's journey from ingestion to citation.

Query-string props (the design tool's controls panel, minus the tool):

| Parameter | Default | Effect |
|---|---|---|
| `?speed=` | `1` | Playback rate, clamped to 0.5–1.6. |
| `?grid=0` | on | Hides the drafting grid. |
| `?scan=0` | on | Disables the ambient scan sweep. |
| `?explore=1` | off | Boots straight into Explore mode. |

## How it works

There is no animation library and no per-frame JavaScript. The entire timeline
is CSS animations driven by **one custom property**, `--tt` (timeline time, in
seconds), set on the scaling wrapper. Every animated element declares:

```css
animation: k-in .45s cubic-bezier(.4,0,.2,1) both paused;
animation-delay: calc((6.5 - var(--tt)) * 1s);
```

The animation is permanently `paused`, so it never advances on its own. Once
`--tt` passes the element's start time the delay goes negative, which makes the
browser render that animation at exactly that offset. Setting `--tt` therefore
renders the whole document at a timestamp, synchronously, in one style
recalculation.

That single decision is what makes the piece scrubbable, pausable and
exportable in about thirty lines of JavaScript:

- `setTT(t)` is the only state mutation.
- **Play** is a `requestAnimationFrame` loop doing `setTT(tt + dt * speed)`,
  wrapping to 0 at 62s.
- **Pause** is simply not calling `setTT`.
- **Reduced motion** (`prefers-reduced-motion: reduce`) swaps the continuous
  clock for a scene stepper: it holds ~3.2s on each scene's settled state and
  cuts to the next. Every stage is still revealed, as controlled fades, and all
  text stays selectable throughout.

The only unpaused animations are ambient loops — the blinking caret, the pulsing
status LEDs, the scan line. They are decoration, not timeline. Keep it that way:
converting any of this to per-element JavaScript tweens would cost scrubbing,
export and reduced-motion support all at once. `test/engine/test_blueprint.py`
guards the invariant.

The camera is a single `@keyframes k-cam` on a 4000×2100 layer; `#rain-scale` is
a fixed 1920×1080 box that a `ResizeObserver` scales to fit, so everything
inside is authored at absolute 1920×1080 coordinates.

## Scenes

| # | Start | Name |
|---|---|---|
| 01 | 0s | System initialization |
| 02 | 6s | Knowledge sources — 12 source plugins feed the ingestion trunk |
| 03 | 13s | Modular ingestion — five stages normalize every source into one `Document` |
| 04 | 21s | Dual index construction — lexical strips and 384-dim vectors into `engineering_docs` |
| 05 | 28s | Query split — one hybrid query fans out to BM25 and kNN |
| 06 | 36s | Reciprocal Rank Fusion — the two rankings fuse at `k = 60` |
| 07 | 43s | Citation-first answer — assembled from retrieved evidence |
| 08 | 51s | Graceful degradation — four fallbacks preserve the core search path |
| 09 | 56s | Complete exploded view |

Every engine value on screen is verbatim from this repository: the index name
`engineering_docs`, `all-MiniLM-L6-v2` local embeddings at 384 dimensions,
`multi_match` over `title^3 abstract^2 search_text authors equations`, RRF
`score(d) = Σᵢ weightᵢ / (k + rankᵢ(d))` with `k = 60`, and
`GET /api/v1/search?q=…&mode=hybrid`. The three citations in scene 07 are real
documents. Nothing in the piece implies unsupported generation — the framing is
that the answer is assembled from retrieved evidence.

## Rendering the stills and the preview

[`render_frames.py`](render_frames.py) drives the page in headless Chromium,
seeking with the same `data-om-seek-to-time-frame` event the piece exposes for
video export, so every frame is rendered at an exact timestamp rather than
captured off a running clock.

```bash
pip install playwright imageio-ffmpeg
python docs/blueprint/render_frames.py
```

It writes `frames/01.png` … `frames/09.png` (one settled still per scene),
`preview.gif` (the whole sequence at 3× for the README) and `preview.mp4` (real
time). Playwright's bundled ffmpeg is a `--disable-everything` build with no GIF
or H.264 encoder, which is why `imageio-ffmpeg` is in that install line.

## Provenance

Designed in Claude Design as `RAIN DataMatrix Blueprint.dc.html` and ported here
by hand. The authored source ran inside a design-tool harness (`<x-dc>`,
`<helmet>`, `support.js`) whose bundled build pulls React and Google Fonts off
CDNs; this port drops the harness, vendors both typefaces locally, and keeps the
markup, timing table and clock architecture intact. Fonts are Archivo and IBM
Plex Mono, both under the SIL Open Font License 1.1.
