# SENTINEL Drone Tools and Worker Component Specification

**Document version:** 0.1
**Status:** Supporting draft; authoritative V0 details are in the V0 coherence specification. The detailed worker work package is intentionally deferred until after WP0–WP3.
**Component owner:** PM/architect initially; drone-tools engineer during implementation
**Parent specification:** [`SENTINEL_Demo_State_Database_Spec_v0.2.md`](SENTINEL_Demo_State_Database_Spec_v0.2.md)
**Existing implementation boundary:** `src/sentinel/client.py`, `runner.py`, `envelope.py`, `network.py`, `video.py`, `djitellopy_backend.py`, `dry_run.py`, `emergency.py`
**Scope:** Coordinator inspection tools, worker lifecycle, named Tello choreography, mission-envelope enforcement, telemetry/heartbeat, video capture and emergency stop for V0

This document is retained as supporting design history for the later worker work package. It does not define the current executable WP0–WP3 contract. The V0 coherence specification and WP0 contract spec are authoritative for all interfaces visible before that later work package.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Safety objective

A text-triggered inspection may cause physical motion only when all of the following are true:

- an operator has activated a stored mission envelope during setup;
- the requested section and drone are authorized by that envelope;
- the named choreography is registered and validated;
- the worker has a healthy lease and current heartbeat;
- preflight checks pass;
- each movement endpoint remains inside the envelope;
- no emergency latch or conflicting mission exists; and
- every operation is traceable to a run, mission, tool call and idempotency key.

The agent never supplies Tello SDK strings, arbitrary vectors, distances, speeds, routes or socket addresses. It selects from coordinator tools; the worker executes a validated named choreography.

## 2. Relationship to the current harness

The current low-level package provides the first implementation substrate:

- `DroneClient` is the backend-independent transport/protocol boundary;
- `DryRunClient` provides deterministic no-socket behavior;
- `FlightEnvelope` validates the open-loop 1 m cube and movement endpoints;
- `FlightTestRunner` owns preflight, command order, landing/abort policy and command audit;
- `VideoReceiver` binds and decodes the Tello H.264 stream through PyAV;
- `DjitellopyClient` is the pinned DJITelloPy 2.5.0 compatibility boundary;
- `network.py` performs read-only adapter discovery, reachability probing and route preservation checks;
- `EmergencySender` sends the independent emergency datagram.

The coordinator/worker layer should compose these boundaries rather than bypass them. Existing internal methods such as `DroneClient.send` remain implementation details and are not agent-facing tools.

DJITelloPy process-global command/state receiver threads mean that V0 permits one real backend per worker process. A second real backend in the same process must fail closed; a later multi-drone release uses separate worker processes and explicitly allocated network interfaces.

## 3. Component ownership

| Responsibility | Coordinator | Worker | Tello backend | UI/operator |
|---|---:|---:|---:|---:|
| Activate mission envelope | validate/read | enforce | unaware | operator action |
| Select section and choreography | yes | validate registry | no | request intent |
| Convert model output into movement | never | never | no | no |
| Execute SDK commands | request/lease | yes | transport/protocol | emergency only through dedicated path |
| Validate endpoints and command budget | gateway pre-check | per-command gate | basic argument checks | display |
| Decode continuous video | no | yes | video socket boundary | display |
| Select/persist evidence frame | policy/config | yes | frame transport | display |
| Write SQLite | only coordinator | no | no | no |
| Emergency stop | latch mission state | issue/record | send datagram | independent operator action |

## 4. Logical worker architecture

```text
Coordinator
  |  dispatch(request), heartbeat, worker events
  v
Worker supervisor
  +-- mission lease and envelope gate
  +-- FlightTestRunner / choreography executor
  +-- DroneClient (dry-run or DJITelloPy)
  +-- VideoReceiver and frame-quality selector
  +-- progress/event publisher
  +-- independent EmergencySender path
```

The worker may keep local transient state needed to execute the active mission, but the coordinator is the system of record. Worker messages are accepted only after schema, run, mission, worker identity and lease validation.

## 5. Mission-envelope contract

The coordinator stores an immutable activated envelope. A V0 envelope is conceptually:

```json
{
  "mission_envelope_id": "envelope-v0-north-trail",
  "version": 1,
  "active": true,
  "allowed_drone_ids": ["tello-1"],
  "allowed_section_ids": ["NORTH_TRAIL"],
  "allowed_choreography_ids": ["north-trail-primary-v1"],
  "bounds": {
    "frame": "OPEN_LOOP_RELATIVE_CM",
    "min_x_cm": -50,
    "max_x_cm": 50,
    "min_y_cm": -50,
    "max_y_cm": 50,
    "min_z_cm": 150,
    "max_z_cm": 250
  },
  "max_command_count": 40,
  "max_duration_s": 180,
  "minimum_battery_percent": 40,
  "maximum_closer_observations_per_target": 1,
  "activated_by_operator_id": "operator-local",
  "activated_at": "UTC ISO-8601 timestamp",
  "policy_version": "mission-envelope-v1"
}
```

Rules:

1. The envelope is immutable for a mission. Updating a bound or choreography creates a new version and requires operator activation.
2. The current position is an open-loop estimate from acknowledged commands. It is not GPS or globally accurate localization.
3. Every move validates both the distance type/range and the resulting endpoint before transmission.
4. A command outside bounds, command budget, duration, battery or allowed choreography is rejected before reaching the backend.
5. A missing, inactive, expired or mismatched envelope is a hard failure.
6. The UI shows which envelope is active and when it was activated.
7. An emergency stop is available throughout the mission independently of the agent state.

The current harness's 1 m x 1 m x 1 m cube is the V0 default. The physical area around the cube must be substantially larger and clear, with an operator able to intervene independently.

## 6. Coordinator-facing inspection tool

The agent-facing logical call is:

```text
inspection.dispatch(section_id, mission_envelope_id)
```

The coordinator enriches it with `run_id`, `mission_id`, `worker_id`, `choreography_id`, correlation ID and idempotency key. The worker-facing request is:

```json
{
  "schema_version": 1,
  "mission_id": "uuid",
  "run_id": "uuid",
  "worker_id": "worker-1",
  "drone_id": "tello-1",
  "section_id": "NORTH_TRAIL",
  "mission_envelope_id": "envelope-v0-north-trail",
  "choreography_id": "north-trail-primary-v1",
  "idempotency_key": "run-uuid:dispatch:NORTH_TRAIL",
  "requested_at": "UTC ISO-8601 timestamp",
  "deadline_at": "UTC ISO-8601 timestamp"
}
```

The worker returns one of:

```json
{"accepted":true,"status":"ACCEPTED","mission_id":"uuid","worker_request_id":"uuid"}
```

or a typed rejection:

```json
{"accepted":false,"status":"REJECTED","error_code":"ENVELOPE_INACTIVE|DRONE_BUSY|LOW_BATTERY|PREFLIGHT_REQUIRED|DUPLICATE|SCHEMA_ERROR","message":"bounded operator-safe message"}
```

The worker MUST return the original result for a duplicate idempotency key. If execution may have started but the acknowledgement is lost, it returns/retains `IN_DOUBT` and blocks a second dispatch until the coordinator reconciles worker state.

## 7. Choreography registry

A choreography is a versioned, code-owned sequence, not a model-generated plan. Each definition includes:

```json
{
  "choreography_id": "north-trail-primary-v1",
  "version": 1,
  "observation_step": "PRIMARY_CAPTURE",
  "steps": [
    {"name":"TAKEOFF","operation":"takeoff"},
    {"name":"ENTER_ALTITUDE","operation":"enter_altitude_band"},
    {"name":"FORWARD_SCAN","operation":"move","direction":"forward","distance_cm":50},
    {"name":"RIGHT_SCAN","operation":"move","direction":"right","distance_cm":50},
    {"name":"PRIMARY_CAPTURE","operation":"capture_burst"},
    {"name":"RETURN_LEFT","operation":"move","direction":"left","distance_cm":50},
    {"name":"LAND","operation":"land"}
  ],
  "allowed_operations": ["takeoff","move","rotate","stop","capture_burst","land"],
  "max_command_count": 20,
  "policy_version": "choreography-v1"
}
```

The exact first choreography may use the current harness sequence, but the observation step must be named and the active step supplies the trusted `section_id`. Commanded yaw, attitude and elapsed time are evidence in `capture_context_json`; they are not global pose claims.

Registry validation MUST reject:

- unknown operations or directions;
- non-integer or out-of-range distances/angles;
- a movement whose worst-case endpoint leaves the active envelope;
- an observation step with no capture gate;
- a choreography without a bounded timeout and command budget;
- an unversioned definition;
- an automatic closer choreography that is not explicitly enabled by the envelope.

## 8. Worker mission state machine

```text
IDLE
  -> ACCEPTED
  -> PREFLIGHT
  -> TAKEOFF_REQUESTED
  -> FLYING
  -> OBSERVING
  -> LANDING
  -> COMPLETED

Any non-terminal state -> ABORTING -> ABORTED
Any non-terminal state -> FAILED
Any airborne state -- emergency --> EMERGENCY_ABORTED
```

Required events include the parent-spec events plus worker details in payloads:

- `MISSION_CREATED`
- `MISSION_AUTHORISED`
- `DRONE_DISPATCH_REQUESTED`
- `DRONE_COMMAND_COMPLETED`
- `OBSERVATION_CAPTURED`
- `MISSION_COMPLETED`
- `MISSION_ABORTED`
- `SYSTEM_FAULT_RECORDED`

Every command event includes mission/drone/worker IDs, choreography step, command kind (not arbitrary model text), result/ack status, command sequence, envelope version, current open-loop estimate, timestamps and error code when applicable.

The worker emits events to the coordinator. It never increments the coordinator's `events.sequence_no` itself and never assumes an event is durable until the coordinator acknowledges receipt or the transport's retry contract makes delivery explicit.

## 9. Preflight gate

Before takeoff, the worker MUST pass all configured checks:

1. resolve or validate the explicitly selected local adapter;
2. verify the adapter can reach the Tello endpoint using the harmless SDK command;
3. capture the internet default-route source/interface and preserve it for comparison;
4. construct the backend using explicit command/state/video binding;
5. connect and enter command mode;
6. validate battery within the envelope minimum;
7. validate height telemetry and that current state is not above the envelope;
8. set and read back the configured command speed;
9. start video and decode at least one valid frame;
10. stop the preflight stream cleanly unless the choreography requires it to remain active;
11. verify the envelope, choreography, lease, deadline and emergency path are all ready.

Any failure prevents takeoff. The worker records the failed phase and reason. It does not attempt a compensating movement after a failed preflight.

No automatic network operation may disable adapters, change route metrics or switch Wi-Fi networks. The current harness's explicit `local_ip`, interface-name and interface-index options remain the supported selection mechanisms.

## 10. Command gate and telemetry

Before each physical command:

1. check the emergency latch;
2. check mission deadline and command count;
3. verify worker lease and heartbeat freshness;
4. read/validate required telemetry according to the step;
5. calculate the candidate endpoint from the last acknowledged open-loop position;
6. validate the endpoint against the active envelope;
7. transmit through the backend boundary;
8. normalize and record the response;
9. update the position estimate only after an accepted movement response;
10. re-check emergency state before advancing to the next step.

A movement acknowledgement does not prove exact physical displacement. The report must retain this distinction.

For vertical steps, the worker validates measured height after the command and fails closed if it is invalid or outside the cube. For V0, horizontal movement remains open-loop. No GPS arrival, map position or centimeter-level pose is claimed.

## 11. Heartbeat and lease behavior

The coordinator gives the worker a mission lease with a bounded expiry. The worker publishes heartbeats containing:

```json
{
  "worker_id":"worker-1",
  "drone_id":"tello-1",
  "mission_id":"uuid",
  "state":"FLYING",
  "battery_percent":84,
  "flight_status":"FLYING",
  "last_command_sequence":7,
  "sent_at":"UTC ISO-8601 timestamp"
}
```

The configured heartbeat timeout must be shorter than the mission deadline and visible in scenario configuration. On missed coordinator heartbeat, worker heartbeat or required telemetry:

- no further automatic movement is sent;
- the worker emits a fault event;
- if airborne and the emergency latch is not set, it may attempt the configured controlled landing once;
- if the emergency latch is set, no landing command follows the emergency command;
- the coordinator marks the mission degraded/aborted according to the fault policy;
- recovery requires explicit reconciliation before another dispatch.

Heartbeat loss is not treated as success and must not be hidden by a retry.

## 12. Video and selected-frame capture

Continuous Tello video is received and displayed by the worker/UI path. It is not written frame-by-frame to SQLite and is not sent continuously to the hosted detector.

A capture is eligible only when all parent-spec conditions hold:

1. the choreography has reached its named observation step;
2. the movement/rotation command has been acknowledged;
3. commanded relative yaw has completed, with attitude checked when available;
4. the configured settle interval has elapsed;
5. recent frames pass the configured motion, blur and exposure gate; and
6. the mission remains inside its time, command and battery envelope.

The worker captures a short burst and selects the best frame using a deterministic, versioned quality policy. A selected observation stores:

- image artifact URI and SHA-256;
- source frame number/timestamp where available;
- width, height and pixel format;
- `PRIMARY` or `CLOSER_CONFIRMATION` viewpoint;
- `quality_json` with the measured metrics and selection score;
- `capture_context_json` with choreography step, commanded yaw, attitude, settle duration and frame IDs;
- `captured_at`, mission/drone/section IDs and `provenance_kind`.

If no frame passes the gate before the step deadline, the worker emits a capture failure and does not submit a poor or unidentified frame as valid evidence. The operator may still see the live stream and fault state.

## 13. Emergency stop

Emergency stop is a separate, terminal control path:

- operator action or independent hotkey latches the emergency state before attempting transmission;
- a fresh explicitly bound UDP socket sends the complete `emergency` datagram to the Tello command endpoint;
- the sender does not wait for an acknowledgement because the Tello protocol does not acknowledge this command;
- the number of bytes accepted by the local OS and any send error are recorded;
- after the latch, no normal command, landing command, stream cleanup command that transmits, or retry may be issued;
- the coordinator records the emergency event and reports the limitation that motor stoppage is not locally observed;
- a new real run requires a fresh worker process/backend according to the current DJITelloPy limitation and a new operator preflight.

The independent emergency sender rejects a missing or wildcard local IP. It must not route through a possibly blocked normal command-response socket.

## 14. Backend and process contract

### 14.1 Dry run

`DryRunClient` and the worker simulator MUST implement the same logical mission/choreography/event contract without opening network sockets. Dry run is the default for automated tests and coordinator integration.

It must be deterministic enough to assert:

- command sequence and envelope decisions;
- observation creation and frame metadata;
- heartbeat and failure transitions;
- idempotency behavior;
- emergency terminal behavior.

A dry run is not evidence of physical flight.

### 14.2 Real backend

The real worker uses the pinned DJITelloPy 2.5.0 compatibility boundary and PyAV video decoder. It binds command, state and video sockets to the selected local adapter. The worker must fail closed if the library's process-global receiver threads are already initialized.

No automated test sends real flight commands. A real hardware acceptance run requires a clear physical area, independent operator intervention, the dedicated adapter, fresh progress evidence and the exact envelope/choreography version recorded in the report.

## 15. Fault and recovery matrix

| Fault | Immediate action | Recovery requirement |
|---|---|---|
| Envelope violation | Reject before send | Correct envelope/choreography and start a new mission |
| Low battery | No takeoff / controlled end if detected airborne | Recharge/re-preflight |
| Adapter/route change | Stop automatic progression | Operator reconciles network and starts a new run |
| No command/state response | Stop further motion; fault/landing policy | Physical state reconciliation before retry |
| No decoded frame | Abort capture/mission according to phase | Fix stream or use explicit labelled replay fallback |
| Provider failure after capture | Keep observation, fault perception path | Retry same request or operator/replay decision |
| Worker process crash | Coordinator marks lease lost and mission uncertain | Operator verifies physical state; no blind redispatch |
| Emergency latch | Motor-stop path; terminal normal-command ban | Fresh physical preflight and new run |
| Duplicate dispatch | Return original result | No second physical mission |
| Stale mission lease | Reject command | Reconcile and issue a new lease |

## 16. Component acceptance tests

1. A dispatch with no active envelope is rejected before backend connection or takeoff.
2. A dispatch with an unknown choreography or section mismatch is rejected before physical commands.
3. Every movement is rejected when its candidate endpoint lies outside the 1 m cube.
4. A command budget or deadline exhaustion prevents the next command from being transmitted.
5. A duplicate dispatch idempotency key returns the original mission and sends no second takeoff.
6. A lost dispatch acknowledgement creates an `IN_DOUBT` state and blocks blind redispatch.
7. Preflight fails closed on adapter reachability, route change, low battery, invalid height, speed readback failure or missing decoded frame.
8. A capture step does not produce an observation until acknowledgement, settle and quality gates pass.
9. The selected observation contains a hash, quality metrics, capture context and the active choreography section.
10. Continuous video remains available to the operator while only selected frames enter the perception path.
11. A heartbeat timeout stops further automatic movement and follows the configured landing policy.
12. An emergency latch prevents all later normal commands, including landing and cleanup commands that would transmit.
13. Emergency sending uses a fresh explicitly bound socket, rejects wildcard local IP and records short-write/send errors.
14. Dry-run and real-backend contract tests expose the same logical mission state transitions.
15. A coordinator restart or worker restart cannot cause a second physical dispatch without reconciliation.
16. A supervised real rehearsal produces a progress log before the final report and records adapter, telemetry, video, command and emergency state evidence.
17. One end-to-end V0 run yields the parent-spec events in a reconstructable order and never claims GPS arrival or live patroller state.

## 17. Implementation increments

1. Define the choreography registry and mission-envelope JSON schema.
2. Wrap the current `FlightTestRunner` in a worker mission executor with explicit leases and event payloads.
3. Add the coordinator-facing dispatch/ack/idempotency adapter.
4. Add heartbeat, command budget, deadline and reconciliation states.
5. Implement deterministic burst capture and observation-artifact persistence.
6. Connect the existing `VideoReceiver` and expose operator stream status.
7. Add independent emergency integration and terminal-state tests.
8. Run the complete simulated M1 path.
9. Perform supervised physical preflight and a bounded one-drone rehearsal only after simulated and dry-run gates pass.

## 18. Open decisions for team review

1. What exact heartbeat interval/timeout is appropriate for the Windows worker and local network after measurement?
2. Which choreography is the first frozen `PRIMARY_CAPTURE` sequence, and what are its measured duration and command budget?
3. Which local artifact directory/service owns evidence images and how long are they retained?
4. Does the worker keep the video stream active through the whole choreography or restart it around capture steps?
5. What is the operator-visible physical-state reconciliation procedure after a worker crash or lost acknowledgement?
6. Which later hardware/localization increment, if any, will replace the V0 open-loop horizontal estimate?

These decisions can refine implementation parameters but cannot weaken mission-envelope enforcement, fail-closed preflight, emergency terminal behavior, coordinator-only database writes, provenance or idempotency.
