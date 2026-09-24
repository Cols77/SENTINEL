# SENTINEL Mission Control

The local Mission Control host connects this vanilla browser UI to the production survey loop. Rehearsal and Live use the same application service, approval broker, snapshot contract and survey/report validation. Rehearsal injects synthetic camera, flight and perception boundaries; its results are computed by the runtime.

## Start a rehearsal

From the repository root, install the project dependencies (`uv sync`), then:

```sh
uv run python -m sentinel.ui --mode rehearsal --data-dir artifacts/my-rehearsal --port 8771
# Equivalent entry point:
uv run sentinel mission-control --mode rehearsal --data-dir artifacts/my-rehearsal
```

Open http://127.0.0.1:8771/console/, ask **count the computers**, inspect the proposal and explicitly approve or reject it. Text cannot authorize flight. Status questions remain available while approval waits. One host owns one session/run; use a fresh data directory for a new rehearsal. Do not run two hosts against the same session database.

For a room-agent UI preview with **Jev rehearsal and Live Tello selectable**, prepare a room Live pack in this checkout and start the host with both room flags:

```sh
uv run python scripts/room_live_prepare.py --run-root runs --local-ip 192.168.10.2
uv run python -m sentinel.ui --mode rehearsal --data-dir artifacts/room-ui-preview --port 8771 --room-live-config runs/live.pack.json --i-authorise-paid-calls
```

Preparation reuses today's recorded CanningSt filming consent and refuses if it is absent. The pack and its run artifacts stay in this checkout. Selecting and approving Live Tello can dispatch a real flight, even though the host's survey mode is rehearsal; follow the room Live flight checklist before approval. Re-run preparation when its site assurances expire.

In the room-agent Live setup, **Prepare next flight** runs the same offline preparation from the browser. It creates a fresh run, grant, manifest and assurances using the configured pack's run root and existing consent; it sends no drone command. The panel also shows whether local Tello UDP port 8889 is occupied. If this server retains that port after a landed flight, restart Mission Control before proposing the next Live run. Pack preparation alone does not release DJITelloPy's process-wide receiver socket.

The clean scenario yields one confirmed synthetic computer. Pass `--scenario fixtures/mission-control/uncertain-followup.json` for a separately approved follow-up, or `--scenario fixtures/mission-control/missing-analysis.json` for a provider gap. Missing analysis is visibly incomplete, never interpreted as a zero count. Rehearsal localization runs the real AprilTag estimator over scripted image inputs, including no-tag intervals that age and recover the last measured fix.

Land interrupts normal sequencing and requests controlled landing. Hold Emergency for 1.2 seconds to latch the session and all active runners, then use the independent transport. Both are simulated in Rehearsal. Acknowledgement is not proof of physical landing or motor stop.

## Live startup and site profiles

Start Live with the selected physical site profile:

```sh
sentinel mission-control --mode live --site-profile CanningSt --data-dir artifacts/local-live
```

The default registry is `config/mission-control-sites.v1.json`; use `--site-profiles PATH` for another registry. Entry paths are resolved relative to the registry file. Live refuses before binding if the selected profile is missing, disabled, incomplete, or fails site/coverage validation. `CanningSt` is the current enabled profile. `Demo` is intentionally disabled until its venue is measured and calibrated. Add another venue by adding a registry entry with an ID, label, enabled/setup status, and validated site, tag-map, pose database/mode, and surface-height fields; pose mode `off` also requires start coordinates. This is data-only configuration.

The Live-only profile selector is available while the session is pristine and idle. Any mission activity or evidence locks it. A permitted switch applies atomically in the running host without a restart, and does not construct a drone, provider, or transport. Readiness refreshes by polling, discovers the network route, and displays current blockers only. Opening or polling the page does not connect to a drone or provider.

The Mission Control browser projection is schema v3: route coverage and return-leg facts, commanded/measured freshness, capture detection progression, public proposer provenance, and explicit COMPLETE/ABORTED/RECOVERY_REQUIRED terminal states are rendered from validated backend facts. Live detection starts in a separate supervised process before capture, binds cached results to run/sweep/path/hash identity, and never repeats a provider call during survey ingestion. Detector failure is an explicit analysis gap and does not take over flight control.

Explicitly approving a Live proposal confirms dispatch of a real flight and starts bounded preflight. Detector paid-call authorization is implicit only for that approved Mission Control dispatch. Standalone `live_detect`, survey, and cube commands retain their explicit `--i-authorise-paid-calls` requirement. Provider profile approval and recorded filming consent remain dispatch gates. `OPENROUTER_API_KEY` and `GEMINI_OPENROUTER_MODEL` load from the environment or nearest `.env`; secret values are never shown. The provider profile must match the selected provider/model, and consent must already be recorded for the exact location/date. The host does not record consent on the operator's behalf.

The Live adapter uses `CubeFlight`, the measured Tello camera model, real provider perception, and the existing F rules proposer only after those gates admit the corresponding capability. Land is unavailable without an active runner; Emergency is unavailable until its independent transport is initialized. The bounded chat supports computer-count requests and status; when planning is unavailable, count requests return actionable refusal text without starting the worker. Pose evidence comes from the runner's real localization bursts, so it can honestly age between bursts. Voice cannot approve or reject a proposal or trigger Emergency. The exact controlled-Land phrase and cancellable three-second countdown are unchanged. Restarted interrupted Live sessions require recovery and cannot dispatch again automatically. Reconcile the aircraft and previous run before starting a new session.

Physical flight, demo/site calibration, manual microphone behavior, provider performance, and actual landing/emergency outcomes require supervised hardware validation. Automated acceptance uses injected clients only.

## Verify

```sh
uv run pytest -q --ignore=fixtures
node --test tests/survey_console/*.test.mjs
uv run python scripts/ui_assets/check_assets.py
node scripts/ui_assets/verify_mission_control.cjs
```

The browser verifier starts and stops four real local backends with separate temporary data directories. It requires Playwright/Chromium in the tooling environment; no frontend build is needed. Acceptance covers Live readiness guidance and refusal, clean count, approved uncertain follow-up, missing analysis, approval reload/reconciliation, production survey/report/evidence, stale-to-fresh localization, deterministic browser speech, independent safety controls, 1440×900, 1920×1080 and narrow layouts, reduced motion, and local-only traffic. Artifacts go under ignored `artifacts/mission-control/`.

## Retained fixture regression surface

The original eight-scenario fixture demo remains isolated at `/examples/survey-console/` on a static repository server:

```sh
python3 -m http.server 8770 --bind 127.0.0.1
node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8770
```

That surface is labelled FIXTURE DEMO and cannot dispatch a flight. Its reviewer controls exercise snapshots, uncertain/lower-bound counts, evidence races, and approval state transitions. It does not prove runtime integration. The legacy asset-kit study remains `/assets/sentinel-ui/console.html`.

See [the contract](contract.md), [approved design](../../docs/superpowers/specs/2026-09-21-sentinel-mission-control-design.md), and [execution handoff](../../docs/handoffs/2026-09-21-mission-control.md).
## Browser voice input

When the browser exposes `SpeechRecognition`, the mission composer provides an explicit hold-to-talk control. Recognition is English using the browser's English regional tag where available. The browser speech service may process audio remotely; Sentinel does not upload or store audio and never listens passively. Only the exact spoken phrase `Sentinel, land now` enters the visible three-second controlled-Land countdown. Other voice phrases use the bounded chat path, including a safe refusal for unsupported safety and flight requests. Text remains available when speech recognition is unsupported or permission is denied.
