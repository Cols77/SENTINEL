# SENTINEL WP1 — Coordinator and Agentic Loop

> **Status:** Revised after independent review; re-review pending
> **Owners:** Colin — coordinator semantics/repository; Alan — LLM runtime adapter
> **Prerequisite:** WP0 `sentinel.v0` contracts and fixtures

## 1. Objective

Implement the coordinator and real-LLM adapter against WP0. The deterministic fake agent/worker/perception adapters are mandatory before any provider or drone integration.

## 2. Ownership and prohibited access

**Colin owns:** SQLite repository/migrations, event order, command gateway, state transitions, idempotency, operation reconciliation, threshold/ranking policy, worker/perception ingress and coordinator read models.

**Alan owns:** `AgentModelPort`, provider configuration, prompt/tool-policy revisions, structured response parser, bounded correction behavior and agent-facing status summaries.

Alan’s adapter cannot access SQLite, drone sockets, perception providers, policy arithmetic, approval transitions or UI storage. WP2 alone owns rendering. Colin’s coordinator must not parse model-native provider responses.

## 3. Ports

```text
AgentModelPort.propose(https://sentinel.local/contracts/sentinel-v0-wire.schema.json#AgentRequest) -> #AgentModelResult
WorkerPort.dispatch(https://sentinel.local/contracts/sentinel-v0-wire.schema.json#WorkerDispatch) -> #ResultEnvelope
WorkerPort.reconcile(operation_id, idempotency_key) -> #ReconciliationResult
WorkerPort.read_video_status(run_id) -> #VideoStatus
WorkerPort.open_live_video(run_id) -> #SafeVideoStreamHandle
PerceptionPort.observe(https://sentinel.local/contracts/sentinel-v0-wire.schema.json#PerceptionRequest) -> #PerceptionResult
CoordinatorPort: all WP0 command/read methods
```

`AgentRequest` contains run/state summary, allowed intent schema revision, compact event/evidence references and deadline. `AgentModelResult` is exactly one of `INTENT`, `NO_ACTION`, `MALFORMED`, `UNAVAILABLE`; it never contains raw SDK calls. Required V0 intents are `INSPECT_SECTION`, `REQUEST_OPERATOR_APPROVAL`, `NO_ACTION_REQUIRED`.

Runtime configuration validates against [`contracts/llm-adapter-config-v1.schema.json`](contracts/llm-adapter-config-v1.schema.json). It is built from `SENTINEL_LLM_PROVIDER`, `SENTINEL_LLM_MODEL`, `SENTINEL_LLM_TIMEOUT_MS`, `SENTINEL_LLM_PROMPT_REVISION`, `SENTINEL_LLM_TOOL_POLICY_REVISION`, and optional `SENTINEL_LLM_API_KEY_ENV`; artifacts contain only the environment-variable name, never a secret. Missing required input fails before provider construction with `LLM_CONFIG_MISSING` naming the missing variable. Non-integer/out-of-range timeout, invalid schema, blank required values, or an invalid API-key environment variable name fails with `LLM_CONFIG_INVALID`; no fallback is attempted. Missing the named optional dependency at real-adapter construction fails `LLM_DEPENDENCY_MISSING`; absent referenced credential at invocation fails `LLM_CREDENTIAL_MISSING`. The optional dependency name is `llm` (installation as an optional `llm` extra is WP1 implementation work; this spec does not modify `pyproject.toml`). Tests use the deterministic fake adapter and require no credential.

## 4. Run transition matrix

| From | Event/guard | To | Result |
|---|---|---|---|
| `NEW` | `RUN_CREATED` | `RUNNING` | agent may plan |
| `RUNNING` | valid inspection intent + authorised envelope | `WAITING_FOR_OBSERVATION` | durable `PENDING` operation then `DRONE_DISPATCH_REQUESTED` |
| `WAITING_FOR_OBSERVATION` | `OBSERVATION_CAPTURED` | `RUNNING` | perception request is eligible |
| `RUNNING` | qualifying `DETECTION_RECORDED` under frozen provider profile | `WAITING_FOR_APPROVAL` | task/ranking/proposal persisted |
| `RUNNING` | below-threshold or `UNSCORED` detection | `NEEDS_REVIEW` | no task assignment |
| `RUNNING` or `WAITING_FOR_OBSERVATION` | provider/worker failure with no safe evidence | `FAILED` or `NEEDS_REVIEW` by policy | no task assignment |
| `WAITING_FOR_APPROVAL` | versioned Confirm | `COMPLETED` | one simulated patroller becomes assigned |
| `WAITING_FOR_APPROVAL` | versioned Reject | `REJECTED` | no assignment |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILED_SUCCEEDED` | prior safe waiting state | result envelope correlates `operation_id`; no redispatch |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILED_FAILED` | `FAILED` or `NEEDS_REVIEW` by evidence policy | result envelope correlates `operation_id` |
| nonterminal with `IN_DOUBT` operation | `OPERATION_RECONCILIATION_UNKNOWN` | `FAILED` | fault persisted; motion blocked |
| any nonterminal | `EMERGENCY_LATCHED` | `EMERGENCY` | ordinary commands disabled |

Terminal states are `NEEDS_REVIEW`, `COMPLETED`, `REJECTED`, `FAILED`, `EMERGENCY`. `APPROVED` is not a run state.

Agent phases are `RECEIVE_REQUEST`, `PLAN`, `REQUEST_MISSION`, `WAIT_FOR_OBSERVATION`, `INTERPRET_EVIDENCE`, `WAIT_FOR_APPROVAL`, `FINALIZE`. The replay cursor is the last committed `sequence_no`.

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
- No score, stale/mismatched artifact, non-LIVE artifact/result in judged mode, replay in judged mode, unapproved/missing provider profile, or missing reproducible benchmark evidence blocks approval.
- One V0 section per run, no assignment before operator confirmation, deterministic simulated patroller ranking.

## 7. Simulated acceptance flow

The fixture path persists parent-compatible events including `USER_MESSAGE_RECEIVED`, `AGENT_STARTED`, `MISSION_CREATED`, `MISSION_AUTHORISED`, `DRONE_DISPATCH_REQUESTED`, `DRONE_COMMAND_COMPLETED`, `OBSERVATION_CAPTURED`, `DETECTION_RECORDED`, `TASK_CREATED`, `PATROLLER_ASSIGNMENT_PROPOSED` and either confirmation/rejection event.

Tests must restart the coordinator after every transition, replay by sequence, assert legal next actions, assert duplicate command behavior and exercise `IN_DOUBT`/unknown reconciliation.

## 8. Physical dependency

WP1 uses a fake worker initially. The real worker belongs to the later detailed WP4 slice but is a V0 release prerequisite and must implement the same `WorkerPort`/operation/evidence contracts. WP1 must not silently call the existing harness directly.

## 9. Acceptance criteria

- `uv run python -m pytest tests/coordinator tests/agent -q` passes after implementation.
- Every transition-matrix row has an explicit test.
- Deterministic fake provenance is `SIMULATED`, never "fake real-mode"; a real-mode test must use a live-capable adapter or be rejected from the judged path.
- Configuration tests prove each exact `LLM_CONFIG_MISSING`, `LLM_CONFIG_INVALID`, `LLM_DEPENDENCY_MISSING`, and `LLM_CREDENTIAL_MISSING` failure, and a fake-adapter test proves no optional dependency or credential is needed.
- Restart, key conflict, stale operator action, provider error, scoreless result, rejection, emergency and `IN_DOUBT` are tested without hardware/network.
- No coordinator test imports NiceGUI or raw provider/SDK implementation.
