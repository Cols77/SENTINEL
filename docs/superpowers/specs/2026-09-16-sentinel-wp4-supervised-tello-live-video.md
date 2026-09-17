# SENTINEL WP4 — Supervised Tello Worker and Live Video

> **Status:** Release-prerequisite contract slice; implementation/evidence pending (kept live, not archived)
> **Owner:** Colin
> **Prerequisite:** WP0 `sentinel.v0` contracts; G3-approved provider profile (joint Colin+Dzivha approval) before judged integration

## 1. Boundary

WP4 executes only the named, pre-authorised choreography on one supervised Tello across the bounded six-zone indoor-alley route in the operator-selected layout mode (marker mode by default, or the explicit `ROUTE_ONLY_2_ZONE` fallback limited to the two middle zones). It implements `WorkerPort.dispatch`, `WorkerPort.reconcile`, `WorkerPort.read_video_status(run_id) -> #VideoStatus`, and `WorkerPort.open_live_video(run_id) -> #SafeVideoStreamHandle`. It creates no alternate wire format.

`WorkerPort.dispatch` consumes the immutable `SurveyPlanReference`, its `approval_reference` and the exact run-bound `PreflightReport`. Dispatch executes only when the plan reference, approval and preflight all match the coordinator's approved run and the approval binds the plan hash, worker, choreography, run/state version and expiry. The worker honors at-most-one coordinator dispatch before reconciliation.

WP4 owns producing the live stream and status. `VideoStatus` is read-only and reports stream lifecycle, freshness, dimensions, degradation, and selected-frame artifact ID/SHA-256. `SafeVideoStreamHandle` is an expiring, read-only served endpoint; it contains neither a filesystem path nor video bytes. A judged run requires `LIVE` status and `LIVE` source kind throughout the judged observation, with a selected frame correlated to the `ArtifactRef` consumed by `PerceptionRequest` and returned by `PerceptionResult`.

The coordinator may retain only run-associated status/reference and selected image evidence through the artifact contract. It must not persist video bytes or proxy raw frames. WP2 owns rendering the handle/status and the visible `DEGRADED`/`UNAVAILABLE` path.

Perception may run asynchronously while the worker follows its already-approved route. It has no worker/control port and can never change an in-flight route; no flight mutation comes from perception.

## 2. Preflight

Real flight is DJITelloPy-only. Each real run starts in a fresh process with a fresh preflight and `--real --confirm`. Before takeoff exactly the 8 named preflight checks must pass:

1. WIFI_INTERFACE,
2. DRONE_LINK,
3. BATTERY_TELEMETRY,
4. LIVE_VIDEO,
5. LAYOUT_MARKERS,
6. BOUNDED_AREA,
7. HEIGHT_ENVELOPE,
8. EMERGENCY_LATCH.

Operator authorization is not a preflight check: it is carried as the plan's `approval_reference` and evaluated by the coordinator at dispatch.

The preflight report is immutable and run-and-plan-bound; the agent receives it read-only and may assess only its structured pass/fail facts. A failed preflight may be overridden only by an operator-only `FORCE_RUN_CREATE` naming the failed report, an operator reason, and a new run/plan ID — never a resume of the prior operation and never under an active latch. It always requires fresh preflight and `--real --confirm`.

## 3. Capture, landing and emergency

At each approved station WP4 captures one to three live JPEG/PNG frames using the approved headings/count. A station capture records the immutable plan hash, plan step, station ID, zone ID, marker ID when applicable, configured height, timestamp, artifact ID and SHA-256 hash, with full provenance. A marker-mode capture is accepted only with its expected marker; a route-only capture is visibly labelled `route-only, not visually verified`. Missing an expected marker in marker mode is a failure.

Landing is permitted only while no active latch is armed. The `EMERGENCY` latch is terminal and non-overridable: after the emergency command SENTINEL sends no landing, cleanup or retry command. A future run requires a fresh manual preflight.

The mandatory rehearsal's test evidence must show `VideoStatus.status: LIVE`, a successful safe-handle open, a selected live JPEG/PNG `ArtifactRef` whose SHA-256 equals `PerceptionRequest.artifact_sha256`, the correlated `PerceptionResult`, operation/reconciliation outcome, and safe landing.

## 4. Rehearsal and acceptance

WP4 rehearsal acceptance command (implementation work; this specification fabricates no result): `uv run python -m pytest tests/worker tests/integration -q -k "tello_rehearsal"`. G4 remains blocked until the real rehearsal evidence exists. The 8-check preflight list is mirrored in coherence spec §7.1.