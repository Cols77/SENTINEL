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
  `sentinel.room.coverage.project_coverage`). The per-detection `(x, y)`
  estimates grounded counting now persists (below) are **not** tested for
  containment in a cell's polygon.
- **Counts are grounded in this session's own observations** (Plan 03
  residual task R, closing final-review finding F2). Every `OBSERVE_SUCCEEDED`
  outcome persists the frame's count evidence
  (`sentinel.room.grounding.ground_frame`): each static detection rayed onto
  its declared support plane from the checkpoint's *measured* post-dispatch
  pose (`spatial.estimate_detection`), the frame's people split
  (high-confidence confirmed, medium/low uncertain) and every detection that
  could not be placed, with the reason. A terminal count answer projects the
  session COUNT fact from that append-only evidence
  (`grounding.project_objective_count`: `association.associate_static`, then
  `counting.project_counts`) and renders it with `counting.render_count_answer`,
  including the scope-worded coverage caveat, latest capture and
  analysed/unanalysed frames. The projection is persisted on the terminal
  outcome (`count_projection`).
- Count semantics: a static object is **confirmed** only when seen from more
  than one distinct view by non-low-confidence sightings; a low-confidence
  sighting can create or join an object but never confirms it, so uncertain
  detections stay uncertain (`test_low_confidence_detections_stay_uncertain_in_the_terminal_answer`).
  People never enter association and are reported as the best single frame's
  lower bound ("at least N people visible; M additional detections are
  uncertain"). A detection that cannot be placed -- no count geometry
  configured, support undeclared for its class, stale pose, frame size not the
  camera model's, box not on the declared plane -- is reported per class as
  "could not be placed ... and is not counted", never guessed and never
  silently dropped. A class whose coverage cells are `NOT_RESOLVABLE` carries
  that caveat on its own line (residual F8).
- Placement needs a `CountGeometry` (camera model plus per-class support
  declarations) supplied to `RoomController`; it is controller configuration,
  not yet a `RoomSite` field. Without it every static detection is reported
  unplaced.
- **Answer routes for a `COUNT` objective, once observation is enabled:**
  - `ANSWER:COUNTS` (terminal `COMPLETED`, code `ANSWERED_COUNT`): offered
    only when every pending `COUNT` objective is ready -- a room-wide
    objective once declared coverage is complete, a region objective
    (`"left/rear/bottom"`-style scope) once its requested region's `OBSERVE`
    has succeeded -- and every other objective is answerable from persisted
    reads. A region answer is `VIEWPOINT`-scoped: it counts only that pose's
    frames against that pose's own declared cells.
  - `ANSWER:<objective>:INCOMPLETE` (terminal `INCOMPLETE_RETURNED`): a
    room-wide objective whose coverage no remaining legal view can improve, or
    a region objective whose region can no longer be observed (refused or
    unresolvable) without ever having succeeded. Its answer carries the same
    grounded count sections, each with its incomplete-coverage caveat.
  - The plain legacy `ANSWER` is never offered for a pending `COUNT`
    objective while observation is enabled (`RoomController._legacy_count_answer_blocked`),
    even when a `READ_COUNTS` survey report has been read
    (`test_completed_coverage_answers_and_stops_offering_observation`).
  Both answer routes request a supervisor-owned safe return first.
- Legacy `READ_COUNTS` (merged multi-view survey reports from
  `sentinel.survey`'s `SurveyStore`) remains a *read*. It can still complete a
  `COUNT` objective through the plain `ANSWER` only in a read-only session
  without observation collaborators (Plan 01's historical-read contract),
  where it is rendered with its own "full-room coverage ... unknown"
  uncertainty.
- `COUNT` objectives with a scope that is neither `ROOM` nor a semantic
  region (for example a current-view scope) have no count route; they end
  only through `ESCALATE`/`NO_LEGAL_CANDIDATES`.

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
  `test_stale_perception_result_is_refused_and_coverage_not_credited`);
- a version conflict raised after an earlier command of the same checkpoint
  already executed is recorded as `CHECKPOINT_FAILED`, never as a
  `STALE_DECISION` that would claim nothing was dispatched
  (`test_version_conflict_after_partial_motion_is_checkpoint_failed_not_stale`);
- a recorded `LANDED` state projects as `SAFE_LANDED` only when the same
  record says its completion is known; otherwise `ABORTED`
  (`test_landed_drone_state_is_safe_only_when_completion_is_known`).

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
ingests a single low-confidence computer detection, framed for the measured
Tello camera model (960x720, 8.6 degrees down) and placed on the floor from
the checkpoint's measured pose; its outcome persists that grounded count
evidence. A `READ_OBSERVATIONS` turn reads the raw detection back. Because
the second cell can never be resolved by any declared view, coverage can
never complete, and the only legal route to a terminal answer is the explicit
`ANSWER:g1:INCOMPLETE` candidate, whose answer carries the grounded count
("computer: 0 confirmed; 1 uncertain") and names the uncovered cell, requests
a supervisor-owned safe return, and completes as
`TerminalState.INCOMPLETE_RETURNED` once the landing is confirmed.
(Regenerated for Plan 03 residual task R; `PROJECTOR_REVISION` is now
`room-projectors-v2`, since `ReadQueries.project` restores count evidence.)
The golden fixture's `expected` block is `replay_fixture`'s own output
against this one frozen trace (computed once, then bound into the manifest),
not an independently re-derived value.

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
  to; a terminal `ANSWERED` outcome whose recorded text does not match
  what `render_answer` independently renders from the replayed facts; or a
  terminal count answer whose recorded COUNT fact, or the answer section
  rendered from it, does not match the counts re-derived from the replayed
  observation evidence — is rejected (`ReplayIntegrityError`), never silently
  reproduced;
- the projector code this replay depends on is explicitly version-bound
  (`sentinel.room.replay.PROJECTOR_REVISION`); a fixture bound to a
  different revision is refused rather than silently replayed against code
  that may no longer compute the same thing.

It does **not** prove: model quality, live provider behavior, real detector
accuracy, real flight/localization behavior, coverage-cell polygon
containment against a real site, a confirmed (multi-view) count or a
`COMPLETED` count answer (this fixture's one sighting is single-view and its
coverage is incomplete; those routes are covered by
`tests/room/test_count_return.py`), or anything about closer inspection,
certified landing or following (none of those are exercised). One fixture is
not a population estimate; it is a regression baseline against silent
projector drift.

**Fix round 3 (F10) correction:** this is projection-only replay, not
independently re-derived decisions or coverage. `decision_stream_sha256`
hashes the raw `ROOM_CHOICE_ATTEMPT`/`ROOM_TOOL_OUTCOME` records exactly as
the manifest recorded them; it does not recompute the legal candidate set a
turn actually chose from, or the coverage projection behind an
`ANSWER:<objective>:INCOMPLETE` outcome's coverage note. `report_sha256`
independently recomputes and cross-checks the terminal `ANSWERED` case
(`render_answer` against the replayed facts) and, since residual task R, the
*counts* of a terminal count answer (`ANSWERED_COUNT`/`ANSWERED_INCOMPLETE`
with a `count_projection`): they are re-derived from the replayed
observation evidence and must match the recorded projection and its rendered
answer section. The recorded *coverage* rows inside that projection are
still taken as given, and every other terminal code's answer text is hashed
as recorded. So a
regression in `coverage.py`, `candidates.py` or the controller's own
candidate-legality logic that still happened to reproduce the same recorded
bytes would not be caught by this replay path. Closing that gap would
require binding the site (and its coverage cells/declared visibility) into
the manifest and reconstructing the controller's own candidate/coverage
projection from the replayed state to compare against what was actually
recorded -- not attempted here, since it requires regenerating the golden
fixture against that richer binding, which risks invalidating the fixture
this fix round's own F1/F3/F4/F5/F9 controller changes were verified
against. Until that lands, "replay" in this report means projection-only
replay: byte-stable reproduction of a frozen trace through the store/query
projectors, not an independent re-derivation of the decisions or coverage
recorded in it.

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
  `RoomController._capture_completed`, `_measured_pose_matches_declared_pose`)
  — it does not test per-detection placement within a cell.
- Grounded placement uses one support declaration per class (for example,
  every computer on the floor). A class that rests on different surfaces in
  one room, or a held object, cannot be placed correctly by it; such
  detections should be left undeclared so they are reported unplaced.
- The replay fixture is one generated scenario, not a corpus, and it
  completes through the `ANSWER:<objective>:INCOMPLETE` route; the
  `ANSWER:COUNTS` completion route is exercised by the controller tests only.

## Evidence

- `src/sentinel/room/replay.py`, `tests/room/test_replay.py`,
  `fixtures/room_agent/v1/{manifest.json,events.jsonl,frame-1.png}`.
- `tests/room/test_count_return.py` (Plan 03 Task 4's full controller/safety
  trajectory suite) for the safety-transition and count-semantics claims
  above.
- `tests/room/test_grounding.py` (per-frame grounding and the COUNT
  projection) and `tests/room/test_count_return.py` tests 25-31 (residual
  task R: NB1 and the grounded count answer routes).
- `rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room tests/cube tests/survey/test_live_detect.py tests/survey/test_cube_bridge.py tests/survey/test_loop.py -q --tb=short -o addopts='' -p no:cacheprovider`
  — 1207 passed at the residual task R commit (see
  `.superpowers/sdd/2026-09-22-jev-room-agent-03-count-closer-return/task-R-report.md`).
