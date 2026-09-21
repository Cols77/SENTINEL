# Mission Control execution handoff

Implemented on `codex/survey-console-fixtures` for PR #8, building on the PR #7 asset foundation. Published integration source `6d7f24b641feb94188d15507a10da5ec65ae1aaa` was merged as `9796db0`; no history was rewritten. Implementation review head: `bb73ef2`. Verified fixes: `e764217`.

## Delivered

- Local NiceGUI/FastAPI host, vanilla browser UI, closed shared v2 snapshot schema, allowlisted projection and binary evidence capabilities.
- One durable mission thread, bounded computer-count/status intent, explicit inline approval/rejection, payload-bound idempotency and lost-response reconciliation. Display aging does not invalidate approval.
- One guarded mission worker using the real `run_survey` loop, legal candidate policy, merge and report validator in both modes. Rehearsal supplies synthetic boundaries rather than precomputed result snapshots.
- Clean-computer, uncertain follow-up and missing-analysis input scenarios; explicit source/provenance and unavailable/partial facts.
- Backend route/progress, measured versus commanded position, actual AprilTag estimation over synthetic camera inputs in Rehearsal, exact-image tag overlays and backend freshness. Live pose evidence comes from existing runner localization bursts.
- Registered Live `CubeFlight` runners, measured camera model, selected Gemini/Moondream perception, existing F rules proposer, consent/profile/configuration/preflight checks and independent emergency transport. No automatic Live-to-Rehearsal fallback.
- Controlled Land and independent Emergency; session/runner latch before emergency transport, asynchronous audit, separate UI pending states. Shutdown closes new-run admission and requests controlled landing; a failed landing request attempts independent emergency. Acknowledgements never assert physical completion.
- Locally bundled IBM Plex fonts/license; preserved isolated eight-scenario fixture surface.

## Launch

```sh
uv sync
uv run python -m sentinel.ui --mode rehearsal --data-dir artifacts/my-rehearsal --port 8771
```

Open http://127.0.0.1:8771/console/ and request “count the computers”. A proposal is not a flight; explicitly approve it. Use a fresh data directory for a new rehearsal. See [setup and Live flags](../../examples/survey-console/README.md) and [contract](../../examples/survey-console/contract.md).

## Verification

All final commands ran against the implementation including the review fixes:

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q --ignore=fixtures` | **1358 passed, 13 skipped, 5 warnings**, 102.59s |
| `node --test tests/survey_console/*.test.mjs` | **55 passed**, no failures/skips |
| `.venv/bin/python scripts/ui_assets/check_assets.py` | Passed, including contrast checks |
| `node scripts/ui_assets/verify_mission_control.cjs` | Passed against an owned temporary real host |
| `node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8772` | Passed all eight retained fixture scenarios |
| `.venv/bin/uv lock --no-cache` | Resolved 71 packages |
| `git diff --check` | Passed |
| Unconfigured Live CLI | Refused with `LIVE_CONFIRM_REQUIRED`, no hardware contact |

Browser acceptance covers request, reload at approval, lost committed response and reconciliation, one real survey-loop execution, count/report/evidence, stale-to-fresh pose while approval waits, exact tag overlay, keyboard hold, Emergency available during pending Land, 1440×900/1920×1080/390×844, reduced motion and no external requests/browser exceptions. Additional component checks cover late/displayed pose revocation and accurate Live image labels. The browser harness owns and removes its temporary backend data. Port 8772 was a temporary static regression host; the user's original preview was not replaced.

Python warnings: two `jsonschema.RefResolver` deprecations, Starlette/httpx and AnyIO deprecations, and the existing unregistered `unit` marker. Skipped tests are not counted as passed. No paid provider calls or drone operation were used.

Toolchain: Python 3.12 project venv sharing the bundled scientific runtime, pytest 9.1.1, NiceGUI 3.17.1, jsonschema 4.26.0, Pillow 12.3.0, OpenCV contrib 5.0.0.93, PyAV 14.2.0. Limited local disk initially prevented duplicate dependency installation; the pinned environment was repaired and the full suite passed. JSON-schema and Pillow are explicit runtime dependencies.

## Independent review and fix pass

A fresh `gpt-6-astra` reviewer inspected the whole branch against the approved plan/spec, with particular attention to identity collisions, display/decision versions, blocked safety paths, revocation races and lost approval responses. It found four issues; all entered one author fix pass:

1. Shutdown previously latched emergency without transport, suppressing landing. An injected active-run regression failed, then passed with controlled shutdown/admission closure.
2. A detached uncertain approval could not reconcile without reload. A lost-response/follow-up test failed, then passed with immutable retries independent of current proposal eligibility.
3. Revoked pose artifacts remained projected/displayed. A backend revocation regression failed, then passed; browser checks cover displayed and in-flight image removal while numeric pose remains available.
4. Live captures inherited synthetic captions. A real-browser component assertion failed, then passed with mode/frame-derived provenance labels.

An additional failing/passing regression marks interrupted Rehearsal runs ABORTED instead of leaving orphaned approvals active. The final full suite passed after the fix pass. There was no second independent review and no deferred minor finding.

## Operational limits

Software composition is verified; measured flight readiness, real network timing, calibration quality, landing completion and motor-stop outcomes are **not hardware-verified**. Live checks are refusal gates, not certification. Live chat currently supports computer-count requests/status and the existing deterministic F proposer; unsupported intent clarifies instead of dispatching. A session is one run. Restart never resumes an interrupted flight: Live requires recovery; Rehearsal requires a fresh data directory. Live localization can age between runner bursts. Latest-only display queues can drop display frames without altering authoritative pose processing.

PR #8 targets `integration/survey-demo` so its review excludes the already-published runtime commits; it includes the two PR #7 foundation commits until those are incorporated upstream. PR #7 remains the foundation review. Neither PR was merged.

The complete implementation decisions, costs and review exclusions are retained in [the execution record](2026-09-21-mission-control-execution.md). Approved [design](../superpowers/specs/2026-09-21-sentinel-mission-control-design.md) and [plan](../superpowers/plans/2026-09-21-sentinel-mission-control.md) remain the source requirements.
