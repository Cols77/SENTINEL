# SENTINEL WP4 — Supervised Tello Worker and Live Video

> **Status:** Release-prerequisite contract slice; implementation/evidence pending
> **Owner:** Colin
> **Prerequisite:** WP0 `sentinel.v0` contracts; G3-approved provider profile before judged integration

## 1. Boundary

WP4 executes only the named, pre-authorised choreography on one supervised Tello. It implements `WorkerPort.dispatch`, `WorkerPort.reconcile`, `WorkerPort.read_video_status(run_id) -> #VideoStatus`, and `WorkerPort.open_live_video(run_id) -> #SafeVideoStreamHandle`. It creates no alternate wire format.

WP4 owns producing the live stream and status. `VideoStatus` is read-only and reports stream lifecycle, freshness, dimensions, degradation, and selected-frame artifact ID/SHA-256. `SafeVideoStreamHandle` is an expiring, read-only served endpoint; it contains neither a filesystem path nor video bytes. A judged run requires `LIVE` status and `LIVE` source kind throughout the judged observation, with a selected frame correlated to the `ArtifactRef` consumed by `PerceptionRequest` and returned by `PerceptionResult`.

The coordinator may retain only run-associated status/reference and selected image evidence through the artifact contract. It must not persist video bytes or proxy raw frames. WP2 owns rendering the handle/status and the visible `DEGRADED`/`UNAVAILABLE` path.

## 2. Rehearsal and acceptance

The single mandatory G4 rehearsal is one supervised Tello, one operator, one bounded area, emergency stop available, and named pre-authorised choreography. Preflight verifies worker identity, link, battery, emergency latch path, area, and explicit authorization. The test evidence must show `VideoStatus.status: LIVE`, a successful safe-handle open, a selected live JPEG/PNG `ArtifactRef` whose SHA-256 equals `PerceptionRequest.artifact_sha256`, the correlated `PerceptionResult`, operation/reconciliation outcome, and safe landing.

WP4 rehearsal acceptance command (implementation work; this specification fabricates no result): `uv run python -m pytest tests/worker tests/integration -q -k "tello_rehearsal"`. G4 remains blocked until the real rehearsal evidence exists.
