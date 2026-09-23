# SENTINEL reusable interface kit

Open `review.html` directly in a browser, or serve this folder locally. Everything required to render the review is included. `console.html` is a separate shared-screen composition study; both pages use illustrative data and perform no network calls or drone commands.

## What's included

- Faithful reconstructed symbol/full-logo light and dark SVGs, an unmodified source JPEG, and separately simplified compact symbols.
- Transparent symbol PNGs at 32/64/128/256/512 pixels; complete-logo PNGs at 512/1024 pixels wide; compact PNGs at 16/24/32/48/180 pixels; SVG and multi-size ICO favicons.
- 14 original 24×24 currentColor interface icons.
- Exact six-zone and route-only SVGs, a copy of the source fixture, five persisted-state examples, and independent selection/focus styling.
- Camera/evidence placeholders and separate marker/degraded-video/pose presentation assets.
- JSON/CSS design tokens, component specifications, and an illustrative state inventory.
- Local interactive review, composition study, provenance manifest and validation report.

These are usable asset files, not flattened dashboard images. They are also not a completed NiceGUI integration. Read `component-specs.md` for the data boundaries and operational-state requirements.

## Brand usage

The supplied `SENTINEL LOGO.jpg` is preserved under `brand/source/`. The masters are reconstructed vector candidates, not original source vectors. `brand/provenance.json` records the source SHA-256, exact sampled red, tracing method and intentional differences.

- `symbol-dark.svg`: light artwork on a dark surface. Use at 40 CSS pixels in the console header, with 8px clear space. Accessible name: SENTINEL. No adjacent wordmark in the header.
- `symbol-light.svg`: dark artwork on a light surface.
- `logo-dark.svg` / `logo-light.svg`: complete mark and outlined wordmark for presentations/opening screens.
- `symbol-compact-*.svg`: intentionally simplified for 16–32 pixel use. Retains eye, circle and red center while dropping fine aperture detail.
- `source-aligned-reconstruction.svg`: untrimmed 1024×1024 comparison master, aligned to the original source.

The red center is `#A40722`, sampled from the source interior; it never means recording, connection or emergency. Emergency uses a separate semantic color. JPEG noise and antialiased edges have been replaced by spline contours and solid fills; compare source and reconstruction in the review before accepting the brand master.

## Icons

The 14 icons are original artwork made for this kit. They share a 24×24 viewBox, 2px strokes, rounded caps/joins and `currentColor`; no third-party icon license is required. Inline SVG inherits its parent color. An external SVG used as `<img>` does not inherit the parent document's `currentColor`; inline it for dynamic tinting.

When an icon accompanies visible text, give its SVG `aria-hidden="true"`. For icon-only controls, provide a labelled native button. Do not communicate state using color alone.

## Schematic

`schematics/source-fixture.json` is copied from the versioned WP0 fixture. The SVG polygon coordinates and group IDs match it exactly. Right zones occupy the upper row and left zones the lower row. Front is left; back is right. The source's wider back polygons remain wider.

Inline the SVG for interactive use. Set each `.survey-zone` group's `data-status` to one of UNSURVEYED, SURVEYING, SURVEYED, HAZARD_PENDING, BLOCKED. Update its visible status text and accessible label together. `.is-selected` and keyboard focus are independent of survey state. `STALE` is a separate badge, not a polygon status.

The standalone group elements provide IDs, labels and keyboard targets. A consumer must supply click/Enter/Space handlers; the review demonstrates them in `review.js`. The console study deliberately makes its diagram static. Route-only includes exactly the two middle zones and visibly says `route-only, not visually verified`.

## States and data

`component-states.json` is an illustrative inventory, not a set of coordinator wire fixtures. The review page's controls preview states locally. `component-specs.md` maps actual inspected contract fields and identifies integration gaps: live telemetry, live marker pose, and the WP2/legal-action enum discrepancy need an adopted coordinator mapping before runtime integration. This kit adds no invented backend fields or actions.

No camera photograph or marker graphic in this package is real evidence. Placeholders explicitly indicate absent data. The marker label/box are treatment examples, not printable physical detection markers. Perception scores must retain their provider semantics.

## Tokens and accessibility

`tokens/tokens.json` is authoritative; `tokens/tokens.css` is derived. Use the supplied semantic tokens, not colors sampled from a screenshot. Body text is 16px, secondary metadata 14px; heading and mission scales are 24px and 32px. Brand lettering is outlined artwork and independent of interface fonts.

Text and status/focus color pairs are checked in `validation.json`. Bright action fills use dark text. Selection has a visible outline; focus adds a dashed treatment in the schematic. Reduced-motion preferences disable smooth scrolling. The review includes a skip link, real labels and polite/urgent live regions.

## Rebuilding

The kit renders without build tools or installed dependencies. Optional preparation scripts are kept in the repository under `scripts/ui_assets/`; run them from the repository root with Python 3.12, Pillow, numpy, VTracer 0.6.15, and Node with Sharp/Playwright available:

```sh
python3 scripts/ui_assets/build_brand.py
python3 scripts/ui_assets/build_graphics.py
python3 scripts/ui_assets/build_review.py
node scripts/ui_assets/export_assets.cjs
python3 scripts/ui_assets/check_assets.py
python3 -m http.server 8769 --bind 127.0.0.1 --directory assets/sentinel-ui
node scripts/ui_assets/verify_browser.cjs http://127.0.0.1:8769
```

`build_brand.py --source /path/to/source.jpg` is used only to supply the original JPEG on the first build; subsequent builds use the included source. The source geometry/sample coordinates are calibrated to this supplied 1024×1024 logo, not arbitrary replacement artwork.

VTracer is an optional raster-to-vector preparation dependency, not part of the deployed UI. No additional paid image-generation calls were made for this kit.

## Verification and handoff

`manifest.json` records each deliverable's purpose, source, variants and SHA-256. It excludes itself and `validation.json` to avoid recursive hashes. The validator checks SVG structure, exact fixture geometry, icon consistency, transparency/export sizes, ICO entries, token agreement, contrast, local links and sensitive-data patterns. `validation.json` also records source-mask comparison, which measures contour overlap rather than artistic approval.

Browser checks exercise keyboard selection, state/freshness independence, route-only membership, marker/preflight/emergency previews, direct-file loading and the five console regions at 1440×900. Screenshots are review evidence, not WP2 smoke-test evidence or proof of flight-system behavior.

The next stage is the actual NiceGUI components and coordinator/fake-client scenario fixtures. Preserve the immutable approval, run-scoped evidence, emergency and idempotency rules from WP2 when implementing them.
