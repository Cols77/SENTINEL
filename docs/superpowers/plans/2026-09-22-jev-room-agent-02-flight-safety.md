# Adaptive Flight Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a connected one-checkpoint-at-a-time cube executor with deterministic localization recovery, calibrated energy reserve and honest terminal outcomes.

**Architecture:** Extract one immutable checkpoint command plan consumed by both execution and estimation. A pose tracker and energy guard feed a room safety supervisor which preempts Jev. Legacy full missions use the same lifecycle so regression coverage proves extraction did not change their command behavior.

**Tech Stack:** Python, existing cube/pose runner, AprilTag map/localizer, Tello client protocol, dataclasses, JSON calibration profiles, pytest.

**Approved design:** [Jev Agentic Room Session and Bounded Localization Recovery](../specs/2026-09-22-jev-agentic-room-design.md)

---

## File map

- `src/sentinel/cube/checkpoint.py`: pure heading-aware command graph used by execution and estimation; it must not import `cube.mission`.
- `src/sentinel/cube/localization.py` and `recovery.py`: propagated pose health and bounded stationary tag recovery.
- `src/sentinel/cube/energy.py`: calibrated reserve arithmetic.
- `src/sentinel/cube/step_executor.py`: one connected takeoff-to-terminal lifecycle.
- `src/sentinel/room/site.py`: base surveyed free-space, home route and terminal-contingency contract; Plan 04 extends it with landing zones.
- `src/sentinel/room/supervisor.py`: mandatory preemption and supervised waits.
- `src/sentinel/room/store.py`, `queries.py`, and `controller.py`: persist current drone-state events and expose them on the next snapshot.

### Task 1: Extract exact checkpoint command plans

**Files:**
- Create: `src/sentinel/cube/checkpoint.py`
- Create: `tests/cube/test_checkpoint.py`
- Modify: `src/sentinel/cube/mission.py`
- Modify: `tests/cube/test_mission.py`

- [ ] **Step 1: Write failing planner parity tests**

Define:

```python
@dataclass(frozen=True)
class PlannedCommand:
    kind: Literal["SET_SPEED", "READ_TELEMETRY", "MOVE", "ROTATE", "SETTLE",
                  "WATCHDOG", "LOCALISE", "TRIM_ALLOWANCE", "CAPTURE", "LAND"]
    arguments: tuple[tuple[str, int | float | str], ...]
    upper_bound_s: float
    interruptible: bool

@dataclass(frozen=True)
class CheckpointPlan:
    checkpoint_id: str
    start: Position
    target: Position
    start_heading_deg: float
    terminal_heading_deg: float
    commands: tuple[PlannedCommand, ...]
    worst_case_s: float

@dataclass(frozen=True)
class HomeDefinition:
    pad_id: str
    nominal_pose: Position
    heading_deg: float
    route_id: str
```

Test the exact speed/telemetry/watchdog phases, axis order, dead band, 20 cm minimum, speed-dependent settle, canonical-heading requirement, yaw/look restoration, capture count, localization and maximum trim/reanchor allowances. Canonicalize command arguments into key-sorted immutable tuples and prove mutating the caller's source dict cannot change the plan or digest. Compare legacy mission command/log order before and after extraction.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube/test_checkpoint.py tests/cube/test_mission.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because `sentinel.cube.checkpoint` does not exist; existing mission tests still pass when run alone.

- [ ] **Step 3: Implement planner and refactor mission execution to consume it**

Make estimation and execution share the same `CheckpointPlan`; prohibit a second hand-written duration/path model. Keep actual trim decisions bounded by the plan's maximum allowance.

Expose one pure builder and one executor boundary:

```python
def plan_checkpoint(*, checkpoint_id: str, start: Position, target: Position,
                    start_heading_deg: float, terminal_heading_deg: float,
                    speed_cm_s: int, looks_deg: tuple[int, ...],
                    capture_count: int, limits: CommandLimits) -> CheckpointPlan: ...

class CheckpointCommandSink(Protocol):
    def execute_command(self, command: PlannedCommand) -> CommandOutcome: ...
```

The builder appends commands in this fixed order: set speed, telemetry/watchdog admission, restore canonical heading if needed, X/Y/Z moves with required settles, requested look/capture/localize groups, canonical-heading restoration, and final telemetry. `worst_case_s` is the sum of the commands' upper bounds, including every declared trim allowance. `CubeMissionRunner` translates legacy waypoints into these plans and dispatches every command through the sink; no legacy `_go_to` path may issue a movement independently.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube/test_checkpoint.py tests/cube/test_mission.py tests/test_runner.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/cube/checkpoint.py src/sentinel/cube/mission.py tests/cube/test_checkpoint.py tests/cube/test_mission.py
rtk git commit -m "refactor(cube): extract checkpoint command plans"
```

Expected: all selected tests pass; legacy command/log order remains identical and execution and estimation consume the same immutable plan.

### Task 2: Add calibrated localization coasting and recovery

**Files:**
- Create: `src/sentinel/cube/localization.py`
- Create: `src/sentinel/cube/recovery.py`
- Create: `src/sentinel/room/site.py`
- Modify: `src/sentinel/room/store.py`
- Create: `docs/superpowers/specs/contracts/room-site-base-v1.schema.json`
- Modify: `src/sentinel/cube/coverage.py`
- Create: `tests/cube/test_localization.py`
- Create: `tests/cube/test_recovery.py`
- Modify: `tests/cube/test_coverage.py`
- Modify: `tests/room/test_store.py`

- [ ] **Step 1: Write failing state/forecast/recovery tests**

Pin states `ANCHORED`, `COASTING`, `RECOVERY_REQUIRED`, `LOST`. A versioned drift profile separately tracks camera age, accepted-fix age, estimator failures, intentional look-away, translation, yaw and propagated XY/heading uncertainty. Test one missed tag remains coasting, the next proposed step triggers recovery before exhaustion, contradictory fixes degrade immediately, deadline/attempt exhaustion, and lost state permits neither translation nor exploratory yaw.

Define a base `RoomSite` containing versioned 3D free/forbidden volumes, concrete corridor and route geometry, a static surveyed home definition, tag/cube-map references, clearance provenance and condition-specific terminal contingencies. Require separate entries for `NAVIGATION_UNAVAILABLE` and `RETURN_ENERGY_UNAVAILABLE`; missing entries block real mode. A navigation-unavailable entry cannot claim `RETURN_VIA_ROUTE`. Any `LAND_IN_PLACE` or `CONTROLLED_DESCENT` entry carries static surveyed landing geometry, a clearance source/policy, maximum assurance age and required pose-health state. At session setup or a qualifying observation, the room layer validates that policy and appends a session/clock-domain-bound `ContingencyAssurance`; absolute monotonic values never appear in a reusable site fixture. The supervisor refuses a contingency when its pose, energy, clearance, geometry or freshness prerequisites are unavailable and escalates to the existing validated emergency path. Test recovery order: stationary burst first; then finite yaw headings ranked by mapped joint-tag visibility. Accept only fresh mapped joint-tag bursts with enough distinct reports, bounded spread, plausible pose/heading, containment and height consistency. Successful reanchor atomically resets pose and heading plus propagation history, restores canonical mission heading, invalidates routes/candidates/target locks, and requires fresh route/energy validation before resume.

```python
@dataclass(frozen=True)
class Volume3D:
    volume_id: str
    polygon_xy_cm: tuple[tuple[float, float], ...]
    z_interval_cm: tuple[float, float]

@dataclass(frozen=True)
class RouteDefinition:
    route_id: str
    checkpoints: tuple[Position, ...]
    corridor_volume_ids: tuple[str, ...]

class ContingencyTrigger(StrEnum):
    NAVIGATION_UNAVAILABLE = "NAVIGATION_UNAVAILABLE"
    RETURN_ENERGY_UNAVAILABLE = "RETURN_ENERGY_UNAVAILABLE"

class ContingencyKind(StrEnum):
    LAND_IN_PLACE = "LAND_IN_PLACE"
    CONTROLLED_DESCENT = "CONTROLLED_DESCENT"
    EMERGENCY = "EMERGENCY"

@dataclass(frozen=True)
class TerminalContingency:
    contingency_id: str
    trigger: ContingencyTrigger
    kind: ContingencyKind
    landing_polygon_xy_cm: tuple[tuple[float, float], ...]
    z_band_cm: tuple[float, float]
    required_pose_state: Literal["ANCHORED", "COASTING", "ANY"]
    clearance_source: str
    assurance_policy_id: str
    max_assurance_age_ms: int

@dataclass(frozen=True)
class ContingencyAssurance:
    assurance_id: str
    contingency_id: str
    session_id: str
    clock_domain_id: str
    state: Literal["CURRENT", "REVOKED"]
    assured_at: str
    assured_monotonic_ms: int
    expires_monotonic_ms: int
    evidence_refs: tuple[str, ...]

@dataclass(frozen=True)
class RoomSite:
    site_id: str
    cube_site_sha256: str
    tag_map_sha256: str
    free_space_volumes: tuple[Volume3D, ...]
    forbidden_volumes: tuple[Volume3D, ...]
    corridors: tuple[Volume3D, ...]
    routes: tuple[RouteDefinition, ...]
    home: HomeDefinition
    terminal_contingencies: tuple[TerminalContingency, ...]
    clearance_provenance: str
```

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube/test_localization.py tests/cube/test_recovery.py tests/cube/test_coverage.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because localization, recovery and the base room-site contract do not exist.

- [ ] **Step 3: Implement heading-aware coverage and recovery**

Extend `CoverageModel` with commanded yaw and uncertainty while preserving canonical defaults. Recovery rotates only when pose/clearance integrity supports the swept yaw. Use an injected clock and burst source. Missing validated drift profile disables real tag-free execution.

Pin the forecast and recovery boundaries:

```python
@dataclass(frozen=True)
class RecoveryVolume:
    volume_id: str
    polygon_xy_cm: tuple[tuple[float, float], ...]
    z_interval_cm: tuple[float, float]

@dataclass(frozen=True)
class RecoveryEnvironment:
    session_id: str
    clock_domain_id: str
    mapped_heading_tag_ids: tuple[tuple[int, tuple[int, ...]], ...]
    rotation_clearance_volumes: tuple[RecoveryVolume, ...]
    navigation_contingency_id: str
    navigation_contingency_expires_monotonic_ms: int

class LocalizationTracker:
    def accept_fix(self, fix: PoseFix) -> PoseHealth: ...
    def note_command(self, command: PlannedCommand, completed_at: float) -> PoseHealth: ...
    def forecast(self, plan: CheckpointPlan, now: float) -> PoseForecast: ...

class RecoveryController:
    def next_action(self, pose: PoseHealth, environment: RecoveryEnvironment,
                    attempted_headings: tuple[int, ...]) -> RecoveryAction: ...
    def assess_burst(self, action: RecoveryAction,
                     reports: tuple[PoseReport, ...]) -> RecoveryOutcome: ...
```

`navigation_contingency_id` must resolve to exactly one `TerminalContingency.contingency_id` in the adapted `RoomSite`; missing or duplicate keys reject the site before flight. The room adapter also requires exactly one current `ContingencyAssurance` matching the active session and clock-domain IDs, clamps its lifetime to `max_assurance_age_ms`, and copies only its monotonic deadline into `RecoveryEnvironment`. Wall-clock `assured_at` is audit metadata only. Recovery and contingency admission compare the injected monotonic clock with the environment deadline. Tests include forward/backward wall-clock jumps, process/restart clock resets and cross-session assurance reuse; none may revive or import clearance.

`RecoveryEnvironment` belongs to `cube.recovery` and contains no `sentinel.room` type. The room supervisor adapts the current `RoomSite` revision into it and retains responsibility for resolving the returned contingency ID. `forecast` transitions `ANCHORED -> COASTING -> RECOVERY_REQUIRED` before a proposed plan crosses any calibrated age, translation, yaw or uncertainty bound; integrity contradictions transition directly to `LOST`. Recovery first requests a stationary burst. Only after that fails may it rank mapped headings, filter them through rotational swept-clearance and energy admission, and rotate to the first untried heading. A successful joint-tag burst atomically emits a reanchor event plus route/candidate/target invalidations; exhaustion selects the matching current terminal contingency and never generates translation.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube/test_localization.py tests/cube/test_recovery.py tests/cube/test_coverage.py tests/cube/test_mission.py tests/room/test_store.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/cube/localization.py src/sentinel/cube/recovery.py src/sentinel/cube/coverage.py src/sentinel/room/site.py src/sentinel/room/store.py docs/superpowers/specs/contracts/room-site-base-v1.schema.json tests/cube/test_localization.py tests/cube/test_recovery.py tests/cube/test_coverage.py tests/room/test_store.py
rtk git commit -m "feat(cube): add bounded localization recovery"
```

Expected: all selected tests pass; one missed tag can coast, recovery begins before the calibrated limit, accepted reanchor invalidates stale work, and `LOST` emits no movement or exploratory yaw.

### Task 3: Add calibrated energy reserve

**Files:**
- Create: `src/sentinel/cube/energy.py`
- Create: `tests/cube/test_energy.py`
- Modify: `src/sentinel/dry_run.py`

- [ ] **Step 1: Write failing reserve tests**

Define `EnergyProfile`, `EnergySample`, `EnergyEstimate`, `ReserveDecision(CONTINUE, RETURN_NOW, LAND_CONTINGENCY)` and `ReserveGuard`. Compute `action_cost = ceil(conservative_percent_per_second * worst_case_seconds + telemetry_allowance)` for next checkpoint, recovery allowance, provider wait, exact home route and landing. Continue iff `current_battery - action_cost >= landing_floor`; test equality once, upward rounding, integer battery noise/increases, stale/invalid samples, observed burn increasing the bound, motor-time telemetry as a cross-check to monotonic time, and irreversible return.

```python
@dataclass(frozen=True)
class EnergyProfile:
    profile_id: str
    site_sha256: str
    aircraft_id: str
    percent_per_second_upper: float
    telemetry_allowance_pct: int
    landing_floor_pct: int
    sample_max_age_s: float
    execution_scope: Literal["SIMULATION_ONLY", "CALIBRATION", "PROMOTED"]

@dataclass(frozen=True)
class EnergyEstimate:
    current_pct: int
    action_cost_pct: int
    projected_landing_pct: int
    landing_floor_pct: int
    decision: ReserveDecision
    components: tuple[tuple[str, float], ...]
```

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube/test_energy.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because `sentinel.cube.energy` and its reserve contracts do not exist.

- [ ] **Step 3: Implement guard and scriptable dry-run telemetry**

Production real mode requires a validated matching profile. Synthetic profiles carry `execution_scope="SIMULATION_ONLY"`. Dry-run accepts injected battery/time sequences so reserve transitions are testable. Canonicalize component values into immutable key-sorted tuples and test post-construction source mutation does not change admission or replay hashes. Online observations may raise but never lower the calibrated burn bound.

Implement this admission boundary:

```python
class ReserveGuard:
    def __init__(self, profile: EnergyProfile, clock: Clock) -> None: ...
    def observe(self, previous: EnergySample, current: EnergySample) -> None: ...
    def assess(self, *, current: EnergySample, next_plan: CheckpointPlan | None,
               recovery_plan: CheckpointPlan | None, provider_wait_s: float,
               termination_plan: CheckpointPlan) -> EnergyEstimate: ...
    def latch_return(self, estimate: EnergyEstimate) -> None: ...
```

Reject bool/noninteger/out-of-range/stale battery samples. Sum the exact plan upper bounds and provider wait, multiply by `max(profile.percent_per_second_upper, observed_upper)`, add telemetry allowance, and round upward once. If the return latch has been set, every later assessment is `RETURN_NOW` or `LAND_CONTINGENCY`; it can never return `CONTINUE`.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube/test_energy.py tests/test_runner.py tests/cube/test_mission.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/cube/energy.py src/sentinel/dry_run.py tests/cube/test_energy.py
rtk git commit -m "feat(cube): add calibrated return reserve"
```

Expected: all selected tests pass; equality is admitted exactly once, every cost rounds conservatively, stale telemetry fails closed, and return is irreversible.

### Task 4: Extract connected step-capable flight lifecycle

**Files:**
- Create: `src/sentinel/cube/step_executor.py`
- Create: `src/sentinel/cube/supervised_wait.py`
- Create: `tests/cube/test_step_executor.py`
- Modify: `src/sentinel/runner.py`
- Modify: `src/sentinel/cube/mission.py`
- Modify: `tests/test_runner.py`
- Modify: `tests/cube/test_mission.py`

- [ ] **Step 1: Write failing lifecycle tests**

Define runtime `HomeState(pose, heading_deg, route_id, measured_at)` in `cube.step_executor`; it is produced from the measured takeoff state and the static `cube.checkpoint.HomeDefinition` stored by `RoomSite`, so `sentinel.cube` never imports `sentinel.room`. Pin `CheckpointOutcome(status, command_states, observation_ids, telemetry, pose, completion_known)` and `TerminalOutcome(state, reason, pose, battery, completed_at)`. Pin methods `start(home_definition: HomeDefinition) -> HomeState`, `execute(plan) -> CheckpointOutcome`, `return_home(plan) -> CheckpointOutcome`, `land() -> TerminalOutcome`, `close()`. Test connect/preflight/video/takeoff once, several checkpoints without reconnecting, landing/close once, exception cleanup, idempotent terminal calls, and no action after terminal state.

Add `SupervisedWait.run(operation, deadline, on_deadline)` with an independent monotonic watchdog. Provider waits are cancellable and stop producing candidates after timeout. SDK movement remains noninterruptible through the client command lock; deadline expiry records `COMMAND_COMPLETION_UNKNOWN`, never sends a concurrent replacement movement, and invokes only the firmware-supported validated landing/emergency path. Test the supervisor remains live during provider/perception waits and records SDK uncertainty while the full noninterruptible interval was admitted in advance.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube/test_step_executor.py tests/test_runner.py tests/cube/test_mission.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because `step_executor` and `supervised_wait` do not exist; existing runner/mission tests still pass when run without the new test.

- [ ] **Step 3: Implement shared lifecycle and adapt legacy runner**

Reuse existing preflight/emergency/video/landing behavior. Establish explicit measured home without silent clamping. Preserve complete-mission public behavior by composing the step executor.

Implement one explicit lifecycle state machine:

```python
class ExecutorState(StrEnum):
    NEW = "NEW"
    AIRBORNE = "AIRBORNE"
    RETURNING = "RETURNING"
    TERMINAL = "TERMINAL"
    CLOSED = "CLOSED"

class StepExecutor:
    def start(self, home_definition: HomeDefinition) -> HomeState: ...
    def execute(self, plan: CheckpointPlan) -> CheckpointOutcome: ...
    def return_home(self, plan: CheckpointPlan) -> CheckpointOutcome: ...
    def land(self) -> TerminalOutcome: ...
    def close(self) -> None: ...
```

`start` performs connect/preflight/video/takeoff exactly once and records measured home without clamping. `execute` accepts only `AIRBORNE`; `return_home` atomically latches `RETURNING`; both iterate the immutable command list through the single client command lock. Deadline expiry marks completion unknown and forbids subsequent movement. `land` and `close` are idempotent, and every exception path attempts only the already validated terminal action before closing video/client resources.

- [ ] **Step 4: Run tests, commit, then execute roadmap rebase checkpoint**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube tests/test_runner.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/cube/step_executor.py src/sentinel/cube/supervised_wait.py src/sentinel/cube/mission.py src/sentinel/runner.py tests/cube/test_step_executor.py tests/cube/test_mission.py tests/test_runner.py
rtk git commit -m "refactor(cube): add connected checkpoint executor"
```

Expected: all selected tests pass; one connection/takeoff spans multiple checkpoints, terminal calls occur once, and unknown SDK completion never releases a replacement movement.

### Task 5: Integrate deterministic supervisor and honest bridge outcomes

**Files:**
- Create: `src/sentinel/room/supervisor.py`
- Modify: `src/sentinel/room/store.py`
- Modify: `src/sentinel/room/queries.py`
- Modify: `src/sentinel/room/controller.py`
- Create: `tests/room/test_supervisor.py`
- Modify: `src/sentinel/survey/cube_bridge.py`
- Modify: `src/sentinel/survey/loop.py`
- Modify: `tests/survey/test_cube_bridge.py`
- Modify: `tests/survey/test_loop.py`

- [ ] **Step 1: Write failing supervisor and bridge tests**

Before every movement, capture, recovery, detector/Jev wait and landing approach, assert the supervisor refreshes telemetry, forecasts localization, validates path/clearance and prices the site-declared safe termination. Test mandatory `RECOVER`, `RETURN_NOW` and landing contingency preempt Jev and invalidate its snapshot. Test no optional action after return latch, and missing/invalid site contingency blocks real start.

```python
@dataclass(frozen=True)
class SupervisorRequest:
    snapshot_version: int
    checkpoint: CheckpointPlan | None
    provider_wait_s: float
    pose: PoseHealth
    energy_sample: EnergySample

@dataclass(frozen=True)
class SupervisorOutcome:
    transition: Literal["CONTINUE", "RECOVER", "RETURN_NOW", "LAND_CONTINGENCY"]
    reason: str
    invalidates_snapshot: bool
    energy: EnergyEstimate
    pose_forecast: PoseForecast
```

Test checkpoint completion persists current battery, pose, pose source/quality/age, localization state, airborne/terminal state and action progress as append-only room events. The next snapshot and `READ_DRONE` return those measured values and provenance; stale values are labelled stale and cannot authorize physical work.

Assert aborted/partial `RunReport` makes the legacy sweep `ABORTED`; partial frames remain evidence but do not count as a completed flight or successful report.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_supervisor.py tests/survey/test_cube_bridge.py tests/survey/test_loop.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because `sentinel.room.supervisor` does not exist and bridge tests expose the current unconditional flown/success behavior.

- [ ] **Step 3: Implement transitions and typed flight outcomes**

Supervisor output includes decision, reason, current samples, pose forecast, cost components, calibration hashes and snapshot invalidation. `controller.py` uses `SupervisedWait` for Jev/perception waits and reprojects state after preemption. SDK uncertainty follows the step-executor contract above. `CubeFlight` propagates runner terminal status instead of discarding it.

Implement admission as a pure decision followed by one effectful dispatcher:

```python
class SafetySupervisor:
    def assess(self, request: SupervisorRequest, site: RoomSite) -> SupervisorOutcome: ...
    def dispatch(self, outcome: SupervisorOutcome,
                 executor: StepExecutor) -> CheckpointOutcome | TerminalOutcome: ...
```

`assess` validates site/contingency freshness, telemetry, pose forecast, swept path and reserve in that order. `LOST` selects a currently admissible condition-specific contingency or the existing emergency path; `RECOVERY_REQUIRED` selects `RECOVER`; unaffordable optional work latches `RETURN_NOW`. Only `CONTINUE` may return control to Jev. `dispatch` persists the preemption before issuing a command and persists measured completion, battery and pose afterward. The bridge derives `FLOWN` only from a completed runner outcome; aborted/partial outcomes remain `ABORTED` while preserving captured frames.

- [ ] **Step 4: Run Plan-02 suite and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_supervisor.py tests/cube tests/survey/test_cube_bridge.py tests/survey/test_loop.py tests/test_runner.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/supervisor.py src/sentinel/room/store.py src/sentinel/room/queries.py src/sentinel/room/controller.py src/sentinel/survey/cube_bridge.py src/sentinel/survey/loop.py tests/room/test_supervisor.py tests/survey/test_cube_bridge.py tests/survey/test_loop.py
rtk git commit -m "feat(room): supervise adaptive flight safety"
```

Expected: all selected tests pass; mandatory transitions preempt Jev, stale state cannot authorize work, partial runs remain aborted, and `READ_DRONE` exposes the latest measured provenance.

Run independent specification and quality reviews, fix important findings, and repeat the Plan-02 suite. No hardware commands are part of this plan.
