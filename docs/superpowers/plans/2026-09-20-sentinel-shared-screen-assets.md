# SENTINEL Shared-Screen Assets Implementation Plan

> **For agentic workers:** Execute task by task in this session. The user explicitly requested immediate generation after approving the written specification; no additional approval round is required.

**Goal:** Deliver real vector/raster brand exports, UI graphics, tokens, component specifications and a local interactive review sheet.

**Architecture:** `assets/sentinel-ui/` is a self-contained static kit. Preparation scripts under `scripts/ui_assets/` produce assets from the supplied logo and versioned fixture. The review sheet loads local files only; no runtime drone/coordinator code changes.

**Tech Stack:** SVG/CSS/HTML/JavaScript, Python/Pillow/VTracer, Sharp export, Playwright validation.

**Spec:** `docs/superpowers/specs/2026-09-20-sentinel-shared-screen-asset-kit-design.md`.

## Global Constraints

- Symbol alone in header; source JPEG defines the identity; wordmark must use vector paths.
- Transparent light/dark masters and separately identified compact variants.
- Exact fixture zone IDs, polygons, statuses and route-only membership.
- Source, freshness, video health, survey status and emergency remain separate concepts.
- No API credentials, remote dependencies or live-data claims in the review page.
- Asset delivery does not complete NiceGUI integration or WP2.

## Review Focus

- Tiny icons: compact marks at 16/24/32 pixels and faithful mark at 40 pixels.
- Contrast: calculate body text, badge labels and focus visibility.
- Missing marker/evidence: explicit unavailable values and no invented confidence/pose.
- SVG interaction: keyboard selection preserves survey status and exact geometry.
- Packaging: local-file review works with correct relative links and verified hashes.

## Task 1 — Brand masters and tokens

Files: `scripts/ui_assets/build_brand.py`, `assets/sentinel-ui/brand/`, `assets/sentinel-ui/tokens/`.

- [x] Copy source unchanged; record SHA-256; sample solid interior red.
- [x] Trace separate ink/red masks into SVG paths. Preserve full composition; extract symbol with a separate viewBox.
- [x] Create faithful light/dark and compact variants. Compare rendered contours against the source.
- [x] Write token JSON and derived CSS, with measured contrast pairs.

Check: path-only brand SVGs, no white backing, outlined lettering, correct sampled color, visual comparison at equal scale.

## Task 2 — Graphics and component specifications

Files: `scripts/ui_assets/build_graphics.py`, `assets/sentinel-ui/{icons,schematics,placeholders,overlays}/`, `assets/sentinel-ui/component-specs.md`.

- [x] Create 14 original 24×24 `currentColor` icons.
- [x] Generate SVG IDs/polygons from the fixture; include state CSS and route-only file.
- [x] Create four placeholders, degraded video banner, marker box/label and pose presentation.
- [x] Specify read-model fields, actions, unavailable data, variants and accessibility for every component.

Check: exact fixture comparison, all five statuses, separate freshness, all eight preflight checks, no invented backend capabilities.

## Task 3 — Exports and review sheet

Files: `scripts/ui_assets/export_assets.cjs`, `assets/sentinel-ui/{review.html,review.css,review.js,README.md}`, brand exports.

- [x] Rasterize masters with Sharp and assemble 16/32/48 ICO images.
- [x] Build local review using actual files, source/trace comparison, light/dark backgrounds and small-size previews.
- [x] Add clearly illustrative schematic, freshness, marker and emergency-state interactions; support keyboard selection.
- [x] Show component states and an assembled 1440×900 composition with symbol-only header.

Check: Playwright on localhost and direct file URL; missing resources/console errors; keyboard and toggle behavior; desktop screenshots and visual inspection.

## Task 4 — Validation and packaging

Files: `scripts/ui_assets/check_assets.py`, `assets/sentinel-ui/{manifest.json,validation.json}`, `artifacts/ui-assets/`.

- [x] Check SVG paths, fixture geometry, alpha, export dimensions, ICO sizes, token agreement and contrast.
- [x] Record stable asset IDs, intended use, provenance, variants and SHA-256 hashes.
- [x] Check local links and absence of credentials, expiring URLs and machine-specific dependencies.
- [x] Package the complete kit as ZIP, verify CRCs and open the review page.

Commands:

```sh
python3 scripts/ui_assets/check_assets.py
node scripts/ui_assets/export_assets.cjs
python3 -m http.server 8769 --bind 127.0.0.1 --directory assets/sentinel-ui
```

## Execution record

- Immediate execution authorized by user. No new paid image generations are needed for these deterministic vector assets.
- Use targeted asset invariants and browser checks instead of application regression tests; no app behavior changes.
- Reconstructed brand masters remain reviewable candidates until user visual acceptance.

- Completed: 79 manifest entries; 413 asset checks and 32 browser checks passed. The local review works over HTTP and directly from disk.
- Packaged `artifacts/ui-assets/sentinel-ui-kit.zip` with 81 files; ZIP CRCs and all manifest hashes verified. This local delivery archive and screenshots remain under the existing ignored artifacts directory.
- Restored iCloud-offloaded files after storage became available and reran verification before PR publication.
