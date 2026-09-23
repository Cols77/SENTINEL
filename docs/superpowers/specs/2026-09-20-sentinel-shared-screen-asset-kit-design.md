# SENTINEL shared-screen asset kit

Status: written specification awaiting user review.

Date: 2026-09-20. Design direction approved in conversation. This document specifies the first reusable asset kit; it does not authorize implementation or replace the WP2 coordinator boundary.

## 1. Intent and approved decisions

The demo uses one screen shared by the operator and audience. The operator must understand the run, review proposed actions and evidence, and access emergency stop. The audience must be able to follow progress. Success means separate usable assets and explicit component/state specifications, not additional flattened dashboard pictures.

The user supplied `/Users/alanphilip/Downloads/SENTINEL LOGO.jpg` as the authoritative brand reference. It contains an eye/aperture symbol within complete circular outlines, a red center, and a spaced SENTINEL wordmark. Preserve this version rather than combining it with the different pitch-slide mark or symbols invented in earlier dashboard generations.

Approved header treatment: **symbol alone**, light outlines on the dark surface, retaining the red center. Keep the full symbol-plus-wordmark logo for presentations and an optional opening screen. The logo is static branding; it never indicates recording, connection, freshness, or emergency state.

The source is a JPEG. Any SVG is a documented reconstruction, not an original vector supplied by the user. The source filename and checksum must be recorded with the delivered kit.

## 2. Scope and references

This first delivery includes brand assets, interface icons, schematic artwork, placeholders, overlay elements, design tokens, component specifications, an asset manifest, and a review sheet. It does not include working NiceGUI controls, coordinator integration, or generated images presented as real evidence.

The next stage is a separate implementation effort: build the specified NiceGUI components, connect coordinator/fake-client data, add scenario fixtures, and verify the assembled UI.

Project references:

- `docs/superpowers/specs/2026-09-16-sentinel-wp2-nicegui-human-surface.md`: layout, legal actions, evidence, freshness, emergency behavior, and acceptance requirements.
- `docs/superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md`: coordinator contracts.
- `docs/superpowers/specs/contracts/mountain-v0.fixture.json` and its schema: authoritative zone IDs, polygons, modes, and persisted statuses.
- Existing images under `docs/design-assets/2026-09-20-nano-banana-pro/`: composition references only. Their invented copy, simplified approval panels, and inaccurate geometry are not requirements.

If an adopted contract amendment changes these interfaces before implementation, reconcile the affected component specification against that contract. Proposal documents alone do not authorize new commands or states.

## 3. Delivery structure

The kit lives under `assets/sentinel-ui/`. Components consume files by stable asset name, not by generation URLs or a user's Downloads path.

| Path | Deliverable |
| --- | --- |
| `brand/source/sentinel-logo-original.jpg` | Unmodified user-supplied source, copied when the kit is implemented |
| `brand/*.svg` | Symbol and complete-logo masters, light/dark variants, compact symbol |
| `brand/png/` | Transparent raster exports |
| `brand/favicon/` | SVG, ICO, and PNG favicon assets |
| `icons/*.svg` | Consistent standalone interface icons |
| `schematics/` | Six-zone and route-only SVGs plus state styling |
| `placeholders/` | Camera/evidence placeholders |
| `overlays/` | Marker box, label, and pose-readout presentation examples |
| `tokens/tokens.json` | Authoritative visual token definitions |
| `tokens/tokens.css` | CSS custom properties derived from the token definitions |
| `component-specs.md` | Required data, presentation states, actions, and accessibility behavior |
| `manifest.json` | Asset IDs, paths, formats, intended use, variants, provenance, and checksums |
| `review.html` | Local review sheet using real asset files and labelled illustrative states |
| `README.md` | Usage, provenance, accessibility notes, and implementation handoff |

The review sheet demonstrates the kit and its visual states; it does not imply a live coordinator connection. It uses no remote assets or credentials. The implementation plan will define export tooling and package-resource wiring without creating a second source of truth for tokens.

## 4. Brand design

Create separate `symbol-light`, `symbol-dark`, `logo-light`, and `logo-dark` SVGs. Here `light` means artwork intended for a light background; `dark` means artwork intended for a dark background. Both use transparent backgrounds. Preserve the source mark's line arrangement, aperture relationships, red center, wordmark proportions, and letter spacing. The light variant uses dark ink; the dark variant uses light ink. White areas become negative space, not a white rectangular backing.

Use outlined paths for the master wordmark so the logo does not depend on an unidentified installed font. Do not substitute an approximate typed wordmark and describe it as an exact reconstruction.

Create a separately labelled compact symbol for small sizes. It may simplify fine aperture/ring intersections while preserving the eye, circular silhouette, and red center. Use the faithful symbol at 40 CSS pixels and larger; use the compact variant at 16, 24, and 32 pixels. Inspect both at their intended sizes. The header symbol is 40 pixels, with at least 8 pixels of surrounding clear space. It has accessible name `SENTINEL` and no implied status meaning.

Export symbol PNGs at 32, 64, 128, 256, and 512 pixels square, and complete-logo PNGs at 512 and 1024 pixels wide with the master aspect ratio preserved. Provide compact-symbol PNGs at 16, 32, 48, and 180 pixels; bundle 16/32/48 variants in the ICO. No distortion or white matte is permitted.

Compare the reconstructed light master with the supplied source at matching scale and document differences. Obtain visual review before treating the reconstruction as the accepted brand master. New image-generation outputs must not replace the source identity without explicit approval.

## 5. Tokens and iconography

Starting interface palette:

| Token | Value/use |
| --- | --- |
| `surface.canvas` | `#101820` |
| `surface.panel` | `#1B2733` |
| `border.default` | `#344653` |
| `text.primary` | `#F3F6F8` |
| `text.secondary` | `#A9BAC8` |
| `accent.primary` | `#47D7C8` |
| `status.warning` | `#F2BA55` |
| `status.emergency` | `#F06565` |

Measure the logo center's solid interior color from the supplied source during asset preparation and record that hex value as `brand.center`; it is independent of the emergency color. Exclude antialiased edge pixels when sampling. The screenshot palette is not a sampling source.

Use an 8-pixel spacing scale with 4-pixel subdivisions, 8-pixel panel radius, and 2-pixel standard icon strokes. Interface typography uses `system-ui, sans-serif`; machine identifiers and telemetry use `ui-monospace, monospace`. Use 16-pixel primary body text, 14-pixel secondary metadata, 24-pixel panel/section emphasis where space permits, and 32-pixel primary mission status. These are interface tokens; the traced brand wordmark remains artwork.

Provide 24×24 viewBox icons for drone, battery, connected, disconnected, camera, camera-off, marker, warning, emergency-stop, approve, reject, event, clock, and information. Use `currentColor`, consistent stroke weight, and no embedded text. Status color belongs to the component wrapper. Document source/license for any reused icon artwork.

Every state uses text and/or a meaningful icon alongside color. Check readable text contrast at 4.5:1 for normal text and 3:1 for large text; essential graphical boundaries and focus indicators target 3:1 against adjacent colors. Use dark text on bright teal/amber/red fills where required by measurement. Decorative panel borders need not carry state information.

## 6. Fixture-based schematic

Create `alley-six-zone.svg` directly from the fixture polygons in its 100×100 coordinate system. Preserve the exact IDs as SVG group identifiers:

- `LEFT_ALLEY_FRONT`, `LEFT_ALLEY_MIDDLE`, `LEFT_ALLEY_BACK`.
- `RIGHT_ALLEY_FRONT`, `RIGHT_ALLEY_MIDDLE`, `RIGHT_ALLEY_BACK`.

With SVG y increasing downward, right-alley zones occupy the upper row and left-alley zones the lower row. Front is at the left; back is at the right. Back polygons are wider than front/middle polygons. Do not recreate the geometry by tracing generated dashboard illustrations.

Persisted status styles are exactly `UNSURVEYED`, `SURVEYING`, `SURVEYED`, `HAZARD_PENDING`, and `BLOCKED`. Selection and keyboard focus use independent outlines; neither changes survey status. `STALE` is an independent freshness badge, not a sixth persisted zone state. Expose readable labels and status descriptions. The consuming component must provide keyboard selection and an accessible textual equivalent.

Create `alley-route-only.svg` using only `LEFT_ALLEY_MIDDLE` and `RIGHT_ALLEY_MIDDLE`, preserving their fixture coordinates. Label this mode `route-only, not visually verified`. Marker mode does not automatically switch to route-only when markers are missing.

Include a review example for every persisted state, selection/focus, freshness, and both modes. No route/path, breadcrumbs, capture points, GIS terrain, drone location, or revisit proposal is part of this delivery.

## 7. Placeholders and perception overlays

Provide scalable SVG placeholders for camera connecting, camera unavailable, evidence empty, and evidence unavailable. Provide a separate video-degraded banner treatment that can overlay an existing stream without inventing a replacement frame. Distinguish an absent current frame from a retained frame explicitly labelled stale.

Provide a marker box and label treatment plus a pose-readout specification. Bounding-box position, marker ID, and pose values must come from supported coordinator data. A presentation example can use visibly labelled sample values; it is not a valid physical marker or detection result. Do not generate printable detection markers as decorative artwork.

Confidence is optional and must retain the provider's score semantics. Never synthesize a percentage from an unrelated raw score. Unknown values use an em dash with a reason where available; missing marker data must not display invented pose information.

Any photographic demo evidence in the later integration stage must come from supplied/recorded material with explicit provenance. Generated camera illustrations remain labelled mockups and cannot be used as judged-run evidence.

## 8. Component specifications and data boundaries

Each component entry in `component-specs.md` must state its purpose, authoritative read-model fields, unavailable-data behavior, visual variants, legal actions, accessible label/focus behavior, and a labelled review example. Specifications cannot add backend fields or commands merely to support a visual concept.

| Component | Required coverage |
| --- | --- |
| Header | Symbol-only branding; run ID, state version, freshness, source badge, marker/route-only mode |
| Telemetry | Connection, battery, supplied flight mode and telemetry; unavailable/stale variants; units for numeric values |
| Operator panel | Chat and structured activity; idle, submitting, pending, error and disabled presentation; no private chain-of-thought |
| Mission/progress | Coordinator-declared current state and high-level events; awaiting approval, dispatch and failure presentations; no inferred flight progress |
| Approval | Immutable plan/lifecycle, approval binding, hashes, expiry, expected state version, ranking and evidence/provenance; Confirm/Reject available only through coordinator-declared legal actions |
| Evidence | Artifact/frame correlation, source/provider/model/revision details, score semantics and threshold; empty, missing, forbidden, failed and uncertain cases |
| Preflight | Exact eight checks; eligibility and failed-report presentation; gated force-new-run decision with required reason and explicit new-run semantics |
| Emergency | Permanently visible one-click stop, keyboard fallback, immediate ordinary-control disablement, pending acknowledgement and acknowledged latch; no inference that the drone landed |
| Events/warnings | Timestamped high-level events, low-battery/marker warnings when supplied, and source/freshness distinctions |

The eight preflight checks are `WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, and `EMERGENCY_LATCH`. A check result must communicate its meaning; a checkmark beside `EMERGENCY_LATCH` must not ambiguously imply an active latch is acceptable.

Document `LIVE`, `SIMULATED`, `REPLAYED`, `STALE`, `IN_DOUBT`, `DEGRADED`, and `UNAVAILABLE` in their proper source/freshness/outcome/video contexts. Do not combine them into a single mutually exclusive global status enumeration.

All operational data flows through WP2's `CoordinatorClient`/`CoordinatorPort`. The UI owns presentation state only. Evidence uses run-scoped evidence access, and video uses the safe stream handle. No asset or component accesses SQLite, drone SDKs, raw artifact paths, credentials, or raw video frames.

Duplicate clicks/retries on the same decision attempt retain its command envelope/idempotency key. Version rejection triggers refresh before a new action attempt. Emergency controls stay accessible during pending ordinary operations. These behaviors are specified now and implemented/tested in the subsequent component stage.

## 9. Review and acceptance

Asset-kit acceptance requires:

1. Every declared asset is a separate usable file, listed in the manifest with format, intended use, variant, provenance, and checksum. A bitmap embedded in an SVG is not an editable vector master.
2. Brand source fidelity is visually reviewed. Transparent light/dark exports and the compact icon are legible at intended sizes without clipping or distortion.
3. SVG IDs and polygons match the fixture, all five persisted statuses have examples, and selection/freshness remain independent.
4. The review sheet displays the actual files on light/dark backgrounds, small-size logo previews, icon set, schematic variants, placeholders, and required component states. It labels all sample content as illustrative.
5. Tokens and CSS values agree; contrast and keyboard-focus treatments are checked. No generated image text is treated as authoritative product copy.
6. Component specifications cover all rows and boundary behaviors above, with no invented backend capabilities.
7. A packaging check finds no secrets, API credentials, absolute developer-machine dependencies, or expiring remote URLs required for the kit to render.

The later NiceGUI acceptance stage must exercise normal approval, marker missing, stale data, degraded/unavailable video, failed preflight, missing/forbidden evidence, `IN_DOUBT`, duplicate decisions, version rejection, and emergency acknowledgement using contract-valid fixtures. It must use WP2's fake-client seam and verify the Header/Left/Center/Right/Bottom regions at 1440×900 without horizontal overflow. The retained smoke screenshot is evidence of the running UI, distinct from this asset review sheet.

## 10. Handoff sequence

1. User reviews this written specification.
2. After written-spec approval, create the implementation plan using the writing-plans skill and present execution options.
3. After the user selects execution, create and verify the reusable kit. Review the reconstructed brand master before freezing it.
4. Continue with the separately planned NiceGUI components and scenario-fixture integration. Do not claim this asset-kit delivery completes WP2.
