# SENTINEL Agentic Loop and Coordinator Component Specification

**Document version:** 0.1
**Status:** Supporting draft; authoritative V0 details are in [`docs/superpowers/specs/2026-09-16-sentinel-wp1-coordinator-agentic-loop.md`](superpowers/specs/2026-09-16-sentinel-wp1-coordinator-agentic-loop.md) and the V0 coherence spec.
**Component owner:** Agentic-loop engineer, with PM/architect approval
**Parent specification:** [`SENTINEL_Demo_State_Database_Spec_v0.2.md`](SENTINEL_Demo_State_Database_Spec_v0.2.md)
**Scope:** The coordinator, one orchestrator agent run, typed tools, event-driven resume, and the operator approval boundary for V0

This document is retained as supporting design history. It does not define the current executable V0 contract. Agents must use the linked WP1 and coherence specifications as authority; the database specification remains the parent persistence reference.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Component objective

The coordinator turns user intent into a bounded, auditable sequence of typed actions:

```text
user message
    -> coordinator creates run-scoped mission and agent state
    -> agent reads a compact state summary
    -> agent calls inspection.dispatch(...)
    -> coordinator yields; it does not poll
    -> worker/perception emit committed events
    -> the same agent run resumes from its event cursor
    -> deterministic target/task/ranking logic supplies evidence
    -> agent calls assignment.propose(...)
    -> operator calls assignment.confirm(...)
    -> agent resumes and reports the final state
```

The component is successful when the V0 sequence can be replayed from `events`, resumed after a process restart, and explained to an operator without exposing hidden chain-of-thought or allowing the model to bypass safety and approval boundaries.

## 2. V0 responsibilities and non-responsibilities

### 2.1 The coordinator owns

- run creation, lifecycle and clean run isolation;
- durable agent state and the resume cursor;
- the only writes to SQLite;
- validation and dispatch of typed tool calls;
- idempotency-key enforcement;
- atomic state/event writes;
- event subscription and agent wake-up;
- conversion of worker/perception results into the database schema;
- deterministic target consolidation, threshold policy and patroller ranking;
- operator confirmation and rejection actions;
- correlation, causation and provenance fields on all records;
- a compact operator timeline derived from events.

### 2.2 The orchestrator agent owns

- interpreting the user's text into a small structured goal;
- choosing the next allowed tool call based on the state summary and recent events;
- explaining the current decision in operator-facing text;
- deciding when the evidence is sufficient to continue, request review, or stop;
- proposing the highest-ranked eligible patroller unless an explicit operational reason is recorded.

The model does **not** own facts, thresholds, ranking arithmetic, mission-envelope validation, approval state, or physical commands.

### 2.3 Other component boundaries

| Component | May do | Must not do |
|---|---|---|
| Drone worker | Execute an already-authorised named choreography; publish telemetry, frame and command events | Accept arbitrary model-generated SDK commands or write SQLite |
| Perception adapter | Send selected evidence frames to a pinned provider; return normalized results and raw provenance | Mutate detections/targets directly or invent scores |
| State repository | Validate and persist coordinator-owned records and append events atomically | Accept direct writes from the agent, worker or UI |
| UI/operator API | Display state and issue operator-only confirmation/rejection/emergency actions | Turn simulated state into live state or confirm on behalf of the agent without an operator action |
| Agent runtime | Call the typed tool gateway and wait for events | Open SQLite, call a provider, access drone sockets, or poll flight state |

### 2.4 Explicit non-goals

- multi-agent runtime;
- general-purpose autonomous planning;
- free-form SQL or arbitrary plugin execution;
- model-generated flight paths;
- automatic external patroller notification;
- private chain-of-thought persistence;
- continuous model polling while a mission is active;
- making a VLM confidence value comparable to a detector score;
- silently switching to replay mode.

## 3. Logical architecture

```text
+------------------+       +----------------------+
| UI / operator    | ----> | Coordinator API      |
+------------------+       |                      |
                           |  +----------------+  |
                           |  | Agent runtime  |  |
                           |  +--------+-------+  |
                           |           | typed RPCs
                           |  +--------v-------+  |
                           |  | Tool gateway  |  |
                           |  +---+--------+---+  |
                           |      |        |      |
                           |  state  inspection  task/assignment
                           |      |        |      |
                           |  +----v--------v----+ |
                           |  | Coordinator DB  | |
                           |  | + event stream  | |
                           |  +----+--------+----+ |
                           +-------|--------|------+
                                   |        |
                              worker events  perception events
```

The transport can initially be an in-process Python interface or a local HTTP/event adapter. The logical contracts below are stable regardless of transport. Every request carries a run context and an idempotency key; every accepted operation produces an auditable `tool_calls` record.

## 4. Common request and event envelope

All coordinator-internal RPCs and emitted events use the following envelope fields:

```json
{
  "schema_version": 1,
  "event_id": "uuid-or-null",
  "event_type": "EVENT_NAME-or-null",
  "run_id": "uuid",
  "entity_type": "entity-kind-or-null",
  "entity_id": "uuid-or-null",
  "correlation_id": "uuid",
  "causation_event_id": "uuid-or-null",
  "idempotency_key": "stable-operation-key",
  "actor_type": "AGENT|WORKER|PERCEPTION|OPERATOR|SYSTEM",
  "actor_id": "stable-actor-id",
  "provenance_kind": "LIVE|SIMULATED|REPLAYED|OPERATOR_PROVIDED|INFERRED",
  "occurred_at": "UTC ISO-8601 timestamp",
  "recorded_at": "UTC ISO-8601 timestamp"
}
```

`event_id`, `event_type`, `entity_type`, `entity_id` and `provenance_kind` are mandatory for events and are null only in a request envelope. `sequence_no` is mandatory for a persisted event and is assigned by the coordinator, not by an external producer.

Rules:

1. `run_id` is mandatory for all V0 work. A request without a run context is rejected.
2. `occurred_at` is the source-side time; `recorded_at` is assigned by the coordinator.
3. `causation_event_id` points to the event that caused the operation when one exists.
4. `correlation_id` stays constant for one user request/mission chain.
5. An idempotency key is scoped to the operation name and run. Reusing a key with a different payload is a conflict, not a new operation.
6. Payloads are JSON-serializable and versioned. Unknown required fields or an unsupported schema version fail closed.

## 5. Coordinator persistence, concurrency and replay contract

### 5.1 Coordinator-only writes

The coordinator is the only component with SQLite write access. The agent runtime, UI, drone worker and perception adapter use coordinator commands or authenticated event ingress. They do not receive a database handle and cannot bypass the tool gateway by importing repository internals.

The database connection MUST enable foreign keys and WAL mode for every coordinator process. The schema MUST enforce, at minimum:

- foreign keys from missions, observations, detections, targets, agent state, tasks, tool calls and events to their parent run/entity;
- controlled enum values at the application/schema boundary;
- unique `(run_id, sequence_no)` for events;
- immutable event rows after commit;
- a scoped unique idempotency key for every side-effecting operation;
- optimistic `state_version` checks on mutable drone/task/approval records; and
- one active agent lease per `agent_run_id` and one active physical mission lease per drone.

### 5.2 Atomic mutation and event transaction

Every coordinator-only state mutation follows one transaction. A command that invokes an external physical or provider operation uses an intent transaction before the call and a completion transaction after the result is known:

```text
BEGIN IMMEDIATE
  validate run, authorization, foreign keys and expected versions
  look up scoped idempotency key
  if duplicate: return the stored result and result_event_id
  persist the pending tool call or command intent
COMMIT

perform the external operation exactly once using the durable idempotency key

BEGIN IMMEDIATE
  re-read the pending operation and expected entity versions
  apply the factual/agent/derived state mutation
  allocate the next per-run sequence number
  append the immutable result event
  complete the tool call with result_event_id
COMMIT
```

External work is not falsely reported as complete inside the intent transaction. A crash between the two commits leaves an `IN_DOUBT`/pending operation that must be reconciled using the same idempotency key; it must not be blindly re-executed. Pure local mutations such as an operator confirmation may use one transaction because no external side effect is involved.

An accepted event is visible to the event dispatcher only after commit. A failed transaction emits no state mutation and no success event. Event delivery is at-least-once: consumers deduplicate by event ID and operation idempotency key.

### 5.3 Canonical resume cursor and leases

`(run_id, sequence_no)` is the canonical resume cursor because it is the ordering used by `state.get_recent_events`. Store `last_processed_sequence` on `agent_state` (or in a versioned equivalent) and retain `last_processed_event_id` as a denormalized audit reference. The two values must be updated together.

An agent resume acquires a short-lived lease for its `agent_run_id`. A second resume while the lease is valid is rejected or coalesced. Lease expiry does not authorize replaying side effects: the new owner first loads the checkpoint, reconciles pending tool calls, and resumes strictly after the committed sequence.

Checkpoint advancement, `AGENT_RESUMED`, and the next waiting/terminal state are committed atomically. A duplicate event delivery or duplicate wake-up therefore cannot advance the cursor twice or invoke a second side-effecting tool call.

### 5.4 UI and operator command boundary

The UI is a read-only projection plus coordinator-mediated operator commands. It may call read APIs equivalent to `state.get_summary` and `state.get_recent_events`, and it may submit `assignment.confirm`, `assignment.reject` and the independent emergency action. It MUST NOT write SQLite, call the drone SDK, change mission envelopes, or treat a UI display update as an accepted state transition.

Operator commands include operator identity, action ID, expected entity version and the exact proposed candidate/envelope where applicable. The coordinator validates these values and records the command and resulting event. The UI must display `SIMULATED`, `REPLAYED`, `INFERRED` and stale values rather than flattening them into live status.

### 5.5 Event additions required by this component

The parent event list remains authoritative. To make the failure and approval paths reconstructable, add these versioned event types to the coordinator event catalog:

- `MISSION_ENVELOPE_ACTIVATED`
- `TOOL_FAILED`
- `DETECTION_FAILED`
- `DRONE_HEARTBEAT_EXPIRED`
- `PATROLLER_ASSIGNMENT_REJECTED`
- `APPROVAL_EXPIRED` when approval expiry is enabled.

If a separate event type is not added for a deployment, the same information MUST be carried in a typed `SYSTEM_FAULT_RECORDED` payload with a stable `failure_code`; a generic free-form error string is insufficient for replay.

## 6. Coordinator lifecycle

### 6.1 Run lifecycle

A V0 run follows:

```text
READY -> RUNNING -> COMPLETED
                 |-> ABORTED
                 |-> FAILED
```

Only the coordinator can transition a run. A new user request starts with a clean run-scoped agent state. A failed or aborted run is never reused for a new physical mission.

### 6.2 Agent lifecycle

The `agent_state` row uses the following states and phases:

| Status | Phase | Meaning |
|---|---|---|
| `RUNNING` | `UNDERSTAND` | Parse the user request and validate that a supported section/goal was identified |
| `RUNNING` | `DISPATCH` | Read current state and issue one bounded inspection dispatch |
| `WAITING_FOR_EVENT` | `WAIT_FOR_DETECTION` | Mission is executing or perception is pending; no model polling is allowed |
| `RUNNING` | `EVALUATE` | Read the new evidence and target state |
| `RUNNING` | `ASSIGN` | Create a task, read deterministic ranking and propose an assignment |
| `WAITING_FOR_EVENT` | `WAIT_FOR_APPROVAL` | Wait for `PATROLLER_ASSIGNMENT_CONFIRMED` or `PATROLLER_ASSIGNMENT_REJECTED` |
| `COMPLETED` | `DONE` | Final result is recorded and reported |
| `FAILED` | any | An unrecoverable error or safety stop is recorded |

The agent may not skip from `UNDERSTAND` to `ASSIGN`, and it may not call `assignment.confirm`; that action is operator-only.

### 6.3 Normal V0 transition table

| Current phase | Trigger | Coordinator action | Next state |
|---|---|---|---|
| `UNDERSTAND` | `USER_MESSAGE_RECEIVED` | Parse goal, create mission and agent row | `DISPATCH` |
| `DISPATCH` | valid tool call | Persist `MISSION_CREATED`, `MISSION_AUTHORISED` as applicable, and `DRONE_DISPATCH_REQUESTED` | `WAIT_FOR_DETECTION` |
| `WAIT_FOR_DETECTION` | `DETECTION_RECORDED` | Advance cursor; load detection and target evidence | `EVALUATE` |
| `EVALUATE` | target policy result | Create/update target; either create task or record review/stop decision | `ASSIGN` or `DONE` |
| `ASSIGN` | `PATROLLER_ASSIGNMENT_PROPOSED` | Persist pending approval and yield | `WAITING_FOR_EVENT` / `WAIT_FOR_APPROVAL` |
| `WAIT_FOR_APPROVAL` | operator confirmation/rejection event | Apply the already-authorized operator transition; resume agent | `DONE` |
| any non-terminal state | timeout, worker fault or policy rejection | Record a legible fault and stop further automatic physical action | `FAILED` or `DONE` according to policy |

`MISSION_COMPLETED` may arrive before or after `DETECTION_RECORDED`. The agent resumes only when the required evidence and mission outcome are available; it must not treat mission completion alone as a detection.

## 7. Typed tool gateway

The gateway is the only callable interface exposed to the model. Each operation validates its request, writes a `tool_calls` row, enforces authorization and idempotency, then returns a structured result. Tool failures are data returned to the agent, not uncaught exceptions that cause an uncontrolled retry.

### 7.1 Required tool contracts

#### `state.get_summary`

Request:

```json
{
  "run_id": "uuid",
  "include": ["mission", "drone", "section", "agent", "pending_approval"]
}
```

Response contains only current operational facts and derived state:

```json
{
  "run_id": "uuid",
  "active_mission": {"mission_id": "uuid", "section_id": "NORTH_TRAIL", "status": "OBSERVING"},
  "drone": {"drone_id": "tello-1", "connection_status": "BUSY", "battery_percent": 84},
  "agent": {"phase": "WAIT_FOR_DETECTION", "last_processed_event_id": "uuid"},
  "pending_approval": null,
  "provenance_summary": {"patrollers": "SIMULATED"}
}
```

The response must label stale, simulated, replayed and inferred values. It must not include raw database connections or hidden prompts.

#### `state.get_recent_events`

Request:

```json
{"run_id": "uuid", "after_sequence": 12, "limit": 50}
```

Response:

```json
{
  "events": [
    {"event_id": "uuid", "sequence_no": 13, "event_type": "DETECTION_RECORDED", "entity_type": "detection", "entity_id": "uuid", "payload": {}, "provenance_kind": "LIVE"}
  ],
  "next_after_sequence": 13,
  "has_more": false
}
```

The sequence is the authoritative replay order. Event pages are bounded and deterministic.

#### `inspection.dispatch`

Request:

```json
{
  "run_id": "uuid",
  "section_id": "NORTH_TRAIL",
  "mission_envelope_id": "envelope-v0-north-trail",
  "idempotency_key": "run-uuid:dispatch:NORTH_TRAIL"
}
```

Response:

```json
{
  "mission_id": "uuid",
  "status": "DISPATCHED",
  "drone_id": "tello-1",
  "choreography_id": "north-trail-primary-v1",
  "accepted": true
}
```

The gateway verifies that the envelope is active, the section matches, the selected drone is eligible, and no conflicting mission lease exists. It never accepts a model-supplied raw movement command.

#### `detection.get`

Request: `{"detection_id":"uuid"}`

Response includes the normalized detection, source observation, detector/provider/model/prompt versions, raw score semantics and provenance. A missing detection is a typed `NOT_FOUND` result.

#### `target.get`

Request: `{"target_id":"uuid"}`

Response includes all linked detection IDs, the evidence summary, policy version, threshold decision, and current target status. It must distinguish `CANDIDATE`, `CONFIRMED`, `TASK_CREATED` and `DISMISSED`.

#### `patroller.list_available`

Request: `{"section_id":"NORTH_TRAIL","run_id":"uuid"}`

Response contains simulated roster records with explicit provenance. This tool does not claim live staff location or availability.

#### `task.create`

Request:

```json
{
  "run_id": "uuid",
  "target_id": "uuid",
  "task_type": "MARK_ROCK_HAZARD",
  "priority": "HIGH",
  "instructions": "Inspect and mark the detected rock hazard in North Trail."
}
```

Response returns a new or existing task keyed by idempotency. It may only reference an existing target. A duplicate request returns the original task rather than creating a second one.

#### `patroller.rank_for_task`

Request: `{"task_id":"uuid"}`

Response is deterministic application output:

```json
{
  "task_id": "uuid",
  "ranking_policy_version": "patroller-ranking-v1",
  "candidates": [
    {"patroller_id":"patroller-1", "rank":1, "score":0.91, "reasons":{"eligible":true,"travel_time_s":120,"workload":1}}
  ]
}
```

The model may explain this result but may not alter the score or invent a candidate. A candidate with missing required evidence is excluded or returned as ineligible.

#### `assignment.propose`

Request: `{"task_id":"uuid","patroller_id":"patroller-1","reason":"rank-1 eligible candidate"}`

The gateway checks that the candidate is in the deterministic ranking and that the task is not already assigned. It creates `PATROLLER_ASSIGNMENT_PROPOSED` and leaves the task in `AWAITING_OPERATOR_CONFIRMATION`.

#### `assignment.confirm` (operator-only)

Request:

```json
{
  "task_id": "uuid",
  "proposed_patroller_id": "patroller-1",
  "operator_id": "operator-local",
  "confirmation_token": "ui-action-uuid"
}
```

The UI/API authenticates the operator action according to the deployment environment. The agent runtime cannot call this tool. The coordinator verifies that the proposed candidate has not changed, performs one atomic transition to `ASSIGNED`, emits the confirmation and assignment events, and makes retries idempotent.

#### `assignment.reject` (operator-only)

Request:

```json
{
  "task_id": "uuid",
  "proposed_patroller_id": "patroller-1",
  "operator_id": "operator-local",
  "reason": "operator-declined",
  "action_id": "ui-action-uuid"
}
```

The coordinator verifies that the task is still awaiting confirmation and that the proposed candidate matches the current proposal. It atomically records `PATROLLER_ASSIGNMENT_REJECTED`, changes the task to `REJECTED`, and returns the prior result for a duplicate action ID. Rejection is not an implicit assignment change and cannot be issued by the agent runtime.

### 7.2 Stretch tool

`inspection.observe_closer(target_id, choreography_id)` is disabled unless:

- the target is a candidate below the configured confirmation threshold or policy explicitly requests more evidence;
- the choreography is inside the already active mission envelope;
- the target has no prior closer observation;
- the mission and drone are healthy; and
- the one-per-target retry budget is unused.

The gateway rejects the call rather than silently expanding the envelope.

## 8. Tool-call and retry semantics

A tool call follows:

```text
REQUESTED -> VALIDATING -> EXECUTING -> SUCCEEDED
                                  |-> FAILED
REQUESTED -> DUPLICATE (returns original result)
REQUESTED -> CONFLICT (same key, different payload)
```

The coordinator MUST:

1. persist the request before starting external work;
2. persist the terminal result and `result_event_id` after the external result is known;
3. use the same idempotency key when retrying a timeout whose delivery is uncertain;
4. never infer physical success from a client-side timeout;
5. treat an unknown dispatch outcome as `IN_DOUBT`, reconcile with worker state, and block a second dispatch until resolved;
6. record error code, bounded message, provider/worker request ID where available, and timestamps.

Only transient, explicitly classified errors may be retried automatically. A safety rejection, envelope violation, stale approval, schema error or authorization error is terminal for that call.

## 9. Event-driven resume

The coordinator subscribes to committed events after the transaction that created them. It does not busy-loop on database rows or repeatedly ask the drone whether it has finished.

Resume algorithm:

1. Load the `agent_state` row for the run.
2. Read events with `sequence_no > last_processed_sequence`.
3. Ignore already-processed event IDs; a duplicate delivery must not cause a second tool call.
4. Select only events causally relevant to the current mission/agent run.
5. Advance the cursor in the same transaction as the agent checkpoint.
6. Invoke the agent with a compact summary and the new bounded event page.
7. Persist the next tool call or the waiting/terminal state before returning.

If the process restarts after the physical operation but before the cursor update, replaying the event is safe because the resulting tool call uses the original idempotency key. If the event payload is malformed or its referenced entity is missing, the run fails visibly with `SYSTEM_FAULT_RECORDED`; the coordinator does not fabricate a replacement entity.

## 10. Agent input and output contract

The model receives:

- the user's original text;
- the parsed goal fields that the coordinator considers valid;
- the current state summary;
- bounded recent events;
- the list of tools allowed in the current phase;
- explicit provenance labels and policy versions;
- the instruction that physical actions are bounded by coordinator validation.

The model returns one of:

```json
{"kind":"tool_call","tool_name":"inspection.dispatch","arguments":{}}
```

```json
{"kind":"wait","reason":"waiting for DETECTION_RECORDED"}
```

```json
{"kind":"operator_message","text":"...","state":"DONE|NEEDS_REVIEW|FAILED"}
```

Malformed output, an unknown tool, a tool not allowed in the current phase, or extra executable instructions is rejected and recorded. The agent receives a typed error and a bounded opportunity to correct its output; repeated invalid output fails the run safely.

No chain-of-thought is stored. `checkpoint_json` contains only fields such as `goal`, `phase`, `current_target_id`, `next_intended_action`, `retry_count`, `last_processed_sequence`, `last_processed_event_id`, and a short operator-facing rationale.

## 11. Deterministic policy boundary

The following decisions are application policy, not model arithmetic:

- whether a detection score qualifies for the configured threshold;
- target association for same-section/same-mission detections;
- target state transition;
- task type and required fields;
- patroller eligibility and ranking;
- assignment confirmation transition;
- mission-envelope and heartbeat enforcement;
- whether a closer observation is permitted.

The policy output records the raw inputs, threshold, model/prompt version where relevant, policy version, and evidence IDs. The agent can choose to request and explain policy output; it cannot rewrite it.

## 12. Failure and operator behavior

| Condition | Required behavior |
|---|---|
| Unsupported section or ambiguous user request | Ask for clarification or mark the run `FAILED`; do not dispatch |
| No ready drone or inactive envelope | Return a typed rejection and leave no physical side effect |
| Worker timeout/heartbeat loss | Stop automatic motion according to the drone-tools spec and resume only from a fault event |
| Provider timeout/schema failure | Record a failed perception attempt; no detection or target is fabricated |
| Low raw score | Keep candidate/review state; do not compare it to a made-up calibrated probability |
| Simulated roster used | Show persistent `SIMULATED` provenance in the UI and final message |
| Operator rejects assignment | Emit rejection, leave task `REJECTED`, and complete or escalate according to configured policy |
| Emergency stop | Mark mission/run safely and never issue a later normal flight command |
| Restart during waiting | Replay from `last_processed_event_id`; do not poll or duplicate dispatch |

## 13. Observability requirements

For every agent decision and tool call, the operator/debug report must make it possible to answer:

- Which user message and run caused this action?
- Which agent run, model and prompt version made the request?
- Which event caused the agent to resume?
- Which tool request/result and idempotency key were used?
- Which worker, observation, detection, target or task supplied the evidence?
- Was the data `LIVE`, `SIMULATED`, `REPLAYED`, `OPERATOR_PROVIDED` or `INFERRED`?
- Why did the coordinator allow, reject, retry or stop the action?

The timeline is derived from `events` and `tool_calls`; it is not a second un-audited history.

## 14. Component acceptance tests

The agentic-loop implementation is not accepted until these tests pass with a simulated worker, simulated perception adapter and simulated roster:

1. A text request creates exactly one run, mission and agent state row with a matching correlation ID.
2. A valid request produces exactly one `inspection.dispatch` tool call and one `DRONE_DISPATCH_REQUESTED` event.
3. Re-delivering the same dispatch request returns the original mission and does not create a second physical dispatch.
4. A dispatch whose idempotency key is reused with different arguments is rejected as a conflict.
5. The agent enters `WAITING_FOR_EVENT` and does not call the state API in a polling loop while the mission is active.
6. A committed `DETECTION_RECORDED` event resumes the same agent run from its cursor and causes no duplicate processing when delivered twice.
7. A detection below the threshold remains a candidate and does not create a confirmed hazard task without the configured policy path.
8. A qualifying detection creates one target and one task, even when the agent retries `task.create`.
9. Deterministic ranking returns the same ordered candidates for the same snapshot, independent of model output.
10. `assignment.propose` creates a pending approval and cannot make the task `ASSIGNED`.
11. Only an operator confirmation changes the task to `ASSIGNED`; duplicate confirmation is harmless and conflicting confirmation is rejected.
12. A provider or worker fault is represented in events and does not produce synthetic detection, assignment or success state.
13. A restart between event delivery and checkpoint persistence resumes from the last cursor and converges to the same state.
14. A complete simulated V0 run reconstructs the acceptance sequence in the parent specification from `events` without database edits.

## 15. Implementation increments

1. Define typed Python request/result objects and error codes.
2. Implement the coordinator repository with atomic state/event transactions.
3. Implement the tool gateway with idempotency and authorization checks.
4. Implement the event dispatcher and durable agent cursor.
5. Add a deterministic simulated worker/perception/roster vertical slice.
6. Add operator confirmation/rejection API and timeline projection.
7. Connect the real drone-worker and perception adapters only after the simulated acceptance test is green.
8. Add restart, duplicate-delivery and fault-path tests before physical rehearsal.

## 16. Open decisions for team review

1. Which local transport should carry coordinator/worker events first: in-process adapter, local HTTP, or a small message stream?
2. Which model/runtime is used for the first orchestrator implementation, and how are model/prompt versions pinned?
3. What is the maximum number of agent correction attempts after malformed structured output?
4. Should operator rejection terminate the run immediately or return the agent to a bounded alternative proposal path?
5. What authentication mechanism is sufficient for the local operator confirmation endpoint?

These decisions must not weaken the database ownership, event replay, envelope, idempotency or operator-confirmation invariants in the parent specification.
