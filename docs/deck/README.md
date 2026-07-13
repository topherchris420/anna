# The Anna deck

An eight-slide overview of the project — *Open infrastructure for knowledge* —
built as a self-contained HTML presentation on the Organic design system
(Claude Design handoff, implemented here for real).

## Presenting

- Open [`index.html`](index.html) in any browser. Navigate with ←/→, Space,
  PgUp/PgDn, Home/End, or the number keys; press `R` to reset.
- Every running Anna instance also serves the deck at
  [`/deck`](http://localhost:8000/deck).
- **PDF export:** the deck lays out one slide per page under `@media print`,
  so the browser's Print → Save as PDF produces a clean PDF with no setup.
- Press `B` (or add `?baselines` to the URL) to review the baseline grid.

The deck is fully self-contained: Caprasimo and Figtree are vendored as woff2
files in [`_ds/…/fonts/`](_ds/organic-b5c8af1b-d438-4186-a607-4f630c701b41/fonts/fonts.css)
(both are OFL-licensed), so it renders identically offline and on air-gapped
deployments — the same promise the platform itself makes.

## Regenerating the slide images

[`slides/`](slides/) holds a 1280×720 PNG of each slide for the top-level
README. After editing the deck, re-render them with the pre-installed
Chromium + Playwright:

```bash
pip install playwright
python docs/deck/render_slides.py            # writes slides/01.png … 08.png
```

The script serves this directory locally, steps through the slides with the
component's number-key navigation, strips the on-screen controls, and
screenshots each slide once fonts and the entrance animation have settled.
