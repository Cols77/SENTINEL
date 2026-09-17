# SENTINEL WP0 — Shared Coherence Contracts

> **Status:** Revised after independent review; re-review pending
> **Owner:** Colin / PM-architect
> **Authority:** [`2026-09-16-sentinel-v0-coherence-spec.md`](2026-09-16-sentinel-v0-coherence-spec.md)
> **Normative public root:** [`contracts/sentinel-v0-contract.schema.json`](contracts/sentinel-v0-contract.schema.json) (registry: [`contracts/sentinel-v0-wire.schema.json`](contracts/sentinel-v0-wire.schema.json))

## 1. Objective and boundary

WP0 supplies executable contracts, validators, fixtures and a declarative DDL/constraint manifest. It does **not** implement the production coordinator, LLM client, NiceGUI surface, detector call or Tello command path.

The parent baseline is `SENTINEL Demo State Database Specification @ document version 0.8`, stored at the historical `v0.2` filename. The V0 compatibility mapping is in the coherence spec and is binding.

## 2. Required artifacts

```text
src/sentinel/contracts/                 # WP0 implementation target
  __init__.py
  schemas.py
  validators.py
  ddl_manifest.py
  fixtures.py
docs/superpowers/specs/contracts/       # normative specification artifacts
  sentinel-v0-contract.schema.json       # directly-validatable compatibility root
  sentinel-v0-wire.schema.json           # directly-validatable oneOf registry
  mountain-v0.schema.json
  mountain-v0.fixture.json
  survey-catalogue-v1.schema.json
  provider-profile-v1.schema.json
  provider-profile-unapproved.template.json
  evaluation-manifest-v1.schema.json
  llm-adapter-config-v1.schema.json
fixtures/sentinel/v0/
  events-success.jsonl
  events-reject.jsonl
  events-in-doubt.jsonl
  perception-results.json
  provider-profile.json
tests/contracts/
  test_schema_validation.py
  test_state_transitions.py
  test_idempotency.py
  test_fixture_integrity.py
```

WP0 must provide a versioned SQLite DDL/constraint manifest: tables/foreign keys, unique `(run_id, sequence_no)`, scoped idempotency uniqueness, state-version check, append-only events, tool-operation state, envelope/choreography registry references, approval actions and emergency records. WP1 owns the production migration/repository implementation.

## 3. Canonical type system

The JSON Schema is authoritative for wire representation. Frozen Python dataclasses are convenience wrappers only and must round-trip through it without information loss.

### 3.1 Identity classes

- Runtime UUIDs: `run_id`, `mission_id`, `agent_run_id`, `event_id`, `command_id`, `operation_id`, `observation_id`, `artifact_id`, `request_id`, `result_id`, `target_id`, `task_id`, `action_id`, `correlation_id`, `causation_id`.
- Configured stable IDs: `section_id`, `drone_id`, `worker_id`, `mission_envelope_id`, `choreography_id`, `provider_id`, `model_id`, `policy_revision`, `prompt_revision`, `catalogue_id`, `catalogue_revision`, `station_id`, `zone_id`.
- Runtime UUID text is RFC 4122 canonical lowercase and matches `^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`; configured IDs use the schema pattern. Whitespace is invalid.

### 3.2 Envelopes

The public root is a directly-validatable `oneOf` registry keyed by `wire_type`; every entry is closed (`additionalProperties: false`) and every command/event/result payload is a typed closed object. Concrete public IDs are `https://sentinel.local/contracts/sentinel-v0-wire.schema.json#CommandEnvelope`, `#ResultEnvelope`, `#EventEnvelope`, `#PerceptionRequest`, `#PerceptionResult`, `#StateSnapshot`, `#MountainStateSnapshot`, `#ApprovalReadModel`, `#HazardReadModel`, `#OperationRecord`, `#ReconciliationResult`, `#EmergencySignal`, `#AgentRequest`, `#AgentModelResult`, `#WorkerDispatch`, `#SurveyPlanReference`, `#PreflightReport`, `#StationCaptureProvenance`, `#PlanApproval`, `#PlanReadModel`, `#VideoStatus`, and `#SafeVideoStreamHandle`. The approved catalogue for a plan validates against [`survey-catalogue-v1.schema.json`](contracts/survey-catalogue-v1.schema.json). Cross-WP ports MUST reference these IDs, not an unconstrained object.

The registry defines separate objects:

- `CommandEnvelope`: caller intent before execution;
- `ResultEnvelope`: coordinator response with `ACCEPTED`, `COMPLETED`, `REJECTED` or `IN_DOUBT`;
- `EventEnvelope`: immutable persisted fact with coordinator-assigned `sequence_no` and `recorded_at`.

The idempotency scope is `(run_id, actor_type, actor_id, idempotency_key)` for the run lifetime. Same scope, command type, expected state version and canonical payload hash returns the original result. Reuse of that scope with a different command type, expected state version or canonical payload hash returns `IDEMPOTENCY_KEY_CONFLICT` and performs no action.

### 3.3 Core records and required fields

The schema contains `ArtifactRef`, `CommandEnvelope`, `ResultEnvelope`, `EventEnvelope` and `PerceptionResult`. WP0 dataclasses must additionally provide these exact records:

```text
StateSnapshot(run_id, state_version, run_status, agent_phase,
              legal_operator_actions, active_operation, latest_event_sequence,
                      mountain_snapshot_version, updated_at)

LocalPoint(x: float[0..100], y: float[0..100])
LocalPose(point: LocalPoint, heading_deg: float|None, observed_at, source_kind)

SectionState(section_id, name, polygon, status, coverage_percent|None,
             last_observation_id|None, stale: bool)

HazardMarker(target_id, section_id, perception_result_id,
             point: LocalPoint|None, status, evidence_artifact_id)

MountainStateSnapshot(run_id, snapshot_version, as_of, sections[6],
                      drone_pose|None, flight_path, hazards,
                      active_mission_id|None, active_task_id|None,
                      source_kind, stale)

PerceptionRequest(request_id, run_id, observation_id, artifact_id,
                  artifact_sha256, target_class="rock", judged_path: bool,
                  provider_profile_id, station_capture: StationCaptureProvenance)

OperationRecord(operation_id, run_id, mission_id, command_id, worker_id,
                idempotency_key, status: PENDING|DISPATCHED|SUCCEEDED|FAILED|IN_DOUBT,
                dispatch_recorded_at, reconciled_at|None)

EmergencySignal(signal_id, run_id|None, mission_id|None, worker_id,
                latched_at, source_kind, reason_code)
```

#### 3.3.1 Indoor-alley catalogue survey

Indoor-alley runs use only the six catalogue-bound zones in `mountain-v0.fixture.json`. "Section" and "zone" name the same six fixed entities: `SectionState.section_id`, `layout_modes[].active_zone_ids`, `SurveyPlanReference.station_ids` and `StationCaptureProvenance.zone_id` all use the identical six IDs. The worker emits immutable `SurveyPlanReference`, `PreflightReport` and `StationCaptureProvenance` records. `WorkerDispatch` must carry the exact plan reference, approval reference and preflight report; equality of IDs/hashes is checked by the semantic validator. Layout mode is `MARKER_VERIFIED` (normal) or the fallback `ROUTE_ONLY_2_ZONE`; route-only is limited to exactly `LEFT_ALLEY_MIDDLE` and `RIGHT_ALLEY_MIDDLE` and never claims visual marker verification. `StationCaptureProvenance` binds a decisive capture to its plan step per station (`station_id`, `zone_id`, `plan_step`, `artifact_id`, `artifact_sha256`, `capture_index` in 1..3), so replay picks the same station without ambiguity.

The worker runs preflight before flight and emits the immutable run/plan-bound `PreflightReport` as the `PREFLIGHT_REPORTED` event. The report has exactly eight named checks — `WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH` — and `passed` is the conjunction of `check.passed` (all eight must pass). A failed preflight may be overridden only by a durable operator-only force-new-run approval (`FORCE_RUN_CREATE`) that names the failed report, operator reason and new run and plan IDs; it cannot resume or redispatch the old operation and never overrides an active emergency latch.

#### 3.3.2 Run outcomes and approval vocabulary

The only V0 terminal run outcomes are `COMPLETED`, `FAILED` and `EMERGENCY`; an active emergency latch is terminal and cannot be forced. `REJECTED` is event-only: a rejected plan (`PLAN_REJECTED`) or task (`OPERATOR_ASSIGNMENT_REJECTED`) is refined and re-proposed, leaving the run refinement-eligible; `REJECTED` is never a terminal run status. `NEEDS_REVIEW` is a review-hold state, not terminal, and is escaped only by an explicitly approved new/revisit plan. `APPROVED` is not a run state.

### 3.4 Snapshot/version rule

`STALE` is never a persisted `SectionState.status`. It is a read-time boolean derived as `now - MountainStateSnapshot.as_of > mountain.fixture.snapshot_stale_after_ms OR MountainStateSnapshot.snapshot_version < StateSnapshot.latest_event_sequence`. On every committed event that changes any mountain projection field, the coordinator atomically increments `state_version`, assigns the next `sequence_no`, and writes a mountain snapshot with `snapshot_version == sequence_no` and `as_of == recorded_at`. Events that do not alter mountain projection still increment `state_version` and `latest_event_sequence`, but retain the prior mountain snapshot version; that relationship makes its freshness derivation explicit. `StateSnapshot.latest_event_sequence` always equals the highest committed event sequence for the run.

`PerceptionResult` must contain every schema field, including request/artifact/hash/source-class/transform revision, `provider_profile_id`, `class_mapping_revision`, `artifact_source_kind` and `judged_path`. `source_class` may be `pebble_cluster`, but `canonical_class` is always `rock` under mapping revision `v0-rock-only`. `DETECTED` requires `rock|pebble_cluster` and numeric score; `NOT_DETECTED` requires `none` and numeric score; `UNSCORED`/`PROVIDER_ERROR` require `unknown` and null score. `MountainStateSnapshot` is complete and closed: `drone_pose`, ordered `flight_path`, typed `hazards`, `active_mission_id`, and `active_task_id` are required (nullable where no value exists). `HazardReadModel` and `ApprovalReadModel` each embed exactly one typed `ArtifactRef` (`evidence`) and one typed `PerceptionResult` (`perception`), plus `decision_threshold`, typed `ranking`, and `provenance`; this supplies score/semantics/profile/provider/model/prompt/transform/box/evidence without reconstructing display fields from SQLite. Both `ApprovalReadModel.ranking` and `HazardReadModel.ranking` are ordered, non-empty arrays (`minItems = 1`, `maxItems = 64`) under the same typed item schema; an empty ranking is invalid in either read model.

### 3.5 Controlled vocabularies

Persisted events use the schema enum, including `RUN_CREATED`, parent-compatible `DETECTION_RECORDED`, `DRONE_DISPATCH_REQUESTED`, `DRONE_COMMAND_COMPLETED`, `PATROLLER_ASSIGNMENT_CONFIRMED`, the plan lifecycle `PLAN_PROPOSED`/`PLAN_CREATED`/`PLAN_APPROVED`/`PLAN_REJECTED`, `PREFLIGHT_REPORTED`, and V0 `OPERATOR_ASSIGNMENT_REJECTED`. Reconciliation has exactly `OPERATION_RECONCILED_SUCCEEDED`, `OPERATION_RECONCILED_FAILED`, or `OPERATION_RECONCILIATION_UNKNOWN`; adapter-local activities are not persisted as substitute event names.

Stable error codes include `INVALID_CONTRACT`, `FORBIDDEN_ACTOR_COMMAND`, `NOT_FOUND`, `FORBIDDEN`, `IDEMPOTENCY_KEY_CONFLICT`, `STALE_STATE_VERSION`, `INVALID_STATE_TRANSITION`, `ARTIFACT_HASH_MISMATCH`, `MISSING_EVIDENCE`, `EVIDENCE_HANDLE_EXPIRED`, `EVIDENCE_HANDLE_REVOKED`, `UNSUPPORTED_MEDIA`, `EVIDENCE_TOO_LARGE`, `UNSCORED_PERCEPTION`, `PERCEPTION_PROVIDER_UNAVAILABLE`, `AGENT_PROVIDER_UNAVAILABLE`, `WORKER_UNAVAILABLE`, `OPERATION_IN_DOUBT`, `REPLAY_NOT_ALLOWED` and `EMERGENCY_LATCHED`. The LLM adapter codes `LLM_CONFIG_MISSING`, `LLM_CONFIG_INVALID`, `LLM_DEPENDENCY_MISSING` and `LLM_CREDENTIAL_MISSING` are adapter-local (WP1 §3.1); they are not part of the shared coordinator registry.

## 4. State, operation and recovery rules

- Events are immutable; `(run_id, sequence_no)` is unique and monotonically increasing.
- Coordinator commits state mutation plus persisted event atomically.
- A side effect has an `OperationRecord` before dispatch. Legal operation transitions are `PENDING -> DISPATCHED -> SUCCEEDED|FAILED`, `PENDING|DISPATCHED -> IN_DOUBT`, and `IN_DOUBT -> SUCCEEDED|FAILED`; no other transition, terminal rewrite, or redispatch is legal.
- If the process crashes after dispatch but before outcome, operation becomes `IN_DOUBT`.
- Only `WorkerPort.reconcile(operation_id, idempotency_key)` may resolve `IN_DOUBT` to `SUCCEEDED` or `FAILED`.
- `UNKNOWN` reconciliation result fails the run and blocks later motion; it never triggers blind redispatch.
- Emergency is independently latched at the low-level path, then ingested as `EMERGENCY_LATCHED` when the coordinator is reachable. It makes the run terminal and disables ordinary commands.

## 5. Artifact and provider profile contracts

`ArtifactStorePort.put_selected_frame()` accepts only JPEG/PNG below 10 MiB, computes SHA-256, writes only run-scoped `storage_key` paths and returns `ArtifactRef`. `get(run_id, artifact_id)` is coordinator-mediated and verifies the artifact belongs to that run; UI receives only the bounded `EvidenceContentHandle`/safe served response, never a raw path. The V0 retention policy is run-lifetime plus explicit manual cleanup; absence/corruption fails evidence use.

A selected provider profile must validate against [`provider-profile-v1.schema.json`](contracts/provider-profile-v1.schema.json). [`provider-profile-unapproved.template.json`](contracts/provider-profile-unapproved.template.json) is deliberately `UNAPPROVED`; it is not selectable. For this demo, an approved profile requires a named joint approval record (shared Colin + Dzivha) over the versioned demo test-image-set hash; WP3 execution is Dzivha-only. There is no release-benchmark ceremony or wider joint-approval process beyond that named record. [`evaluation-manifest-v1.schema.json`](contracts/evaluation-manifest-v1.schema.json) defines those reproducible inputs.

For `judged_path: true`, both the selected `ArtifactRef.source_kind` and `PerceptionResult.source_kind`/`artifact_source_kind` MUST be `LIVE`; schema conditionals reject `SIMULATED` and `REPLAYED`. Deterministic fakes always emit `SIMULATED`; there is no "fake real-mode".

## 6. Coordinator read/command boundary

```text
CoordinatorPort.submit(#CommandEnvelope) -> #ResultEnvelope
CoordinatorPort.read_state(run_id) -> #StateSnapshot
CoordinatorPort.read_events(run_id, after_sequence, limit) -> #EventEnvelope[]
CoordinatorPort.read_mountain(run_id) -> #MountainStateSnapshot
CoordinatorPort.read_evidence(run_id, artifact_id) -> EvidenceRead
CoordinatorPort.read_approval(task_id) -> #ApprovalReadModel
CoordinatorPort.read_plan(plan_id) -> #PlanReadModel
CoordinatorPort.read_preflight(run_id) -> #PreflightReport
CoordinatorPort.read_hazard(target_id) -> #HazardReadModel
CoordinatorPort.read_perception(result_id) -> #PerceptionResult
CoordinatorPort.read_video_status(run_id) -> #VideoStatus
CoordinatorPort.open_live_video(run_id) -> #SafeVideoStreamHandle
```

`EvidenceRead` is a Python-only pair of the schema-validated `#ArtifactRef` and an opaque `EvidenceContentHandle`; it is not a wire-schema anchor. The handle is a coordinator-created, run-scoped capability with this minimum contract:

- It exposes only `artifact_id`, `run_id`, `media_type`, `expires_at` and `read(max_bytes: int = 10 * 1024 * 1024) -> bytes`; `max_bytes` must be an integer in `1..10 * 1024 * 1024`.
- `media_type` is allowlisted to `image/jpeg` or `image/png`; each read is re-authorized for the bound run and is limited to `max_bytes`, 10 MiB and the coordinator's configured evidence-read timeout. No partial bytes are returned.
- Expiry or coordinator revocation makes the handle unusable. Missing, foreign-run, expired, revoked, unsupported-media and over-limit reads fail closed with a typed error (`NOT_FOUND`, `FORBIDDEN`, `EVIDENCE_HANDLE_EXPIRED`, `EVIDENCE_HANDLE_REVOKED`, `UNSUPPORTED_MEDIA` or `EVIDENCE_TOO_LARGE`) and perform no state mutation.
- The handle never exposes a filesystem path, `storage_key`, credentials, a URL without the coordinator's authorization/expiry controls, or raw video bytes. `AgentReadToolPort` cannot return or invoke it in V0; a future agent evidence tool requires a later WP0 amendment.

`WorkerPort.read_video_status(run_id) -> #VideoStatus` and `WorkerPort.open_live_video(run_id) -> #SafeVideoStreamHandle` are typed, read-only live-video boundaries. `VideoStatus` carries lifecycle/freshness/dimensions/degradation and selected-frame artifact ID/SHA-256 correlation; a judged run is valid only while it reports `status: LIVE`, `source_kind: LIVE`, and a selected-frame reference. `SafeVideoStreamHandle` is an expiring read-only served URL with no storage path, bytes, or raw-frame field. WP4 produces both; WP2 renders status/handle and degradation. The coordinator may expose their run-associated reference/status but must not persist video bytes or proxy raw frames.

Artifact hash equality is semantic, not a JSON Schema claim: WP0 validator tests must reject any `PerceptionRequest` where `artifact_sha256 != artifact.sha256` with `ARTIFACT_HASH_MISMATCH`, including `contracts/fixtures/perception-request-artifact-hash-mismatch.invalid.json`.

Bounding-box geometry is likewise a semantic validation rule: when `PerceptionResult.bbox_xyxy_normalized` is present, it must satisfy `0 <= x1 < x2 <= 1` and `0 <= y1 < y2 <= 1`. The WP0 validator rejects inverted or zero-area boxes even though JSON Schema constrains only the individual array values.

### 6.1 Python implementation handoff

This section fixes Python ownership and injection points; it does not create a second wire contract. The JSON Schema anchors above remain authoritative for field names, required fields, discriminators and closed-object behavior.

#### Public import and validation rule

- `sentinel.contracts` is the only shared contract import for WP1–WP4. Consumers use its public exports for schema validation and contract wrappers; they do not copy the JSON models into local dataclasses.
- Every port boundary receives or returns the named contract represented by the corresponding `sentinel-v0-wire.schema.json#Anchor`, except the explicitly Python-only `AgentToolContext` capability context and `EvidenceRead`/`EvidenceContentHandle` capability defined in §6. No arbitrary `dict`, model-native SDK object or provider response crosses a port.
- A port implementation validates incoming and outgoing wire values at its boundary. Adapter-local configuration and provider errors remain adapter-local; coordinator state and persisted error vocabulary remain Colin's responsibility.
- The canonical protocol declarations are implementation targets at `sentinel.coordinator.ports` for `CoordinatorPort`, `AgentReadToolPort`, `WorkerPort` and `PerceptionPort`, and at `sentinel.adapter.llm` for `AgentModelPort`. The allowed dependency direction is ports/adapters -> `sentinel.contracts`; `sentinel.contracts` never imports coordinator, adapter, UI or worker code. A consumer may use structural typing, but it must not publish a second incompatible protocol.

#### Canonical Python signatures

```python
@dataclass(frozen=True)
class AgentToolContext:
    agent_run_id: str
    run_id: str
    state_version: int
    request_id: str  # coordinator-generated capability/request identity
    allowed_reference_ids: frozenset[str]
    deadline: datetime

class AgentReadToolPort(Protocol):
    def read_state(self, context: AgentToolContext) -> StateSnapshot: ...
    def read_mountain(self, context: AgentToolContext) -> MountainStateSnapshot: ...
    def read_events(
        self, context: AgentToolContext, after_sequence: int, limit: int
    ) -> tuple[EventEnvelope, ...]: ...

class AgentModelPort(Protocol):
    def propose(
        self, request: AgentRequest, read_tools: AgentReadToolPort
    ) -> AgentModelResult: ...

class WorkerPort(Protocol):
    def dispatch(self, request: WorkerDispatch) -> ResultEnvelope: ...
    def reconcile(self, operation_id: str, idempotency_key: str) -> ReconciliationResult: ...
    def read_video_status(self, run_id: str) -> VideoStatus: ...
    def open_live_video(self, run_id: str) -> SafeVideoStreamHandle: ...

class PerceptionPort(Protocol):
    def observe(self, request: PerceptionRequest) -> PerceptionResult: ...

class CoordinatorPort(Protocol):
    def submit(self, command: CommandEnvelope) -> ResultEnvelope: ...
    def read_state(self, run_id: str) -> StateSnapshot: ...
    def read_events(
        self, run_id: str, after_sequence: int, limit: int
    ) -> tuple[EventEnvelope, ...]: ...
    def read_mountain(self, run_id: str) -> MountainStateSnapshot: ...
    def read_evidence(self, run_id: str, artifact_id: str) -> EvidenceRead: ...
    def read_approval(self, task_id: str) -> ApprovalReadModel: ...
    def read_plan(self, plan_id: str) -> PlanReadModel: ...
    def read_preflight(self, run_id: str) -> PreflightReport: ...
    def read_hazard(self, target_id: str) -> HazardReadModel: ...
    def read_perception(self, result_id: str) -> PerceptionResult: ...
    def read_video_status(self, run_id: str) -> VideoStatus: ...
    def open_live_video(self, run_id: str) -> SafeVideoStreamHandle: ...
```

`AgentReadToolPort` is a coordinator-owned, V0-only capability view. Before each model invocation, the coordinator constructs an immutable `AgentToolContext` from the `AgentRequest` and a fresh coordinator-generated `request_id`; the model cannot alter `agent_run_id`, `run_id`, `state_version`, `allowed_reference_ids` or `deadline`. The adapter may choose only the three named read methods, and every call receives that context rather than a caller-supplied run identifier. The coordinator enforces ownership of every reference, the exclusive event-cursor range, the request deadline and V0 tool/event limits, validates arguments, and delegates to the corresponding `CoordinatorPort` read. It is not a database handle, SQL interface, second state store or second coordinator protocol. Tool invocations write a durable `ToolOperationAudit` record with `request_id`, `agent_run_id`, `run_id`, expected/observed state version, tool-policy revision, tool name, bounded canonical-argument summary plus hash, outcome, error code when rejected, and start/completion timestamps. Raw SQL, filesystem paths and credentials are never an agent contract. The coordinator rechecks the request state version before accepting the final `AgentModelResult`; a stale proposal is rejected without plan creation or dispatch. These in-process capability calls are not new `sentinel.v0` wire payloads; a future cross-process tool transport requires a WP0 contract amendment.

`read_events` uses an exclusive `after_sequence` cursor and returns committed events in ascending `sequence_no` order. `submit` is the only coordinator command entry point: UI, agent and coordinator callers construct a complete `CommandEnvelope`; workers return typed `WorkerPort` facts/results instead of submitting caller-selected commands. Callers do not invoke coordinator methods such as `approve()`, `dispatch()`, `set_threshold()` or `update_state()`.

#### Coordinator-enforced actor/command authorization

The wire schema permits the full `actor_type` vocabulary on `CommandEnvelope` for correlation and forward compatibility; the coordinator MUST enforce this closed V0 matrix before any state or operation mutation:

| `actor_type` | Commands accepted through `CoordinatorPort.submit` |
|---|---|
| `AGENT` | `AGENT_INTENT_PROPOSE` only; the payload is proposal-only and cannot contain dispatch, worker, perception or emergency instructions |
| `OPERATOR` | `RUN_CREATE`, `OPERATOR_DECIDE`, `EMERGENCY_SIGNAL`, `FORCE_RUN_CREATE` |
| `COORDINATOR` | `INSPECTION_DISPATCH`, `PERCEPTION_INGEST`, `TASK_CREATE`, `TASK_RANK`, `OPERATION_RECONCILE` |
| `WORKER` | none; worker facts, command outcomes and reconciliation observations enter through the typed `WorkerPort`, not a caller-selected command envelope |
| `SYSTEM` | `EMERGENCY_SIGNAL` only, and only from the low-level emergency-latch ingress |

No other actor/command pair is legal. `OPERATOR_DECIDE` and `FORCE_RUN_CREATE` are operator-only; an agent cannot submit either, request emergency control, dispatch or redispatch, and a worker cannot manufacture an operator decision. An unauthorized pair is rejected with `FORBIDDEN_ACTOR_COMMAND` before state/operation mutation or physical dispatch. The low-level emergency path remains available even when ordinary command processing is disabled; an active latch remains terminal and cannot be cleared or overridden by any command.

Python `str` parameters above carry the schema's validated UUID/configured-ID values. The signatures do not relax the schema or permit a caller to pass a different identifier class.

#### Ownership and file boundaries

| Area | Owner | Implementation and test boundary | Must not own or import |
|---|---|---|---|
| Contracts and canonical fixtures | Colin / WP0 | `src/sentinel/contracts/`, `docs/superpowers/specs/contracts/`, `fixtures/sentinel/v0/`, `tests/contracts/` | coordinator policy, provider SDKs, NiceGUI, Tello sockets |
| Coordinator and simulation wiring | Colin / WP1 | `src/sentinel/coordinator/`, `tests/coordinator/`, coordinator integration harness, fake `WorkerPort` and coordinator-owned `AgentReadToolPort` façade | NiceGUI, model-native provider parsing, direct provider calls |
| LLM adapter and agent tests | Alan / WP1 | `src/sentinel/adapter/llm/`, `tests/agent/`, fake `AgentModelPort` | SQLite, worker/perception/UI storage, policy arithmetic, drone/SDK access |
| NiceGUI surface and client seam | Alan / WP2 | `src/sentinel/ui/`, `tests/ui/`, fake `CoordinatorClient` implementing `CoordinatorPort` by injection | a second coordinator protocol, SQLite, raw artifact paths, Tello/provider calls |
| Perception adapter and fixtures | Dzivha / WP3 | `src/sentinel/perception/`, `fixtures/perception/`, `tests/perception/`, fake `PerceptionPort` responses | SQLite, coordinator events/state, task/ranking policy, worker/drone control |
| Physical worker and live video | Colin / WP4 | `src/sentinel/worker/`, `tests/worker/`, `tests/integration/` | alternate wire formats, autonomous route changes, blind redispatch |

The concrete provider profile and evaluation manifest are prepared and executed by Dzivha; Colin and Dzivha jointly approve the versioned profile/hash gate. The checked-in `UNAPPROVED` template remains the only selectable state until that approval exists.

#### Fake-adapter ownership and composition

- Alan owns the deterministic fake agent and its parser/provider-outcome fixtures.
- Colin owns the deterministic fake worker and the coordinator restart/replay harness used by G1.
- Dzivha owns the deterministic fake perception responses and perception fixtures used by WP1/WP3.
- Alan owns the fake `CoordinatorClient` used by WP2 UI tests; it is a test double of `CoordinatorPort`, not a second state store.
- All fake adapters emit `source_kind: SIMULATED`, require no credentials or network, and are rejected from the judged path. Replayed fixtures are labelled `REPLAYED` and are also rejected from judged use.
- Cross-package composition happens only in an injected integration harness: the coordinator receives `AgentModelPort`, `WorkerPort` and `PerceptionPort` instances. One adapter must not import or privately construct another owner's implementation.

#### Integration and acceptance handoff

1. Colin publishes the WP0 contract package/fixtures, the `sentinel.coordinator.ports` declarations and the coordinator-owned V0 `AgentReadToolPort` façade.
2. Alan implements `AgentModelPort`; its model loop may choose only the injected `AgentReadToolPort` reads. Dzivha implements `PerceptionPort`; Colin implements the coordinator plus fake `WorkerPort`. Each package is testable with only `sentinel.contracts` and its declared fixtures.
3. Alan's `CoordinatorClient` is injected with a `CoordinatorPort` instance. The UI sends complete command envelopes and consumes read models; it never reaches around the port.
4. The first cross-package proof is the deterministic simulated path. It must restart and replay the coordinator from committed events, then cover the bounded V0 agent read-tool loop, duplicate idempotency, stale versions, rejection, provider error, scoreless results, emergency and `IN_DOUBT`/`UNKNOWN` reconciliation.
5. The package-level acceptance commands are `uv run python -m pytest tests/contracts -q`, `uv run python -m pytest tests/coordinator tests/agent -q`, `uv run python -m pytest tests/ui -q` (with the WP2 smoke command), and `uv run python -m pytest tests/perception -q`. A package is not handoff-complete because another package's tests pass.
6. If a required field, method or event is missing, stop at the contract boundary and amend WP0 first. Do not add an adapter-local field or overload a nearby field to avoid a contract amendment.

No UI, worker, perception adapter or LLM adapter opens SQLite directly.

### 6.1 WP0/WP1 coordinator seam

The canonical ingress is `CoordinatorPort.submit(#CommandEnvelope) -> #ResultEnvelope`; adapters do not append events directly. `AGENT_INTENT_PROPOSE` carries the catalogue `catalogue_plan_id` when its intent is `INSPECT_SECTION` or `REQUEST_OPERATOR_APPROVAL`; `NO_ACTION_REQUIRED` carries no plan identity. The coordinator resolves the catalogue revision/hash and persists that immutable plan identity before approval or dispatch.

The worker submits `PREFLIGHT_INGEST` with the immutable, run/plan-bound full `#PreflightReport` (exactly eight checks). The coordinator validates and persists the report, then emits `PREFLIGHT_REPORTED`; the worker and agent cannot emit that event themselves. `ASSIGNMENT_PROPOSE` is likewise a coordinator command carrying `task_id`, `patroller_id`, and a bounded `reason`; it creates a pending approval and cannot confirm the assignment.

The agent-facing gateway preserves exactly three coordinator-mediated read/wait tools: `state.get_summary`, `state.get_recent_events`, and `wait_for_event`. All proposal, task, ranking, assignment and approval operations are typed commands through `submit`; the model has no authority over facts, thresholds, ranking arithmetic, plan identity, preflight checks, persistence, physical dispatch, or operator approval.

Shared `ResultEnvelope` fields are `status`, `error_code`, `reason`, `operation_id`, `correlation_id`, and `state_version` where applicable. Shared coordinator errors remain in the WP0 registry; adapter-local configuration/provider failures (`LLM_CONFIG_MISSING`, `LLM_CONFIG_INVALID`, `LLM_DEPENDENCY_MISSING`, `LLM_CREDENTIAL_MISSING`) are returned by the adapter boundary and are never promoted into persisted coordinator error codes or event names.

## 7. Acceptance criteria

WP0 passes only when all are true:

- `uv run python -m pytest tests/contracts -q` passes.
- Schema validation accepts valid and rejects malformed command/result/event/perception fixtures. It directly rejects all five discriminator negatives: `RUN_CREATE` + approval payload, `OPERATOR_DECIDE` + request text, `RUN_CREATED` + operation payload, `INTENT` without intent, and `NO_ACTION` + `INSPECT_SECTION`.
- WP0 semantic validator tests load `perception-request-artifact-hash-mismatch.invalid.json` and reject it with `ARTIFACT_HASH_MISMATCH`; they compare `PerceptionRequest.artifact_sha256` exactly to nested `ArtifactRef.sha256` rather than relying on JSON Schema.
- WP0 semantic validator tests reject inverted and zero-area normalized `bbox_xyxy` fixture values.
- Fixtures cover success, rejection, scoreless result, provider error, stale version, duplicate key conflict, `IN_DOUBT`, emergency and restart cursor replay.
- The mountain fixture has exactly six valid zones and deterministic serialization.
- Coordinator boundary tests prove `AgentToolContext` cannot be altered by the model, reject foreign references/cursors and expired deadlines, and record the complete `ToolOperationAudit` correlation fields.
- Evidence capability tests accept only run-scoped JPEG/PNG reads within the 10 MiB and configured timeout bounds, and fail closed for missing/foreign/expired/revoked/unsupported/over-limit access without partial bytes, paths, storage keys or state mutation.
- Command-gateway tests cover every matrix cell and reject every unauthorized actor/command pair with `FORBIDDEN_ACTOR_COMMAND` before state/operation mutation; in particular they reject agent/operator confusion, worker-submitted commands, agent force-run/emergency/dispatch attempts, and non-latch system commands.
- The fixture validates against `mountain-v0.schema.json`; every polygon is a closed, non-self-intersecting `(x,y)` local-schematic ring in inclusive `[0,100]` bounds.
- DDL/constraint manifest names every cross-WP entity and invariant.
- A fake consumer from WP1, WP2 and WP3 imports only `sentinel.contracts` and fixtures.

## 8. Non-goals

WP0 does not implement production migrations, HTTP, broker, NiceGUI, LLM client, provider client or physical command execution.