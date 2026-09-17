# SENTINEL WP1 — Coordinator and Agentic Loop

> **Status:** Revised after independent review and V0 scope decision; re-review pending
> **Owners:** Colin — coordinator semantics/repository; Alan — LLM runtime adapter
> **Prerequisite:** WP0 `sentinel.v0` contracts and fixtures

## 1. Objective

Implement the coordinator and real-LLM adapter against WP0. The deterministic fake agent/worker/perception adapters are mandatory before any provider or drone integration. The active V0 flow is catalogue-plan proposal, immutable operator approval, supervised dispatch, live frame capture and coordinator-persisted perception; an agent never dispatches a plan and the operator does not review database state.

## 2. Ownership and prohibited access

**Colin owns:** SQLite repository/migrations, event order, command gateway, state transitions, idempotency, operation reconciliation, threshold policy, worker/perception ingress and coordinator read models. Task/ranking/patroller and database-state review policy remain retained compatibility work deferred to V0.1.

**Alan owns:** `AgentModelPort`, provider configuration, prompt/tool-policy revisions, structured response parser, bounded catalogue-plan proposal and agent-facing status summaries, plus the fake-agent runtime and UI-client tests. Revisit behavior is retained as V0.1 work and is not implemented by this V0 loop. Colin owns coordinator persistence and transition/recovery tests; Alan does not own or implement that persistence.

Alan’s adapter cannot access SQLite, drone sockets, perception providers, policy arithmetic, approval transitions or UI storage. It receives worker preflight facts read-only and may assess their structured pass/fail values only; it has no hardware access and cannot physically correct a route or issue a correction command. WP2 alone owns rendering. Colin’s coordinator must not parse model-native provider responses.

This is a separation of cognition from authority, not a claim that agents must remain capability-blind. For the MVP, the coordinator gives the agent a bounded request plus a small read-only capability view; the agent may choose which named read to call, while the coordinator remains the only component that validates state, records events, applies approval/preflight/idempotency rules and invokes external ports. This prevents an agent retry, stale read, malformed provider output or prompt-level instruction from becoming an untracked state mutation or physical action without forcing a large context snapshot into every request.

### 2.1 V0 coordinator-mediated agent read tools

V0 exposes exactly three named, read-only tools through the coordinator-owned `AgentReadToolPort`: `read_state(context: AgentToolContext) -> StateSnapshot`, `read_mountain(context: AgentToolContext) -> MountainStateSnapshot` and `read_events(context: AgentToolContext, after_sequence, limit) -> EventEnvelope[]`. Alan’s model loop may choose among these tools, but it never emits SQL, opens SQLite, reads a filesystem path or calls a provider, perception adapter or worker. The coordinator creates the immutable `AgentToolContext` for the current `AgentRequest`; model-supplied arguments cannot replace its `agent_run_id`, `run_id`, expected `state_version`, `request_id`, allowed references or deadline. The tool façade validates the remaining cursor/limit arguments, enforces ownership and deadline/limits, and delegates to the corresponding `CoordinatorPort` read.

Each tool result is typed, bounded and associated with the observed state version. Tool activity records the complete `ToolOperationAudit` described in WP0 §6.1. The coordinator rechecks the request state version before accepting the final `AgentModelResult`; a stale proposal is rejected without plan creation or dispatch. Tool calls are an in-process Python capability seam, not new `sentinel.v0` wire payloads; a future cross-process tool transport requires a WP0 contract amendment.

Evidence/file reads, perception requests and operation proposals remain deferred. A later WP0 amendment may add them only as coordinator-mediated, versioned capabilities with explicit schemas, allowlists, limits, run/state binding, audit and fail-closed errors. A prompt/tool-policy configuration alone cannot enable them. V0 remains proposal-only for state mutation and physical action: only the coordinator may submit commands or call `WorkerPort`.

If a later amendment adds an operation-proposal tool, its result is non-executing. The coordinator may translate it only into the canonical V0 catalogue-plan flow: resolve one exact immutable `SurveyPlanReference`; obtain the matching operator-only `PlanApproval`; require fresh preflight; persist the scoped `PENDING` operation; make at most one dispatch attempt; and reconcile by the original operation ID/idempotency key. A proposal that cannot resolve to that flow is rejected. The agent has no `WorkerPort`, emergency ingress, `FORCE_RUN_CREATE`, operator-decision or redispatch capability; prompt text and tool policy cannot grant one.

If a later amendment adds a perception tool, the coordinator must resolve the stored run-bound artifact, `StationCaptureProvenance`, approved provider profile and frozen threshold/provenance facts; construct and validate the canonical `PerceptionRequest`; reject artifact-hash mismatches and non-`LIVE` judged evidence fail-closed; invoke `PerceptionPort` only through its typed boundary; validate the returned `PerceptionResult`; and persist it through the existing coordinator command/event ingress. The agent cannot construct or submit `PerceptionRequest`/`PerceptionResult` directly, select an unapproved profile, alter thresholds or bypass the persisted result path.

## 3. Ports

The authoritative Python protocol declarations and the complete `CoordinatorPort` surface are defined in WP0 §6.1. Implementations use those signatures; this section states the WP1 ownership and behavior only.

```text
sentinel.coordinator.ports.CoordinatorPort
sentinel.coordinator.ports.AgentReadToolPort
sentinel.coordinator.ports.WorkerPort
sentinel.coordinator.ports.PerceptionPort
sentinel.adapter.llm.AgentModelPort
```

`CoordinatorPort.submit()` is the only command entry point. `AgentReadToolPort` is the only V0 tool surface visible to the model loop; it delegates the three named reads to the coordinator and cannot submit commands. `read_events()` uses an exclusive `after_sequence` cursor and returns committed events in ascending sequence order. `WorkerPort.reconcile()` is the only legal resolver for an `IN_DOUBT` operation; an `UNKNOWN` result fails closed and never causes redispatch. The UI, agent and adapters must not add convenience methods that bypass these boundaries.

### 3.1 Module layout and fake adapters

Colin’s coordinator lives in `sentinel/coordinator/` (repository, port declarations, transitions, idempotency, command gateway and read models); Alan’s runtime lives in `sentinel/adapter/llm/` (`AgentModelPort`, provider config and structured parser). Colin owns the deterministic fake worker and the coordinator restart/replay harness. Alan owns the deterministic fake agent. Dzivha owns the deterministic fake perception responses consumed by WP1 and the corresponding perception fixtures. Alan also owns the fake `CoordinatorClient` used by WP2 tests; it is a test double of `CoordinatorPort`, not a second state store. All fake adapters import only `sentinel.contracts` and declared fixtures, emit `source_kind: SIMULATED`, require no credentials or network, and are rejected from the judged path. Replayed fixtures are labelled `REPLAYED` and are also rejected from judged use. A real-mode test must use a live-capable adapter or be rejected from the judged path.

WP0 supplies the closed, directly-validatable `AgentRequest` and `AgentModelResult` schemas, their discriminators/enums, validators and fixtures. `AgentRequest` remains small: it carries agent/run identity, expected `state_version`, allowed intent-schema revision and deadline; it does not carry a live database handle or arbitrary context. During `propose`, Alan’s adapter receives the injected coordinator-owned `AgentReadToolPort` and may choose only `read_state`, `read_mountain` and bounded `read_events` to obtain typed context. A successful `INTENT` proposes exactly one `catalogue_plan_id`; the coordinator resolves its catalogue revision/hash and creates the immutable plan. `NO_ACTION`, `MALFORMED` and `UNAVAILABLE` carry no plan proposal. No model result contains raw SDK calls, SQL, a dispatch instruction or an operation command. WP1 must consume these contracts rather than add adapter-local wire fields.

Runtime configuration validates against [`contracts/llm-adapter-config-v1.schema.json`](contracts/llm-adapter-config-v1.schema.json). It is built from `SENTINEL_LLM_PROVIDER`, `SENTINEL_LLM_MODEL`, `SENTINEL_LLM_TIMEOUT_MS`, `SENTINEL_LLM_PROMPT_REVISION`, `SENTINEL_LLM_TOOL_POLICY_REVISION`, and optional `SENTINEL_LLM_API_KEY_ENV`; artifacts contain only the environment-variable name, never a secret. Missing required input fails before provider construction with `LLM_CONFIG_MISSING` naming the missing variable. Non-integer/out-of-range timeout, invalid schema, blank required values, or an invalid API-key environment variable name fails with `LLM_CONFIG_INVALID`; no fallback is attempted. Missing the named optional dependency at real-adapter construction fails `LLM_DEPENDENCY_MISSING`; absent referenced credential at invocation fails `LLM_CREDENTIAL_MISSING`. `LLM_CONFIG_MISSING`, `LLM_CONFIG_INVALID`, `LLM_DEPENDENCY_MISSING` and `LLM_CREDENTIAL_MISSING` are adapter-local codes, not shared coordinator registry codes. The optional dependency name is `llm` (installation as an optional `llm` extra is WP1 implementation work; this spec does not modify `pyproject.toml`). Tests use the deterministic fake adapter and require no credential.

## 4. Active V0 run transition matrix: perception path

The run surveys an approved catalogue plan over the fixed six-zone catalogue
(1–6 stations). V0 has one approval point: the exact immutable plan before
dispatch. Perception results are coordinator-owned read models, not operator
approval targets. `WAITING_FOR_APPROVAL` is the plan target in the active path;
`TASK` and `ZONE_RESULT` remain retained deferred targets.
The run does not end until every selected station has a valid capture and a
persisted perception result. Perception results remain coordinator-owned facts;
future agentic actions require a new plan and operator approval.

| From | Event/guard | To | Result |
|---|---|---|---|
| `NEW` | `RUN_CREATED` | `RUNNING` | agent may propose a catalogue plan |
| `RUNNING` | `AGENT_INTENT_PROPOSE` with `INTENT` and one valid `catalogue_plan_id` | `WAITING_FOR_APPROVAL` (plan) | `PLAN_PROPOSED`; coordinator resolves catalogue revision/hash and persists the immutable plan (`PLAN_CREATED`); no dispatch |
| `WAITING_FOR_APPROVAL` (plan) | `OPERATOR_DECIDE` target `PLAN`, `CONFIRM` | `WAITING_FOR_OBSERVATION` | `PLAN_APPROVED`; fresh preflight, durable `PENDING` operation, then one coordinator dispatch attempt |
| `WAITING_FOR_APPROVAL` (plan) | `OPERATOR_DECIDE` target `PLAN`, `REJECT` | `RUNNING` | `PLAN_REJECTED`; no dispatch; proposal may be refined |
| `WAITING_FOR_OBSERVATION` | `OBSERVATION_CAPTURED` | `RUNNING` | perception request is eligible and remains hash/provenance bound |
| `RUNNING` | qualifying `DETECTION_RECORDED` under the frozen provider profile | `RUNNING` or `COMPLETED` after the final station | result is persisted for the station; no task, ranking, assignment or database-state review is created |
| `RUNNING` or `WAITING_FOR_OBSERVATION` | perception/`DETECTION_RECORDED` with `status == PROVIDER_ERROR` (provider failure/error or worker/video fault) | `FAILED` | `PROVIDER_ERROR` precedes score interpretation; no physical retry |
| `RUNNING` or `WAITING_FOR_OBSERVATION` | perception/`DETECTION_RECORDED` with `status` `UNSCORED`/`NOT_DETECTED` (scoreless or below-threshold) | `NEEDS_REVIEW` | scoreless review-hold; disjoint from `PROVIDER_ERROR` by status; no invented score |

| `WAITING_FOR_APPROVAL` (TASK) | `OPERATOR_DECIDE` target `TASK`, `CONFIRM` or `REJECT` | unchanged/deferred | retained compatibility lifecycle; not an active V0 transition |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILED_SUCCEEDED` | prior safe waiting state | result envelope correlates `operation_id`; no redispatch |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILED_FAILED` | `FAILED` or `NEEDS_REVIEW` by evidence policy | result envelope correlates `operation_id` |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILIATION_UNKNOWN` | `FAILED` | fault persisted; motion blocked |
| any nonterminal | `EMERGENCY_LATCHED` | `EMERGENCY` | ordinary commands disabled |

### 4.1 Outcomes, rejection and review-hold

The only V0 terminal run outcomes are `COMPLETED`, `FAILED` and `EMERGENCY`;
`APPROVED`/`REJECTED` are not terminal run states. `PLAN_REJECTED` returns the
run to proposal/refinement without dispatch. `NEEDS_REVIEW` is a review-hold
state, not an automatic revisit trigger; it has no operator database-state
review transition in V0. A future agentic action requires a new immutable plan
and the existing operator plan-approval path. An active emergency latch cannot
be overridden.

The retained `OPERATOR_ASSIGNMENT_REJECTED`, `ZONE_RESULT_REVIEWED` and related
TASK lifecycle remain available for compatibility but are not emitted or
required by the V0 perception path.

### 4.2 Worker preflight

The worker performs preflight and emits the immutable run/plan-bound
`PreflightReport` as the `PREFLIGHT_REPORTED` event. The report has exactly
eight named checks per WP0 §3.3.1 (`WIFI_INTERFACE`, `DRONE_LINK`,
`BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`,
`HEIGHT_ENVELOPE`, `EMERGENCY_LATCH`); `passed` is the conjunction of
`check.passed`. The agent receives only its persisted structured facts and
never performs the checks itself or accesses the drone, network, video,
telemetry or emergency latch. A failed preflight makes the run `FAILED`; only
the operator-only `FORCE_RUN_CREATE` command may override it, creating a new
run/plan that records the failed report and operator reason
(`new_plan_reference`, `real_confirm: true`) and never resumes or re-dispatches
the prior operation. `FORCE_RUN_CREATE` never overrides an active emergency
latch.

### 4.3 Plan identity and canonical serialization

`plan_sha256` is the SHA-256 of the canonical serialization of the plan: the
immutable `SurveyPlanReference` fields (`plan_id`, `catalogue_id`,
`catalogue_revision`, ordered `station_ids`) serialized in schema field order
with deterministic formatting and no whitespace variance. Equality of
`plan_sha256` is semantic: two plan documents are the same plan iff their
canonical serializations hash equally, so the coordinator (dispatching), the
adapter (referencing) and the worker (executing) agree on plan identity. The
coordinator resolves `catalogue_id`/`catalogue_revision` against the approved
`survey-catalogue-v1` catalogue and persists the immutable plan before any
dispatch.

Active V0 agent phases are `RECEIVE_REQUEST`, `PLAN`, `WAIT_FOR_APPROVAL`
(plan proposal only), and `FINALIZE`. Coordinator-owned phases
`WAIT_FOR_OBSERVATION`, perception, coordinator persistence and recovery surround the model
call. The legacy `REQUEST_MISSION`, `INTERPRET_EVIDENCE`, and TASK approval
labels remain retained documentation/compatibility vocabulary and are deferred
from V0. The replay cursor is the last committed `sequence_no`.

## 5. External side effects and reconciliation

1. Validate command, actor, expected state version and scoped idempotency key.
2. Persist command/operation `PENDING` and event atomically.
3. Dispatch through the port once; record acknowledgement/result in a second transaction.
4. Crash/lost acknowledgement becomes `IN_DOUBT`.
5. Reconciliation queries the worker using the original operation ID/key and emits exactly one of `OPERATION_RECONCILED_SUCCEEDED`, `OPERATION_RECONCILED_FAILED`, or `OPERATION_RECONCILIATION_UNKNOWN`.
6. `SUCCEEDED`/`FAILED` produces exactly one durable outcome event and correlated `ResultEnvelope.operation_id`. `UNKNOWN` emits `OPERATION_RECONCILIATION_UNKNOWN` plus `SYSTEM_FAULT_RECORDED`, makes the run `FAILED`, and prohibits redispatch.

No requirement claims physical exactly-once execution; the requirement is at-most-one coordinator dispatch attempt before reconciliation.

## 6. Policy

- Canonical target class is `rock`; parent `pebble_cluster` is preserved as source evidence and maps to it.
- Approval eligibility is `raw_score >= provider_profile.decision_threshold` with matching provider/model/prompt/transform revisions and non-null score.
- No score, stale/mismatched artifact, non-LIVE artifact/result in judged mode, replay in judged mode, or unapproved/missing owner-approved provider profile blocks approval.
- One approved catalogue plan per run covering 1–6 catalogue-bound zones, no physical dispatch before operator confirmation, and a coordinator-persisted perception result after each capture. Task creation, ranking, patroller selection, assignment and DB-state review are retained compatibility contracts deferred to V0.1.
- Provider approval/review is a named joint approval record shared Colin + Dzivha over the versioned demo test-image-set hash; WP3 execution is Dzivha-only; there is no release-benchmark ceremony.

## 7. Simulated acceptance flow: active perception path

The fixture path persists the active events `RUN_CREATED`, `AGENT_STARTED`, `PLAN_PROPOSED`/`PLAN_CREATED`/`PLAN_APPROVED`/`PLAN_REJECTED`, `PREFLIGHT_REPORTED`, `DRONE_DISPATCH_REQUESTED`, `DRONE_COMMAND_COMPLETED`, `OBSERVATION_CAPTURED`, and `DETECTION_RECORDED`. Existing `MISSION_*`, `TASK_*`, `PATROLLER_*`, assignment events and `ZONE_RESULT_REVIEWED` remain retained compatibility fixtures; they are not part of this V0 flow.

Tests must restart the coordinator after every transition, replay by sequence, assert legal next actions, assert duplicate command behavior and exercise `IN_DOUBT`/unknown reconciliation.

## 8. Physical dependency

WP1 uses a fake worker initially. The real worker belongs to the later detailed WP4 slice but is a V0 release prerequisite and must implement the same `WorkerPort`/operation/evidence contracts. WP1 must not silently call the existing harness directly.

## 9. Acceptance criteria

- `uv run python -m pytest tests/coordinator tests/agent -q` passes after implementation.
- V0 agent-tool tests prove that the model may choose only `read_state`, `read_mountain` and bounded `read_events`; foreign run IDs, invalid cursors, over-limit reads, expired deadlines and stale final proposals fail closed, and no tool call can submit a command or invoke worker/perception code.
- Every active transition-matrix row has an explicit test, including catalogue-plan resolution, immutable approval-before-dispatch, coordinator-owned perception results, preflight failure, and operator-only `FORCE_RUN_CREATE`; retained deferred branches remain covered by compatibility tests where already present.
- Deterministic fake provenance is `SIMULATED`, never "fake real-mode"; a real-mode test must use a live-capable adapter or be rejected from the judged path.
- Configuration tests prove each exact `LLM_CONFIG_MISSING`, `LLM_CONFIG_INVALID`, `LLM_DEPENDENCY_MISSING`, and `LLM_CREDENTIAL_MISSING` failure, and a fake-adapter test proves no optional dependency or credential is needed.
- Alan’s fake-agent runtime and UI-client tests cover provider/parser outcomes without credentials; Colin’s coordinator tests cover persistence. Restart, key conflict, stale operator action, provider error, scoreless result, rejection, emergency and `IN_DOUBT` are tested without hardware/network.
- No coordinator test imports NiceGUI or raw provider/SDK implementation.