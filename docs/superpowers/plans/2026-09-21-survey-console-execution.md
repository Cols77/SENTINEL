# Survey console execution record

Implemented all eight approved tasks on `codex/survey-console-fixtures`, based on PR #7 head `367d29ce8d06e9fb3e03fcb3c72a0a741732ad16`. Existing asset-kit files and Python backend remain unchanged.

## Verification

- 45 Node unit tests passed: `node --test tests/survey_console/*.test.mjs`.
- Playwright acceptance passed: all eight scenarios, evidence scoping/revocation/late responses, thin-image rendered bounds, uncertain-decision recovery, keyboard, reduced motion, inert text, local-only requests and desktop/mobile layout.
- Existing asset-kit baseline: 32 browser checks passed. Its generated validation file was restored after the baseline run.
- Existing Python suite: 662 passed, 3 skipped, 3 warnings (two RefResolver deprecations and an unregistered unit mark).
- `git diff --check` passed. Screenshots and browser report are ignored local artifacts under `artifacts/survey-console/`.

These checks establish fixture behavior, not live-provider quality, flight behavior, NiceGUI/WP2 conformance or a production evidence transport.

## Independent review and fix pass

One fresh-context gpt-6-astra reviewer assessed `367d29c..b7500b3` read-only. No Critical issues; one Important uncertain-result recovery issue and one Minor thin-image rendering issue were reproduced. No behavior was declined for judgment.

The thin-image issue was regraded Important because valid evidence dimensions placed a sighting outside actual image content, violating the explicit provenance/geometry requirement.

Both findings were fixed in one pass, each with a failing regression first:

1. IN_DOUBT retries reopen pending reconciliation with the same immutable attempt. Terminal outcomes remain cached, duplicate pending retries share a promise, and different attempts remain blocked while unresolved. Unit tests and browser acceptance demonstrate IN_DOUBT → retry → ACKNOWLEDGED with unchanged identity and flight budget.
2. Overlays paint an inset stroke and clip labels without a border-imposed minimum width. A browser regression for a 1×10,000 PNG failed with a 4px box around 0.024px image content and passes after the fix.

Deferred minor findings: none. The final fix pass passed 45 unit tests and browser acceptance; it was not independently re-reviewed.

## Rulings made

- Used a fresh bare clone and linked worktree under `~/.codex/worktrees/sentinel-survey` because the synced Documents checkout repeatedly stalled reading Git metadata. This preserved original edits and PR #7. Cost if wrong: an extra local clone to remove.
- Added a UI-only decision history array to retain old proposal/result identities. Cost if wrong: a small internal API extension; no backend wire change.
- Corrected the plan's unconditional cached-promise sketch: terminal outcomes stay cached, while IN_DOUBT reconciles under the same identity. Cost if wrong: fixture retry behavior could differ from a future backend and needs adapter conformance tests.

## Integration gates

Backend-owned/versioned projection; authoritative count/merge/flight/observability facts; event-watermark semantics; run-scoped evidence capability; typed decision and emergency mapping; integrated survey/WP5/proposer work; consent/provider approval; detector readiness. No database, sibling worktree, provider or drone was accessed by the UI.
