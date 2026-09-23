# Count, Closer Inspection, and Return Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the first useful room workflow: Jev explores a bounded surveyed area, counts supported objects, requests useful closer views, returns home and reports evidence honestly.

**Architecture:** Semantic goals resolve into deterministic safe viewpoints. Coverage and count projections remain separate. Stable object hypotheses generate information-gain candidates; the Plan-02 supervisor filters every physical action and owns recovery/return.

**Tech Stack:** `sentinel.room` kernel, survey merge/report/observability, tiled detector, cube coverage/executor, Jev fixed-choice API, pytest.

**Approved design:** [Jev Agentic Room Session and Bounded Localization Recovery](../specs/2026-09-22-jev-agentic-room-design.md)

---

## File map

- `semantic.py`: 27-region/landmark vocabulary resolved through the base `RoomSite`.
- `coverage.py`: site-declared cells, visibility/resolvability evidence and exact completion projection.
- `spatial.py`: support-aware 3D object estimates and uncertainty, created before association.
- `association.py` and `counting.py`: stable static-object association and honest class counts.
- `viewpoints.py`: information-gain observation candidates.
- `controller.py`: one admitted observation at a time, with perception-result wait and supervisor preemption.

### Task 1: Resolve semantic room poses

**Files:**
- Create: `src/sentinel/room/semantic.py`
- Modify: `src/sentinel/room/site.py`
- Modify: `docs/superpowers/specs/contracts/room-site-base-v1.schema.json`
- Create: `tests/room/test_semantic.py`
- Add: `fixtures/room_agent/v1/semantic-site.json`

- [ ] **Step 1: Write failing resolver tests**

Define:

```python
@dataclass(frozen=True)
class SemanticPose:
    horizontal: Literal["left", "center", "right"]
    depth: Literal["rear", "middle", "front"]
    height: Literal["bottom", "middle", "top"]
    look: Literal["toward_targets", "left", "right"] = "toward_targets"

@dataclass(frozen=True)
class ResolvedPose:
    pose_id: str
    x_cm: int
    y_cm: int
    z_cm: int
    yaw_deg: int

@dataclass(frozen=True)
class SemanticFrame:
    envelope_volume_id: str
    left_to_right_axis_xy: tuple[float, float]
    rear_to_front_axis_xy: tuple[float, float]
    target_side: Literal["FRONT"]
    landmarks: tuple[tuple[str, Position], ...]
```

Extend `RoomSite` with one versioned `SemanticFrame`. Test all 27 canonical positions, deterministic boundaries/insets, front as the side closest to tables/targets (`max_y` for the current site), top/bottom as altitude, distinct IDs, site landmarks, schema/hash rejection and preservation of user order. The 27 positions are a compositional vocabulary, not a fixed itinerary: any ordered subset/repetition and any declared landmark can be requested, while object-relative viewpoints remain dynamically generated.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_semantic.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because `sentinel.room.semantic` and the semantic-site fields do not exist.

- [ ] **Step 3: Implement resolver and versioned fixture**

Map semantic thirds inside the declared envelope and validate the endpoint against static site geometry. The model never receives raw coordinates. Preserve `docs/wp5-flight3-corners.json` as a historical floor-plan fixture and explicitly exclude it from the semantic resolver; the new fixture is the sole 3-axis vocabulary source.

Pin one resolver entrypoint:

```python
def resolve_semantic_pose(site: RoomSite, pose: SemanticPose) -> ResolvedPose:
    """Resolve one endpoint inside the declared SemanticFrame."""
    ...
```

Project horizontal/depth thirds along the two declared unit axes, project height within the semantic envelope's Z interval, and derive `toward_targets` yaw from the rear-to-front axis. Reject non-orthogonal or unnormalized axes and duplicate landmark IDs while loading the site. This pure resolver validates only endpoint containment. Task 2 candidate generation and the Plan-02 supervisor receive current pose/home state and validate the current-to-target swept corridor, recovery-heading coverage, reserve and home route.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_semantic.py tests/cube/test_coverage.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/semantic.py src/sentinel/room/site.py docs/superpowers/specs/contracts/room-site-base-v1.schema.json tests/room/test_semantic.py fixtures/room_agent/v1/semantic-site.json
rtk git commit -m "feat(room): resolve semantic room poses"
```

Expected: all selected tests pass; all 27 IDs resolve uniquely, front/top retain the approved meanings, and the historical floor-plan fixture is never loaded as the semantic frame.

### Task 2: Add support-aware geometry, coverage and grounded counts

**Files:**
- Create: `src/sentinel/room/spatial.py`
- Create: `src/sentinel/room/coverage.py`
- Create: `src/sentinel/room/association.py`
- Create: `src/sentinel/room/counting.py`
- Modify: `src/sentinel/room/site.py`
- Modify: `src/sentinel/room/candidates.py`
- Modify: `src/sentinel/room/queries.py`
- Modify: `docs/superpowers/specs/contracts/room-site-base-v1.schema.json`
- Modify: `fixtures/room_agent/v1/semantic-site.json`
- Create: `tests/room/test_spatial.py`
- Create: `tests/room/test_coverage.py`
- Create: `tests/room/test_counting.py`

- [ ] **Step 1: Write failing coverage/count tests**

Extend `RoomSite`, its schema and semantic fixture with bounded coverage cells as polygons/height bands plus a fixture visibility matrix or calibrated frustum/occlusion source. Coverage cells track `UNVISITED`, `OBSERVED`, `OCCLUDED`, `UNANALYSED`, and `NOT_RESOLVABLE` per requested class. `OCCLUDED` requires positive declared/measured evidence; unseen defaults to unvisited/incomplete. Completion requires every required cell to be observed at class-resolvable geometry with terminal analysis, or produces an explicitly incomplete result. Count projections return confirmed/uncertain intervals for static classes and maximum-simultaneously-visible lower bounds for people. Test duplicate sightings, stable hypothesis revisions, missing frames, rejected detections, empty DB, viewpoint-scoped wording, room-scoped incomplete wording and unsupported classes.

Test ordinary `OBSERVE:<objective-id>:<semantic-pose-id>` generation separately from closer inspection. Generate user-requested semantic regions in their original order first. For a room-wide count, generate only legal site-declared views that add at least one unresolved class-resolvable coverage cell, ordered by deterministic uncovered-cell benefit, physical cost and stable ID. Suppress a view after unchanged relevant state or a typed refusal. If no candidate can improve coverage, produce an explicit incomplete answer/return candidate instead of looping.

Define `SupportKind(FLOOR, DECLARED_HORIZONTAL_PLANE, UNKNOWN)` and immutable `SpatialEstimate(estimate_id, source_detection_id, class_name, x_cm, y_cm, z_interval_cm, xy_covariance_cm2, support, support_plane_id, captured_at, pose_provenance, evidence_refs)`. `source_detection_id` is exactly the immutable `DetectionEvidence.detection_id` from Plan 01. Test one-to-one source-ID mapping, stable source identity and class preservation, floor-phone bottom-box rays with measured pose, declared table height, rejection of center-ray range for held/table/unknown objects, stale or commanded pose widening/invalidation, and multi-view consistency.

```python
class SupportKind(StrEnum):
    FLOOR = "FLOOR"
    DECLARED_HORIZONTAL_PLANE = "DECLARED_HORIZONTAL_PLANE"
    UNKNOWN = "UNKNOWN"

@dataclass(frozen=True)
class SpatialEstimate:
    estimate_id: str
    source_detection_id: str
    class_name: str
    x_cm: float
    y_cm: float
    z_interval_cm: tuple[float, float]
    xy_covariance_cm2: tuple[float, float, float]
    support: SupportKind
    support_plane_id: str | None
    captured_at: str
    pose_provenance: str
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class CoverageCell:
    cell_id: str
    polygon_xy_cm: tuple[tuple[float, float], ...]
    z_band_cm: tuple[float, float]
    required_classes: tuple[str, ...]

@dataclass(frozen=True)
class CoverageProjection:
    by_cell_and_class: tuple[tuple[str, str, str], ...]
    complete: bool
    uncovered: tuple[str, ...]
    evidence_refs: tuple[str, ...]
```

Construct `by_cell_and_class` in `(cell_id, class_name)` sort order and test that mutating source dictionaries after projection cannot change the projection or replay digest.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_spatial.py tests/room/test_coverage.py tests/room/test_counting.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because the spatial, coverage, association and counting modules do not exist.

- [ ] **Step 3: Implement projections using existing survey rules**

Implement the support-aware geometry first. Reuse `survey.report.class_counts` semantics without using static association for people. Implement `association.py` as a pure one-to-one static-object matcher over class, support plane, covariance overlap, time and multi-view geometry. It consumes each sighting's declared support-aware spatial estimate; floor phones and table computers never share one `delta_z` plane. `counting.py` projects immutable association revisions into confirmed/uncertain intervals. Canonicalize coverage rows by `(cell_id, class_name)` into immutable tuples before hashing. Implement semantic/coverage candidate generation with stable IDs and declared visibility benefit. Every answer includes scope, latest capture, analysed/unanalysed frames and uncovered cells. Zero with incomplete coverage never means none in the room. Pin the requested honest people wording with a fixture that renders “at least 2 people visible; 1 additional detection is uncertain” instead of an exact count.

Use these boundaries and deterministic ordering:

```python
def estimate_detection(detection: DetectionEvidence, pose: PoseEvidence,
                       support: SupportDeclaration,
                       camera: CameraModel) -> SpatialEstimate | None: ...

def associate_static(previous: tuple[ObjectRevision, ...],
                     estimates: tuple[SpatialEstimate, ...]) -> tuple[ObjectRevision, ...]: ...

def project_counts(objects: tuple[ObjectRevision, ...],
                   people_by_frame: tuple[FramePeople, ...],
                   coverage: CoverageProjection) -> tuple[ClassCount, ...]: ...
```

Estimate floor objects from the bottom-box ray and declared-plane objects from the plane intersection; return `None` for unknown support or invalid pose geometry. Build compatible static-object edges only when class and support plane match and covariance/time/multi-view gates pass. Sort edges by normalized separation and stable IDs, consume each estimate/object at most once, then emit immutable revisions. People bypass this matcher and use the maximum simultaneous count per frame. Coverage candidates sort by descending newly resolvable cells, then physical cost, then stable ID.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_spatial.py tests/room/test_coverage.py tests/room/test_counting.py tests/survey/test_geometry.py tests/survey/test_merge.py tests/survey/test_report.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/spatial.py src/sentinel/room/coverage.py src/sentinel/room/association.py src/sentinel/room/counting.py src/sentinel/room/site.py src/sentinel/room/candidates.py src/sentinel/room/queries.py docs/superpowers/specs/contracts/room-site-base-v1.schema.json fixtures/room_agent/v1/semantic-site.json tests/room/test_spatial.py tests/room/test_coverage.py tests/room/test_counting.py
rtk git commit -m "feat(room): project coverage and grounded counts"
```

Expected: all selected tests pass; floor/table objects use distinct support geometry, matching is one-to-one and deterministic, people remain a visible lower bound, and incomplete coverage cannot produce an exact room-wide zero.

### Task 3: Generate safe closer-inspection candidates

**Files:**
- Create: `src/sentinel/room/viewpoints.py`
- Modify: `src/sentinel/room/candidates.py`
- Create: `tests/room/test_viewpoints.py`

- [ ] **Step 1: Write failing spatial/viewpoint tests**

Reuse the support-aware `SpatialEstimate` contract from Task 2. Test closer candidates that change distance/bearing/height, predict pixel resolvability, include evidence benefit/cost, reject unsafe standoff/swept path/recovery/energy, suppress unchanged ineffective views, and remove disproved/lost hypotheses.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_viewpoints.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because `sentinel.room.viewpoints` and closer-inspection candidates do not exist.

- [ ] **Step 3: Implement estimates and viewpoint generation**

Generate controller-owned IDs such as `validate:<hypothesis-id>:<view-id>`. If metric position is unavailable, offer safe semantic or yaw observations and state that no approach range is proven. Each observation outcome is `CONFIRMED`, `REJECTED`, `AMBIGUOUS`, or `LOST`; confidence is never forced upward.

```python
@dataclass(frozen=True)
class ViewpointCandidate:
    candidate_id: str
    hypothesis_revision_id: str
    resolved_pose: ResolvedPose
    expected_pixel_interval: tuple[float, float]
    expected_information_gain: float
    checkpoint: CheckpointPlan
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class ObservationPlanningContext:
    start: Position
    heading_deg: float
    camera: CameraModel
    pose_health: PoseHealth
    energy_sample: EnergySample
    energy_profile: EnergyProfile
    route_revision: int
    site_revision: int

def closer_viewpoints(hypothesis: ObjectRevision, snapshot: RoomSnapshot,
                       site: RoomSite,
                       planning: ObservationPlanningContext) -> tuple[ViewpointCandidate, ...]: ...
```

Generate bounded distance, alternate-bearing, height and hover-capture proposals from the measured start/heading and camera model. Use the shared checkpoint planner plus pose/energy forecasts to validate standoff, full swept volume, pose-recovery opportunity and reserve before returning them; the supervisor revalidates the resulting checkpoint after Jev selects it. Reject a context whose route/site revisions differ from the snapshot. Sort by descending information gain, ascending conservative physical cost and stable ID. Record `(hypothesis_revision_id, view_id, relevant_state_sha256, outcome)` and suppress an unchanged ineffective view. If metric location is absent, emit only validated semantic/yaw observations and never synthesize a range-based approach.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_spatial.py tests/room/test_viewpoints.py tests/survey/test_geometry.py tests/survey/test_providers.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/viewpoints.py src/sentinel/room/candidates.py tests/room/test_viewpoints.py
rtk git commit -m "feat(room): generate closer inspection viewpoints"
```

Expected: all selected tests pass; unsafe or repeated ineffective views are absent and ambiguous evidence is never promoted automatically.

### Task 4: Connect observation actions to the room controller

**Files:**
- Modify: `src/sentinel/room/controller.py`
- Modify: `src/sentinel/room/supervisor.py`
- Create: `tests/room/test_closer_inspection.py`
- Create: `tests/room/test_count_return.py`

- [ ] **Step 1: Write failing trajectory tests**

Cover: read-only question with zero movement; ordered corner observations; uncertain phone confirmed by closer view; false detection rejected; temporary tag loss/coasting; mandatory recovery before unsafe next step; battery return before an unaffordable view; completed coverage then return; incomplete returned safely; failed return never success; stale Jev decision; typed tool error followed by a different legal action.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_closer_inspection.py tests/room/test_count_return.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: tests fail because observation candidates are still capability-disabled and the controller cannot execute a checkpoint/perception round trip.

- [ ] **Step 3: Enable observation and closer capabilities**

The controller resolves candidate IDs into checkpoint plans, asks the supervisor for admission, executes one checkpoint, emits its `PerceptionRequest`, waits through the bounded sidecar inbox protocol, ingests the correlated result and deliberates again. Mandatory recovery/return bypass Jev. Completion requires declared coverage or an explicit incomplete result plus requested safe termination. This task enables observation/closer capabilities for replay and simulation; real mode remains disabled until Plan 06 promotes matching pose/energy/site profiles.

Add an effectful handler with one checkpoint per call:

```python
def execute_observation(self, session_id: str, candidate: RoomCandidate,
                        snapshot: RoomSnapshot) -> TurnOutcome:
    plan = self.resolve_checkpoint(candidate, snapshot)
    admission = self.supervisor.assess_for_candidate(snapshot, plan)
    if admission.transition != "CONTINUE":
        return self.apply_preemption(session_id, admission)
    outcome = self.executor.execute(plan)
    request = self.perception_requests.from_checkpoint(candidate, outcome)
    result = self.perception_wait.wait(request)
    return self.ingest_and_reproject(session_id, candidate, outcome, result)
```

Revalidate the snapshot and evidence revision before `executor.execute`. Append the accepted action before execution and its measured outcome afterward. One call must never execute a second optional checkpoint. Timeout, provider failure, stale result or deterministic refusal becomes typed feedback in the next snapshot; mandatory recovery/return is applied before another Jev call.

- [ ] **Step 4: Run affected suite and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room tests/cube tests/survey/test_live_detect.py tests/survey/test_cube_bridge.py tests/survey/test_loop.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/controller.py src/sentinel/room/supervisor.py tests/room/test_closer_inspection.py tests/room/test_count_return.py
rtk git commit -m "feat(room): complete adaptive count and return"
```

Expected: all selected tests pass; every accepted observation executes one checkpoint, safety preemption bypasses Jev, and failed return or incomplete coverage cannot be reported as completed.

### Task 5: Add replay fixtures and capability report

**Files:**
- Create: `fixtures/room_agent/v1/manifest.json`
- Create: `fixtures/room_agent/v1/events.jsonl`
- Create: `src/sentinel/room/replay.py`
- Create: `tests/room/test_replay.py`
- Create: `docs/reports/room-agent-count-readiness.md`
- Modify: `docs/superpowers/plans/2026-09-21-bounded-multiturn-demo-qa.md`

- [ ] **Step 1: Add failing replay integrity tests**

Bind fixture hashes, code/schema revision, synthetic/recorded label and expected terminal state. Test missing, reordered, duplicate, corrupted and stale-decision records. Replayed snapshots, decisions and report must match byte-for-byte without provider/drone calls.

- [ ] **Step 2: Run the replay tests and verify they fail**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_replay.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: failure because the room replay loader/fixtures do not exist.

- [ ] **Step 3: Implement replay and render the offline report**

The report states supported request shapes, exact scenarios, denominators, coverage, count semantics, safety transitions, limitations and that replay is not a fresh model/hardware measurement.

```python
@dataclass(frozen=True)
class ReplayResult:
    final_snapshot_sha256: str
    decision_stream_sha256: str
    report_sha256: str
    terminal_state: TerminalState

def replay_fixture(manifest_path: Path, *, provider_factory=forbidden,
                   client_factory=forbidden) -> ReplayResult: ...
```

Verify every manifest-bound file hash before opening the event stream. Require contiguous sequence/state versions, replay each event through the same projectors as live mode, and compare normalized snapshots, decisions, terminal state and rendered report to the manifest. Duplicate, missing, reordered or stale-authorizing records raise `ReplayIntegrityError`; replay never constructs provider or drone factories.

- [ ] **Step 4: Amend demo-QA disposition**

Update `docs/superpowers/plans/2026-09-21-bounded-multiturn-demo-qa.md` with an amendment that its room/drone answers use `sentinel.room.queries`; retain its other outstanding tasks and do not mark them complete.

- [ ] **Step 5: Run Plan-03 suite and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add fixtures/room_agent/v1/manifest.json fixtures/room_agent/v1/events.jsonl src/sentinel/room/replay.py tests/room/test_replay.py docs/reports/room-agent-count-readiness.md docs/superpowers/plans/2026-09-21-bounded-multiturn-demo-qa.md
rtk git commit -m "test(room): add count and return replay baseline"
```

Expected: all room tests pass; replay is byte-stable, tampering fails closed, the report identifies replay as offline evidence, and no external dependency is constructed.

Run independent specification/quality reviews and fix important findings. Execute Plan 06 through count-and-return promotion before advertising real capability.
