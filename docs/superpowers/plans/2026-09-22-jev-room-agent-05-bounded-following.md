# Person-Phone Relation and Bounded Following Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify a designated consenting person holding a phone, maintain observation by bounded yaw, then permit one short validated follow step at a time inside a cleared surveyed corridor.

**Architecture:** Structured relation evidence identifies the person/phone pair. A moving-target tracker owns identity and freshness; a local controller owns yaw corrections. Translational steps are controller-generated from fresh range/motion evidence and are revalidated by the common supervisor. Jev chooses mission-level actions, not steering increments.

**Tech Stack:** Gemini structured vision adapter, room evidence store, local tracker port, cube step executor, surveyed corridor geometry, pytest/replay.

**Approved design:** [Jev Agentic Room Session and Bounded Localization Recovery](../specs/2026-09-22-jev-agentic-room-design.md)

---

## File map

- `relations.py` and `gemini_relations.py`: structured, consent-gated holding evidence.
- `tracking.py` and `local_tracker.py`: stable moving-target identity plus measured freshness/latency.
- `target_recovery.py`: bounded reacquisition candidates after target loss.
- `yaw_control.py`: one local yaw/capture correction that restores canonical heading before translation.
- `following.py`: one short corridor-constrained translation candidate.
- `controller.py`, `supervisor.py`, and `step_executor.py`: execute exactly one selected target action, persist its result, and require a fresh target lock.

### Task 1: Add structured person-holding-phone evidence

**Files:**
- Create: `src/sentinel/room/relations.py`
- Create: `src/sentinel/survey/providers/gemini_relations.py`
- Modify: `src/sentinel/room/ingest.py`
- Modify: `src/sentinel/room/candidates.py`
- Modify: `src/sentinel/room/controller.py`
- Create: `tests/room/test_relations.py`
- Create: `tests/survey/test_relation_provider.py`

- [ ] **Step 1: Write failing provider/relation tests**

Define:

```python
class RelationKind(StrEnum):
    HOLDING = "HOLDING"

@dataclass(frozen=True)
class RelationEvidence:
    relation_id: str
    kind: RelationKind
    subject_revision_id: str
    object_revision_id: str
    observation_id: str
    confidence_band: Literal["high", "medium", "low"]
    raw_score: float | None
    score_kind: str | None
    ambiguous: bool
    captured_at: str
    captured_monotonic_ms: int
    fresh_until_monotonic_ms: int
    evidence_refs: tuple[str, ...]
```

`captured_at` is audit metadata only. Admission and freshness use the monotonic fields from the session clock. Test near-but-not-held phone, two people/two phones ambiguity, unmatched provider boxes, crop/full-frame mismatch, stale relation, forward/backward wall-clock jumps, designated actor qualifier, malformed/failing provider and resolved model identity. No provider construction/call may happen before existing paid-call and filming-consent gates.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_relations.py tests/survey/test_relation_provider.py -q --tb=short -o addopts='' -p no:cacheprovider
```

- [ ] **Step 3: Implement adapter and conservative same-frame association**

The preceding command must fail because the relation provider, typed evidence and `VERIFY_RELATION` controller path do not exist yet.

The adapter requests structured person/phone pair references and boxes. Match them to same-frame stored evidence under explicit overlap/ambiguity rules. Free-text descriptions and proximity never create `HOLDING`. Persist unmatched/ambiguous results. Wire `VERIFY_RELATION:<person-revision>:<phone-revision>` as a controller candidate; its correlated result updates the next snapshot or produces typed provider/ambiguity feedback.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_relations.py tests/survey/test_relation_provider.py tests/room/test_ingest.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/relations.py src/sentinel/room/ingest.py src/sentinel/room/candidates.py src/sentinel/room/controller.py src/sentinel/survey/providers/gemini_relations.py tests/room/test_relations.py tests/survey/test_relation_provider.py
rtk git commit -m "feat(room): verify person phone relationships"
```

Expected: the selected tests pass and provider/ambiguity failures persist typed feedback without moving the drone.

### Task 2: Add stable moving-target tracks

**Files:**
- Create: `src/sentinel/room/tracking.py`
- Create: `src/sentinel/room/local_tracker.py`
- Create: `src/sentinel/room/target_recovery.py`
- Modify: `src/sentinel/room/sql/schema.sql`
- Modify: `src/sentinel/cube/step_executor.py`
- Create: `tests/room/test_tracking.py`
- Modify: `tests/cube/test_step_executor.py`

- [ ] **Step 1: Write failing identity tests**

Define `TrackState(ACQUIRED, AMBIGUOUS, LOST)` and the contracts below. Test crossing people, occlusion, new entrant, stale detection, expired holding relation, pose-recovery invalidation, seed-frame/hash/camera mismatch, and prohibition on silently assigning a new person to the old track ID. The V1 metric source is `FLOOR_CONTACT_PROJECTION`: a full-body person box whose bottom-center floor contact is visible, projected through the calibrated camera and current measured pose onto the declared floor plane. Cropped feet, commanded/stale pose, unknown support or inconsistent multi-frame range leave `range_source=UNAVAILABLE` and prohibit translation. Test `REACQUIRE_TARGET` candidates use bounded yaw/known semantic views, never silently change identity, and terminate in return/declared landing when the bounded attempt/deadline expires.

- [ ] **Step 2: Run tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_tracking.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: missing tracking/local-tracker/reacquisition interfaces.

- [ ] **Step 3: Implement tracker port and append-only track revisions**

Expose `TrackerSession.initialize(seed, frame) -> TargetTrack`, `update(frame) -> TargetTrack`, and `close()`. The controller resolves stored relation/person evidence into an immutable `TrackerSeed`; the tracker never queries the store. `LocalFrameTracker` adapts a local CV backend that accepts full-frame BGR pixels plus an initial full-frame XYXY box and returns a box plus association score; crop/tile coordinates are converted to full-frame coordinates before seed construction. The connected step executor exposes a read-only `subscribe_video_frames() -> FrameSubscription` over its existing retained video stream; the higher-level controller owns the tracker session and drains timestamped frames without giving it command authority. Reinitialize only after explicit verified relation evidence creates a new generation. Record capture monotonic time, processing latency, frame hash, camera hash, pose revision and loss reason. Compute `fresh_until_monotonic_ms` from `captured_monotonic_ms`, measured processing latency and the promoted tracker profile; compare it only with the injected monotonic session clock. Wall timestamps remain audit metadata and cannot make a stale track fresh. Add fake-backend identity handoff, queue-overrun, wall-clock-jump and teardown tests. Real capability remains disabled without a promoted tracker freshness profile, but the production adapter exists for Plan 06 calibration.

```python
@dataclass(frozen=True)
class TrackerFrame:
    frame_id: str
    captured_monotonic_ms: int
    width_px: int
    height_px: int
    full_frame_bgr: bytes
    frame_sha256: str
    camera_sha256: str
    pose_revision: int

@dataclass(frozen=True)
class TrackerSeed:
    designated_actor_ref: str
    subject_revision_id: str
    relation_id: str
    observation_id: str
    frame_id: str
    frame_sha256: str
    camera_sha256: str
    full_frame_bbox_xyxy_px: tuple[float, float, float, float]
    image_size_px: tuple[int, int]
    evidence_refs: tuple[str, ...]

class TrackerSession(Protocol):
    def initialize(self, seed: TrackerSeed, frame: TrackerFrame) -> "TargetTrack": ...
    def update(self, frame: TrackerFrame) -> "TargetTrack": ...
    def close(self) -> None: ...

@dataclass(frozen=True)
class TargetTrack:
    track_id: str
    generation: int
    state: TrackState
    designated_actor_ref: str
    appearance_evidence_refs: tuple[str, ...]
    relation_id: str
    last_observation_id: str
    bbox_xyxy_px: tuple[float, float, float, float]
    image_size_px: tuple[int, int]
    range_interval_cm: tuple[float, float] | None
    range_source: Literal["FLOOR_CONTACT_PROJECTION", "UNAVAILABLE"]
    velocity_xy_cm_s: tuple[tuple[float, float], tuple[float, float]] | None
    captured_monotonic_ms: int
    processing_latency_ms: int
    frame_sha256: str
    camera_sha256: str
    pose_revision: int
    fresh_until_monotonic_ms: int
    association_score: float
    loss_reason: str | None = None
```

`relation_id` refers directly to one immutable `RelationEvidence` row. Reverification creates a new relation ID; it never mutates or refreshes the old row. Tests prove an expired relation cannot seed or refresh a tracker, while a new verified relation produces a new tracker generation with the new relation ID.

- [ ] **Step 4: Run tests green and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_tracking.py tests/room/test_store.py tests/cube/test_step_executor.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/tracking.py src/sentinel/room/local_tracker.py src/sentinel/room/target_recovery.py src/sentinel/room/sql/schema.sql src/sentinel/cube/step_executor.py tests/room/test_tracking.py tests/cube/test_step_executor.py
rtk git commit -m "feat(room): persist stable moving target tracks"
```

Expected: the selected tests pass; identity ambiguity never transfers the existing track ID.

### Task 3: Maintain view with a local yaw controller

**Files:**
- Create: `src/sentinel/room/yaw_control.py`
- Modify: `src/sentinel/room/candidates.py`
- Modify: `src/sentinel/room/controller.py`
- Create: `tests/room/test_yaw_control.py`
- Create: `tests/room/test_yaw_tracking.py`

- [ ] **Step 1: Write failing yaw-control tests**

Pin `YawProfile(camera_sha256, input_width_px, input_height_px, horizontal_fov_deg, pixel_deadband, max_step_deg, max_total_deg, observation_timeout_s)` and compute bounded correction from full-frame horizontal target error using `degrees_per_pixel = horizontal_fov_deg / input_width_px`. Test camera/resolution/crop mismatch, center deadband, direction/sign, capped step, swept-clearance/tag-recovery/energy refusal, target loss, stale frame, pose recovery invalidating the target, and fresh reacquisition before another correction.

- [ ] **Step 2: Run tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_yaw_control.py tests/room/test_yaw_tracking.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: missing yaw controller and `MAINTAIN_OBSERVATION` integration.

- [ ] **Step 3: Implement local corrections behind `yaw_tracking` flag**

Jev selects `MAINTAIN_OBSERVATION`; the local controller verifies the track camera hash and full-frame dimensions, performs at most one admitted yaw, settle and capture before yielding, then restores the canonical `+y` mission heading before any axis-aligned translation candidate is generated. Cloud latency never controls incremental steering. A slow staged actor may be used only under the promoted camera-bound freshness/latency profile.

- [ ] **Step 4: Run tests green and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_yaw_control.py tests/room/test_yaw_tracking.py tests/cube/test_step_executor.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/yaw_control.py src/sentinel/room/candidates.py src/sentinel/room/controller.py tests/room/test_yaw_control.py tests/room/test_yaw_tracking.py
rtk git commit -m "feat(room): maintain designated target by yaw"
```

Expected: the selected tests pass; each decision produces at most one yaw/capture and restores canonical heading before translation.

### Task 4: Generate one bounded follow step

**Files:**
- Create: `src/sentinel/room/following.py`
- Modify: `src/sentinel/room/site.py`
- Modify: `src/sentinel/room/candidates.py`
- Modify: `src/sentinel/room/controller.py`
- Modify: `src/sentinel/room/supervisor.py`
- Modify: `src/sentinel/cube/step_executor.py`
- Create: `tests/room/test_following.py`
- Create: `tests/room/test_bounded_follow.py`

- [ ] **Step 1: Write failing follow planner tests**

Define a corridor polygon, maximum step/duration/extent, standoff interval, terminal behavior and target range/velocity uncertainty. Test safe step, approaching/retreating target, excessive lateral motion, stale or bbox-only range, crossing person, corridor boundary, insufficient reserve, coasting limit, pose recovery, uncertain command completion and duration exhaustion. The trajectory test proves one accepted `FOLLOW_STEP` causes exactly one translation, hover, fresh pose/evidence, a new target-lock generation and a new Jev decision; a second movement cannot be queued by the original decision.

- [ ] **Step 2: Run tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_following.py tests/room/test_bounded_follow.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: missing follow planner/controller integration or disabled candidate.

- [ ] **Step 3: Implement deterministic one-step planning**

Candidate generation requires a fresh unambiguous track, calibrated range/motion, restored canonical heading and clear swept corridor. Price target motion over decision/command delay. The controller resolves one candidate into one checkpoint; the supervisor revalidates its monotonic deadline immediately before dispatch and the step executor returns to hover. Persist pose/evidence, invalidate the old track generation and require fresh acquisition before another candidate. Target/pose loss triggers bounded `REACQUIRE_TARGET`, then site-declared return/landing. No second step can be queued in advance. Tests move the wall clock both directions while monotonic time advances and prove an expired track or follow step stays inadmissible.

```python
@dataclass(frozen=True)
class FollowStep:
    candidate_id: str
    track_id: str
    track_generation: int
    checkpoint: CheckpointPlan
    predicted_standoff_cm: tuple[float, float]
    expires_monotonic_ms: int
```

- [ ] **Step 4: Run tests green and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_following.py tests/room/test_bounded_follow.py tests/room/test_tracking.py tests/room/test_supervisor.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/following.py src/sentinel/room/site.py src/sentinel/room/candidates.py src/sentinel/room/controller.py src/sentinel/room/supervisor.py src/sentinel/cube/step_executor.py tests/room/test_following.py tests/room/test_bounded_follow.py
rtk git commit -m "feat(room): plan bounded designated-person follow steps"
```

Expected: the selected tests pass; an accepted `FOLLOW_STEP` produces one translation followed by hover and fresh reacquisition.

### Task 5: Add replay scenarios and disabled-capability honesty

**Files:**
- Add: `fixtures/room_agent/v1/person_phone/`
- Create: `tests/room/test_follow_replay.py`
- Modify: `docs/reports/room-agent-count-readiness.md`

- [ ] **Step 1: Add recorded/synthetic labelled scenarios**

Include held phone, nearby phone not held, crossing actors, occlusion/reappearance, pose recovery, target loss, reserve return and one accepted step. Label synthetic and operator-designated facts explicitly.

- [ ] **Step 2: Run replay tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_follow_replay.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: missing fixture hashes/replay expectations.

- [ ] **Step 3: Implement and test capability gating**

Without relation/tracker/follow profile, the same request must produce explicit unavailable/clarify behavior and zero motion. With replay profiles, it may exercise the typed workflow. No replay result enables real mode.

- [ ] **Step 4: Run Plan-05 suite and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_relations.py tests/room/test_tracking.py tests/room/test_yaw_control.py tests/room/test_yaw_tracking.py tests/room/test_following.py tests/room/test_bounded_follow.py tests/room/test_follow_replay.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add fixtures/room_agent/v1/person_phone/manifest.json fixtures/room_agent/v1/person_phone/events.jsonl tests/room/test_follow_replay.py docs/reports/room-agent-count-readiness.md
rtk git commit -m "test(room): add bounded following replay evidence"
```

Expected: all Plan-05 tests pass; replay capability flags never promote real following.

Run independent specification and quality reviews. Plan 06 governs live promotion; until then, real `FOLLOW_STEP` remains disabled.
