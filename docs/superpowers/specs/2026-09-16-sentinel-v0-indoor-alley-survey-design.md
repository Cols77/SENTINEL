# SENTINEL V0 Indoor Alley Survey Design

> **Status:** Approved design; implementation planning pending review
> **Authority:** Resolves the V0 room-demo decisions. The existing V0 coherence
> profile and WP0–WP4 specifications must be amended to conform to this design.

## Goal

Demonstrate a supervised Tello survey of rocks on the floor of a lecture room's
two table alleys. An agent proposes a fixed survey assembled from a registered
catalogue; a human approves it; the worker executes it; live frames are sent to
perception while the route continues; the UI reports coordinator-validated
zone-level perception results.

The demo is safe and observable without claiming autonomous navigation, SLAM,
GPS, or precise rock coordinates.

## 1. Room layout and localization

The canonical V0 map replaces the fictional three trail sections with exactly
six fixed zones:

```text
LEFT_ALLEY_FRONT   LEFT_ALLEY_MIDDLE   LEFT_ALLEY_BACK
RIGHT_ALLEY_FRONT  RIGHT_ALLEY_MIDDLE  RIGHT_ALLEY_BACK
```

The map uses local room coordinates only. A zone is a floor-alley segment, not
a table group and not a GPS area.

### Marker mode

Marker mode is the normal demo configuration. Each of the six stations has one
visible, uniquely numbered AprilTag at a configured local position. Before an
accepted capture, the worker verifies the station's expected tag. This provides
an approximate visual pose for display and proves the capture's zone. It does
not steer the drone or correct its route.

### Route-only fallback

The operator selects the mode before preflight. In `ROUTE_ONLY_2_ZONE` mode,
there are exactly two active zones: `LEFT_ALLEY_MIDDLE` and
`RIGHT_ALLEY_MIDDLE`. No marker claim is made; each capture is visibly labelled
`route-only, not visually verified`. A marker-mode run never silently falls
back: a missing expected marker is a failure.

## 2. Registered plan and authority boundary

The worker owns a finite survey catalogue. Each catalogue entry has an ID,
revision, canonical hash, ordered station steps, expected zone/marker, allowed
capture headings/count, route bounds, timeout, command budget, and pre-approved
height for each segment. Height is configurable per segment and may include
pre-planned ascent/descent; every requested target and vertical leg must be in
the configured room safety envelope.

The agent can propose only a catalogue-bound plan reference. It cannot emit raw
SDK commands, arbitrary coordinates, arbitrary heights, or an unbounded capture
count. The proposal identifies the catalogue ID/revision/hash and intended
stations. The coordinator creates an immutable plan record.

The operator reviews and approves that exact plan version. The approval binds
the plan hash, worker, choreography, run/state version and expiry. Dispatch
accepts only the matching immutable plan and makes at most one physical attempt.
V0 records each selected station's coordinator-validated zone-level perception
result. Revisit proposals and new physical dispatches are retained as V0.1
capability; any future agentic action still requires a new immutable plan and
operator approval, and is not implemented in the V0 execution path.

## 3. Preflight, force-run and emergency

The worker—not the agent—performs preflight and emits an immutable,
run-and-plan-bound report. It runs exactly eight checks —
`WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`,
`LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH`.
Operator authorization is not a ninth check: it is carried as the plan's
`approval_reference` and evaluated by the coordinator at dispatch, not by the
preflight itself. The agent
receives the report read-only and may only assess its structured pass/fail facts.

An ordinary run dispatches only after a fresh passing preflight. A real run also
requires `--real --confirm`.

An operator may force a **new** run after a failed preflight. The override is a
durable approval with the failed report, an operator reason, a new plan/run ID,
and the usual `--real --confirm` acknowledgement. It may override any failed
preflight condition except an active emergency latch. It never resumes a prior
operation or reuses its dispatch attempt.

The UI has a permanently visible one-click `EMERGENCY STOP` control and a
keyboard fallback. It sends the latch through the selected drone interface,
immediately disables ordinary controls and records acknowledgement. An active
emergency latch is terminal and cannot be overridden; a future run requires a
fresh manual preflight.

## 4. Capture, perception and evidence

At each approved station the worker captures one to three live JPEG/PNG frames
using the approved headings/count. Each capture is a distinct record carrying its
`StationCaptureProvenance` (`plan_id`, `plan_sha256`, `plan_step`, `station_id`,
`zone_id`, `marker_id` when applicable, `height_m`, `captured_at`), its ordered
per-station `capture_index` (1–3), and its typed `ArtifactRef` with `artifact_id`
and SHA-256 hash. A marker-mode capture is
accepted only with its expected marker; a route-only capture has no marker
claim.

The perception request receives that exact artifact and its station/zone
provenance. It returns a hash-bound result. The coordinator verifies that the
request/result image hash, artifact, station, zone, provider profile and
threshold match before persistence. A result is assigned to the station's zone;
V0 does not calculate a rock's precise local X/Y coordinate.

Perception may run asynchronously while the worker follows its already-approved
route. It has no worker/control port and can never change the in-flight route.

## 5. Outcomes

- `FAILED`: any failed preflight (unless a force-new-run override is approved),
  missing marker in marker mode, worker/video/provider fault, invalid capture,
  integrity mismatch, or unresolved operation.
- `NEEDS_REVIEW`: the route and evidence are valid, but perception is
  inconclusive or below its configured confidence threshold. V0 records the
  hold for future coordinator/agent planning; the operator does not review
  database state, and no score or automatic revisit is invented.
- `COMPLETED`: every approved station and valid perception result is processed,
  including a valid no-rock result; no separate operator result decision is
  required.
- `EMERGENCY`: emergency latch is active. It is terminal and cannot be forced.

No dynamic correction, perception-driven motion, automatic revisit, automatic
provider failover, or physical command retry belongs in V0.

## 6. UI scope

V0 shows the configured mode, preflight facts, current worker/progress facts,
marker-derived approximate position when available, zone-level detections and
evidence provenance, force-run history, and emergency state/control.

The numbered planned-route overlay, capture points and a drawn revisit proposal
are V0.1 features. They must render coordinator facts only; they never calculate
or command flight movement.

## 7. Minimal provider gate

The checked profile contains only the selected provider/model, prompt/transform
revision, score threshold, owner approval record and a small versioned test-image
set. It is sufficient to reproduce the demo configuration. Cost targets,
latency targets, formal benchmark governance and provider failover are deferred.

## 7.1 Contract disposition: active V0 versus retained V0.1

The contract registry is deliberately broader than the V0 runtime path. This is a
staging decision, not a deletion decision: every existing schema, command/event
branch, artifact, and fixture remains available and continues to be validated
where applicable.

**Active V0 runtime subset**

- coordinator-mediated, read-only, run-scoped agent reads: `read_state`, `read_mountain`, and bounded `read_events`;
- typed agent proposal resolving to one immutable, survey-catalogue-bound plan;
- operator approval/rejection of the exact plan;
- fresh preflight for every real run, deterministic supervised dispatch, at-most-one dispatch attempt, and fail-closed reconciliation;
- live frame capture with `VideoStatus` and station capture provenance;
- perception result for each fixed indoor-alley zone;
- coordinator-persisted perception result for each fixed zone, rendered read-only by the UI;
- evidence/artifact integrity, provider provenance, version checks, idempotency, emergency handling, and recovery.

**Retained but deferred to V0.1**

- `CommandTaskCreatePayload`, `CommandTaskRankPayload`, `TASK_CREATE`, `TASK_RANK`, and task/ranking/patroller assignment lifecycles;
- target aggregation, mission/task ranking, patroller selection, assignment approval loops, automatic revisit, and external notifications;
- precise hazard coordinates, `flight_path`, detailed `drone_pose`, GIS/SLAM/location abstractions, and route-geometry semantics;
- `SafeVideoStreamHandle`, browser-stream serving, and richer served-video lifecycle/transport;
- generalized mission/task/event families and richer evidence, diagnostic, storage, and provenance projections not needed to produce the active zone-level perception result;
- `ZONE_RESULT` operator targets and `ZONE_RESULT_REVIEWED` events, retained as compatibility contracts for a future DB-state review flow;

The central `sentinel-v0-wire.schema.json` remains intact apart from additive
scope metadata and retained compatibility definitions. No existing branch is
removed or narrowed. A V0 implementation/profile must not instantiate, require,
or emit a deferred branch merely because that branch remains in the registry.

## 8. Required artifact migration

- Update the root coherence specification to name this indoor-alley V0 profile
  and six-zone fixture.
- Update WP0 schemas/fixtures for the fixed zone catalogue, immutable plan
  reference, preflight report, capture provenance and force-run approval.
- Update WP1's agent intent/transition policy, immutable approval/dispatch
  binding and outcome matrix.
- Update WP2 for preflight facts, force-run decision, emergency UI state and
  `VideoStatus`; render perception results read-only and defer route overlay,
  served video, richer video transport and DB-state review to V0.1.
- Update WP3 to consume/return hash-bound station and zone provenance while
  retaining no route/control authority.
- Update WP4 for DJITelloPy-only real execution, plan-bound worker behavior,
  asynchronous capture/perception boundary and supervised rehearsal.
- Archive superseded flight material under
  `docs/superpowers/specs/superseded/`, with a replacement header and links.
  The direct-UDP command-test design is superseded. The historical broad worker
  specification is retained as non-authoritative support unless a focused review
  shows a whole file has no remaining live content. This archival follow-up is
  complete in the current working tree; see
  [`superseded/README.md`](superseded/README.md).

## 9. Acceptance evidence

Automated tests validate catalogue-only agent proposals; plan-hash/approval/
dispatch equality; preflight persistence and force-new-run behavior; both layout
modes; height-envelope rejection; one-to-three frame station correlation;
hash/provenance integrity; concurrent perception with no flight mutation;
`FAILED`/`NEEDS_REVIEW`/`COMPLETED`/`EMERGENCY` outcomes; and no post-latch
ordinary command. Retained deferred branches, including the zone-review
fixtures, continue to validate for compatibility but are not required by the V0
vertical slice.

One supervised hardware rehearsal verifies the selected Wi-Fi route, battery,
video, selected layout, approved route, captured live evidence, perception UI,
operator decision, emergency availability and safe landing. Automated tests do
not by themselves clear this physical-demo requirement.

## Non-goals

No GPS, GIS, SLAM, dynamic obstacle map, arbitrary planner, precise rock map
coordinates, autonomous drift correction, automatic revisit, multi-drone work,
broker, or multi-service transport is included.
