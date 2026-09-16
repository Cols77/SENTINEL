# SENTINEL WP2 — NiceGUI Human Surface and Mountain State

> **Status:** Revised after independent review; re-review pending
> **Owner:** Alan
> **Prerequisite:** WP0 contract revision `sentinel.v0` and WP1 `CoordinatorPort`

## 1. Objective

Build a local NiceGUI operator console: ChatGPT-like interaction plus a grown-up mission-control projection. It renders coordinator facts and submits typed commands; it owns no factual state, event sequence, policy or SQLite access.

## 2. Required boundary

The UI consumes only:

```text
read_state(run_id) -> https://sentinel.local/contracts/sentinel-v0-wire.schema.json#StateSnapshot
read_events(run_id, after_sequence, limit) -> #EventEnvelope[]
read_mountain(run_id) -> #MountainStateSnapshot
read_evidence(artifact_id) -> #ArtifactRef
read_hazard(target_id) -> #HazardReadModel
read_approval(task_id) -> #ApprovalReadModel
read_perception(result_id) -> #PerceptionResult
read_video_status(run_id) -> #VideoStatus
open_live_video(run_id) -> #SafeVideoStreamHandle
submit(https://sentinel.local/contracts/sentinel-v0-wire.schema.json#CommandEnvelope) -> #ResultEnvelope
```

NiceGUI begins in-process behind a `CoordinatorClient` protocol. A future HTTP client must implement the same calls. No UI code opens SQLite, reads raw artifact paths, calls the Tello SDK or changes thresholds.

## 3. Required layout

```text
Header: run ID / state version / freshness / LIVE-SIMULATED-REPLAYED badge
Left:   operator chat and structured intent/tool activity
Center: WP0 three-section mountain schematic
Right:  mission state, evidence/provenance, ranking and Confirm/Reject
Bottom: event timeline plus failure/emergency banner
```

A viewport at 1440×900 must show all five regions without horizontal scrolling. Structured statuses/actions are shown; private chain-of-thought is never rendered.

## 4. Mountain and freshness projection

Use [`contracts/mountain-v0.fixture.json`](contracts/mountain-v0.fixture.json) and validate its geometry against [`contracts/mountain-v0.schema.json`](contracts/mountain-v0.schema.json). Render the five persisted section statuses (`UNSURVEYED`, `SURVEYING`, `SURVEYED`, `HAZARD_PENDING`, `BLOCKED`) and the derived freshness flag separately; `STALE` is a label/badge, never a persisted status. Demonstrate the parent mapping in a fixture test.

A snapshot is stale if the coordinator marks it stale, `now - as_of` exceeds fixture `snapshot_stale_after_ms`, or received `latest_event_sequence > snapshot_version`. The UI preserves the last snapshot with a `STALE` label, requests refresh, and never synthesizes missing transitions.

Clicking a section or hazard calls `read_hazard` and exposes the linked observation/artifact/provenance. Evidence access is only through `read_evidence`.

## 5. Approval and failure interaction

Before decision buttons are enabled, UI displays the approval read model, artifact hash/source, provider profile/model/prompt/transform revision, raw score/semantics, selected threshold, box if present, ranking and expected state version.

Confirm/Reject each create a full `CommandEnvelope` with fresh `command_id`, correlation ID and idempotency key. Duplicate click returns the original result; state-version rejection forces refresh.

Emergency disables ordinary controls. `IN_DOUBT`, stale, simulated, replayed and failed evidence remain visibly labelled. Replay cannot be selected in a judged run. WP2 opens only `SafeVideoStreamHandle.stream_url`, renders typed `VideoStatus`, and visibly degrades on `DEGRADED` or `UNAVAILABLE`; it never receives raw frames, bytes, or storage paths. A judged run displays `LIVE` status and selected-frame artifact/hash correlation before perception/approval.

## 6. Acceptance criteria

- Fixture-driven unit tests render every section state and click-through read path.
- `uv run python -m sentinel.ui --smoke --fixture fixtures/sentinel/v0/run-approval.json` is the required smoke command. Its test harness is Playwright launched by `tests/ui/test_smoke.py` against the local NiceGUI process with the fake `CoordinatorClient`; it sets a 1440×900 viewport, asserts each named Header/Left/Center/Right/Bottom region has a visible bounding box within that viewport and asserts no document horizontal overflow. It writes the captured full-page screenshot to `artifacts/ui-smoke/1440x900.png` and emits `UI_SMOKE_OK` only after those assertions.
- Confirm, Reject, stale version, duplicate idempotency, evidence-missing, `IN_DOUBT` and emergency states have direct tests. The smoke test also uses a fake `VideoStatus`/`SafeVideoStreamHandle` to assert LIVE rendering, selected-frame correlation, and visible `DEGRADED` fallback without raw-frame access.
- Tests use fake `CoordinatorClient`; no network, drone or provider credential is required.

## 7. Suggested paths and non-goals

```text
src/sentinel/ui/{app.py,coordinator_client.py,mountain_projection.py,presenters.py}
tests/ui/{test_mountain_projection.py,test_operator_actions.py,test_smoke.py}
```

No GIS, 3D terrain, direct SDK/DB access, map authoring, multi-user auth, notification delivery or autonomous approval belongs in WP2.
