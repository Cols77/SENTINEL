# Survey console fixture demo

A separate follow-up to PR #7. This browser console reuses the asset kit without changing it. It is **not flight-capable**, does not invoke providers, and is not the WP2 NiceGUI smoke surface. Every source badge is shown alongside FIXTURE DEMO. Emergency is visibly disconnected.

## Run

From repository root:

```sh
# Terminal 1
python3 -m http.server 8770 --bind 127.0.0.1
# Terminal 2
node --test tests/survey_console/*.test.mjs
node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8770
```

Open [the survey console](http://127.0.0.1:8770/examples/survey-console/) or [the legacy alley study](http://127.0.0.1:8770/assets/sentinel-ui/console.html). Browser ES modules require HTTP; direct `file://` loading is not supported. Node 22 was used for verification. The browser verifier needs Playwright and its Chromium binary available to Node, as does the existing asset-kit verifier. With the Codex bundled runtime, set `NODE_PATH` to the node_modules directory returned by the workspace-dependencies tool. Outside Codex, install Playwright in your tooling environment and install Chromium with that environment's Playwright CLI; no runtime dependency is required by the demo.

## Reviewer controls

- **Fixture scenario** replaces the entire run/client and clears selection and pending presentation.
- **Next fixture snapshot** exposes the next complete supplied projection. It never advances automatically.
- **Fixture submission result / Resolve fixture submission** settles a pending simulated approval or rejection. It cannot dispatch a flight or advance the snapshot. It is separate from the operator decision panel.
- Select a class, object, frame, or stop to inspect supporting evidence. Evidence images are synthetic 1×1 PNGs with illustrative boxes; no lecture-room frames are included. Capture-strip thumbnails are explicitly placeholders. An absent measured pose stays unavailable.
- The operator's initial request is immutable. There is no chat route.

## Scenarios

| Scenario | What to inspect |
|---|---|
| Computers · close follow-up | Six stages; five confirmed plus one uncertain; complete closer proposal targets computer-6 |
| People · lower bound | One capture, timestamped lower bound of three, no merged person identity |
| Phone · closer sweep | Six projected pixels and NOT_OBSERVABLE, then 18 pixels and one uncertain phone after flight 2 |
| Consent required | No retained capture, FORBIDDEN evidence, approval disabled |
| Partial sweep aborted | Flight count unchanged at 0/2; frame excluded from merge |
| Provider timeout | One of three analysed; failed and pending frames; unknown/dropped labels |
| Report rejected | Invented count rejected; authoritative computer range unchanged |
| Source & stale state | LIVE then REPLAY then STALE, unchanged operational facts |

The flight budget comes from the projection. Person counts remain SINGLE_VIEW_LOWER_BOUND. Pen arithmetic uses explicitly illustrative optics. Raw detector scores retain their supplied semantics. The console does not merge sightings or manufacture counts.

## Verification and handoff

Unit tests cover validation, scenario semantics, scoped client reads, event/snapshot races, count copy, image geometry and decision identity. Browser acceptance covers all eight scenarios, source/freshness distinctions, evidence denials and races, approval outcomes, keyboard controls, reduced motion, inert public text, local-only requests, and 1440×900 / 390×844 layout. It writes screenshots and `validation.json` under ignored `artifacts/survey-console/`; these are fixture-review evidence, not proof of flight behavior or WP2 conformance.

Read [the proposed contract](contract.md), [the approved specification](../../docs/superpowers/specs/2026-09-21-survey-console-follow-up.md), and [the implementation plan](../../docs/superpowers/plans/2026-09-21-survey-console-follow-up.md). Real backend projection, evidence transport, emergency support, NiceGUI integration, consent/provider approval and flight/proposer readiness require a separate integration project.
