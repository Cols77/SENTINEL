---
id: sentinel-v0-coherence-spec
title: "SENTINEL V0 Coherence Specification"
status: review-pending
---

# SENTINEL V0 Coherence Specification

> **Status:** Revised after independent review; re-review pending
> **Authority:** Canonical V0 product/integration profile
> **Parent baseline:** `SENTINEL Demo State Database Specification @ document version 0.8`, at historical path [`docs/SENTINEL_Demo_State_Database_Spec_v0.2.md`](../../SENTINEL_Demo_State_Database_Spec_v0.2.md)
> **Contract revision:** `sentinel.v0`

## 1. Purpose

This profile makes the smallest supervised physical-AI loop implementable by separate coding agents without allowing UI, agent, perception or worker code to invent state semantics.

```text
operator request -> real LLM intent -> coordinator -> supervised Tello mission
-> selected evidence frame -> perception -> event/state -> mountain projection
-> operator Confirm/Reject
```

`V0` is the product release label. It is not the parent document version or a replacement filename.

## 2. PM decisions

| ID | Decision | Consequence |
|---|---|---|
| D-001 | WP0–WP3 are the immediate planning/implementation priorities | Each has one dedicated spec and a common contract revision |
| D-002 | Colin owns coordinator semantics; Alan owns the LLM adapter and WP2; Dzivha owns WP3 | No overlapping write ownership |
| D-003 | NiceGUI is the V0 UI | Replaceable read/command client only |
| D-004 | Mountain state is a fixed six-zone indoor-alley schematic | Fixture is `contracts/mountain-v0.fixture.json`; no GIS/SLAM/GPS |
| D-005 | First integration is transport-neutral and in-process | HTTP/SSE/broker are deferred transport decisions |
| D-006 | Judged path uses a real LLM; tests use a deterministic fake | Model provenance is required; fake fallback is forbidden in judged mode |
| D-007 | Provider is gated by a named joint Colin+Dzivha approval over a versioned test-image-set hash | An approved provider profile freezes score semantics and threshold; execution of the provider work is Dzivha's |
| D-008 | Judged path has one supervised real Tello | WP4 physical slice is a V0 release prerequisite, not an optional future capability |
| D-009 | Canonical V0 class is `rock` | Parent `pebble_cluster` source output maps to canonical `rock` under the versioned mapping below |
| D-010 | WP6 combines technical document and pitch material | It is non-normative and evidence-derived |
| D-011 | (Colin, 2026-09-20) The rail-sweep survey-and-count replaces the indoor-alley survey as the **demonstrated** scenario: recorded video 2026-09-25, live demo 2026-09-27 | The alley design, code and contracts are retained, not deleted. §4 and D-004/D-009 still define the *judged* V0 path and are not re-baselined by this decision; the survey flow runs under its own `sentinel-survey-v1` contract and claims no judged evidence. Re-baselining §4 is a separate decision that has not been taken |

## 3. Authority hierarchy and compatibility

1. This document selects the V0 profile.
2. The approved [indoor-alley survey design](2026-09-16-sentinel-v0-indoor-alley-survey-design.md) is the V0 decision record: it resolves the V0 room-demo decisions (six-zone route, station capture, layout modes, preflight/force-run/emergency semantics) that the coherence profile and WP0–WP4 below must conform to. It ranks just below this document.
3. [`WP0`](2026-09-16-sentinel-wp0-shared-coherence-contracts.md) and its machine-readable contract artifacts define executable shared semantics.
4. WP1–WP3 define component behavior only within WP0. The [WP5 cube design](2026-09-20-wp5-cube-positioning-design.md) and the [survey-counting design](2026-09-20-survey-counting-design.md) rank here too: component designs that may refine but not widen this document. The survey design declares exactly one exception to a WP3 rule (its §6.1) and nothing else in it overrides WP0.
5. The parent database specification remains the persistence/safety source unless the table below explicitly defines the V0 profile mapping.
6. Historical broad component drafts are supporting design history, not current V0 authority.

| Parent concept | Parent value | Canonical V0 representation | Rule |
|---|---|---|---|
| Parent document identity | filename `v0.2`, header `0.8` | document version `0.8`, historical path unchanged | WP0 records both identifiers |
| Detection class | `rock`, `pebble_cluster` | `canonical_class: rock`; `source_class` preserves either value | `pebble_cluster -> rock` uses `class_mapping_revision: v0-rock-only` |
| Detection score | `confidence` | `raw_score` plus `score_semantics` | no calibration/probability claim; provider profile supplies threshold |
| Detection event | `DETECTION_RECORDED` | `DETECTION_RECORDED` | persisted canonical event; provider-local completion is not a substitute |
| Section inspection status | `NOT_INSPECTED`, `INSPECTING`, `CLEAR`, `HAZARD_FOUND`, `FAILED` | `UNSURVEYED`, `SURVEYING`, `SURVEYED`, `HAZARD_PENDING`, `BLOCKED` | mapping is in the mountain fixture; `STALE` is freshness-derived only |
| Agent wait phase | `WAIT_FOR_DETECTION` | `WAITING_FOR_OBSERVATION` run state / `WAIT_FOR_OBSERVATION` agent phase | explicit V0 lifecycle below |
| Parent starting threshold | `0.90` | `provider_profile.decision_threshold` | Colin+Dzivha jointly approved a provider-profile threshold over a versioned test-image-set hash |
| Evidence provenance | `OPERATOR_PROVIDED` | explicitly prohibited on the judged perception/evidence path | V0 accepts operator commands, not operator-supplied detection evidence; retain only as imported historical metadata outside judged eligibility |
| Decision provenance | `INFERRED` | preserved as historical-only `decision_provenance` on imported parent task records | V0 decisions are operator Confirm/Reject; `INFERRED` cannot authorize an assignment or qualify evidence |
| Target lifecycle | `TARGET_CREATED` | explicitly prohibited as a new V0 persisted event | V0 has no independent target aggregate; qualifying `DETECTION_RECORDED` plus `TASK_CREATED` creates the reviewable hazard projection |
| Target lifecycle | `TARGET_CONFIRMED` | maps to versioned `OPERATOR_DECIDE` with `PATROLLER_ASSIGNMENT_CONFIRMED` | preserve parent event as imported history only; V0’s canonical persisted outcome is the assignment confirmation |
| Assignment event | `PATROLLER_ASSIGNMENT_PROPOSED` | `PATROLLER_ASSIGNMENT_PROPOSED` | persisted unchanged |
| Assignment event | `PATROLLER_ASSIGNMENT_CONFIRMED` | `PATROLLER_ASSIGNMENT_CONFIRMED` | persisted unchanged after operator Confirm |
| Assignment event | `PATROLLER_ASSIGNED` | explicitly prohibited as a new V0 event | assignment is represented by `PATROLLER_ASSIGNMENT_CONFIRMED`; preserve imported parent event as historical-only |

No component may use a parent value outside this table without a WP0 compatibility amendment.

## 4. Judged V0 scenario

Operator request:

```text
Inspect the left and right table alleys for rocks
```

1. NiceGUI submits `RUN_CREATE` through `CoordinatorPort`.
2. The real LLM returns one schema-validated agent intent proposing a catalogue-bound plan reference.
3. The coordinator records `MISSION_CREATED`, records `DRONE_DISPATCH_REQUESTED`, and creates the immutable plan record bound to that plan version/hash.
4. The operator explicitly approves that exact plan version before any dispatch; the approval binds the plan hash, worker, choreography, run/state version and expiry.
5. WP4 executes the pre-authorised named choreography on one Tello.
6. WP4 stores a selected live JPEG/PNG through the coordinator-managed artifact contract.
7. WP3 calls the frozen provider profile and returns a hash-bound `PerceptionResult`.
8. Coordinator ingests it as `DETECTION_RECORDED`, updates the mountain snapshot and applies the frozen threshold.
9. A qualifying scored result enters `WAITING_FOR_APPROVAL`; lower, scoreless or provider-failed results enter `NEEDS_REVIEW` or `FAILED` by the transition matrix. `NEEDS_REVIEW` is a review-hold, not a terminal outcome; it is escaped only by an approved new/revisit plan.
10. Confirm or Reject is a versioned, idempotent `OPERATOR_DECIDE` command — the separate assignment decision, distinct from the plan-approval in step 4. `REJECTED` is event-only; a rejected proposal is refined and re-proposed.

Live: LLM, one Tello, preflight/flight/video/still capture, selected detector provider, coordinator persistence and operator decision.

Simulated: fixed six-zone geometry, nonvisited context, patrollers, travel time and external notifications.

The only terminal run outcomes are `COMPLETED`, `FAILED` and `EMERGENCY`. `NEEDS_REVIEW` is a review-hold, not a terminal outcome. `REJECTED` is event-only and never terminal: reject → refine → re-propose.

Replay is rehearsal-only and is rejected in a judged run.

## 5. Mountain-state fixture

The normative fixture [`contracts/mountain-v0.fixture.json`](contracts/mountain-v0.fixture.json) defines exactly six fixed zones:

- `LEFT_ALLEY_FRONT`, `LEFT_ALLEY_MIDDLE`, `LEFT_ALLEY_BACK`;
- `RIGHT_ALLEY_FRONT`, `RIGHT_ALLEY_MIDDLE`, `RIGHT_ALLEY_BACK`.

The normal mode is marker-verified. `ROUTE_ONLY_2_ZONE` is the explicit fallback and activates only `LEFT_ALLEY_MIDDLE` and `RIGHT_ALLEY_MIDDLE`; it never claims visual marker verification. A marker-mode run fails when an expected marker is missing. Snapshot/state/event advancement is defined normatively in WP0 §3.4.

All coordinates are local schematic `0..100`, not geographic. Each section polygon is a closed, non-self-intersecting `(x,y)` ring: first point equals last point and every coordinate is in inclusive `0..100`. `STALE` is not persisted section status: it is a derived boolean when `now - as_of > snapshot_stale_after_ms`, or when `snapshot_version < StateSnapshot.latest_event_sequence`. The UI renders unknown/stale values; it never interpolates them.

## 6. Shared artifacts

| Artifact | Authority | Consumers |
|---|---|---|
| [`contracts/sentinel-v0-contract.schema.json`](contracts/sentinel-v0-contract.schema.json) | WP0 canonical public root (directly-validatable compatibility root) | all WPs, reviewers |
| [`contracts/sentinel-v0-wire.schema.json`](contracts/sentinel-v0-wire.schema.json) | WP0 direct-validation `oneOf` typed port registry | WP1–WP4 |
| [`contracts/mountain-v0.schema.json`](contracts/mountain-v0.schema.json) + [`contracts/mountain-v0.fixture.json`](contracts/mountain-v0.fixture.json) | WP0 | WP1, WP2 |
| [`contracts/provider-profile-v1.schema.json`](contracts/provider-profile-v1.schema.json) | WP0 | WP1, WP3 |
| [`contracts/evaluation-manifest-v1.schema.json`](contracts/evaluation-manifest-v1.schema.json) + [`contracts/provider-profile-unapproved.template.json`](contracts/provider-profile-unapproved.template.json) | WP0 | WP3, PM |
| [`WP4 supervised Tello/live video`](2026-09-16-sentinel-wp4-supervised-tello-live-video.md) | WP4 | WP1, WP2, release reviewers |
| selected provider profile | WP3; approved jointly by Colin+Dzivha; execution Dzivha | WP1–WP3 |
| SQLite DDL/constraint manifest | WP0 declaration, WP1 implementation | WP1, reviewers |
| state/event fixtures | WP0 | all WPs |

## 7. Work-package critical path

```text
WP0 executable contracts
  -> WP1 coordinator + deterministic fake adapters
  -> WP2 NiceGUI client and WP3 provider adapter in parallel
  -> integrated simulated V0 path
  -> WP4 physical worker compatibility adapter and supervised rehearsal
  -> V0 release
```

WP4 is deferred only as a detailed work-package specification, not from the V0 release. It must consume the same WP0 worker/operation contracts as the fake worker.

### 7.1 Bounded WP4 interface and supervised rehearsal (release prerequisite)

This bounded release-prerequisite slice does not change the immediate WP0–WP3 documentation priority order. `WorkerPort.dispatch` consumes `https://sentinel.local/contracts/sentinel-v0-wire.schema.json#WorkerDispatch` bearing the immutable `SurveyPlanReference`, its `approval_reference` and the exact `PreflightReport`, and returns `#ResultEnvelope`; `WorkerPort.reconcile` consumes operation ID/idempotency key and returns `#ReconciliationResult`; emergency ingress uses `#EmergencySignal`. The worker must create no alternate wire format and must honor at-most-one coordinator dispatch before reconciliation.

The only permitted physical rehearsal is a named, pre-authorised choreography on one supervised Tello with an operator present and emergency stop available. Real flight is DJITelloPy-only, each real run starts in a fresh process with a fresh preflight and `--real --confirm`, and dispatch binds the approved plan hash plus approval. Before takeoff exactly the 8 named preflight checks must pass — `WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH` — and `passed` is the conjunction of the eight. Operator authorization is not a ninth check: it is carried as the plan's `approval_reference` and evaluated by the coordinator at dispatch. During the run, `WorkerPort.read_video_status` must remain `LIVE` and `WorkerPort.open_live_video` must yield the read-only safe handle; it returns a `LIVE` selected JPEG/PNG `ArtifactRef` correlated by ID/SHA-256 to that live status. At each approved station WP4 captures one to three frames with full provenance. Perception may run asynchronously and has no worker/control port, so it can never mutate an in-flight route. The judged perception request/result must both be `LIVE`, hash-bound, and profile-bound. Landing is permitted only while no active latch is armed; the `EMERGENCY` latch is terminal, non-overridable, and permits no landing/cleanup/retry afterward. A failed preflight may be overridden only by an operator-only `FORCE_RUN_CREATE` naming a new run/plan (no resume of the prior operation, never under an active latch) with fresh preflight and `--real --confirm`. WP4 records operation lifecycle/reconciliation outcome, emergency behavior if invoked, and safe landing; it does not introduce autonomy, multi-drone work, or live patroller assignment.

WP4 acceptance command (implementation work, not evidence fabricated by this spec): `uv run python -m pytest tests/worker tests/integration -q -k "tello_rehearsal"`. Acceptance additionally asserts persisted `RUN_CREATED`, operation transitions, a `LIVE` artifact/hash, `LIVE` perception result, event/snapshot correlation, operator decision path, and no simulated/replayed judged evidence. G4 remains required for V0 release.

## 8. Acceptance gates

| Gate | Owner | Required evidence |
|---|---|---|
| G0 Contract | Colin | schema/fixture validation, DDL constraint manifest, contract tests |
| G1 Coordinator simulation | Colin + Alan | deterministic fake-agent replay, duplicate and `IN_DOUBT` tests |
| G2 Human surface | Alan | NiceGUI smoke fixture, click-through evidence, Confirm/Reject stale-version test |
| G3 Perception | execution: Dzivha; approved/reviewed: Colin + Dzivha | reproducible evaluation-manifest evidence and a named joint approval over the versioned test-image-set hash |
| G4 Physical | Colin | supervised one-Tello rehearsal using WP0 operation/evidence contracts |

V0 release requires G0–G4. G0–G3 can be completed without live hardware; G4 cannot be substituted by dry-run success.

**G3 is BLOCKED.** The only checked-in provider-profile instance is the `UNAPPROVED` template. No provider profile may be used in the judged path until Colin and Dzivha together make a named joint approval over a versioned test-image-set hash for a concrete instance, with reproducible evaluation-manifest evidence. Execution of that provider work is Dzivha's. This specification intentionally declares no provider name, score threshold, evaluation data, or benchmark result.

## 9. Non-goals

V0 excludes real GIS/terrain, GPS/global localisation, multi-drone coordination, autonomous patroller dispatch, external notification, hidden chain-of-thought display, automatic provider failover and a broker.

> **Amendment (2026-09-17, Colin Aubé, PM authority):** one safety carve-out is
> added to these non-goals: autonomous **geofence hard stop** (SR-SENTINEL-V0-W08).
> The worker continuously verifies its AprilTag-derived position against the
> configured room safety envelope and, on envelope exit, issues the terminal
> emergency hard stop without waiting for coordinator or operator action. This is
> a fail-safe reflex, not autonomy: it can only stop the drone and is always
> ingested as `EMERGENCY_LATCHED`. No other autonomous motion, correction or
> dispatch is introduced.

> **Amendment (2026-09-21, Colin Aubé, PM authority):** the reflex above is a **controlled
> landing** through the existing abort policy, not the motor cut, and the envelope is the
> operating cube derived from the site file (W08-a and W08-c, §10.3). The run is reported as
> failed with the containment reason; `EMERGENCY_LATCHED` and the motor cut remain the
> operator's emergency control. "Continuously" means at every localised stop and during moves
> whenever tags are in view (W08-b).

V0 includes host-side AprilTag-based room-local pose estimation emitted as `DRONE_POSE_REPORTED`. Pose never originates a target and never acts while the drone is moving; the uses it is permitted are enumerated in §10.2, which is the single statement of that boundary.

## 10. WP5 drone pose amendment (contract-amendment)

> **Revised 2026-09-21 (coherence alignment).** The original single paragraph is replaced by the
> subsections below. It mixed contract, implementation and safety statements, and several of them
> had become false; §10.7 lists each change and the evidence for it. The heading is unchanged so
> that the `source:` anchors of SR-060…SR-063 still resolve.

### 10.1 Contract surface (normative)

The WP0 contract is amended by this specification. WP5 adds:

- a closed `DronePoseReport` wire object in the `sentinel-v0-wire.schema.json` `oneOf` registry
  (unknown fields rejected; `method: APRILTAG_36H11_PNP`);
- `DRONE_POSE_REPORTED` in the WP0 §3.5 event enum;
- a `drone_pose` snapshot read model subject to the WP0 §3.4 staleness rule;
- the `tag-map-v1` schema, with `room_scale_m_per_unit` fixed at 0.1 for V0 determinism.

Pose estimates are room-local, carry `source_kind` provenance (`LIVE`/`SIMULATED`/`REPLAYED`) and
are never a GPS claim. The ingestion slice appends run-scoped `DRONE_POSE_REPORTED` events to the
SQLite store and projects them into `drone_pose`; the full coordinator remains the WP1 boundary.

### 10.2 Safety boundary (normative)

**Pose never originates a target and never acts while the drone is moving.** It may end a flight
through the existing abort path, and at a stationary hover it may correct where the runner
believes the drone is. It does nothing else.

| Use of pose | Status | Authority |
|---|---|---|
| Evidence, display, `drone_pose` read model | permitted | SR-060, SR-061, SR-062 |
| Verifying the zone of a capture | permitted | SR-SENTINEL-V0-W07 |
| Advisory containment: alert the operator; trip the **existing** abort policy | permitted | SR-063 (as revised at consent 2026-09-18); SR-SENTINEL-V0-W08; §9 amendment |
| At a stationary hover, after the quality and plausibility gates: replacing the open-loop position estimate and sending at most two rounds of trim moves and one trim rotation toward the already-approved waypoint, each validated by the envelope (`reanchor`) | permitted since 2026-09-21 | SR-063 as amended at consent 2026-09-21 (C1); cube design §5 C1 |
| Steering, route correction, originating a target, any control while the drone is moving | excluded from V0 | SR-063; W07 |

`runner.py` consumes no pose data (SR-063 AC-1); re-anchoring lives in the cube mission runner. A
detector fault can produce wrong advisory data, an unnecessary abort, or at worst a misplaced
start of one leg — and that only after the quality gate, the plausibility gate (within 60 cm of
dead reckoning), the envelope and containment have all passed.

### 10.3 SR-SENTINEL-V0-W08: three decisions taken, one half still to build

W08 and SR-063 agree in kind: both let pose end a flight and neither lets it steer one. Three
differences between W08's 2026-09-17 text and the implementation were found on 2026-09-21:

| # | 2026-09-17 text | Implemented | Status |
|---|---|---|---|
| W08-a | the reflex is the *terminal emergency hard stop* (motor cut, W05 latch) | a measured position outside the cube raises an abort that ends in a **controlled landing** | **decided 2026-09-21 (Colin): controlled landing.** A single-tag fix is realistically noisy wherever only one tag is in view (measured 10.4-12.5 cm spread off-axis), so a false `OUTSIDE` is realistic there; with two tags in view the joint solve measured 0.45 cm at the same range, so a false `OUTSIDE` is implausible at a stop where both are seen. A motor cut at up to 1.8 m near people is the more harmful failure regardless. W08 amended |
| W08-c | the envelope is *the 1 m demo cube of W03* | the envelope is derived from the site file | **decided 2026-09-21 (Colin): the configured envelope.** W08 amended |
| W08-b | the worker verifies position *continuously* | checked only at localised stops | **decided 2026-09-21 (Colin): at every localised stop, and during moves whenever tags are in view.** W08 amended; the in-move half (level 2 below) is built, not yet bench-tested |

**W08-b: what "continuously" can mean.** Navigation is open-loop. A waypoint is reached with at
most three blocking Tello `move` commands (x, then y, then z), each validated against the envelope
*before* it is sent, from a position that is dead-reckoned: after an `ok` the runner simply assumes
the commanded endpoint, and only altitude is read back from a sensor. The drone executes each
`move` with its own onboard optical-flow loop; the host cannot adjust a leg in flight. Pose enters
only at a stop, after the drone has settled. Three levels are possible:

| Level | What pose does | Requirement impact | State |
|---|---|---|---|
| 1. Stop-time check (`passive`) | measured at each localised stop; outside the cube means a controlled landing | none: SR-063 as written | implemented |
| 2. In-leg watchdog | the pose stream, which already stays open all flight, is also evaluated *during* legs whenever tags are in view; outside the cube means a controlled landing. It can end the flight and nothing else | none on SR-063; **required by W08 since 2026-09-21**. Needs a second thread beside the blocking `move`, and a bench test that the Tello honours `land` while a `move` is executing | built (`sentinel.pose.watchdog` + `CubeMissionRunner.move_checked`); sending `land` mid-move is behind a flag defaulting off, pending bench test B1 |
| 3. Stop-time correction (`reanchor`) | a good fix replaces the dead-reckoned position at a hover and sends at most two bounded trim moves toward the approved waypoint | SR-063 amendment C1, **consented 2026-09-21 (Colin)** | implemented; run with `cube-run --pose reanchor --sr063-amended`; not yet flown (cube design Stage 3) |

Continuous *steering* from pose — a velocity loop on `rc` commands — is excluded from V0 by SR-063
and W07, and is not a safe proposition on this hardware: pose arrives at video rate with several
hundred milliseconds of latency, only while tags are in frame, and its accuracy in motion is
unmeasured. Levels 2 and 3 are independent and both are now adopted: level 3 by the SR-063
amendment, level 2 by W08. Level 2 is owed by WP5; until it is built and bench-tested **no
artifact may claim W08 satisfied**.

### 10.4 Implementation (informative, owned elsewhere)

This specification no longer pins the detector. Its original text named `SOLVEPNP_IPPE_SQUARE`;
that is now only the single-tag path, and two or more tags are solved jointly. The detector, localisation burst,
containment check and mission runner are specified by the
[WP5 cube design](2026-09-20-wp5-cube-positioning-design.md), which is a component design within
this profile and may not widen §10.2.

### 10.5 Camera model (normative, single source)

One camera model applies to every consumer of Tello 960x720 stream pixels: pose, cube observation
planning and survey geometry alike. Its source is the chessboard calibration in
[`calibration/tag-map-calibrated.json`](../../../calibration/tag-map-calibrated.json)
(`calibration_source: chessboard`), validated by the pose accuracy measured with it (1.0 cm at
40 cm, 2.1 cm at 80 cm):

| Quantity | Value |
|---|---|
| Focal length | fx 892.6 px, fy 893.7 px |
| Principal point | cx 467.8, cy 353.8 |
| Field of view of the stream | 56.5° horizontal, 43.9° vertical |
| Optical-axis pitch | about 8.6° **down**; the horizon sits near image row 219, not row 360 |
| View relative to the horizon | about 13° above to 31° below |

The datasheet figure of 82.6° does **not** describe the 960x720 stream, under either reading:
taken as a horizontal angle it gives f = 547 px, taken as a diagonal it gives f = 683 px, and the
measured value is 893 px. The nominal intrinsics (`calibration_source: nominal`, fx 547) remain
valid for **synthetic rendered frames only**, where the renderer and the solver share them;
`pose-stream` and `pose-score` already refuse a nominal map on live data.

The calibration belongs to one airframe. A different Tello is re-calibrated with
`scripts/calibrate_camera.py` before its pixels are trusted, and the pitch is re-measured from the
horizon row.

Known non-conforming artifacts as of this revision, each to be corrected by its owner:

| Artifact | Uses | Consequence |
|---|---|---|
| WP5 cube design E4 and `sentinel.cube.observe` | field of view 70.2° x 55.6° | view width per unit of range is taken as 1.41 where the measured figure is 1.08, so a "back-and-wide" stop stands about 30 % too close to span the alley mouth it promises to; and the bottom of the view is taken as 36.4° below the horizon where the measured figure is about 31° |

(The survey-counting design carried the same error, f = 683 px with a level optical axis, until
its rev 5 of the same date; see that document's §5.1.)

### 10.6 Acceptance and gates

All development and CI use synthetic rendered tag frames only; no implementation task connects to
a live drone. Acceptance is a user-approved supervised physical test run (detector works, database
position updates, real-time x/y/z terminal logging).

- Every real flight is behind the **G4** operator consent (owner: Colin), fresh per run.
- Motors-off stages (`pose-stream`, the static stand test) need no flight gate.
- **G3 does not gate pose.** G3 is the perception-provider approval of §8 and is currently
  BLOCKED; it applies only when a run feeds the judged perception path. The earlier phrase "G3
  owner approval" attached to pose flights was a category error and, read literally, made every
  WP5 flight wait on a rock-detection provider profile.

### 10.7 What this revision changed, and why

| Original statement | Problem | Now |
|---|---|---|
| "pose never mutates flight state" / "never a flight-control input" | contradicted the §9 W08 carve-out in the same document, SR-063 as revised at consent, FEAT-021 and the implemented `passive` mode, all of which let pose trip the abort path | §10.2 |
| "`SOLVEPNP_IPPE_SQUARE`" | implementation detail in the top-authority document; stale since the joint multi-tag solve | §10.4 |
| "nominal intrinsics … fx 547" stated without scope | read as the camera model; three focal lengths were in use across the repository | §10.5 |
| "safety: G3 owner approval" | G3 is the perception gate, not a flight gate | §10.6 |
| §9 "GPS/localisation" | SR-061 AC-3 requires "GPS/global localisation"; the wording was lost when the integration branch merged §9 | §9 |
