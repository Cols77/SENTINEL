# SENTINEL WP1 — Coordinator and Agentic Loop

> **Status:** Revised after independent review; re-review pending
> **Owners:** Colin — coordinator semantics/repository; Alan — LLM runtime adapter
> **Prerequisite:** WP0 `sentinel.v0` contracts and fixtures

## 1. Objective

Implement the coordinator and real-LLM adapter against WP0. The deterministic fake agent/worker/perception adapters are mandatory before any provider or drone integration. The V0 agent flow is catalogue-plan proposal, immutable operator approval, then coordinator dispatch; an agent never dispatches a plan.

## 2. Ownership and prohibited access

**Colin owns:** SQLite repository/migrations, event order, command gateway, state transitions, idempotency, operation reconciliation, threshold/ranking policy, worker/perception ingress and coordinator read models.

**Alan owns:** `AgentModelPort`, provider configuration, prompt/tool-policy revisions, structured response parser, bounded proposal/revisit behavior and agent-facing status summaries, plus the fake-agent runtime and UI-client tests. Colin owns coordinator persistence and transition/recovery tests; Alan does not own or implement that persistence.

Alan’s adapter cannot access SQLite, drone sockets, perception providers, policy arithmetic, approval transitions or UI storage. It receives worker preflight facts read-only and may assess their structured pass/fail values only; it has no hardware access and cannot physically correct a route or issue a correction command. WP2 alone owns rendering. Colin’s coordinator must not parse model-native provider responses.

## 3. Ports

```text
AgentModelPort.propose(https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentRequest) -> #AgentModelResult
WorkerPort.dispatch(https://sentinel.local/contracts/sentinel-v0-wire.schema.json#WorkerDispatch) -> #ResultEnvelope
WorkerPort.reconcile(operation_id, idempotency_key) -> #ReconciliationResult
WorkerPort.read_video_status(run_id) -> #VideoStatus
WorkerPort.open_live_video(run_id) -> #SafeVideoStreamHandle
PerceptionPort.observe(https://sentinel.local/contracts/sentinel-v0-wire.schema.json#PerceptionRequest) -> #PerceptionResult
CoordinatorPort: all WP0 command/read methods
CoordinatorPort.read_plan(plan_id) -> #PlanReadModel
CoordinatorPort.read_preflight(run_id) -> #PreflightReport
```

### 3.1 Module layout and fake adapters

Colin’s coordinator lives in `sentinel/coordinator/` (repository, transitions, idempotency, command gateway, read models); Alan’s runtime lives in `sentinel/adapter/llm/` (`AgentModelPort`, provider config, structured parser). The deterministic fake agent, fake worker and fake perception providers are mandatory before any provider or drone integration and import only `sentinel.contracts` and fixtures. Every fake adapter emits `source_kind: SIMULATED`; there is no "fake real-mode". A real-mode test must use a live-capable adapter or be rejected from the judged path.

WP0 supplies the closed, directly-validatable `AgentRequest` and `AgentModelResult` schemas, their discriminators/enums, validators and fixtures. `AgentRequest` supplies the run/state summary, allowed intent-schema revision, compact event/evidence references and deadline that this adapter needs; preflight is supplied as a typed, immutable `PreflightReport` with its WP0-defined check list, read-only facts only. A successful `INTENT` proposes exactly one `catalogue_plan_id`; the coordinator resolves its catalogue revision/hash and creates the immutable plan. `NO_ACTION`, `MALFORMED` and `UNAVAILABLE` carry no plan proposal. No model result contains raw SDK calls or a dispatch instruction. WP1 must consume these contracts rather than add adapter-local wire fields.

Runtime configuration validates against [`contracts/llm-adapter-config-v1.schema.json`](contracts/llm-adapter-config-v1.schema.json). It is built from `SENTINEL_LLM_PROVIDER`, `SENTINEL_LLM_MODEL`, `SENTINEL_LLM_TIMEOUT_MS`, `SENTINEL_LLM_PROMPT_REVISION`, `SENTINEL_LLM_TOOL_POLICY_REVISION`, and optional `SENTINEL_LLM_API_KEY_ENV`; artifacts contain only the environment-variable name, never a secret. Missing required input fails before provider construction with `LLM_CONFIG_MISSING` naming the missing variable. Non-integer/out-of-range timeout, invalid schema, blank required values, or an invalid API-key environment variable name fails with `LLM_CONFIG_INVALID`; no fallback is attempted. Missing the named optional dependency at real-adapter construction fails `LLM_DEPENDENCY_MISSING`; absent referenced credential at invocation fails `LLM_CREDENTIAL_MISSING`. `LLM_CONFIG_MISSING`, `LLM_CONFIG_INVALID`, `LLM_DEPENDENCY_MISSING` and `LLM_CREDENTIAL_MISSING` are adapter-local codes, not shared coordinator registry codes. The optional dependency name is `llm` (installation as an optional `llm` extra is WP1 implementation work; this spec does not modify `pyproject.toml`). Tests use the deterministic fake adapter and require no credential.

## 4. Run transition matrix

The run surveys an approved catalogue plan over the fixed six-zone catalogue (1–6 stations). Each `RUNNING` survey step observes and can propose exactly one station task; the run does not end until every approved station is captured and approved. `WAITING_FOR_APPROVAL` is disambiguated by an explicit approval target: plan approval necessarily precedes dispatch, task approval precedes a station assignment. The wire `run_status` enum has the single value `WAITING_FOR_APPROVAL`; the target (plan vs task) rides in `StateSnapshot.legal_operator_actions` and the pending proposal referenced by the command, and is shown below as `(plan)` / `(task)`. `OPERATOR_DECIDE` is a typed target (`PLAN` or `TASK`); the target must match the pending approval.

| From | Event/guard | To | Result |
|---|---|---|---|
| `NEW` | `RUN_CREATED` | `RUNNING` | agent may plan |
| `RUNNING` | `AGENT_INTENT_PROPOSE` with `INTENT` and one valid `catalogue_plan_id` | `WAITING_FOR_APPROVAL` (plan) | `PLAN_PROPOSED`; coordinator resolves catalogue revision/hash, persists the immutable plan (`PLAN_CREATED`); no dispatch |
| `WAITING_FOR_APPROVAL` (plan) | `OPERATOR_DECIDE` target `PLAN`, `CONFIRM` | `WAITING_FOR_OBSERVATION` | `PLAN_APPROVED`; durable `PENDING` operation then coordinator `DRONE_DISPATCH_REQUESTED` |
| `WAITING_FOR_APPROVAL` (plan) | `OPERATOR_DECIDE` target `PLAN`, `REJECT` | `RUNNING` | `PLAN_REJECTED`; no dispatch; the run remains refinement-eligible and a refined plan is re-proposed |
| `WAITING_FOR_OBSERVATION` | `OBSERVATION_CAPTURED` | `RUNNING` | perception request is eligible |
| `RUNNING` | qualifying `DETECTION_RECORDED` under frozen provider profile | `WAITING_FOR_APPROVAL` (task) | `TASK_CREATED`; task/ranking/proposal persisted; the run continues — it does not end |
| `RUNNING` or `WAITING_FOR_OBSERVATION` | perception/`DETECTION_RECORDED` with `status == PROVIDER_ERROR` (provider failure/error or worker/video fault) | `FAILED` | `PROVIDER_ERROR` precedes any score interpretation; no task assignment or physical retry |
| `RUNNING` or `WAITING_FOR_OBSERVATION` | perception/`DETECTION_RECORDED` with `status` `UNSCORED`/`NOT_DETECTED` (scoreless or below-threshold) | `NEEDS_REVIEW` | scoreless review-hold; disjoint from `PROVIDER_ERROR` by `status`; no task assignment |
| `WAITING_FOR_APPROVAL` (task) | `OPERATOR_DECIDE` target `TASK`, `CONFIRM` | `RUNNING` | `PATROLLER_ASSIGNMENT_CONFIRMED`; station approved; the next plan step proceeds (`OBSERVATION_CAPTURED`), looping until the last plan step |
| `WAITING_FOR_APPROVAL` (task) | `OPERATOR_DECIDE` target `TASK`, `REJECT` | `RUNNING` | task rejected; it is refined and re-proposed; not terminal |
| `WAITING_FOR_APPROVAL` (task), last plan step | `OPERATOR_DECIDE` target `TASK`, `CONFIRM` | `COMPLETED` | `COMPLETED` only after all approved stations on the approved plan are captured and approved |
| `NEEDS_REVIEW` | `OPERATOR_DECIDE` target `PLAN`, `CONFIRM` of an explicitly approved new/revisit plan | `WAITING_FOR_OBSERVATION` | review-hold is escaped only this way; the revisit re-dispatches a new approved plan/run |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILED_SUCCEEDED` | prior safe waiting state | result envelope correlates `operation_id`; no redispatch |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILED_FAILED` | `FAILED` or `NEEDS_REVIEW` by evidence policy | result envelope correlates `operation_id` |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILIATION_UNKNOWN` | `FAILED` | fault persisted; motion blocked |
| any nonterminal | `EMERGENCY_LATCHED` | `EMERGENCY` | ordinary commands disabled |

### 4.1 Outcomes, rejection and review-hold

The only V0 terminal run outcomes are `COMPLETED`, `FAILED` and `EMERGENCY`; `APPROVED`/`REJECTED` are not terminal run states. `REJECTED` is event-only (`PLAN_REJECTED`, `OPERATOR_ASSIGNMENT_REJECTED`): a rejected plan or task is refined and re-proposed, keeping the run refinement-eligible. `NEEDS_REVIEW` is a review-hold state, not terminal; it is escaped only by an explicitly approved new/revisit plan (or driven to `FAILED` by a later `PROVIDER_ERROR` or emergency). An active emergency latch cannot be overridden.

`PROVIDER_ERROR` and scoreless are total-and-disjoint, keyed on `PerceptionResult.status`: `PROVIDER_ERROR` always maps the run to `FAILED` regardless of any score; `UNSCORED`/`NOT_DETECTED` (score null or below-threshold) always map to `NEEDS_REVIEW`. A result cannot be both `PROVIDER_ERROR` and scoreless — the status enum is disjoint — so the precedence is explicit and total, with no ambiguity.

### 4.2 Worker preflight

The worker performs preflight and emits the immutable run/plan-bound `PreflightReport` as the `PREFLIGHT_REPORTED` event. The report has exactly eight named checks per WP0 §3.3.1 (`WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH`); `passed` is the conjunction of `check.passed`. The agent receives only its persisted structured facts and never performs the checks itself or accesses the drone, network, video, telemetry or emergency latch. A failed preflight makes the run `FAILED`; only the operator-only `FORCE_RUN_CREATE` command may override it, creating a new run/plan that records the failed report and operator reason (`new_plan_reference`, `real_confirm: true`) and never resumes or re-dispatches the prior operation. `FORCE_RUN_CREATE` never overrides an active emergency latch.

### 4.3 Plan identity and canonical serialization

`plan_sha256` is the SHA-256 of the canonical serialization of the plan: the immutable `SurveyPlanReference` fields (`plan_id`, `catalogue_id`, `catalogue_revision`, ordered `station_ids`) serialized in schema field order with deterministic formatting and no whitespace variance. Equality of `plan_sha256` is semantic: two plan documents are the same plan iff their canonical serializations hash equally, so the coordinator (dispatching), the adapter (referencing) and the worker (executing) agree on plan identity. The coordinator resolves `catalogue_id`/`catalogue_revision` against the approved `survey-catalogue-v1` catalogue and persists the immutable plan before any dispatch.

Agent phases are `RECEIVE_REQUEST`, `PLAN`, `REQUEST_MISSION`, `WAIT_FOR_OBSERVATION`, `INTERPRET_EVIDENCE`, `WAIT_FOR_APPROVAL`, `FINALIZE`; `WAIT_FOR_APPROVAL` covers both plan and task approval targets. The replay cursor is the last committed `sequence_no`.

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
- One approved catalogue plan per run covering 1–6 catalogue-bound zones, no assignment before operator confirmation, deterministic simulated patroller ranking. Both `ApprovalReadModel.ranking` and `HazardReadModel.ranking` are ordered non-empty arrays (`minItems = 1`, `maxItems = 64`), so an empty ranking is never emitted.
- Provider approval/review is a named joint approval record shared Colin + Dzivha over the versioned demo test-image-set hash; WP3 execution is Dzivha-only; there is no release-benchmark ceremony.

## 7. Simulated acceptance flow

The fixture path persists parent-compatible events including `USER_MESSAGE_RECEIVED`, `AGENT_STARTED`, `MISSION_CREATED`, `MISSION_AUTHORISED`, `DRONE_DISPATCH_REQUESTED`, `DRONE_COMMAND_COMPLETED`, `OBSERVATION_CAPTURED`, `DETECTION_RECORDED`, `TASK_CREATED`, `PATROLLER_ASSIGNMENT_PROPOSED`, the plan lifecycle `PLAN_PROPOSED`/`PLAN_CREATED`/`PLAN_APPROVED`/`PLAN_REJECTED`, `PREFLIGHT_REPORTED`, and `PATROLLER_ASSIGNMENT_CONFIRMED`/`OPERATOR_ASSIGNMENT_REJECTED`.

Tests must restart the coordinator after every transition, replay by sequence, assert legal next actions, assert duplicate command behavior and exercise `IN_DOUBT`/unknown reconciliation.

## 8. Physical dependency

WP1 uses a fake worker initially. The real worker belongs to the later detailed WP4 slice but is a V0 release prerequisite and must implement the same `WorkerPort`/operation/evidence contracts. WP1 must not silently call the existing harness directly.

## 9. Acceptance criteria

- `uv run python -m pytest tests/coordinator tests/agent -q` passes after implementation.
- Every transition-matrix row has an explicit test, including catalogue-plan resolution, immutable approval-before-dispatch, typed preflight failure, and operator-only `FORCE_RUN_CREATE`.
- Deterministic fake provenance is `SIMULATED`, never "fake real-mode"; a real-mode test must use a live-capable adapter or be rejected from the judged path.
- Configuration tests prove each exact `LLM_CONFIG_MISSING`, `LLM_CONFIG_INVALID`, `LLM_DEPENDENCY_MISSING`, and `LLM_CREDENTIAL_MISSING` failure, and a fake-adapter test proves no optional dependency or credential is needed.
- Alan’s fake-agent runtime and UI-client tests cover provider/parser outcomes without credentials; Colin’s coordinator tests cover persistence. Restart, key conflict, stale operator action, provider error, scoreless result, rejection, emergency and `IN_DOUBT` are tested without hardware/network.
- No coordinator test imports NiceGUI or raw provider/SDK implementation.