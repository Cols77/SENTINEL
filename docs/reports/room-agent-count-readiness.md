# Room agent count-and-return readiness — 2026-09-23

This reports what Plan 03 (`docs/superpowers/plans/2026-09-22-jev-room-agent-03-count-closer-return.md`)
delivers through Task 5: a **count-and-return** capability for the agentic room
agent (`src/sentinel/room`), and one frozen, byte-stable **replay fixture**
(`fixtures/room_agent/v1`) that exercises it offline. It is not a capability
promotion and not a live run: **replay is offline evidence only — a
byte-stable reproduction of one already-recorded, synthetic trace, never a
fresh model or hardware measurement.** No live batch, autonomous-action
promotion, or hardware flight informs this report. Capability promotion is
Plan 06's job ("Execute Plan 06 through count-and-return promotion before
advertising real capability", per the Task 5 brief).

## Supported request shape

The room agent (`sentinel.room.controller.RoomController`) accepts one
natural-language request compiled into a `GoalLedger` of `COUNT` and
`ANSWER_STATE` objectives (`sentinel.room.goals`), executes them through a
bounded, append-only session (`sentinel.room.store.RoomStore`), and reports a
single terminal answer plus a `TerminalState`. In simulation mode, with the
observation collaborators wired (`resolve_checkpoint`, `perception_capture`,
`perception_provider`), a `COUNT` objective may also drive `OBSERVE`
checkpoints at surveyed poses declared by the site's `pose_visibility`
(Plan 03 Task 4). **`OBSERVE` is never offered in `REAL` execution mode**
(`RoomController._observation_enabled`; see `test_real_mode_never_offers_observation_candidate`)
until Plan 06 promotes matching pose/energy/site profiles.

## Coverage and count semantics

- Coverage is **region-visited**, not per-detection geometric grounding: once
  an `OBSERVE` checkpoint at a declared pose completes and its perception
  result is ingested and authorized, every `(cell, class)` pair that pose
  declares visibility of is marked resolved (`RoomController._coverage`,
  `sentinel.room.coverage.project_coverage`). Wiring per-detection `(x, y)`
  estimates (`sentinel.room.spatial`) into per-cell polygon containment is
  explicitly out of scope for this completion semantic (see the Task 4
  report and `RoomController._coverage`'s own docstring).
- A plain legacy `ANSWER` is gated off while the site declares coverage cells,
  a `COUNT` objective is pending, and coverage is not yet complete
  (`RoomController._answer_gated_by_coverage`); the only route to a terminal
  answer before that is the explicit `ANSWER:<objective>:INCOMPLETE`
  candidate, which reports what remains uncovered and still requests a safe
  return.
- Grounded counting (`sentinel.room.association.associate_static`,
  `sentinel.room.counting._static_counts`/`project_counts`) exists as a
  library: given associated per-session detections, **a single low-confidence
  sighting classifies as uncertain (`confirmed=0`, `uncertain=1`), never
  promoted to confirmed** — proven directly by
  `test_uncertain_detection_stays_uncertain_after_observation`
  (`tests/room/test_count_return.py`), which builds one such sighting and
  asserts that classification.
  **Fix round 1 (Q1) correction:** neither `RoomController`'s live decision
  path nor the replay fixture actually calls `_static_counts`/`project_counts`
  today — a session's own grounded per-observation counting is not yet wired
  into `ANSWER`. The controller's only routes to a terminal answer for a
  `COUNT` objective are (a) the legacy `READ_COUNTS` survey adapter below, or
  (b) the explicit `ANSWER:<objective>:INCOMPLETE` candidate reporting
  uncovered cells; a session's own `READ_OBSERVATIONS` facts render as raw,
  per-detection historical fields (`class_name=...; confidence_band=...`),
  never as a confirmed/uncertain count. Wiring `_static_counts` into a live
  `ANSWER` is out of scope for this plan and is not claimed here.
- Legacy `READ_COUNTS` (merged multi-view survey reports, sourced from
  `sentinel.survey`'s `SurveyStore`, not from this session's own detections)
  is the **only** route through which a plain `ANSWER` can report a numeric
  confirmed/uncertain count for a `COUNT` objective
  (`sentinel.room.candidates.can_answer`); it is never conflated with a
  session's own grounded `READ_OBSERVATIONS` reads. `replay_fixture` does not
  wire up a `survey_store`, so a fixture that completes through `READ_COUNTS`
  cannot currently be replayed byte-stably (see "Replay" below); the shipped
  fixture instead completes through route (b).

## Safety transitions

Every physical action is admitted and dispatched through
`sentinel.cube.step_executor`'s `SafetySupervisor`, shared with Plans 01/02.
Plan 03 Task 4 adds no new transition kind; `OBSERVE` checkpoints ride the
same `CONTINUE` / `RECOVER` / `RETURN_NOW` / `LAND_CONTINGENCY` machinery
every other checkpoint uses, including:

- a mandatory battery/localization-forced return **before** an unaffordable
  observation is ever dispatched (`test_battery_forces_return_before_an_unaffordable_observation`);
- the checkpoint and its correlated perception wait are admitted as one
  action, so a checkpoint cannot be preempted before its own perception wait
  runs (`test_observation_admission_reserves_the_perception_wait_too`);
- a terminal answer — success or explicit incompleteness — requests a
  supervisor-owned safe return/landing first whenever the session has
  actually flown, and reports `ABORTED` rather than a false success on an
  unconfirmed or failed landing (`RoomController._finalize_terminal`,
  `test_failed_return_never_reports_success`);
- a stale Jev decision, a dispatch exception, and a stale or unauthorized
  perception result are each typed refusals that never credit coverage or a
  physical effect that did not actually happen (`test_stale_observation_decision_is_never_dispatched`,
  `test_dispatch_exception_during_observation_is_typed_not_stale_decision`,
  `test_stale_perception_result_is_refused_and_coverage_not_credited`).

## Disabled / deferred capabilities

Per the operator's 2026-09-23 scope-reduction ruling (Task 5 brief, Plan 03
ledger Ruling 1/2), the following are **disabled or deferred**, not
promoted, by this plan:

- **Closer inspection** (Task 3: closer-inspection viewpoints) — deferred.
  `RoomController._closer_unavailable_candidates` reports an explicit
  `CLOSER_INSPECTION_UNAVAILABLE` outcome for any objective that can only be
  satisfied by getting closer; no closer/validate-by-approach candidate is
  ever offered (`test_closer_inspection_never_offers_an_executable_closer_candidate`).
  There is no `test_closer_inspection.py`; the replay baseline covers
  count-and-return only and never exercises a closer scenario.
- **Certified landing** — not implemented by this plan. `LAND_NEAR` remains a
  compiled goal kind (`sentinel.room.models.GoalKind.LAND_NEAR`) with no
  executing candidate in this controller.
- **Following** — not implemented by this plan. `FOLLOW` remains a compiled
  goal kind with no executing candidate in this controller.

## Replay: what it is and is not

`sentinel.room.replay.replay_fixture` verifies a fixture's manifest-bound
file hashes, its room-store schema and projector revisions, and the
recorded event/perception-ingestion stream's structural and content
integrity (no missing, duplicate, reordered, corrupted, stale-decision or
stale-authorizing record — see `tests/room/test_replay.py`), then replays it
through the **same** store and query projectors live mode uses —
`RoomStore`, `ingest_result`, `ReadQueries.project`, `render_answer` — into a
private, throwaway `RoomStore`, and finally requires the replayed result to
match the manifest's own bound `expected` values byte-for-byte (Fix round 1,
Q2) — a drifted or tampered fixture that still passes every structural check
is still rejected if what it actually replays to has silently changed. It
never constructs a provider or drone client (`provider_factory`/
`client_factory` are asserted unused).

**Fix round 1 (Q1) correction:** the fixture originally shipped here was
hand-authored — its records were appended directly through the same
primitives replay itself uses, RoomController/SafetySupervisor were never
run, the aircraft's own recorded telemetry stayed `LANDED` throughout, and
the terminal `ANSWER` reported raw `READ_OBSERVATIONS` fields. It was
described as exercising "count-and-return" and the uncertain-count
classification; neither claim held. The fixture has been regenerated from an
actual `RoomController` + `SafetySupervisor` run (fully in-repo test
collaborators — a canned `Judge` choice-judge and a deterministic checkpoint
`Executor` stub reused from `tests/room/test_supervisor.py`/
`test_count_return.py`; no network, model or hardware call). Every
`ROOM_EVENT` and the one `PERCEPTION_INGESTED` record in
`fixtures/room_agent/v1/events.jsonl` is exactly what that live session
persisted — real `SAFETY_ASSESSED`/`CHECKPOINT_COMPLETED`/
`DRONE_STATE_RECORDED` checkpoint-dispatch and safe-return/landing events,
real controller-shaped `ROOM_CHOICE_ATTEMPT` records (full projected `state`,
the actual legal `candidates` list Jev chose from), and real causation_id
chaining from each turn's outcome back to its own choice attempt.

The scenario (label `synthetic`, `session_id=session`): a room-wide `COUNT`
objective for "computer" over a site with two declared coverage cells, only
one of which has any declared surveyed view at all. One `OBSERVE` checkpoint
ingests a single low-confidence detection at the one observable cell; a
`READ_OBSERVATIONS` turn reads it back (still rendered as raw historical
fields, per the correction above — this fixture does not claim to exercise
grounded counting); because the second cell can never be resolved by any
declared view, coverage can never complete, and the only legal route to a
terminal answer is the explicit `ANSWER:g1:INCOMPLETE` candidate, which names
the uncovered cell, requests a supervisor-owned safe return, and completes as
`TerminalState.INCOMPLETE_RETURNED` once the landing is confirmed. This
fixture does **not** reach a plain (non-incomplete) `ANSWERED` count: that
route requires the legacy `READ_COUNTS` survey adapter
(`ReadQueries`'s `survey_store`/`survey_run_id`), and `replay_fixture`
constructs `ReadQueries` without one, so a `READ_COUNTS`-sourced completion
cannot currently be reproduced byte-stably by this module. The golden
fixture's `expected` block is `replay_fixture`'s own output against this one
frozen trace (computed once, then bound into the manifest), not an
independently re-derived value — exactly as the previous, hand-authored
fixture's `expected` block was.

This proves:

- the replayed final snapshot, decision stream, and rendered terminal report
  are byte-identical across repeated replay runs of the same fixture
  (`test_replay_is_byte_stable_across_repeated_runs`), and match the
  fixture's own manifest-bound `expected` result;
- tampering with the recorded stream — a missing, duplicate, reordered, or
  corrupted record; a perception-ingestion record whose declared snapshot
  version does not match the replayed session's actual state, or whose
  request was never actually authorized against it; a choice attempt whose
  declared snapshot version is stale; a tool outcome whose candidate_id or
  snapshot_version does not match the choice attempt it causally links back
  to; or a terminal `ANSWERED` outcome whose recorded text does not match
  what `render_answer` independently renders from the replayed facts — is
  rejected (`ReplayIntegrityError`), never silently reproduced;
- the projector code this replay depends on is explicitly version-bound
  (`sentinel.room.replay.PROJECTOR_REVISION`); a fixture bound to a
  different revision is refused rather than silently replayed against code
  that may no longer compute the same thing.

It does **not** prove: model quality, live provider behavior, real detector
accuracy, real flight/localization behavior, coverage-cell polygon
containment against a real site, grounded per-session counting (not
exercised — see the correction above), or anything about closer inspection,
certified landing or following (none of those are exercised). One fixture is
not a population estimate; it is a regression baseline against silent
projector drift.

## Limitations

- No fresh model or provider call is made anywhere in this plan's replay
  path; `READ_OBSERVATIONS`/`READ_COUNTS`/answer wording are deterministic,
  historical reads (`sentinel.room.queries`'s own module docstring). The
  fixture's own generation run used only in-repo test collaborators (a
  canned choice-judge, a deterministic checkpoint executor stub), never a
  live provider or hardware client.
- `REAL` execution mode never offers `OBSERVE`; count-and-return with actual
  flight is `SIMULATION`-only until Plan 06 promotes matching profiles.
- Coverage crediting trusts that a dispatched checkpoint actually reached the
  pose its candidate declared and that its own `CAPTURE` command completed
  (`RoomController._checkpoint_targets_declared_pose`,
  `RoomController._capture_completed`) — it does not independently verify
  per-detection geometric placement within a cell.
- The replay fixture is one generated scenario, not a corpus, and it
  completes through the `ANSWER:<objective>:INCOMPLETE` route rather than a
  plain numeric `ANSWERED` count (see "Replay" above for why). Extending
  `replay_fixture` to wire up a `survey_store` so a `READ_COUNTS`-sourced
  completion can also be replayed, and adding a corresponding fixture, is
  future work, not included here.
- Grounded per-session counting (`counting._static_counts`/`project_counts`)
  is validated as a unit (`test_uncertain_detection_stays_uncertain_after_observation`)
  but is not wired into any live or replayed `ANSWER`; see the correction in
  "Coverage and count semantics" above.

## Evidence

- `src/sentinel/room/replay.py`, `tests/room/test_replay.py`,
  `fixtures/room_agent/v1/{manifest.json,events.jsonl,frame-1.png}`.
- `tests/room/test_count_return.py` (Plan 03 Task 4's full controller/safety
  trajectory suite) for the safety-transition and count-semantics claims
  above.
- `rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room -q --tb=short -o addopts='' -p no:cacheprovider`
  — 521 passed at the commit this report ships with (see `task-5-report.md`'s
  "Fix round 1" section for the exact run this count is from).
