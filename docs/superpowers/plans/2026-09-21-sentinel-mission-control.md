# SENTINEL Mission Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the existing console to the published integration runtime, first completing an existing survey scenario without a drone, then adding localization presentation and Live adapters through the same application interfaces.

**Architecture:** One MissionControlService owns a persisted UI journal, projection, bounded chat, approval broker and guarded mission worker. Both modes execute the real survey loop, candidate selection, merge and report validation; startup selects hardware/provider boundary adapters. The existing vanilla frontend is served by a local NiceGUI/FastAPI host and consumes typed HTTP responses.

**Tech Stack:** Python 3.11+, NiceGUI/FastAPI, SQLite WAL, vanilla ES modules, inline SVG, pytest, Node tests and Playwright. No JavaScript build system.

**Spec:** [Approved Mission Control design](../specs/2026-09-21-sentinel-mission-control-design.md), approved by Alan after revision `4625fdc`.

**Execution:** Preserve Alan's previously selected **Native** method: implementation in this session, followed by one fresh independent whole-branch review. This revised plan awaits user review before implementation.

## Global Constraints

- “Dry-run and Live use the same application interfaces and production orchestration; deterministic adapters replace external hardware and provider boundaries in dry-run.”
- “The existing frontend is preserved.”
- “No sibling-worktree dependency is allowed.”
- “The canonical frontend URL is `/console/`: `/` redirects there and static serving supplies its index.”
- “The browser decision port retains `submit_decision(attempt)`”.
- “Mission admission is atomic: a second distinct mutating mission is refused while one is active”.
- “Emergency first latches the active runner and prevents subsequent normal command dispatch, then attempts the independent emergency transport.”
- “There is no automatic switch from a failed Live session.”
- “Minimum body text is 16 px and metadata is at least 14 px for projector readability.”
- Event polling defaults to 500 ms while visible, backs off while hidden, and never overlaps. Display frames are bounded to 5 Hz. Pose stale threshold defaults to 2000 ms; AGING starts at half-threshold and STALE only above threshold.
- Complete persisted chat messages; no token streaming, voice, React, draggable panels, browser configuration or direct chat flight authorization.
- Rehearsal requires no hardware, network or provider credentials. Live hardware checks remain outside automated execution.
- Preserve all existing decision/evidence regression coverage. No raw paths, secrets, image bytes, private prompts or provider payloads in public snapshots/events.

## Review Focus

1. Two tabs reuse an idempotency key with different payloads: conflict without a second action (Tasks 1–2).
2. Pose-age polling advances display versions while a proposal waits: approval stays usable until authoritative decision state changes (Tasks 1–2, 9).
3. Emergency arrives during blocked flight or database activity: latch and independent send remain responsive, even if auditing must reconcile later (Task 3).
4. A valid image capability is revoked while a previous request is in flight: remove stale imagery and reject late results (Tasks 5–6).
5. A browser response is lost after approval was committed: reload/retry returns the same result without another mission or approval (Tasks 2, 6, 8).

## Verified integration and file ownership

At planning time the published integration reference is `6d7f24b641feb94188d15507a10da5ec65ae1aaa`; fetch again at execution. PR #8 contains the frontend and docs; PR #7 remains its asset foundation. Merge published ancestry without rewriting either branch.

Existing runtime seams:

- `survey.loop.run_survey`: keyword arguments `store, run_id, request_text, options, policy, flight, detector, on_approval, writer`; optional consent/camera/flight-budget arguments. Approval receives the full rendered candidate call and returns a boolean.
- `survey.proposer.RuleProposer.decide(request, context)`: deterministic legal-candidate selection. Other survey proposer implementations share this seam.
- `survey.flight.FlightPort.fly(option) -> list[FlightFrame]`; `ScriptedDetector.detect(image_path, classes)` provides deterministic detector responses.
- `survey.cube_bridge.CubeFlight` composes `CubeMissionRunner` and uses the measured camera model. Do not replace it with an unrelated runner wrapper.
- `SurveyStore` owns SQL writes; add explicit backend read methods instead of exporting its connection.
- `runner.trigger_emergency()` currently latches then calls the client. `EmergencySender.send()` is independent transport only and has no runner latch.
- `coordinator.agent_loop.AgentLoop` exposes coordinator tools, not a drop-in survey chat session. Add a survey-specific bounded adapter around the adopted survey policy; do not silently map coordinator tools to survey actions.

New files and responsibilities:

| Files | Responsibility |
|---|---|
| `src/sentinel/ui/models.py`, `schema.py` | Strict public payloads, validation and command/result types |
| `src/sentinel/ui/store.py` | UI sessions/runs, commands, offers, messages and ordered event journal |
| `src/sentinel/ui/approval.py`, `mission_worker.py` | Persisted identity-bound decisions and single active mission admission |
| `src/sentinel/ui/safety.py` | Session/run binding and public runner safety ports |
| `src/sentinel/ui/scenarios.py`, `fixtures/mission-control/clean-computer.json` | Reusable dry-run inputs and valid synthetic artifact generation |
| `src/sentinel/ui/runtime.py`, `service.py`, `projection.py` | Shared survey execution, service facade and allowlisted complete snapshots |
| `src/sentinel/ui/chat.py`, `decision_session.py` | Persisted conversation and bounded legal intent adapter |
| `src/sentinel/ui/app.py`, `api.py`, `__main__.py`, `bootstrap.py` | Local host, HTTP routes and immutable mode composition |
| `src/sentinel/ui/pose_projection.py`, `src/sentinel/pose/evidence.py` | Backend freshness and exact-frame diagnostic binding |
| `examples/survey-console/http-client.mjs`, `mission-contract.mjs`, `view-model.mjs` | HTTP boundary, v2 validation and explicit renderer adaptation |
| `examples/survey-console/chat.mjs`, `mission-map.mjs`, `localization.mjs`, `safety.mjs` | New focused views using existing DOM utilities |
| `tests/ui/`, existing `tests/survey_console/`, `scripts/ui_assets/verify_mission_control.cjs` | Runtime/API, frontend and real-host acceptance |

Keep `contract.mjs` and the original fixture verifier usable as a v1 regression harness. New v2 consumers use `mission-contract.mjs`; `view-model.mjs` maps backend fields to existing count/evidence render inputs without calculating authoritative facts. Product startup never selects the old snapshot-advancement fixture client.

## Delivery checkpoints

- **A — Shared-runtime dry-run:** Tasks 0–8. One real survey reaches a validated report through the browser; safety uses fake transports and runtime latches.
- **B — Broader scenarios and localization:** Tasks 9–10. Full visual and failure acceptance.
- **C — Live adapter composition:** Tasks 11–12. Contract/startup tests, whole-branch review and PR handoff. Hardware verification is separate.

---

### Task 0: Incorporate published runtime and record the baseline

**Files:** merge incoming integration files; create `docs/handoffs/2026-09-21-mission-control.md` for exact refs, command results and later acceptance evidence.

**Interfaces:** consumes published Git refs; produces a merged branch with survey/cube/pose seams above and a reproducible baseline.

- [ ] Fetch and inspect ancestry, then merge without force-pushing. Run from the clean isolated worktree; preserve the user's docs commits.

```bash
git fetch origin integration/survey-demo:refs/remotes/origin/integration/survey-demo
git rev-parse HEAD origin/integration/survey-demo
git merge --no-ff origin/integration/survey-demo
rg -n 'class SurveyStore|def run_survey|class CubeMissionRunner|def run_pose_stream|def trigger_emergency' src/sentinel
uv sync --all-extras
uv run pytest -q --ignore=fixtures
node --test tests/survey_console/*.test.mjs
```

Expected: all required seams exist; Node baseline has 45 passing tests before new additions. Record actual Python counts/skips instead of assuming the pre-integration count. Missing or materially incompatible seams block dependent work; do not substitute local sibling files. Resolve ordinary merge conflicts preserving both implementations.

- [ ] Commit the baseline handoff after the merge and successful checks.

```bash
git add docs/handoffs/2026-09-21-mission-control.md
git commit -m "docs: record mission control integration baseline"
```

### Task 1: Define the public contract and durable journal

**Files:** create `src/sentinel/ui/__init__.py`, `models.py`, `schema.py`, `store.py`; create `tests/ui/__init__.py`, `conftest.py`, `test_models.py`, `test_store.py`; create `examples/survey-console/mission-contract.mjs`, `tests/survey_console/mission-contract.test.mjs`, `fixtures/mission-control/contract-v2.json`.

**Interfaces:** `validate_snapshot(dict) -> dict`; JS `validateMission(snapshot) -> snapshot`; `UIStore.open(path)`; `ensure_run(session_id, run_id, mode)`, `read_run(run_id)`, `append_event(run_id, event_type, payload)`, `read_events(run_id, after, limit)`, `claim_command(command)`, `settle_command(command_id, result)`, `read_command(command_id)`, `publish_snapshot(run_id, snapshot)`, `read_snapshot(run_id)`. All store returns are detached dictionaries. Test modules explicitly import pytest, the named production symbols, and threading/concurrent-futures helpers used in their snippets; Node modules import node:test, node:assert/strict and the named exports. `contractFixture` is loaded from `fixtures/mission-control/contract-v2.json` with the repository-relative URL resolved from the test file. Command claims return `{created: bool, command: dict}` or raise `CommandConflict`.

- [ ] Write strict contract and concurrent identity tests. `ui_store` is a temporary-file store fixture defined in this task. `valid_snapshot` loads the checked-in contract JSON; every nested shape is closed, not a permissive pass-through.

```python
def test_unknown_public_field_is_rejected(valid_snapshot):
    valid_snapshot["evidence"]["image_path"] = "/private/frame.png"
    with pytest.raises(ValueError):
        validate_snapshot(valid_snapshot)


def test_same_key_different_payload_conflicts(ui_store):
    first = dict(session_id="s", run_id="r", command_id="c", correlation_id="x",
                 idempotency_key="k", expected_version=0, kind="CHAT", text="count computers")
    assert ui_store.claim_command(first)["created"]
    assert not ui_store.claim_command(first)["created"]
    with pytest.raises(CommandConflict):
        ui_store.claim_command({**first, "text": "land"})
```

- [ ] Run `uv run pytest tests/ui/test_models.py tests/ui/test_store.py -q`; confirm failures identify missing implementation.
- [ ] Implement the schema and journal. These names/types are the shared boundary for all later tasks:

```python
@dataclass(frozen=True)
class EvidenceCapability:
    status: str  # AVAILABLE, NOT_FOUND, FORBIDDEN, REVOKED
    run_id: str
    artifact_id: str
    media_type: str | None = None
    payload: bytes | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    reason_code: str | None = None


class CommandConflict(ValueError):
    pass
```

Contract v2 has exactly `schema_version=2` plus the spec's 13 units. Define closed nested models in `schema.py` and mirror their keys/enums in JS:

- `session`: `id, mode, startup_summary`; the summary contains `site_id, pose_mode, detector_name`, no private paths.
- `run`: `id, request_text, status, phase, version, decision_version, updated_at`. Status includes ACTIVE, COMPLETE, ABORTED, RECOVERY_REQUIRED. Phase uses the existing seven UI steps. `version` changes for any new complete snapshot; `decision_version` changes only when command legality/identity changes. Approval `expected_version` compares to `decision_version`, not display polling age. Map this value explicitly into approval controls; preserve the snapshot display version for reconciliation. Increment decision_version on proposal replacement, eligibility changes and settlement, never just on age refresh.
- `source`: `mode, kind, freshness`; mode REHEARSAL/LIVE, kind SIMULATED/REPLAYED/LIVE, freshness FRESH/STALE. Mode must match session.
- `safety`: `land, emergency`; each has `available, status, reason, command_id`. Status IDLE/REQUESTED/ACKNOWLEDGED/COMPLETED/UNAVAILABLE/FAILED. An emergency transport send alone never implies COMPLETED.
- `chat`: `messages, pending_command_id`; message `id, run_id, sequence, role, body, status, snapshot_version` with COMPLETE/FAILED status, inert public text.
- `proposal`: null or the complete existing v1 Proposal plus `run_id, decision_version, correlation_id`; retain geometry, justification, eligibility and hash.
- `mission`: `flights, sweep, envelope, route, active_stop_id, commanded_position`; existing v1 flights/sweep/stop fields remain, with backend map coordinates on separate route entries `stop_id,x,y` and envelope `min_x,max_x,min_y,max_y`. Unknown coordinates are null, not zero.
- `localization`: `status, age_ms, stale_after_ms, latest_report, recent_stream, containment, pose_evidence, map_position`. Report allowlist contains run/report identity, observation time, source, point, heading, altitude, method, tag observations and error. The Task 9 diagnostic defines pose evidence; absent report/evidence/map position is null. Stream counters use the five fields in the spec.
- `evidence`: `frames, sightings, objects, analysis, provider, failures`; preserve existing strict v1 fact shapes, extend frames with `artifact_id,sha256,width,height,source_kind`. New artifact fields may be null only when evidence is unavailable.
- `results`: `counts`; retain v1 count semantics without deriving counts in JS.
- `report`: existing v1 `status,text,reason` plus `run_id,id` (id null when absent).
- `events`: `watermark`; event pages retain existing `id,run_id,sequence,occurred_at,summary`, plus allowlisted `type,refs` where refs is a list of `{kind,id}` within that run.
- `legal_actions`: list of `{action,enabled,reasons}`, actions CHAT/APPROVE/REJECT/LAND/EMERGENCY; this list never substitutes for backend checks.

Use explicit validators for nested types, finite numbers, enum values, unique identities and run/frame/object/stop references. Factor existing JS fact validators into named exports as needed without weakening v1. Contract fixture JSON is shared by Python and Node tests; include one invalid mutation per nested unit and a cross-run reference case.

Store tables: `ui_sessions(session_id PK, mode, config_json)`, `ui_runs(run_id PK, session_id, status, version, decision_version, snapshot_json)`, `ui_events(run_id,sequence_no PK pair,event_id UNIQUE,event_type,public_payload_json,occurred_at)`, `ui_commands(command_id PK,session_id,run_id,idempotency_key UNIQUE,kind,request_json,status,result_json,created_at)`, `ui_messages(message_id PK,run_id,sequence_no UNIQUE per run,role,body,status,snapshot_version)`, `ui_offers(proposal_id PK,run_id,proposal_sha256,decision_version,correlation_id,payload_json,status)`. Parameterize SQL, WAL, foreign keys, one connection per thread, `busy_timeout=5000`, short `BEGIN IMMEDIATE` writes. Atomic claims compare canonical request JSON before returning prior results. Snapshot publication atomically increments version and records its watermark. Never hold a transaction while waiting on approval/provider/hardware.

- [ ] Add two-connection concurrent command/event tests and snapshot monotonicity/reload tests; run Python and Node contract suites to PASS.
- [ ] Commit: `git add src/sentinel/ui tests/ui fixtures/mission-control examples/survey-console/mission-contract.mjs examples/survey-console/contract.mjs tests/survey_console/mission-contract.test.mjs && git commit -m "feat(ui): define durable mission control contract"`.

### Task 2: Persist approvals and refuse concurrent missions

**Files:** create `src/sentinel/ui/approval.py`, `mission_worker.py`, `tests/ui/test_approval.py`, `test_mission_worker.py`.

**Interfaces:** `ApprovalBroker(store).offer(run_id, candidate, correlation_id) -> PendingApproval`; `PendingApproval.wait() -> bool`; `ApprovalBroker.decide(attempt) -> dict`; `recover_interrupted_runs() -> None`; `MissionWorker(run_callable).start(request) -> Future`. `run_callable(request)` creates its own thread-owned stores. `MissionBusy` is a public exception mapped to HTTP 409. Attempts retain existing `attempt_id` and add `session_id,command_id,correlation_id`; commands persist all three identities unchanged.

- [ ] Write tests for exact proposal/hash/decision-version binding, reload, duplicate attempts, lost response reconciliation, unrelated age refresh, replacement, rejection and process recovery. Use synchronization Events rather than sleeps.

```python
def test_worker_refuses_second_active_mission():
    entered, release = Event(), Event()
    def run(request):
        entered.set()
        assert release.wait(2)
        return request
    worker = MissionWorker(run)
    first = worker.start({"run_id": "r"})
    assert entered.wait(1)
    try:
        with pytest.raises(MissionBusy):
            worker.start({"run_id": "other"})
    finally:
        release.set()
    assert first.result(timeout=1)["run_id"] == "r"
```

- [ ] Run the two new test files and confirm missing-class failures.
- [ ] Implement atomic offer/decision journal updates before event publication or waiter notification. A changed decision version yields CONFLICT; a changed display version alone does not. A settled identical attempt returns its persisted result. Reusing an identity with another decision conflicts. Waiters exist only in this process; startup marks interrupted Live work RECOVERY_REQUIRED and terminalizes outstanding waiters without dispatch.

```python
class MissionBusy(RuntimeError):
    pass

class MissionWorker:
    def __init__(self, run_callable):
        self._run = run_callable
        self._lock = Lock()
        self._active = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentinel-mission")

    def start(self, request):
        with self._lock:
            if self._active:
                raise MissionBusy("MISSION_BUSY")
            self._active = True
        try:
            return self._pool.submit(self._execute, request)
        except BaseException:
            with self._lock:
                self._active = False
            raise

    def _execute(self, request):
        try:
            return self._run(request)
        finally:
            with self._lock:
                self._active = False
```

Add `close()` that shuts down the executor after the service resolves/cancels pending waiters; test teardown cannot leave a blocked worker. Service command claims precede admission, so same-command retries read the existing result rather than call start again. A busy rejection settles that command as CONFLICT rather than leaving it pending.

- [ ] Run `uv run pytest tests/ui/test_approval.py tests/ui/test_mission_worker.py tests/ui/test_store.py -q`; expect PASS.
- [ ] Commit: `git add src/sentinel/ui tests/ui && git commit -m "feat(ui): bind durable approvals and mission admission"`.

### Task 3: Establish shared safety semantics with fake transports

**Files:** modify `src/sentinel/runner.py`, `src/sentinel/cube/mission.py`; create `src/sentinel/ui/safety.py`, `tests/ui/test_safety.py`; extend `tests/test_runner.py` and `tests/cube/test_mission.py`.

**Interfaces:** public `FlightTestRunner.latch_emergency() -> bool` atomically sets emergency state, returning whether newly latched; existing `trigger_emergency()` preserves compatibility by using the latch then its current send path. Add `request_controlled_land()` and an explicit controlled-terminal exception/state. `SafetyControlPort.request_land(run_id, command_identity) -> dict`; `trigger_emergency(session_id, command_identity, active_run_id=None) -> dict`. `RunnerSafetyAdapter` receives a thread-safe active-runner registry, independent sender, session ID and audit sink; `SafetyRegistry.bind(run_id, runner)`, `unbind(run_id, runner)` and `get(run_id)` are defined in this file.

- [ ] Write race tests using injected send callbacks and Events. A fake sender asserts the latch before recording a send; then a send failure must leave the runner unable to move. Test no active runner, duplicate identity, blocked normal transport and held UI database writer.

```python
def test_emergency_latch_precedes_independent_send(runner, registry, identity):
    registry.bind("r", runner)
    def send():
        assert runner.emergency_triggered
        raise OSError("offline")
    adapter = RunnerSafetyAdapter(registry, send, "s", audit_sink=lambda result: None)
    result = adapter.trigger_emergency("s", identity, "r")
    assert result["status"] == "FAILED"
    assert runner.emergency_triggered
    with pytest.raises(EmergencyAborted):
        runner.move_checked("forward", 20)
```

Here `identity` is `{command_id,correlation_id,idempotency_key}`; fixture `runner` uses the existing DryRunClient and existing runner test construction. Define both in `tests/ui/conftest.py`. Reuse existing runner test fixtures where importable; do not send a socket packet.

- [ ] Run focused tests to failure; implement latch-before-independent-send with no normal transport or SQLite lock on the critical path. Use an in-memory command ledger for immediate same-process reconciliation and a separate queued audit writer; audit failure is visible and retryable without claiming the physical outcome. Persisted restart state is RECOVERY_REQUIRED, not assumed exactly-once delivery across crashes.

```python
runner = registry.get(active_run_id) if active_run_id else None
if runner is not None:
    runner.latch_emergency()
try:
    bytes_sent = independent_send()
    result = {"status": "ACKNOWLEDGED", "bytes_sent": bytes_sent}
except OSError:
    result = {"status": "FAILED", "reason_code": "EMERGENCY_SEND_FAILED"}
```

This is the critical section within the adapter, after identity/session validation and duplicate lookup. Also handle missing transport as UNAVAILABLE. A transport error cannot clear the latch. The registry also retains a session emergency latch when no runner exists; mission admission and runner registration atomically refuse after it is set. Safety cancellation releases any approval waiter with a terminal outcome, without accepting the proposal. Ensure emergency and controlled-land latches participate in every runner and Cube override's normal-command check. Land latches before dispatch; a distinct terminal outcome ends the mission without being relabelled emergency. Do not hold a normal command lock while waiting for its transport response; a command already sent may finish, but no subsequent command may start after the safety latch wins. Emergency remains usable after Land.

- [ ] Run `uv run pytest tests/ui/test_safety.py tests/test_runner.py tests/cube/test_mission.py -q`; expect PASS including compatibility tests.
- [ ] Commit: `git add src/sentinel/runner.py src/sentinel/cube/mission.py src/sentinel/ui/safety.py tests && git commit -m "feat(safety): bind independent emergency to runner state"`.

### Task 4: Execute the clean scenario through the production survey loop

**Files:** create `src/sentinel/ui/scenarios.py`, `runtime.py`, `fixtures/mission-control/clean-computer.json`, `tests/ui/test_runtime.py`; modify `src/sentinel/survey/loop.py`, `store.py`; extend `tests/survey/test_loop.py`.

**Interfaces:** `ScenarioAdapters` frozen dataclass contains `options,policy,flight,detector,writer,camera,source_kind`; `build_rehearsal_adapters(scenario_path, artifact_root) -> ScenarioAdapters`. `SurveyRuntime(survey_db, ui_db, adapters, approval, safety_registry).execute(request) -> dict`. Add optional `emit(event_type, public_payload)` to `run_survey` and its ingestion helper; default None preserves existing callers. Add `SurveyStore.read_run`, `read_sweeps`, `read_frames_public`, `read_objects_public`, `read_report`, all run-scoped and allowlisted.

- [ ] Promote clean scenario data from `test_a_clean_run_flies_once_counts_and_reports` into JSON inputs: existing envelope, four rail stops, one computer's scripted detections. Build actual PNG bytes with existing Pillow facilities, hash the bytes and use real dimensions. Product code must not import `_StaticFlight`, `_one_computer_script` or other test helpers. Scripted flight implements the existing FlightPort and yields valid FlightFrames; counts/report are never fixture outputs. Register a DryRunClient-backed runner for its active duration and check its safety latches before each simulated stop/capture. Simulated safety terminates the actual dry-run worker; it is not only a change to a UI badge. Worker admission consults the session emergency latch, and terminal cleanup unregisters the exact runner instance.
- [ ] Write the failing test using temporary stores, real broker, adapters and worker. `runtime_harness` is a fixture created in this task that builds those components, exposes `worker,broker,store`, and closes them on teardown. `wait_for_offer()` uses a publication Event, not polling sleep. `attempt_for(offer, decision)` supplies all immutable identity fields with fresh UUIDs.

```python
def test_clean_scenario_requires_approval_and_reports(runtime_harness):
    h = runtime_harness
    future = h.worker.start({"run_id": "clean", "request_text": "count the computers",
                             "correlation_id": "clean-request"})
    offer = h.wait_for_offer()
    assert h.flight_count() == 0
    h.broker.decide(h.attempt_for(offer, "APPROVE"))
    result = future.result(timeout=5)
    assert result["status"] == "REPORTED"
    assert result["counts"]["computer"]["confirmed"] == 1
    assert h.read_report()["body"] == result["report"]
```

- [ ] Run `uv run pytest tests/ui/test_runtime.py -q` to fail, then implement the actual call, opening SurveyStore inside the worker thread:

```python
outcome = run_survey(
    store=survey_store, run_id=request["run_id"], request_text=request["request_text"],
    options=adapters.options, policy=adapters.policy, flight=adapters.flight,
    detector=adapters.detector, writer=adapters.writer, camera=adapters.camera,
    on_approval=lambda candidate: approval.offer(
        request["run_id"], candidate, request["correlation_id"]
    ).wait(), emit=publish_runtime_event,
)
```

`publish_runtime_event` is `SurveyRuntime._publish_event(type,payload)`: it persists only the defined public lifecycle payloads and refreshes the projection after committed runtime changes. Never publish a merge/report completion before the associated store write succeeds. Emit proposal, decision, sweep start/finish, capture, analysis, merge and report result at their actual boundaries. Durable projection failures stop subsequent ordinary work and become explicit failure state; optional display callbacks may fail without breaking flight control. Translate runner controlled-land/emergency termination into ABORTED run state and stop subsequent capture, merge or report work; never turn a cancelled approval into authorization. Close connections in finally blocks.

- [ ] Test rejection with zero flights, no external connection constructor calls, reporter mismatch yielding REPORT_REJECTED, and worker-thread connection ownership. Run `uv run pytest tests/ui/test_runtime.py tests/survey -q` to PASS.
- [ ] Commit: `git add src/sentinel/ui src/sentinel/survey fixtures/mission-control tests/ui tests/survey && git commit -m "feat(ui): run rehearsal through production survey loop"`.

### Task 5: Serve real projections, evidence and the frontend

**Files:** create `src/sentinel/ui/projection.py`, `service.py`, `api.py`, `app.py`, `__main__.py`, `bootstrap.py`; modify `pyproject.toml`, `uv.lock`; create `tests/ui/test_projection.py`, `test_api.py`, `test_app.py`, `test_evidence.py`.

**Interfaces:** `ProjectionService.snapshot(run_id) -> dict`; `MissionControlService.session`, `read_snapshot`, `read_events`, `read_evidence`, `submit_chat`, `submit_decision`, `request_land`, `trigger_emergency`, `close`. Reads take the run ID and event cursor/limit or artifact ID as in the spec; commands take `(run_id, command)` except session emergency `(command)`. `submit_chat` is wired in Task 7; until then the route explicitly returns capability unavailable. `build_app(service, repository_root: Path) -> FastAPI`. `build_service(config) -> MissionControlService` initially supports Rehearsal and explicitly refuses Live until Task 11.

- [ ] Write host tests that resolve HTML references and load modules/CSS, rather than asserting only root HTTP 200. Add run-scoped artifact hash, revoked, missing and malformed-image tests.

```python
def test_console_assets_resolve(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/console/"
    assert client.get("/console/").status_code == 200
    for path in ("/console/app.mjs", "/console/console.css", "/assets/sentinel-ui/tokens/tokens.css"):
        assert client.get(path).status_code == 200
```

- [ ] Run focused tests to fail. Add NiceGUI to dependencies with a compatible locked release; inspect its installed supported `ui.run_with` integration before composing the FastAPI app. Bind localhost; do not enable On Air. Implement static routing:

```python
app.mount("/console", StaticFiles(directory=console, html=True), name="console")
app.mount("/assets/sentinel-ui", StaticFiles(directory=assets), name="assets")
@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/console/", status_code=307)
```

Projection uses public SurveyStore reads and UI journal state. Unknown phase details stay unavailable until supplied; never fabricate pose or infer stop completion from elapsed time. Store a coherent snapshot with its event watermark; increment display version when backend age/freshness changes. State changes and polling must not silently invalidate approval decision_version.

Evidence reads resolve artifact IDs only through the current run's authorization registry, verify stored hash/dimensions, enforce PNG/JPEG and a 10 MiB encoded size/20 megapixel decoded bound, and return `EvidenceCapability`. API AVAILABLE responses send only image bytes plus hash/dimensions headers; denial responses use safe structured JSON. Browser `frame_id` is mapped through the snapshot's frame-to-artifact reference, never treated as a filesystem path.

Route table is exactly spec §7. Event limit is 1–200. Missing resources use 404, denied 403, revoked 410, conflict 409, IN_DOUBT 202. Errors contain stable public code/message only. Use FastAPI sync/threadpool handlers for blocking store calls; do not execute synchronous provider or hardware work on the event loop. Safety critical processing uses its independent path from Task 3.

- [ ] Run `uv run pytest tests/ui/test_projection.py tests/ui/test_api.py tests/ui/test_app.py tests/ui/test_evidence.py -q`; expect PASS.
- [ ] Commit: `git add src/sentinel/ui tests/ui pyproject.toml uv.lock && git commit -m "feat(ui): host bounded runtime projections and evidence"`.

### Task 6: Adopt the HTTP client without losing controller guarantees

**Files:** create `examples/survey-console/http-client.mjs`, `view-model.mjs`, `tests/survey_console/http-client.test.mjs`, `view-model.test.mjs`; modify `controller.mjs`, `app.mjs`, `approval.mjs`, `evidence.mjs` and corresponding tests.

**Interfaces:** `createHttpClient(base, fetchImpl=fetch)` exposes `read_survey(runId)`, `read_survey_events(runId,after,limit)`, `read_evidence(runId,frameId)`, `submit_decision(attempt)`, `submit_chat(runId,command)`, `request_land(runId,command)`, `trigger_emergency(command)`. `toSurveyView(snapshot)` performs field/unit selection only; it does not infer authoritative state. Controller receives explicit contract selectors for v2 run/version/watermark, defaulting to v1 for legacy fixture tests.

- [ ] Write a one-argument decision test and a 409 test before client implementation:

```javascript
test("one-argument decision preserves conflict identity", async () => {
  const attempt = { run_id: "r", attempt_id: "a" };
  const client = createHttpClient("/api/v1", async (url, init) => {
    assert.equal(url, "/api/v1/runs/r/decisions");
    assert.deepEqual(JSON.parse(init.body), attempt);
    return new Response(JSON.stringify({attempt_id: "a", status: "CONFLICT", message: "Changed"}), {status: 409});
  });
  assert.equal((await client.submit_decision(attempt)).status, "CONFLICT");
});
```

- [ ] Run `node --test tests/survey_console/http-client.test.mjs` to fail. Implement the typed decision path; other errors remain explicit transport/capability errors:

```javascript
async function submit_decision(attempt) {
  const response = await fetchImpl(`${base}/runs/${encodeURIComponent(attempt.run_id)}/decisions`, {
    method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(attempt)
  });
  const body = await response.json();
  if (response.status === 409 && body.status === "CONFLICT" && body.attempt_id === attempt.attempt_id) return body;
  if (!response.ok) throw new Error(body.message ?? "Decision transport failed");
  return body;
}
```

The decision controller still validates the full result and tracks detached/stale attempts. Persisted snapshot/command state restores pending identities after reload; do not generate a replacement idempotency key on timeout. Add command/correlation/session identity to new attempts without rewriting outstanding ones.

Evidence client converts bounded binary bytes/headers to the existing EvidenceResult shape, validates identity and dimensions, and returns explicit forbidden/revoked outcomes. Preserve revocation, late-result, URL cleanup, malformed image and very-thin-image regression tests. Remove hardcoded fixture provenance strings in runtime views; use actual mode/source facts.

Poll only after previous refresh completes, schedule next visible refresh after 500 ms or hidden after 5000 ms; dispose cancels timer and discards stale responses. Preserve run epoch guards, conflicting-event detection, gap visibility, same-version mutation rejection and watermark monotonicity for both contracts.

- [ ] Add tests for 202 reconciliation, response loss followed by identical retry, polling overlap, run switch, HTTP denial/revocation and changed display versus decision version. Run `node --test tests/survey_console/*.test.mjs` to PASS.
- [ ] Commit: `git add examples/survey-console tests/survey_console && git commit -m "feat(ui): adopt runtime HTTP client and preserve reconciliation"`.

### Task 7: Connect persistent chat and inline approvals

**Files:** create `src/sentinel/ui/decision_session.py`, `chat.py`, `tests/ui/test_chat.py`, `test_decision_session.py`, `examples/survey-console/chat.mjs`, `tests/survey_console/chat.test.mjs`; modify service/API, app, shell and approval modules.

**Interfaces:** `DecisionReply` frozen dataclass fields `kind, message, request_text` where kind ANSWER/CLARIFY/PROPOSE/REFUSE and request_text is null unless PROPOSE. `SurveyDecisionSession.respond(text, snapshot) -> DecisionReply` wraps an injected bounded intent responder and validates its output. `ChatService.submit(run_id, command) -> dict`; `createChatView(host, client, getSnapshot)` returns `render(messages),submit(text),dispose()`.

- [ ] Write idempotent transcript, unsupported-input and no-chat-approval tests. `chat_harness` builds the real store/service with a scripted intent responder; its `submit(text,key)` constructs UUID identities and current decision version.

```python
def test_text_cannot_approve(chat_harness):
    h = chat_harness
    h.install_pending_proposal()
    reply = h.submit("approve it and take off", key="chat-1")
    assert reply["kind"] in {"CLARIFY", "REFUSE"}
    assert h.pending_proposal()["status"] == "PENDING"
    assert h.flight_count() == 0
```

- [ ] Run both new Python files to fail. Implement one persisted operator message before calling the responder, one complete public reply after it, and idempotent command settlement. Provider failure keeps the operator message and safe failure reply; duplicate delivery does not call the responder twice. On uncertain provider completion, retain a pending/failed public result rather than inventing another execution.

```python
reply = session.respond(command["text"], projection.snapshot(run_id))
if reply.kind == "PROPOSE":
    worker.start({"run_id": run_id, "request_text": reply.request_text,
                  "correlation_id": command["correlation_id"]})
```

This runs after identity claim and before command settlement. `respond` accepts arbitrary text but only legal supported intent becomes PROPOSE; unsupported/contradictory text clarifies or refuses. Rehearsal injects deterministic responder results for existing scenario prompts; Live injects the configured responder under identical response validation. Policy choice still occurs inside run_survey and validates existing legal candidates. Do not reuse coordinator AgentLoop's incompatible tools. Status/count answers cite snapshot version and stay responsive during approval waits; a second proposal uses guarded mission admission.

- [ ] Move the existing full proposal render and Approve/Reject controls into the single run transcript. Preserve immutable retry identity, stale disabling, typed result display, focus and full candidate geometry. Enter submits text; browser failures preserve composer text; duplicate send disabled only for its pending command; history inspection suppresses auto-scroll. Render Markdown as inert text for V1.
- [ ] Run `uv run pytest tests/ui/test_chat.py tests/ui/test_decision_session.py -q` and `node --test tests/survey_console/chat.test.mjs tests/survey_console/approval.test.mjs` to PASS.
- [ ] Commit: `git add src/sentinel/ui tests/ui examples/survey-console tests/survey_console && git commit -m "feat(ui): connect persistent chat and inline decisions"`.

### Task 8: Prove the first dry-run milestone through the real host

**Files:** create `scripts/ui_assets/verify_mission_control.cjs`, `examples/survey-console/safety.mjs`, `tests/survey_console/safety.test.mjs`; modify app/shell/CSS, README and handoff; extend API/integration tests.

**Interfaces:** `createSafetyView(host, client, getSnapshot)` renders availability and binds actions; Land uses one click, Emergency requires a 1200 ms pointer/keyboard hold with cancel on blur/release. Verifier accepts base URL and writes ignored artifacts. Existing `verify_survey_console.cjs` remains the isolated fixture regression suite.

- [ ] Write browser acceptance against a temporary real Rehearsal host: submit request, inspect proposal, reload before deciding, approve, inspect evidence and report. Browser harness starts its own temporary session/database; it does not reuse a completed run across test cases.

```javascript
await page.goto(`${base}/console/`);
await page.getByRole("textbox", {name: "Mission request"}).fill("count the computers");
await page.getByRole("button", {name: "Send"}).click();
await page.getByRole("button", {name: "Approve", exact: true}).waitFor();
await page.reload();
await page.getByRole("button", {name: "Approve", exact: true}).click();
await page.getByRole("status", {name: "Report status"}).getByText("Accepted", {exact: true}).waitFor();
```

Verifier must additionally assert one confirmed computer from API results, authorized image bytes/hash, actual SurveyStore report, complete event provenance, no external requests and no console errors. Store assertions use a backend test harness, never expose test SQL endpoints in product API.

- [ ] Run to fail before missing view wiring is added. Implement accessible safety controls, mode/source labels and minimal layout integration; safety acknowledgements are not physical completion. Simulated safety actions operate the fake adapter registry and remain visibly simulated.
- [ ] Test rejection, lost decision response, two-tab conflict, same-identity retry, concurrent mission refusal, responsive status while waiting, forbidden/revoked image and no automatic approval on reload. Wire adapter test hooks only through injected constructors in tests, never unauthenticated product routes.
- [ ] Start host in a separate terminal on port 8771 (8770 is the existing user preview), then run acceptance:

```bash
uv run python -m sentinel.ui --mode rehearsal --scenario fixtures/mission-control/clean-computer.json --host 127.0.0.1 --port 8771
node scripts/ui_assets/verify_mission_control.cjs http://127.0.0.1:8771
node --test tests/survey_console/*.test.mjs
uv run pytest tests/ui tests/survey tests/test_runner.py -q
```

Expected: all pass. Record checkpoint A as real runtime dry-run with exact commit, artifacts and commands; do not mark full localization or Live complete.
- [ ] Commit: `git add scripts/ui_assets/verify_mission_control.cjs examples/survey-console tests docs/handoffs && git commit -m "test(ui): prove integrated dry-run milestone"`.

### Task 9: Preserve pose diagnostics and render backend freshness

**Files:** create `src/sentinel/pose/evidence.py`, `src/sentinel/ui/pose_projection.py`, `tests/pose/test_evidence.py`, `tests/ui/test_pose_projection.py`, `examples/survey-console/localization.mjs`, `tests/survey_console/localization.test.mjs`; modify pose detector/stream/persistence, UI evidence and snapshot schema fixture.

**Interfaces:** `PoseTagEvidence`, `PoseEvidenceFrame`, `PoseStreamHealth` use exact spec §9 fields. `PoseEstimate(report: DronePoseReport, evidence: PoseEvidenceFrame)` is frozen. Add `estimate_pose_with_evidence(frame,tag_map,*,run_id,report_id,artifact_id,captured_at,source_kind="SIMULATED",joint=True) -> PoseEstimate`. Existing `estimate_pose` signature/result remains unchanged. Stream optional callbacks are `on_health(PoseStreamHealth)` and `on_evidence(PoseEvidenceFrame)`; encoded evidence bytes go to a separate bounded artifact sink, never snapshot JSON.

- [ ] Write exact-image hash/corner/report tests using existing rendered AprilTag fixtures and a clock boundary test:

```python
@pytest.mark.parametrize("age,expected", [(0,"FRESH"),(999,"FRESH"),(1000,"AGING"),(2000,"AGING"),(2001,"STALE")])
def test_freshness_threshold(age, expected):
    assert freshness_status(age, 2000) == expected
```

`freshness_status(age_ms,stale_after_ms)` is defined in pose_projection; absent report yields UNAVAILABLE. Add mismatched artifact/report/run and throwing-display-callback tests before implementation.

- [ ] Run new tests to fail; retain corners at detector solve time, encode/store the exact image once, hash its served bytes and bind all diagnostics to it. Do not rerun detection on another frame or equate raw-array hash with encoded-byte hash. Keep used/not-used tag state and allowlisted rejection reason; failed pose frames count as no-fix and do not acquire a fabricated report ID.

```python
def freshness_status(age_ms, stale_after_ms):
    if age_ms is None:
        return "UNAVAILABLE"
    if age_ms > stale_after_ms:
        return "STALE"
    return "AGING" if age_ms >= stale_after_ms / 2 else "FRESH"
```

Publish aggregated stream health at most once/second; bounded display callbacks must not block localization or flight, and a failing display sink disables itself visibly. Derive age and map coordinates in Python via the adopted site transform; changing age updates display version only.

- [ ] Render supplied freshness and exact-image polygons with existing letterboxing math. Freeze stale measured coordinates; commanded ghost remains separate. Add independently labelled tag/object toggles and recent fix/no-fix ribbon. Preserve thin-image clipping and reduced motion.
- [ ] Run `uv run pytest tests/pose tests/ui/test_pose_projection.py -q` and `node --test tests/survey_console/localization.test.mjs tests/survey_console/evidence.test.mjs`; expect PASS.
- [ ] Commit: `git add src/sentinel/pose src/sentinel/ui tests examples/survey-console && git commit -m "feat(ui): expose exact-frame pose diagnostics and freshness"`.

### Task 10: Complete mission presentation and broader dry-run scenarios

**Files:** create `examples/survey-console/mission-map.mjs`, `tests/survey_console/mission-map.test.mjs`, `fixtures/mission-control/uncertain-followup.json`, `missing-analysis.json`; modify shell/chat/counts/evidence/CSS, runtime event hooks in cube mission/bridge, scenario loader and verifier; add locally vendored fonts/license under `assets/sentinel-ui/fonts/`.

**Interfaces:** `missionMapModel(snapshot) -> {envelope,route,activeStop,commanded,measured}` selects supplied backend coordinates; `renderMissionMap(host, model)` uses inline SVG. Extend CubeMissionRunner optional public lifecycle sink and CubeFlight runner registration callbacks, preserving defaults for existing consumers.

- [ ] Write geometry/progress tests: no measured point when localization is unavailable, no inferred completed stop, same backend units, stale marker frozen. Add runtime scenario tests for uncertain count, operator-approved follow-up and missing analysis. Derive inputs from existing survey tests/evaluation manifests, but feed them through the production loop; never import evaluation outcomes as runtime results.

```javascript
test("missing measured location stays absent", () => {
  const snapshot = structuredClone(contractFixture);
  snapshot.localization.map_position = null;
  assert.equal(missionMapModel(snapshot).measured, null);
});
```

- [ ] Run new tests to fail. Implement stable command bar, left chat, central mission/camera, evidence spine, right facts and audit drawer. Capture/progress events come from real runner callbacks. Display UNAVAILABLE for absent signals; no animation advances operational state.
- [ ] Vendor IBM Plex Sans/Mono from the official distribution with attribution. Keep authoritative tokens JSON/CSS consistent; prefer page-scoped semantic variables for new console styling so the asset foundation does not change unnecessarily. If shared tokens change, edit JSON and generator inputs, regenerate CSS and rerun `scripts/ui_assets/check_assets.py`.
- [ ] Extend actual-host acceptance to 1440x900,1920x1080,390x844, focus/skip-link/keyboard/reduced-motion, inert malicious strings, no external requests or overflow, stale→recovered tags and frame-request cap. Broader dry-run scenarios must complete actual merge/report validation.
- [ ] Run `node --test tests/survey_console/*.test.mjs`, `uv run pytest tests/ui tests/survey tests/cube -q`, the asset checker and real-host verifier to PASS; record checkpoint B.
- [ ] Commit: `git add examples/survey-console src/sentinel fixtures/mission-control assets/sentinel-ui scripts/ui_assets tests docs/handoffs && git commit -m "feat(ui): complete mission evidence and rehearsal scenarios"`.

### Task 11: Compose Live adapters behind the same application service

**Files:** modify `src/sentinel/ui/bootstrap.py`, `runtime.py`, `__main__.py`, `src/sentinel/cli.py`, `src/sentinel/survey/cube_bridge.py`; create `tests/ui/test_bootstrap.py`; extend `tests/test_cli.py`, `tests/survey/test_cube_bridge.py`.

**Interfaces:** frozen `SessionConfig` contains mode, host, port, scenario_path, artifact_root, ui_db, survey_db, site_path, tag_map_path, pose_db, pose_mode, detector_name, location, consent_date, confirm. Secrets are resolved from environment by provider adapters and excluded from public summaries. `build_rehearsal_adapters(config.scenario_path,config.artifact_root)` from Task 4; `build_live_adapters(config) -> ScenarioAdapters` defined here; `validate_live_startup(config) -> None` fails with safe startup errors; `build_service(config)` always returns the same MissionControlService.

- [ ] Write startup gate tests using injected fake adapter factories, with a spy proving Live failure never invokes the rehearsal factory. Parameterize absent confirm/site/tag-map/databases/consent/provider/profile/preflight/independent transport. Verify frozen config and no credential projection.

```python
def test_live_failure_never_constructs_rehearsal(live_config, monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "build_rehearsal_adapters", lambda *args: calls.append(args))
    with pytest.raises(StartupError):
        bootstrap.build_service(replace(live_config, confirm=False))
    assert calls == []
```

- [ ] Run tests to fail. Implement mode composition and the CLI. Flags: `--mode rehearsal|live`, localhost `--host`, `--port`, `--scenario`, `--artifact-root`, `--ui-db`, `--survey-db`, `--site`, `--tag-map`, `--pose-db`, `--pose off|passive|reanchor`, `--detector replay|moondream|gemini`, `--location`, `--consent-date`, `--confirm`.

```python
if config.mode == "REHEARSAL":
    adapters = build_rehearsal_adapters(config.scenario_path, config.artifact_root)
else:
    validate_live_startup(config)
    adapters = build_live_adapters(config)
return MissionControlService(config=config, adapters=adapters)
```

Service constructor composes the same store, projection, broker, worker, chat and safety units from Tasks 1–7. Live adapters use existing CubeFlight, measured camera model, selected survey proposer/detector/writer and pose pipeline. Register each active Cube runner before flight and remove it only after terminal cleanup; independent emergency is session-available when no runner exists. A session emergency also latches admission so a new runner cannot start immediately after a no-active-runner emergency. Check latches atomically at registration/admission, not just once in UI.

Live preflight executes under the backend's existing authorization rules; automated tests inject fakes rather than connect to a drone. A startup exception never constructs a deterministic replacement. Software tests can prove composition and refusal; they do not establish measured hardware readiness.

- [ ] Run `uv run pytest tests/ui/test_bootstrap.py tests/survey/test_cube_bridge.py tests/test_cli.py tests/ui/test_safety.py -q` to PASS, with contract parity tests comparing both modes' payload validation using fake Live adapters.
- [ ] Commit: `git add src/sentinel tests && git commit -m "feat(ui): compose live adapters through shared runtime"`.

### Task 12: Verify, review and publish the integrated change

**Files:** update `examples/survey-console/README.md`, `contract.md`, root `README.md`, `docs/handoffs/2026-09-21-mission-control.md`; implementation fixes only when supported by new failures or review findings.

**Interfaces:** consumes completed Tasks 0–11; produces reproducible handoff, fresh independent review and an updated PR #8 linked to #7.

- [ ] Run complete suites once after focused checks pass:

```bash
node --test tests/survey_console/*.test.mjs
uv run pytest -q --ignore=fixtures
uv run python scripts/ui_assets/check_assets.py
node scripts/ui_assets/verify_mission_control.cjs http://127.0.0.1:8771
```

Run the retained fixture verifier with its static fixture host as a separate regression check; do not claim it proves HTTP/runtime integration. Record exact command results, skips/warnings and source refs. Browser harness owns its temporary processes/data and stops them afterward; preserve the user's original port 8770 preview unless explicitly replacing it.

- [ ] Verify privacy/source/performance invariants: no private fields in DOM/API/logs; no nonlocal requests in Rehearsal; bounded polling/image rates; responsive chat/snapshot/safety during worker activity; approval age updates remain usable; recovery cannot dispatch.
- [ ] Run one fresh independent whole-branch review under native execution. Address actionable findings with targeted failing regressions, then rerun affected checks. Do not replace completed implementation with a review-only report.
- [ ] Write handoff with implemented adapters, milestones A/B/C, exact tests, source/commit ancestry, unresolved hardware-only checks and actual limitations. Do not claim flight, measured pose, landing completion or emergency motor-stop success from fake transports.
- [ ] Commit documentation, push normally, update existing PR #8 title/body to its final scope, comment with the five corrected contract gaps and test evidence, and re-request Cols77 review. Link #7 as the foundation; inspect PR base/merge state after integration before editing it and avoid an unrelated diff or duplicated runtime commits. No force push or merge of the PR is authorized by this plan. Use a structured API body or a temporary Markdown file with `gh pr comment --body-file` for the detailed comment; inspect existing reviewer requests before renewing the request.

## Coverage and self-review

Spec §§1–3 → Tasks 0,4,8,10 (intent, reuse, runtime dry-run). §4 → Tasks 1–7 (host, projection, worker, commands, safety). §5 → Tasks 4,11 (shared mode composition). §6 → Tasks 7,8,10 (chat/presentation). §7 → Tasks 1,5,6 (versioned payload/API). §8 → Tasks 4,5,9,10 (authoritative signals). §9 → Tasks 9,10 (pose diagnostics). §§10–11 → Tasks 2,3,6–8,11 (identity, failure/recovery and safety). §12 → Tasks 6,8–10,12 (accessibility/privacy/performance). §§13–14 → Tasks 8,10–12 (ordered milestones, acceptance, handoff).

Review Focus cases are assigned to concrete tests above. Original plan defects are removed: no snapshot-driven Rehearsal service, no two-argument browser decision call, no throwing on typed 409 conflicts, no root module routing mismatch, no unguarded executor queue, no sender-only emergency adapter. Planning performs no dependency install, product changes or hardware operation.
