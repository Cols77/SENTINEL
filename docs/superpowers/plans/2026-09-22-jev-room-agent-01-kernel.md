# Jev Room Agent Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an append-only, replayable Jev room-session kernel that parses ordered goals, exposes grounded reads, and cannot yet issue physical actions.

**Architecture:** A focused `sentinel.room` package stores immutable events in the survey SQLite database, projects versioned snapshots, compiles bounded goals through Jev, and generates typed candidates. The first controller executes reads and answers only; all physical candidate kinds are capability-disabled until Plans 02–05 enable them.

**Tech Stack:** Python 3.11+, dataclasses, enums, SQLite, JSON Schema, pytest, existing `JevJudge`, `SurveyStore`, and audit sinks.

**Approved design:** [Jev Agentic Room Session and Bounded Localization Recovery](../specs/2026-09-22-jev-agentic-room-design.md)

---

## File map

- Create `src/sentinel/jev_choice.py`: shared pure validation of Jev choices.
- Create `src/sentinel/room/{__init__,models,store,goals,ingest,queries,candidates,controller}.py`.
- Create `src/sentinel/room/sql/schema.sql`: append-only room session/event/evidence schema.
- Modify `src/sentinel/coordinator/proposer.py` and `src/sentinel/survey/proposer.py`: call shared Jev gate without semantic changes.
- Modify `src/sentinel/survey/live_detect.py`: preserve decision-grade correlation fields.
- Add focused tests under `tests/room/` and extend proposer/live-detect tests.

### Task 1: Centralize Jev choice validation

**Files:**
- Create: `src/sentinel/jev_choice.py`
- Create: `tests/test_jev_choice.py`
- Modify: `src/sentinel/judgment.py`
- Modify: `src/sentinel/coordinator/proposer.py`
- Modify: `src/sentinel/survey/proposer.py`
- Modify: `tests/coordinator/test_jev_proposer.py`
- Modify: `tests/survey/test_proposer.py`

- [ ] **Step 1: Write failing pure-gate tests**

Pin this interface and test legal selection, absent/`None` scalar confidence fallback, malformed/non-finite values, missing selected probability, non-max selected label, thin margin and explicit escalation:

```python
@dataclass(frozen=True)
class ChoiceAssessment:
    selected_label: str | None
    final_label: str
    outcome: str
    confidence: float | None
    selected_probability: float | None
    confidence_source: Literal["SCALAR", "SELECTED_PROBABILITY", "MISSING"]
    margin: float | None
    probabilities: tuple[tuple[str, float], ...]

def assess_choice(answer: object, legal_labels: Collection[str], *,
                  escalation_label: str, act_at: float,
                  min_margin: float) -> ChoiceAssessment: ...
```

- [ ] **Step 2: Verify the tests fail because the module is absent**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/test_jev_choice.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: import failure for `sentinel.jev_choice`.

- [ ] **Step 3: Implement the pure gate and migrate both proposers**

Define one `ChoiceJudge` protocol in `judgment.py` whose `choice(state, instruction, *, criteria, name)` form is implemented by `JevJudge`, `StaticJudge`, and test fakes. Require booleans to be invalid numeric values, every available probability finite and in `[0,1]`, selected label legal and selected probability maximal. When scalar confidence is absent or `None`, use the selected probability. Canonicalize probabilities into label-sorted immutable tuples before constructing the assessment; test that later mutation of the provider response cannot change the assessment or its digest. Preserve current proposer-specific audit record names and escalation text.

Implement the gate in this order so provider-controlled input has one deterministic outcome:

```python
def assess_choice(answer, legal_labels, *, escalation_label, act_at, min_margin):
    if not isinstance(answer, Mapping):
        return invalid(escalation_label, "MALFORMED_ANSWER")
    label = answer.get("value")
    probabilities = canonical_probabilities(answer.get("probabilities"))
    if label not in legal_labels or probabilities is None:
        return invalid(escalation_label, "ILLEGAL_OR_MALFORMED")
    selected = probability_for(probabilities, label)
    if selected is None or selected != max(value for _, value in probabilities):
        return invalid(escalation_label, "MISSING_OR_NON_MAX_SELECTION")
    confidence, source = scalar_or_selected_probability(answer, selected)
    alternatives = [value for key, value in probabilities if key != label]
    margin = selected - max(alternatives, default=0.0)
    if confidence < act_at or margin < min_margin:
        return escalated(label, escalation_label, confidence, source, margin, probabilities)
    return accepted(label, confidence, source, margin, probabilities)
```

The private helpers return `ChoiceAssessment` and never raise for provider-controlled input. Add one regression in each proposer test proving its existing audit sink receives the same accepted/escalated label and gate reason after migration.

- [ ] **Step 4: Run gate and proposer regressions**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/test_jev_choice.py tests/coordinator/test_jev_proposer.py tests/coordinator/test_proposer.py tests/survey/test_proposer.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: all pass with no changed accepted/escalated fixtures.

- [ ] **Step 5: Commit**

```powershell
rtk git add src/sentinel/jev_choice.py src/sentinel/judgment.py src/sentinel/coordinator/proposer.py src/sentinel/survey/proposer.py tests/test_jev_choice.py tests/coordinator/test_jev_proposer.py tests/survey/test_proposer.py
rtk git commit -m "refactor: centralize Jev choice validation"
```

### Task 2: Add room contracts and append-only state

**Files:**
- Create: `src/sentinel/room/__init__.py`
- Create: `src/sentinel/room/models.py`
- Create: `src/sentinel/room/store.py`
- Create: `src/sentinel/room/sql/schema.sql`
- Create: `tests/room/test_models.py`
- Create: `tests/room/test_store.py`

- [ ] **Step 1: Write failing domain and store tests**

Define and test these public types:

```python
class GoalKind(StrEnum):
    ANSWER_STATE = "ANSWER_STATE"
    COUNT = "COUNT"
    FIND_OBSERVE = "FIND_OBSERVE"
    FOLLOW = "FOLLOW"
    LAND_NEAR = "LAND_NEAR"

class CandidateKind(StrEnum):
    READ_DRONE = "READ_DRONE"
    READ_OBSERVATIONS = "READ_OBSERVATIONS"
    READ_COUNTS = "READ_COUNTS"
    OBSERVE = "OBSERVE"
    VALIDATE_DETECTION = "VALIDATE_DETECTION"
    VERIFY_RELATION = "VERIFY_RELATION"
    REACQUIRE_TARGET = "REACQUIRE_TARGET"
    MAINTAIN_OBSERVATION = "MAINTAIN_OBSERVATION"
    FOLLOW_STEP = "FOLLOW_STEP"
    LAND_AT_ZONE = "LAND_AT_ZONE"
    RETURN_HOME = "RETURN_HOME"
    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    ESCALATE = "ESCALATE"

@dataclass(frozen=True)
class PhysicalCost:
    distance_cm: float = 0.0
    upper_bound_s: float = 0.0
    provider_wait_s: float = 0.0
    requires_airborne: bool = False

@dataclass(frozen=True)
class RoomCandidate:
    candidate_id: str
    kind: CandidateKind
    snapshot_version: int
    relevant_state_sha256: str
    description: str
    predicted_benefit: str
    evidence_refs: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    physical_cost: PhysicalCost = PhysicalCost()

class TerminalState(StrEnum):
    COMPLETED = "COMPLETED"
    INCOMPLETE_RETURNED = "INCOMPLETE_RETURNED"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    ABORTED = "ABORTED"
    RECOVERY_FAILURE = "RECOVERY_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    SAFE_LANDED = "SAFE_LANDED"
    LANDED_AT_CERTIFIED_ZONE = "LANDED_AT_CERTIFIED_ZONE"

class GoalStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SATISFIED = "SATISFIED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED = "BLOCKED"

@dataclass(frozen=True)
class RoomSnapshot:
    session_id: str
    state_version: int
    goal_revision: int
    pose_revision: int
    home_revision: int
    energy_revision: int
    coverage_revision: int
    static_evidence_revision: int
    target_revision: int
    landing_zone_revision: int
    tool_outcome_revision: int

@dataclass(frozen=True)
class CapabilityGrant:
    capability: str
    state: Literal["SIMULATION_ONLY", "DISABLED", "TRIAL", "PROMOTED"]
    site_sha256: str | None
    aircraft_id: str | None
    profile_sha256s: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    expires_at: str | None
    grant_sha256: str

@dataclass(frozen=True)
class CapabilityProfile:
    grants: tuple[CapabilityGrant, ...]
```

Store tests prove monotonic `state_version`, atomic event append, immutable event rows, idempotency-key duplicate recovery, correlation/causation IDs, replay equivalence after reopen, and coexistence with `SurveyStore` on separate WAL-enabled connections. Pin `PRIMARY KEY(session_id, sequence_no)`, globally unique `event_id`, and `UNIQUE(session_id, idempotency_key)`; a duplicate key with a different canonical event digest is rejected. Test independent `RoomSnapshot` revisions. Test canonical capability-grant hashes, expiry and exact site/aircraft/profile binding. A `TRIAL` grant is legal only for a manifest-bound acceptance session naming one capability; ordinary live execution requires `PROMOTED`. Reject credentials, authorization headers and raw image bytes in event payloads.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_models.py tests/room/test_store.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: missing room modules.

- [ ] **Step 3: Implement schema and store**

Create a versioned migration runner and `room_schema_version`. Create `room_sessions(session_id, request_text, capability_json, status, state_version, created_at)` and append-only `room_events(sequence_no, event_id, session_id, state_version, event_type, correlation_id, causation_id, idempotency_key, event_sha256, payload_json, wall_time, monotonic_ms)` with the keys above. `append_event` performs compare-and-set on `state_version` and inserts the event in one transaction. Add SQL triggers rejecting update/delete of room events and later evidence/revision tables. Snapshot SQLite with the backup API after quiescence, never by copying a live WAL database.

Pin the store boundary before writing SQL:

```python
class RoomStore:
    @classmethod
    def open(cls, path: str | Path) -> "RoomStore": ...
    def create_session(self, session_id: str, request_text: str,
                       capabilities: CapabilityProfile) -> None: ...
    def append_event(self, *, session_id: str, expected_version: int,
                     event_type: str, payload: Mapping[str, object],
                     correlation_id: str, causation_id: str | None,
                     idempotency_key: str) -> StoredEvent: ...
    def events(self, session_id: str, *, after_sequence: int = 0) -> tuple[StoredEvent, ...]: ...
    def snapshot_to(self, destination: Path) -> None: ...
```

`append_event` canonicalizes and rejects forbidden payload keys/byte values, computes `event_sha256`, starts `BEGIN IMMEDIATE`, checks the session version, inserts the event, increments the session version with `WHERE state_version = ?`, and commits. On an existing idempotency key, return the stored event only when its digest matches; otherwise raise `IdempotencyConflict`. Roll back every failed branch.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_models.py tests/room/test_store.py tests/survey/test_store.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/__init__.py src/sentinel/room/models.py src/sentinel/room/store.py src/sentinel/room/sql/schema.sql tests/room/test_models.py tests/room/test_store.py
rtk git commit -m "feat(room): add append-only session state"
```

Expected: all selected tests pass; duplicate delivery returns the original event, digest mismatch raises `IdempotencyConflict`, mutation SQL is rejected by triggers, and reopening the SQLite backup reproduces the same event stream.

### Task 3: Compile ordered bounded goals through Jev

**Files:**
- Create: `src/sentinel/room/goals.py`
- Create: `tests/room/test_goals.py`

- [ ] **Step 1: Write failing goal-compiler tests**

Pin `EntityRef(ref_id, source_objective_id, entity_role, relative_to)` and `GoalObjective(objective_id, ordinal, kind, target_classes, qualifier, relation, spatial_scope, terminal_condition, subject_ref, target_ref, depends_on_objective_ids)`, plus `GoalLedger(request_text, objectives, status)`. Enforce a versioned input-byte limit. Deterministic punctuation/conjunction rules propose clauses without splitting a relation predicate from its subject/object; the compiler then repeats a Jev choice over `NEXT_OBJECTIVE:<clause-id>` or `DONE`, removing each selected clause so the sequence is finite. Use an injected fake judge and assert ordered parsing for: room count plus return, four ordered corners/classes, drone-state read, closer validation, designated man holding phone, land near phone, ambiguous “devices,” unsupported class and contradictory scopes. Pin supported vocabulary aliases `human|humans|people -> person` and `smartphone|cell phone -> phone`; “devices” resolves only to a Jev-selected explicit supported subset or clarification.

Add exact coreference cases. “Find a man that holds a smartphone and follow him” compiles a relation-verification objective followed by `FOLLOW` whose `target_ref` points to the verified designated-person result and whose dependency names that objective. “Find the phone next to you and land nearby it” resolves “you” to the current drone pose snapshot, creates a nearest-phone observation dependency, and binds the `LAND_NEAR.target_ref` to that same phone hypothesis; no other phone can satisfy it. Ambiguous pronouns produce `CLARIFICATION_REQUIRED` and zero physical candidates.

- [ ] **Step 2: Run and observe missing compiler**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_goals.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because `sentinel.room.goals` and its compiler contracts do not exist.

- [ ] **Step 3: Implement hierarchical fixed-choice compilation**

Use the repeated clause protocol plus controller-owned options for goal family, supported class/group, qualifier/relation, spatial scope, entity-reference binding and terminal condition. Treat relative phrases such as “next to you” as one clause attachment and validate them against the snapshot named by the ledger. The final choice is `CONSISTENT` or a controller-owned correction/clarification; inconsistency rolls back the tentative ledger. Persist every attempt through `RoomStore`; failure/low confidence yields `CLARIFICATION_REQUIRED` or `UNSUPPORTED`, never a physical default. Preserve user ordering and retain “man” as a qualifier. Bound each choice set through hierarchical goal-local selection; do not truncate legal semantic regions into an inaccessible top-K.

Use this public compiler and finite state transition:

```python
class GoalCompiler:
    def __init__(self, judge: ChoiceJudge, store: RoomStore,
                 *, input_limit_bytes: int, prompt_revision: str) -> None: ...
    def compile(self, session_id: str, request_text: str,
                snapshot: RoomSnapshot) -> GoalLedger: ...

# compile():
# 1. Reject oversize/empty input before calling the judge.
# 2. clause_parser.propose(request_text) returns immutable ordered clauses.
# 3. Choose NEXT_OBJECTIVE:<id> or DONE from the remaining clauses.
# 4. Classify the chosen clause through family, parameters and references.
# 5. Resolve pronouns only against unique prior EntityRef values or the
#    explicit current-drone ref; ambiguity terminates CLARIFICATION_REQUIRED.
# 6. Run CONSISTENT/correction against the original request.
# 7. Atomically append attempts and the accepted ledger revision.
```

Test both exact coreference prompts with fake-judge transcripts, including every candidate label presented and the persisted attempt sequence. The compiler must never create `FOLLOW` or `LAND_NEAR` with an unresolved reference.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_goals.py tests/test_judgment.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/goals.py tests/room/test_goals.py
rtk git commit -m "feat(room): compile ordered Jev goal ledgers"
```

Expected: all selected tests pass; the exact follow/landing prompts preserve ordered dependencies and ambiguous references produce clarification with zero physical candidates.

### Task 4: Ingest decision-grade perception evidence

**Files:**
- Create: `src/sentinel/room/ingest.py`
- Modify: `src/sentinel/room/sql/schema.sql`
- Modify: `src/sentinel/survey/live_detect.py`
- Modify: `scripts/live_detect.py`
- Modify: `src/sentinel/cube/mission.py`
- Create: `tests/room/test_ingest.py`
- Modify: `tests/survey/test_live_detect.py`

- [ ] **Step 1: Write failing identity/idempotency tests**

Define versioned `PerceptionRequest` and `PerceptionResult` envelopes. Require each frame/result to carry `session_id`, `action_id`, `observation_id`, `frame_id`, `run_id`, snapshot version, request digest, path/hash, capture/result timestamps, pose provenance, provider, requested/pinned model, `resolved_model: str | None`, and identity status `VERIFIED|MISMATCH|UNVERIFIED`. A successful result requires a verified resolved identity. Timeout/auth/transport failures may retain `resolved_model=None` with `UNVERIFIED` and remain valid typed failure results. Test duplicate delivery, identity/hash mismatch, malformed row, provider failure before identity resolution, late obsolete result and historical retention. A late result may add evidence but must not update current target state.

```python
@dataclass(frozen=True)
class PerceptionRequest:
    schema_version: str
    session_id: str
    action_id: str
    observation_id: str
    frame_id: str
    run_id: str
    snapshot_version: int
    request_digest: str
    frame_path: str
    frame_sha256: str
    captured_at: str
    pose_revision_id: str
    requested_model: str

@dataclass(frozen=True)
class PerceptionResult:
    request: PerceptionRequest
    status: Literal["SUCCEEDED", "FAILED"]
    provider: str
    resolved_model: str | None
    identity_status: Literal["VERIFIED", "MISMATCH", "UNVERIFIED"]
    completed_at: str
    detections: tuple[DetectionEvidence, ...]
    error_category: str | None = None

@dataclass(frozen=True)
class DetectionEvidence:
    detection_id: str
    class_name: str
    confidence_band: Literal["high", "medium", "low"]
    raw_score: float | None
    score_kind: str | None
    bbox_xywh: tuple[float, float, float, float]
    bbox_coordinate_space: Literal["FULL_FRAME_PIXELS", "TILE_PIXELS"]
    frame_width_px: int
    frame_height_px: int
    tile_origin_xy_px: tuple[int, int] | None
    evidence_sha256: str
```

Preserve the current `survey.detector.DetectionBatch` semantics exactly: never invent a numeric score from a VLM confidence band. Ingestion converts tile boxes to a separately stored full-frame box only when frame dimensions and tile origin are valid, retaining the original coordinate record for audit. Relation and tracker consumers use the resolved full-frame box and still keep `confidence_band`, optional `raw_score` and `score_kind` distinct.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_ingest.py tests/survey/test_live_detect.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because the room ingestion module and correlated envelopes do not exist.

- [ ] **Step 3: Add append-only evidence tables and ingestion**

Add `room_observations`, `room_perception_results`, `room_detection_evidence` and stable `room_object_hypotheses` plus append-only hypothesis revisions and immutability triggers. Deduplicate immutable provider payload by `(provider, resolved_model, request_digest, frame_sha256)` and link it separately to `(session_id, observation_id)`; never collapse evidence across sessions. Add optional correlation context to the frame producer so IDs exist at capture time. The sidecar atomically appends a result envelope to an inbox JSONL; `RoomStore` polls and ingests whole records idempotently under a bounded wait. Preserve the sidecar's non-commanding process boundary.

Implement `ingest_result(store, result, *, current_snapshot_version) -> IngestOutcome`. Validate envelope identity and frame hash before opening a transaction; persist successful, failed and late results; create hypothesis revisions only for verified successful results; set `authorizes_current_state=False` whenever the result snapshot is stale. The JSONL writer serializes one canonical line, flushes and fsyncs it before making the complete record visible under one process lock, so the reader never ingests a partial record.

- [ ] **Step 4: Run tests and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_ingest.py tests/survey/test_live_detect.py tests/survey/test_providers.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/ingest.py src/sentinel/room/store.py src/sentinel/room/sql/schema.sql src/sentinel/cube/mission.py src/sentinel/survey/live_detect.py scripts/live_detect.py tests/room/test_ingest.py tests/survey/test_live_detect.py
rtk git commit -m "feat(room): ingest correlated perception evidence"
```

Expected: all selected tests pass; duplicate results are idempotent, stale results remain historical only, provider failures remain typed records, and malformed or identity-mismatched results cannot revise hypotheses.

### Task 5: Add deterministic queries and read-only session loop

**Files:**
- Create: `src/sentinel/room/queries.py`
- Create: `src/sentinel/room/candidates.py`
- Create: `src/sentinel/room/controller.py`
- Create: `tests/room/test_queries.py`
- Create: `tests/room/test_candidates.py`
- Create: `tests/room/test_controller.py`

- [ ] **Step 1: Write failing query/candidate/controller tests**

Test `READ_DRONE`, `READ_OBSERVATIONS`, `READ_COUNTS`, `ANSWER`, `CLARIFY`, and `ESCALATE`. Ground answers in stored evidence with observation time/scope/uncertainty. Repeating a read requires changed relevant state. Reject stale snapshot decisions. Feed illegal choices, gate refusals and tool failures into the next snapshot. Assert zero flight/perception-provider construction for read-only questions; an injected Jev judge may still parse/select the read. Assert physical candidate kinds are absent under the Plan-01 capability profile.

- [ ] **Step 2: Run failing tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_queries.py tests/room/test_candidates.py tests/room/test_controller.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection fails because the room query, candidate and controller modules do not exist.

- [ ] **Step 3: Implement deterministic projections and loop**

The controller order is: ingest events, project snapshot, generate legal candidates, Jev choice, shared gate, version/evidence revalidation, execute one read, append outcome, repeat or terminate. Use relevant-state hashes and refusal suppression instead of a model-call limit. In Plan 01, `READ_COUNTS` projects already-persisted legacy `SurveyStore` results with their existing uncertainty semantics; Plan 03 replaces that adapter with coverage-aware room projections. Deterministic templates answer counts/state; Jev never composes factual values.

Pin the one-turn boundary:

```python
@dataclass(frozen=True)
class TurnOutcome:
    candidate_id: str
    accepted: bool
    event_ids: tuple[str, ...]
    terminal_state: TerminalState | None

class RoomController:
    def step(self, session_id: str) -> TurnOutcome: ...
    def run_until_terminal(self, session_id: str) -> TerminalState: ...
```

`step` projects once, applies mandatory transitions, builds candidates for that version, persists the Jev attempt, re-reads the session version immediately before dispatch, and rejects a mismatch as `STALE_DECISION`. Persist `(candidate_id, relevant_state_sha256, refusal_code)` and exclude the same refused action until its relevant hash changes. Read handlers return typed facts; `ANSWER` renders only those facts through deterministic templates.

- [ ] **Step 4: Run the Plan-01 suite**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room tests/coordinator/test_jev_proposer.py tests/survey/test_proposer.py tests/survey/test_live_detect.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: all pass and no live network/drone construction.

- [ ] **Step 5: Commit and review**

```powershell
rtk git add src/sentinel/room/queries.py src/sentinel/room/candidates.py src/sentinel/room/controller.py tests/room/test_queries.py tests/room/test_candidates.py tests/room/test_controller.py
rtk git commit -m "feat(room): run grounded read-only Jev sessions"
```

Dispatch independent specification and quality reviewers. Fix every confirmed important finding and rerun the Plan-01 suite. Rebase checkpoint from the roadmap follows this plan.
