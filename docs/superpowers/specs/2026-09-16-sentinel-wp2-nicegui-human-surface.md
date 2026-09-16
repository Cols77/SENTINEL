# SENTINEL WP2 — NiceGUI Human Surface and Mountain State

> **Status:** Revised after independent review; re-review pending
> **Owner:** Alan
> **Prerequisite:** WP0 contract revision `sentinel.v0` and WP1 `CoordinatorPort`

## 1. Objective

Build a local NiceGUI operator console: ChatGPT-like interaction plus a grown-up mission-control projection for the approved indoor-alley V0. It renders coordinator facts and submits typed commands; it owns no factual state, event sequence, policy or SQLite access. The visible V0 flow is catalogue-plan proposal, immutable operator approval, then coordinator dispatch.

## 2. Required boundary

The UI consumes only:

```text
read_state(run_id) -> https://sentinel.local/contracts/sentinel-v0-wire.schema.json#StateSnapshot
read_events(run_id, after_sequence, limit) -> #EventEnvelope[]
read_mountain(run_id) -> #MountainStateSnapshot
read_evidence(artifact_id) -> #ArtifactRef + #EvidenceContentHandle
read_hazard(target_id) -> #HazardReadModel
read_approval(task_id) -> #ApprovalReadModel
read_plan(plan_id) -> #PlanReadModel
read_preflight(run_id) -> #PreflightReport
read_perception(result_id) -> #PerceptionResult
read_video_status(run_id) -> #VideoStatus
open_live_video(run_id) -> #SafeVideoStreamHandle
submit(https://sentinel.local/contracts/sentinel-v0-wire.schema.json#CommandEnvelope) -> #ResultEnvelope
```

NiceGUI begins in-process behind a `CoordinatorClient` protocol. A future HTTP client must implement the same calls. No UI code opens SQLite, reads raw artifact paths, calls the Tello SDK, changes thresholds, resolves catalogue hashes, or dispatches directly.

## 3. Required layout

```text
Header: run ID / state version / freshness / LIVE-SIMULATED-REPLAYED badge / configured marker-or-route-only mode
Left:   operator chat and structured intent/tool activity
Center: WP0 six-zone indoor-alley schematic and zone-level progress
Right:  mission state, evidence/provenance, ranking and Confirm/Reject
Bottom: event timeline plus failure/emergency banner
```

A viewport at 1440×900 must show all five regions without horizontal scrolling. Structured statuses/actions are shown; private chain-of-thought is never rendered.

## 4. Mountain and freshness projection

Use the WP0 indoor-alley six-zone fixture and its exact read-model/schema contract. Render the six fixed zones (`LEFT_ALLEY_FRONT`, `LEFT_ALLEY_MIDDLE`, `LEFT_ALLEY_BACK`, `RIGHT_ALLEY_FRONT`, `RIGHT_ALLEY_MIDDLE`, `RIGHT_ALLEY_BACK`) and the persisted statuses supplied by WP0; do not revive the fictional three-section mountain. In `ROUTE_ONLY_2_ZONE`, show only the two active middle zones and visibly label captures `route-only, not visually verified`. Marker mode never silently falls back when an expected marker is missing. Render the derived freshness flag separately; `STALE` is a label/badge, never a persisted status. Demonstrate the parent mapping in a fixture test.

A snapshot is stale if the coordinator marks it stale, `now - as_of` exceeds fixture `snapshot_stale_after_ms`, or received `latest_event_sequence > snapshot_version`. The UI preserves the last snapshot with a `STALE` label, requests refresh, and never synthesizes missing transitions.

Clicking a section resolves it to its linked hazards via `read_hazard(target_id)` and exposes the linked observation/artifact/provenance; a target with no linked hazard returns the stable `NOT_FOUND` error code, which the UI renders as an explicit "no linked hazard" state rather than synthesizing one. Evidence access is only through `read_evidence`, which returns the typed `ArtifactRef` plus an `EvidenceContentHandle`; a missing artifact likewise surfaces `NOT_FOUND`.

## 5. Approval and failure interaction

Before plan or task decision buttons are enabled, UI reads and displays the immutable catalogue plan model via `read_plan(plan_id)` together with its `PLAN_*` lifecycle state (proposed → approved → dispatched; `REJECTED` is event-only and never terminal), the typed `PlanApproval` binding plan hash/worker/run-state/expiry, the approval read model, artifact hash/source, provider profile/model/prompt/transform revision, raw score/semantics, selected threshold, box if present, ranking and expected state version. It also displays the worker’s typed immutable `PreflightReport` and its exact eight-check list — `WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH` — plus whether the run is eligible to dispatch. The UI renders only the coordinator-declared `legal_operator_actions`; those always include the gated `FORCE_RUN_CREATE` (operator-only, from a failed preflight, unavailable under an active emergency latch) and `EMERGENCY_STOP`, and every gated action is disabled unless the coordinator lists it as legal for the current state. The UI cannot edit these facts and has no hardware or physical-correction path.

Each Confirm/Reject action attempt creates one full `CommandEnvelope` with a fresh `command_id`, correlation ID and idempotency key. Until that attempt receives a terminal result or the UI refreshes after a state-version rejection, retries and duplicate clicks resend that same envelope/key and return the original result. A new user action after refresh creates a new envelope/key.

Emergency has a permanently visible one-click `EMERGENCY STOP` control and keyboard fallback, disables ordinary controls immediately, and displays latch acknowledgement. `IN_DOUBT`, stale, simulated, replayed and failed evidence remain visibly labelled. Replay cannot be selected in a judged run. WP2 opens only `SafeVideoStreamHandle.stream_url`, renders typed `VideoStatus`, and visibly degrades on `DEGRADED` or `UNAVAILABLE`; it never receives raw frames, bytes, or storage paths. A judged run displays `LIVE` status and selected-frame artifact/hash correlation before perception/approval. A failed preflight exposes an operator-only `FORCE_RUN_CREATE` decision through the typed coordinator command: it records the failed report and operator reason, starts a new run, never resumes the old operation, and is unavailable while the emergency latch is active.

## 6. Acceptance criteria

- Fixture-driven unit tests render every fixed zone state and click-through read path, including marker and route-only facts.
- `uv run python -m sentinel.ui --smoke --fixture fixtures/sentinel/v0/run-approval.json` is the required smoke command. Its test harness is Playwright launched by `tests/ui/test_smoke.py` against the local NiceGUI process with the fake `CoordinatorClient`; it sets a 1440×900 viewport, asserts each named Header/Left/Center/Right/Bottom region has a visible bounding box within that viewport and asserts no document horizontal overflow. It writes the captured full-page screenshot to `artifacts/ui-smoke/1440x900.png` and emits `UI_SMOKE_OK` only after those assertions. Because this smoke screenshot is the G2 human-surface evidence artifact, it is always retained on a passing run (`artifacts/ui-smoke/1440x900.png`); it is not deleted on success. Teardown never removes a passing G2 artifact.
- Confirm, Reject, stale version, duplicate idempotency (including identical transmitted key on duplicate click), evidence-missing, `IN_DOUBT` and emergency states have direct tests. The smoke test also uses a fake `VideoStatus`/`SafeVideoStreamHandle` to assert LIVE rendering, selected-frame correlation, and visible `DEGRADED` fallback without raw-frame access.
- Direct client tests cover `read_plan(plan_id)`, `read_preflight(run_id)`, typed `PreflightReport` rendering, and operator-only `FORCE_RUN_CREATE` semantics (new run, failed-report/reason recorded, no resume, disabled under emergency).
- The smoke harness launches the local NiceGUI process on an OS-assigned port, waits at most 15 seconds for one machine-readable `UI_READY http://127.0.0.1:<port>` line, connects Playwright to that URL, and always terminates and reaps the process in teardown. The passing G2 screenshot `artifacts/ui-smoke/1440x900.png` is always retained. `SENTINEL_KEEP_UI_SMOKE_ARTIFACTS=1` additionally keeps the screenshot on an assertion failure; otherwise teardown removes a failed run's screenshot.
- Tests use fake `CoordinatorClient`; Alan owns the fake-agent runtime/UI-client test seam, while coordinator persistence remains Colin’s WP1 responsibility. No network, drone or provider credential is required.

## 7. Suggested paths and non-goals

```text
src/sentinel/ui/{__main__.py,app.py,coordinator_client.py,mountain_projection.py,presenters.py}
tests/ui/{test_mountain_projection.py,test_operator_actions.py,test_smoke.py}
```

Each named region (Header/Left/Center/Right/Bottom) and every state/action widget exposes a stable `data-testid` identifier so the Playwright smoke harness and unit tests address elements by region, never by pixel or transient label.

No GIS, 3D terrain, direct SDK/DB access, map authoring, route planning, dynamic correction, physical command retry, multi-user auth, notification delivery or autonomous approval belongs in WP2. The numbered planned-route overlay, capture-point overlay and drawn revisit proposal are deferred to V0.1; any later overlay must render coordinator facts only and never calculate or command flight movement.
