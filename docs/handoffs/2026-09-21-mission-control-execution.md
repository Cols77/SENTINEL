# SDD ledger — plan: docs/superpowers/plans/2026-09-21-sentinel-mission-control.md
Native execution authorized; implement Tasks 0–12 without intermediate approval. PR updates/push/re-review authorized. Spec approved. Worktree isolated; integration merged cleanly.
Pre-flight: Tasks 1→2,4–7,9: schema/journal shared; display version separated from decision_version.
Pre-flight: Tasks 2→4,7: offer/wait and guarded worker shared; candidate from run_survey remains source.
Pre-flight: Tasks 3→4,8,11: registry/safety shared; session latch must exist before first dry-run, not only Live.
Pre-flight: Tasks 4→5,7,11: same adapters/runtime; no precomputed report/snapshot substitution.
Pre-flight: Tasks 5→6,8: /console/ static root, one-argument browser decision, typed 409 and binary evidence.
Pre-flight: Tasks 9→10,11: exact encoded image hash; pose callbacks optional, bounded and additive.
Ruling: use the existing isolated worktree rather than create another — native Git worktree verified — no scope cost.
Ruling: toolchain uv absent and disk below 500 MiB; establish a minimal venv using bundled Python/system packages and no download cache before baseline — avoid duplicated large scientific dependencies — record exact runtime versions and any missing-baseline dependency.
Task 0: in progress; Tasks 1–12: pending.
Task 0: baseline initial 1301 pass/13 skipped, 5 CLI failures due to editable package missing; repair install and rerun documented. Full suite remains final gate.
Task 1: store/model RED missing module observed; GREEN 7 Python + 46 Node tests. Shared generated schema avoids Python/JS drift. Task 1 schema is executable and closed at every level.
Ruling: UIStore owns a connection per operation, not a long-lived connection per thread — safer for FastAPI threadpool and same SQLite ownership invariant — small connection overhead.
Ruling: place immutable session mode/identity directly on ui_runs rather than duplicate ui_sessions initially — one session/one run app — separate session table can be added if multi-run becomes scoped.
Task 2: complete (commits a3c69d7..164f532, tests: .venv/bin/python -m pytest tests/ui -q → 11 passed, 2 warnings in 1.23s)
Task 3: complete (commits 164f532..905b2b4, tests: .venv/bin/python -m pytest tests/ui/test_safety.py tests/test_runner.py tests/cube/test_mission.py -q → 60 passed, 2 warnings in 0.52s)
Task 4: complete (commits 905b2b4..38f7d3f, tests: .venv/bin/python -m pytest tests/ui/test_runtime.py tests/survey -q → 228 passed, 2 warnings in 0.67s)
Task 5: complete (commits 38f7d3f..977eb49, tests: .venv/bin/python -m pytest tests/ui -q → 19 passed, 4 warnings in 1.00s)
Task 6: complete (commits 977eb49..5864266, tests: node --test tests/survey_console/approval.test.mjs tests/survey_console/client.test.mjs tests/survey_console/contract.test.mjs tests/survey_console/controller.test.mjs tests/survey_console/counts.test.mjs tests/survey_console/evidence.test.mjs tests/survey_console/http-client.test.mjs tests/survey_console/mission-contract.test.mjs tests/survey_console/mission-controller.test.mjs → # duration_ms 261.501916)
Task 7: complete (commits 5864266..842de1e, tests: .venv/bin/python -m pytest tests/ui/test_chat.py tests/ui/test_runtime.py -q → 4 passed, 2 warnings in 0.35s)
Task 8: complete (commit 03e2b44; owned real-host browser harness passed request/reload/lost approval response/reload reconciliation/one flight/count/report/evidence/keyboard simulated emergency/3 viewports/local-only network).
Task 9: pose tests 134 passed before stream sink; pose+UI 156 passed after full projection. Browser retest running.
Ruling: promote the existing rendered two-tag fixture into committed PNG/map inputs and run real detector on a scripted camera boundary — no fake pose reports — synthetic room-to-survey offset explicitly defined server-side.
Ruling: optional display callbacks use one-slot daemon consumers; slow/failing displays cannot hold pose/flight processing — latest image may drop, persisted pose remains authoritative.
Fix found during execution: pending Land disabled Emergency in UI. Browser failing regression observed; separate pending states implemented and real-host regression rerun.
Task 9: complete (3be8cc9; pose/UI 156 passed, real-host stale-to-recovered evidence passed).
Task 10: complete (f6d8d41; 54 Node tests, two broader scenarios, asset checker and owned real-host browser passed; runner hooks included in Task 11).
Ruling: expose local-IP, measured surface/start coordinates and SR-063 amendment flags required by existing CubeFlight — Live must not invent geometry or relax existing authorization — extra configuration burden.
Ruling: Live uses the existing deterministic F legal-candidate proposer and bounded computer-count chat, with a configured real perception provider — no paid autonomous language responder was configured — conversational coverage is limited and explicitly clarified.
Ruling: keep existing CLI partial-frame behavior; managed UI CubeFlight rejects failed/safety-aborted flights before follow-up — preserves consumers while preventing a UI retry flight after abort — partial aborted imagery is not projected as a completed survey.
Task 11: complete (focused combined runtime/UI/pose/cube/CLI 490 passed; Live startup gates, callback registration and managed safety abort tests passed; no hardware connected).
Final review: fresh gpt-6-astra reviewer at bb73ef2; four actionable findings, no minors. One author fix pass, no second reviewer.
Final: fixed shutdown latch without transport — test_service_shutdown_lands_active_runner_before_cleanup RED→GREEN; shutdown closes admission then requests controlled Land, failed Land falls back to independent emergency. No command is sent merely for an idle session.
Final: fixed detached IN_DOUBT retry — lost committed response/follow-up regression RED→GREEN; immutable reconciliation remains allowed, new decisions remain blocked until settlement.
Final: fixed revoked pose display — server capability removal RED→GREEN plus real-browser displayed/pending-image revocation checks.
Final: fixed Live images labelled synthetic — real-browser Live component assertion RED→GREEN; labels use mode and frame provenance.
Ruling: interrupted Rehearsal sessions become ABORTED on restart — no surviving worker can fulfill old offers — a new rehearsal needs a fresh data directory; regression RED→GREEN.
Ruling: retarget PR #8 to integration/survey-demo — excludes 165 existing runtime commits from the review — includes the two PR #7 foundation commits until that foundation is incorporated upstream; no history rewrite.
Final: Ruling: foundation asset provenance/visual polish stayed outside runtime review — retained fixture browser and asset validation pass — not a new independent provenance audit.
Final: Ruling: exhaustive lockfile dependency auditing stayed outside behavioral review — lock resolution and exercised imports checked — no comprehensive supply-chain audit claimed.
Final: Ruling: physical drone/network performance not tested — only injected clients and synthetic camera inputs exercised — hardware readiness remains unproven.
Final: Ruling: reviewer omitted in-progress Task 12 docs — author checked routes/commands and records against implementation — documentation lacks independent final review.
Task 0: complete (integration merged at 9796db0; initial baseline environment issues repaired; final full suite passes).
Task 1: complete (a3c69d7; shared schema/journal tests pass in full suite).
Final: fixed four reviewed findings in e764217; final suite 1358 passed/13 skipped/5 warnings; Node 55 passed; both browser suites and asset checker passed.
Task 12: verification and documentation complete; authorized normal push, PR retarget/update and review renewal follow.

## 2026-09-23 integration-alignment addendum

PR #8 subsequently merged published `integration/survey-demo`
`4aa9e19361b3ed31a4c8025405405e3c4a6b1b44` as normal merge
`15be1e6573eb1dc2c309f6b2e4faf7cfdc1c4faa`. The original counts above describe the earlier
implementation checkpoint and are superseded by the current
[Mission Control handoff](2026-09-21-mission-control.md). The aligned delivery adds typed mission
observations, the backend survey read model, supervised/cache-bound live detection, schema v3,
bounded hold-to-talk input, and expanded three-scenario real-host acceptance. Hardware flight,
paid-provider performance, and a physical Chrome/Edge microphone smoke remain separate supervised
checks.
