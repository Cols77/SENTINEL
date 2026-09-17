# SENTINEL V0 Coherence Specification

> **Status:** Revised after independent review and V0 scope decision; re-review pending
> **Authority:** Canonical V0 product/integration profile
> **Parent baseline:** `SENTINEL Demo State Database Specification @ document version 0.8`, at historical path [`docs/SENTINEL_Demo_State_Database_Spec_v0.2.md`](../../SENTINEL_Demo_State_Database_Spec_v0.2.md)
> **Contract revision:** `sentinel.v0`

## 1. Purpose

This profile makes the smallest supervised physical-AI loop implementable by separate coding agents without allowing UI, agent, perception or worker code to invent state semantics.

```text
operator request -> real LLM intent -> coordinator -> supervised Tello mission
-> selected evidence frame -> perception -> event/state -> mountain projection
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
| D-011 | V0 ends at a coordinator-persisted zone-level perception result | The UI renders each result as a coordinator-owned fact; operator approval is reserved for proposed plans and future agentic actions, while aggregation, assignment, DB-state review and automatic revisit are deferred |
| D-012 | Live frame capture plus `VideoStatus` is sufficient for V0 | `SafeVideoStreamHandle`, browser-stream serving and richer video transport remain retained V0.1 contracts |
| D-013 | Contract scope is staged without deletion | Existing schemas, command/event branches and fixtures remain retained; deferred means not required, instantiated or emitted by the V0 path |

## 3. Authority hierarchy and compatibility

1. This document selects the V0 profile.
2. The approved [indoor-alley survey design](2026-09-16-sentinel-v0-indoor-alley-survey-design.md) is the V0 decision record: it resolves the V0 room-demo decisions (six-zone route, station capture, layout modes, preflight/force-run/emergency semantics) that the coherence profile and WP0–WP4 below must conform to. It ranks just below this document.
3. [`WP0`](2026-09-16-sentinel-wp0-shared-coherence-contracts.md) and its machine-readable contract artifacts define executable shared semantics.
4. WP1–WP3 define component behavior only within WP0.
5. The parent database specification remains historical persistence/safety context; this document and WP0 explicitly supersede its broader product/V0 behavior where the mapping below defines the current profile.
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
| Decision provenance | `INFERRED` | preserved as historical-only `decision_provenance` on imported parent task records | V0 operator decisions approve plans/future agentic actions; `INFERRED` cannot authorize an assignment or qualify evidence |
| Target lifecycle | `TARGET_CREATED` | retained deferred V0.1 contract | V0 has no independent target aggregate; the coordinator persists a zone-level `PerceptionResult` as a read-only fact |
| Target lifecycle | `TARGET_CONFIRMED` | retained deferred V0.1 contract | DB-state review is deferred; V0 does not emit `ZONE_RESULT_REVIEWED` |
| Assignment event | `PATROLLER_ASSIGNMENT_PROPOSED` | retained deferred V0.1 contract | not emitted or required by the active V0 path |
| Assignment event | `PATROLLER_ASSIGNMENT_CONFIRMED` | retained deferred V0.1 contract | not emitted or required by the active V0 path |
| Assignment event | `PATROLLER_ASSIGNED` | retained deferred V0.1 contract | preserve imported parent event as historical-only; no V0 assignment |

No component may use a parent value outside this table without a WP0 compatibility amendment.

## 4. Judged V0 scenario

Operator request:

```text
Inspect the left and right table alleys for rocks
```

1. NiceGUI submits `RUN_CREATE` through `CoordinatorPort`.
2. The real LLM returns one schema-validated agent intent proposing a catalogue-bound plan reference.
3. The coordinator records the active `RUN_CREATED`/plan lifecycle and creates the immutable plan record bound to that plan hash. Retained `MISSION_CREATED` is not emitted by the active V0 path.
4. The operator explicitly approves that exact immutable plan (its plan hash is bound) before any dispatch; the approval binds the plan hash, worker, choreography, run/state version and expiry.
5. WP4 executes the pre-authorised named choreography on one Tello.
6. WP4 stores a selected live JPEG/PNG through the coordinator-managed artifact contract.
7. WP3 calls the frozen provider profile and returns a hash-bound `PerceptionResult`.
8. Coordinator ingests it as `DETECTION_RECORDED`, updates the mountain snapshot and applies the frozen threshold.
9. A qualifying scored result is persisted as a coordinator-owned zone-level fact; lower or scoreless results enter `HELD_FOR_REVIEW` (a non-terminal review hold), while provider failures enter `FAILED`. No score is invented and no automatic revisit is triggered.
10. Any future agentic operation or revisit proposal must resolve to a new immutable plan and the existing operator plan-approval path. V0 does not add an operator command for reviewing persisted database state.

Live: LLM, one Tello, preflight/flight, live frame capture, `VideoStatus`, selected detector provider, coordinator persistence and operator plan decision.

Simulated: fixed six-zone geometry and nonvisited context. Task/ranking/patroller assignment, target aggregation, automatic revisit and external notifications are deferred rather than simulated as active V0 behavior.

The only terminal run outcomes are `COMPLETED`, `FAILED` and `EMERGENCY`. `HELD_FOR_REVIEW` is a non-terminal review hold, not a terminal outcome. `REJECTED` is event-only and never a terminal run status: `PLAN_REJECTED` returns to plan refinement/re-proposal. A future agentic action requires a new immutable plan and the existing operator plan-approval path; V0 does not add a database-state review command.

Replay is rehearsal-only and is rejected in a judged run. Served browser video is not required; `VideoStatus` plus selected-frame provenance is the V0 video boundary.

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

## 6.1 Active V0 contract subset and deferred retained surface

The active runtime subset is the smallest coherent perception path:

- `read_state`, `read_mountain`, and bounded `read_events` through the coordinator;
- typed agent proposal resolving to a catalogue-bound immutable plan;
- operator plan approval, fresh preflight, supervised deterministic dispatch, and fail-closed reconciliation;
- live frame capture, `VideoStatus`, `StationCaptureProvenance`, `ArtifactRef`, and hash-bound `PerceptionResult`;
- coordinator-persisted zone-level `PerceptionResult` and read-only evidence/provenance projection;
- emergency latch, optimistic state-version checks, scoped idempotency, immutable plan/approval binding, and live/simulated/replayed provenance separation.

The following remain in the registry and are explicitly deferred to V0.1: task
creation/ranking (`CommandTaskCreatePayload`, `CommandTaskRankPayload`,
`TASK_CREATE`, `TASK_RANK`), target aggregation, patroller selection/ranking/
assignment and assignment approvals, automatic revisit, precise hazard
coordinates, `flight_path`, detailed `drone_pose`, GIS/SLAM/location abstractions,
`SafeVideoStreamHandle`, browser-stream serving, and generalized mission/task/
video/evidence/diagnostic/storage projections not needed for the active zone-level
perception result. Their presence in `sentinel-v0-wire.schema.json` does not make them V0
requirements.

Provider profile, unapproved-provider template, evaluation manifest, local LLM
adapter configuration, and semantic validators are retained release/
reproducibility artifacts. They gate reproducibility and judged-provider use but
are not additional runtime agent tools or new wire commands.

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

The only permitted physical rehearsal is a named, pre-authorised choreography on one supervised Tello with an operator present and emergency stop available. Real flight is DJITelloPy-only, each real run starts in a fresh process with a fresh preflight and `--real --confirm`, and dispatch binds the approved plan hash plus approval. Before takeoff exactly the 8 named preflight checks must pass — `WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH` — and `passed` is the conjunction of the eight. Operator authorization is not a ninth check: it is carried as the plan's `approval_reference` and evaluated by the coordinator at dispatch. During the run, `WorkerPort.read_video_status` must remain `LIVE` and it returns a `LIVE` selected JPEG/PNG `ArtifactRef` correlated by ID/SHA-256 to that live status. `WorkerPort.open_live_video` and the retained safe handle are V0.1 capabilities, not V0 rehearsal requirements. At each approved station WP4 captures one to three frames with full provenance. Perception may run asynchronously and has no worker/control port, so it can never mutate an in-flight route. The judged perception request/result must both be `LIVE`, hash-bound, and profile-bound. Landing is permitted only while no active latch is armed; the `EMERGENCY` latch is terminal, non-overridable, and permits no landing/cleanup/retry afterward. A failed preflight may be overridden only by an operator-only `FORCE_RUN_CREATE` naming a new run/plan (no resume of the prior operation, never under an active latch) with fresh preflight and `--real --confirm`. WP4 records operation lifecycle/reconciliation outcome, emergency behavior if invoked, and safe landing; it does not introduce autonomy, multi-drone work, or live patroller assignment.

WP4 acceptance command (implementation work, not evidence fabricated by this spec): `uv run python -m pytest tests/worker tests/integration -q -k "tello_rehearsal"`. Acceptance additionally asserts persisted `RUN_CREATED`, operation transitions, a `LIVE` artifact/hash, `LIVE` perception result, event/snapshot correlation, operator decision path, and no simulated/replayed judged evidence. G4 remains required for V0 release.

## 8. Acceptance gates

| Gate | Owner | Required evidence |
|---|---|---|
| G0 Contract | Colin | schema/fixture validation, DDL constraint manifest, contract tests |
| G1 Coordinator simulation | Colin + Alan | deterministic fake-agent replay, duplicate and `IN_DOUBT` tests |
| G2 Human surface | Alan | NiceGUI smoke fixture, click-through evidence, plan Confirm/Reject stale-version test |
| G3 Perception | execution: Dzivha; approved/reviewed: Colin + Dzivha | reproducible evaluation-manifest evidence and a named joint approval over the versioned test-image-set hash |
| G4 Physical | Colin | supervised one-Tello rehearsal using WP0 operation/evidence contracts |

V0 release requires G0–G4. G0–G3 can be completed without live hardware; G4 cannot be substituted by dry-run success.

**G3 is BLOCKED.** The only checked-in provider-profile instance is the `UNAPPROVED` template. No provider profile may be used in the judged path until Colin and Dzivha together make a named joint approval over a versioned test-image-set hash for a concrete instance, with reproducible evaluation-manifest evidence. Execution of that provider work is Dzivha's. This specification intentionally declares no provider name, score threshold, evaluation data, or benchmark result.

## 9. Non-goals

V0 excludes real GIS/terrain, GPS/localisation, multi-drone coordination, target aggregation, task/ranking/patroller assignment, automatic revisit, precise hazard coordinates, served browser video, external notification, hidden chain-of-thought display, automatic provider failover and a broker. These capabilities remain retained for V0.1 where represented by existing contracts.
