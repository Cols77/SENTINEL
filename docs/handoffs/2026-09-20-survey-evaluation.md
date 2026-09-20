# Survey evaluation readiness — 2026-09-20

Read-only survey source inspection at 73846e4 (feat/survey-counting), following f8d0f61 (candidates), 341eaa1 (geometry/merge), and 4eb271d (detector). Handoff resides on P3b1, inspected at f13568b. No survey production file was modified. No tests, model calls, flight, or capture were run for this review. Reproductions below are deterministic proposed checks and source-derived expected outcomes, not claimed execution results.

## Landed and missing

Present: envelope/options/observability, detector doubles, geometry/merge, store, candidates/context, F/E/J proposers and corresponding unit test files. Absent at inspected HEAD: survey/loop.py, report.py, consent.py, eval.py, cube bridge and the Task 9 episode harness. Consequently Scene, load_scene, score_episode, and compare_modes remain plan interfaces, not callable production APIs.

Tasks 1–10 do not depend on WP5 or a paid provider. Real cube integration remains gated on WP5 integration; provider evidence remains gated on approved provider work and authentic labelled captures. The transitive sentinel.coordinator import from candidates remains a runtime dependency. Existing module/test files are presence evidence only; this review makes no passing-test claim.

## Current source defects and reproduction recipes

1. **Close look claims a range its stops cannot attain.** In options.generate_options, use SweepEnvelope(-150,150,0,100,80,180,600). RAIL_HIGH_WIDE and CLOSE_LOOK_CENTRE both fly at y=100, but advertise ranges 600 and 300 respectively. Phone pixels are 17.075 at 600 cm, below the 20 px threshold, while close look advertises the phone as resolvable at 300 cm. A common target plane at y=700 is still 600 cm from either rail. Expected acceptance: advertised range/resolvability must follow actual permitted stop geometry; a follow-up cannot earn credit for an invented approach.

2. **Axial pinhole depth is used as radial distance.** In geometry.position_of, an untilted camera at (0,0), height 55 cm over the measured plane, looking along +y, observes a target (150,300). Independently project u=480+683*150/300=821.5 and v=360+683*55/300=485.2166667; use a 20-by-20 bbox centred there. The current function computes approximately (134.164,268.328), rather than (150,300), because it normalizes the horizontal ray before multiplying by axial depth 300. Expected acceptance: recover (150,300), including camera translation and heading/look rotation. Existing test generators use hypot(dx,dy) in the vertical projection, matching this implementation error rather than an independent pinhole camera model.

3. **Distinct detections in one frame can become one object.** merge.associate has no exclusion for groups sharing a frame/stop. Construct two high-band computer sightings with different IDs but the same frame and stop, camera origin (0,0), depth 300, bbox centres u=480 and u=480+683*30/300, both with v=360+683*55/300. Their estimated separation is about 30 cm, below the 75 cm tolerance; current grouping produces one uncertain object instead of preserving the two visible detections. Repeat at three stops: count confirmation must not erase this exclusivity constraint. Expected acceptance: one object group contains at most one independently individuated detection from the same frame; cross-frame duplicate handling is a separate concern.

4. **Store-to-merge contract is incomplete.** SurveyStore.sightings() selects g.* (including bbox_json) without decoding bbox, and omits f.delta_z_cm. Passing its rows directly to merge_sightings makes _locate see no bbox and returns only unpositioned objects. Reproduction: create a run/sweep/frame with delta_z_cm=80, add a boxed computer sighting, obtain store.sightings(run_id), and inspect its keys before passing it to merge. Expected acceptance: explicit documented normalization supplies decoded bbox and the actual frame height above the surface. Do not silently use 55 cm at every altitude. The future loop could supply normalization, but no such loop exists at this HEAD.

5. **Follow-up coverage credit overstates the footprint.** _addresses in candidates expands min/max stop x by half viewed_width_cm, although _rail already includes the complete rail span in that width. For a 300 cm wide rail at range 300, the true union extends approximately -360.8 to 360.8 cm, while candidate matching extends approximately -510.5 to 510.5. An uncertain computer at x=450 is credited outside the projected union. Candidate matching also ignores object y/depth, so a class deemed resolvable at the nominal plane earns credit even behind the camera or much farther away. Expected acceptance: independent visibility and resolution calculations gate addresses.

6. **Depth spread conflates viewpoint changes with bad geometry.** _object_from_group flags raw range spread over 30 percent regardless of whether all reconstructed room positions agree. The existing test test_depth_spread_over_30_percent_marks_the_object_uncertain_and_degraded explicitly pins that behavior for a close target. This is a design-level measurement limitation: a correct close approach can worsen the degradation metric. Acceptance should distinguish expected changes in camera-target range from disagreement in reconstructed position, and retain an independently projected near-target scene.

7. **Advisor boundary has unchecked response shapes.** LlmProposer.decide calls .get on tool_calls[0] and on truthy arguments without requiring dictionaries. Inputs with tool_calls=[null] or list-valued arguments escape as AttributeError, rather than escalating. JevProposer likewise assumes the judge result is a mapping after the choice call. These are source-visible boundary gaps; add focused malformed-response cases before treating the seam as fail-closed.

## Acceptance scene requirements

Synthetic scenes must be explicitly labelled synthetic and use an independent forward camera projection. They verify arithmetic/control flow and cannot substitute for provider quality or real-flight evidence.

Required deterministic scene families:

- Empty scene/hard negatives; no invented objects and no falsely successful episode on escalation.
- One static object seen from three genuinely distinct viewpoints; zero count error and full localisation.
- Two nearby same-class objects present together, plus a lattice of regularly spaced objects; no collapse or duplicate confirmation.
- Off-axis objects at nonzero camera positions/headings and differing surface heights; correct inverse geometry.
- Initial uncertainty for a small object with a physically achievable follow-up; the chosen option's actual frames must improve observation. Include a narrow envelope that cannot accommodate the hard-coded centre ±40 cm stops.
- Mobile people seen repeatedly; single-frame lower bound, not accumulation across stops.
- Missing/failed/late frame, absent bbox, unknown class, malformed proposer result, denied approval and exhausted two-flight budget; honest terminal status and limitations.

Authentic acceptance needs both frame-level hand-labelled boxes on varied distances, illumination, blur and negatives (including choreography-representative frames, identified by evaluation_pack_sha256), and episode manifests recording the measured objects/locations actually present. Keep paths, frame hashes, recorded provider responses and capture/command timestamps. Do not manufacture real labels or inferred measurements.

## Planned interface cautions

Task 9 in docs/superpowers/plans/2026-09-20-survey-counting.md specifies Scene(scene_id, objects, frames_dir, responses_dir), load_scene(path), score_episode(scene,outcome,store,run_id) and compare_modes(scene,policies,...). Its example code must not be copied unchanged:

- Test _script() centres bboxes at y=320, above the new ground-plane horizon; it cannot produce the advertised perfect static count. The fake judge accepts only keyword arguments, while the landed Jev proposer calls choice(state,instruction,...,name=...); that fake silently escalates. Merely checking illegal_calls == 0 does not prove successful F/E/J episodes.
- Rev-4 V8c requires review-turn uncertainty and chosen/best-available addressed sets. The task-body scorer merely reads a missing outcome.decision_quality. Persist those events before computing the metric. Do not award follow-up credit from nominal range alone.
- Compare modes with fresh store, flight and replay state, identical ground truth, and option-specific recorded frames. A fixed scripted response sequence across different sweeps can manufacture equivalence. Record terminal status, turns/model calls and flights in addition to numeric scores.
- The task-body illegal-call count scans escalation strings rather than validating every emitted call against the actual legal set. Measure name and arguments from the decision log before normalization; otherwise discarded illegal attempts become invisible.
- Task-body report consistency is true for every status except REPORT_REJECTED, including runs that never submitted a report. Track report presence/validation explicitly to avoid an escalation masquerading as successful reporting.
- Localisation must enforce one-to-one matching independent of input order; the task-body greedy first match is order-sensitive. Count metrics should retain the reporting band's semantics, including mobile lower bounds and non-observable classes.

No three-mode score table is claimed here. The harness, physically valid follow-up geometry, corrected projection/exclusivity and authentic acceptance evidence remain outstanding.

## Evaluator-owned implementation update

P3b1 now contains an independent `sentinel.survey_eval` module and explicitly
synthetic manifests under `fixtures/survey_eval`. This module does not import or
modify Claude's survey production code. It implements version-1 manifest
validation, independent axial-depth forward projection, strict option-indexed
replay, visible/resolvable truth unions, and maximum-cardinality one-to-one
localisation matching. Count intervals distinguish uncertain/lower-bound claims
from exact counts. Missing reports leave report validity and count error unset.

Public interfaces:

- `load_scene(path) -> Scene`
- `project_object(scene, object, pose) -> projection | None`
- `replay_option(scene, option_id) -> tuple[frame, ...]`, raising `Unevaluable`
  for missing or unanalysed observations.
- `coverage(scene, option_ids, previous_option_ids=()) -> dict`
- `score_episode(scene, outcome, reported_objects=None, class_counts=None,
  selected_option_ids=None, match_radius_cm=50) -> dict` (after outcome all
  arguments are keyword-only).
- `compare_modes(scene, episodes) -> dict[mode, score]` takes explicit episode
  payloads, not policies. No production-loop execution is implied.

Precision and recall describe room-localized object hypotheses supplied by the
caller. They do not represent provider bbox precision/recall. Ground-truth
coverage is separately computed from poses, calibration and occlusion metadata.
The fixture README documents expected scene outcomes and limits. Production
loop wiring and authentic labelled frame evaluation remain separate work.

Verification of this evaluator-owned update: initial RED had 15 expected
failures for missing module/assets. After implementation and two fixture/bounds
checks, `rtk proxy uv run pytest tests/test_survey_eval.py -q` passed all 17 tests
in 0.36 seconds. These are evaluator unit tests, not the survey production suite.
Survey production HEAD was rechecked at `73846e4`; no model calls or flights ran.

**Production F/E/J acceptance is BLOCKED.** The evaluator compares explicitly
supplied evidence only. No completed production survey loop adapter or measured
F/E/J survey comparison exists in these assets. Passing synthetic arithmetic
tests is not evidence that any production proposer chose a useful flight.

Review correction: selected_option_ids now requires complete replay and scopes
observed precision/recall to independently visible, resolvable, unoccluded truth.
Whole-scene coverage/localisation are separate. Without selected options the
scope is explicitly whole_scene_hypotheses, with no observed-recall claim.
Report emission requires an explicit payload independently of terminal status;
an escalated episode can carry a valid lower-bound report. Empty/missing count
evidence never invents an exact zero. unique_count_error requires exact report
count evidence across all relevant classes. Fixtures now include distinct
cross-frame duplicates, initial uncertain observations and rotated/elevated
surface geometry.

Review-fix verification: RED reproduced eight scoring/fixture defects (18 other
tests passed); after corrections the evaluator suite passed all 28 tests in
0.43 seconds. This remains synthetic evaluator evidence only, not production
F/E/J acceptance or real-frame provider evidence.
