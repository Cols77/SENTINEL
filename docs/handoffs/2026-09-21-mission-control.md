# Mission Control integration, schema v3, and voice handoff

Implemented on PR #8 (`codex/survey-console-fixtures`). The published
`integration/survey-demo` tip `4aa9e19361b3ed31a4c8025405405e3c4a6b1b44` was incorporated by the normal
merge commit `15be1e6573eb1dc2c309f6b2e4faf7cfdc1c4faa`; no force-push or history
rewrite is required. Integration behavior remains authoritative for survey,
flight, coverage, proposer, localization, and live detection.

## Live site-profile workflow delivery

The implementation was delivered across commits `cef6fd5` through `763ab64`, from the required
physical-profile startup gate through registry/switch hardening, readiness refresh, approval-time
dispatch admission, and browser selector recovery fixes. It adds a versioned data registry with
CanningSt enabled and Demo disabled, idle-only atomic Live switching, route-aware current readiness,
and explicit proposal approval as real-flight dispatch authorization. Mission Control alone admits
the paid detector call after approved dispatch; standalone authorization gates remain in place.

## Delivered

- Typed optional mission observations with no-op compatibility for existing callers: runner
  lifecycle, stop progress, pose and pose health, frame capture, detector result, merge, report,
  abort, and public escalation.
- Runner lifecycle owns fail-closed active-runner registration for controlled Land and Emergency.
  Exact encoded pose evidence and AprilTag diagnostics remain available to the backend projection.
- A backend-only allowlisted `SurveyReadModel`; the browser receives no database or SQL capability.
- Integration flight behavior retained: coverage guarding, configurable trim rounds, leg speed,
  corrected transforms, return-to-start, bounded proposer modes, and escalation semantics.
- Live detection supervision uses the integration `live_detect` subprocess design. Results are
  cached by run, sweep, path, and SHA-256 so survey ingestion cannot repeat a paid provider call.
  Process-tree cleanup is retained through service shutdown; malformed or failed results become
  explicit analysis gaps without taking over flight. Standalone paid-call commands require
  `--i-authorise-paid-calls`; Mission Control detector authorization is implicit only for an
  explicitly approved dispatch.
- Live Mission Control follows integration's default `gemini-openrouter` detector and shared
  nearest-`.env` loading (`OPENROUTER_API_KEY` plus `GEMINI_OPENROUTER_MODEL`). Direct Gemini and
  Moondream remain explicit alternatives.
- Closed Mission Control snapshot schema v3 with route coverage, tag predictions, trim and arrival
  facts, return leg, commanded/measured pose, freshness and vertical consistency, capture detection
  progression/latency, proposer provenance, escalation reasons, strict cross-references, and
  explicit COMPLETE/ABORTED/RECOVERY_REQUIRED responses.
- Backwards-compatible schema-v3 Live readiness facts with ordered planning, flight, detection, and
  independent Emergency checks. New snapshots emit the extension; validators retain support for
  older v3 fixtures that omit it. Public remediation names flags and environment-variable names
  without exposing their values.
- Live startup resolves and validates the selected physical site profile before binding. Host
  composition creates the local session and planning projection without constructing a drone,
  provider, or transport. Idle-only site switches are atomic and do not restart the host. Explicit
  proposal approval confirms real dispatch and starts bounded preflight; provider profile and
  recorded consent remain dispatch gates, while paid-call authorization is admitted only for an
  approved Mission Control dispatch. No failure selects Rehearsal adapters.
- The browser renders route coverage and return-to-start, separate commanded and measured markers,
  exact pose images with AprilTag polygons/IDs, fresh/aging/stale localization, detection gaps,
  authoritative counts, evidence linkage, reports, and terminal recovery guidance.
- Browser hold-to-talk shares the existing idempotent chat submission path. Interim recognition is
  shown in the composer and one final transcript submits on release. Duplicate result/end/pointer
  events collapse to one command. Denial, cancellation, failure, or no final result preserves the
  draft and sends nothing.
- Only the exact normalized phrase `Sentinel, land now` starts a visible three-second controlled-Land
  countdown. Cancel or Escape aborts it; `LAND` is checked at start and execution using one command
  identity. Voice cannot approve/reject a proposal or trigger Emergency. Other phrases remain
  bounded chat input.

## Launch

```sh
uv sync
uv run python -m sentinel.ui --mode rehearsal \
  --data-dir artifacts/my-rehearsal --port 8771
```

Open http://127.0.0.1:8771/console/ and request “count the computers”. A proposal is not a flight;
approve it explicitly. Use a fresh data directory for every rehearsal. The uncertain and
missing-analysis inputs are selected with `--scenario fixtures/mission-control/uncertain-followup.json`
and `--scenario fixtures/mission-control/missing-analysis.json`.

Start Live with the configured Canning St physical profile:

```sh
sentinel mission-control --mode live --site-profile CanningSt --data-dir artifacts/local-live
```

The default registry is `config/mission-control-sites.v1.json`; `--site-profiles PATH` selects an
alternate registry, and entry paths are relative to that registry. Live refuses before binding if
the selected profile is missing, disabled, incomplete, or physically mismatched. CanningSt is the
current enabled profile; Demo remains disabled until venue calibration. Add a venue by adding a
validated registry entry with its site, tag map, pose configuration, and measured surface height;
pose mode `off` also requires start coordinates. This is data-only configuration.

The Live-only selector is usable while the session is pristine/idle and locks after any mission
activity or evidence. A switch applies atomically in the running host, with no restart and no drone,
provider, or transport construction. Readiness refreshes by polling, discovers the network route,
and reports current blockers only. The provider profile and exact-location/date filming consent
remain dispatch gates. `OPENROUTER_API_KEY` and `GEMINI_OPENROUTER_MODEL` load from the environment
or nearest `.env`; secret values are never shown.

Explicitly approving a proposal confirms and dispatches the real flight, beginning bounded
preflight. Paid detector authorization is implicit only for this approved Mission Control dispatch;
standalone `live_detect`, survey, and cube commands still require `--i-authorise-paid-calls`.
Voice cannot approve or reject proposals or trigger Emergency. The exact controlled-Land phrase and
cancellable three-second countdown are unchanged. Physical flight, demo calibration, manual
microphone behavior, and provider performance remain hardware-validation limits.

See [setup and Live prerequisites](../../examples/survey-console/README.md) and the
[schema-v3 contract](../../examples/survey-console/contract.md).

## Verification

The figures below are the earlier integration verification and predate the live site-profile
workflow. Final verification for the profile changes is recorded by the final coordinator at handoff.

Earlier integration verification on 2026-09-23:

| Check | Result |
|---|---|
| `.venv\\Scripts\\python.exe -m pytest -q --ignore=fixtures` | **1638 passed, 3 skipped, 5 warnings**, 1009.32s |
| `node --test tests/survey_console/*.test.mjs` | **86 passed**, no failures/skips |
| `uv run python scripts/ui_assets/check_assets.py` | **414 checks passed across 83 assets** |
| `node scripts/ui_assets/verify_mission_control.cjs` | Passed clean, bare-Live readiness, uncertain-follow-up, and missing-analysis real-host scenarios |
| `node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8772` | Passed all 8 retained fixture scenarios |
| `git diff --check` | Passed |

The real-host browser run owns and removes a separate data directory and backend for each scenario.
It verifies schema v3, request/approval/reload and lost-response reconciliation, route and pose
progression, stale-to-fresh AprilTags, detection/report/evidence/count linkage, controlled Land and
Emergency boundaries, deterministic fake speech, 1440×900, 1920×1080 and 390×844 layouts, reduced
motion, and no external requests. The retained fixture run covers eight reviewer timelines,
evidence races, terminal states, decision identity, keyboard operation, and responsive layout.
Warnings are the existing `jsonschema.RefResolver`, Starlette/httpx and AnyIO deprecations plus the
unregistered `unit` marker; skipped tests are not counted as passed.

## Review fixes found during final acceptance

- Follow-up projection originally replaced the first sweep route with the new proposal route while
  prior frames and the active stop still referenced the first option. A failing real-host scenario
  and Python regression now prove that flown and proposed routes are unioned, the completed sweep
  retains its own option, and the follow-up route remains planned.
- Browser acceptance now waits for the initial snapshot before submitting chat, uses real pointer
  gestures for pointer-capture voice tests, waits for owned host shutdown before deleting SQLite,
  and accepts the intentional absence of decision buttons in explicit terminal states.
- The observer privacy regression now expects the public normalized stop status while continuing to
  prove that private image paths never enter the journal.
- The live-detection cache now rejects unknown or incoherent follower statuses, sightings attached
  to failure rows, and pixel boxes outside the normalized 960×720 frame as explicit analysis gaps,
  while preserving intentional `CLASS_MAP_MISS` partial results.
- A generic document language (for example `en`) now defers to a regional English browser locale
  (for example `en-AU`) before falling back to `en-US`.

## Privacy and operational limits

Text remains the primary reliable input. Browser speech may be processed by the browser vendor only
after the operator presses the microphone. SENTINEL does not upload or retain audio, perform passive
listening, support multilingual command interpretation, approve by voice, or expose voice Emergency.

Automated tests used synthetic/injected clients only. No paid provider request, drone command, or
physical microphone test was performed. Measured flight readiness, real network timing, calibration,
provider performance, actual landing, motor stop, and Chrome/Edge microphone permission behavior on
the demo laptop still require supervised hardware validation. Acknowledgement is never evidence that
the aircraft landed or the motors stopped.

PR #8 remains the delivery vehicle and is not merged automatically.
