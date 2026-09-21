# SENTINEL Mission Control Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn LightCode's fixture survey console into a projector-ready Mission Control application that supports persistent text chat, identity-bound approvals, Rehearsal and Live runtime projections, visible localization/tag performance, and independent Land/Emergency controls.

**Architecture:** Keep the tested static HTML/CSS/ES-module frontend and serve it from a local NiceGUI/FastAPI host. A Python projection service exposes complete snapshots, ordered events, bounded evidence, and idempotent commands; a single mission worker owns mission mutation, while safety commands use a separate typed port. Land a fully working Rehearsal vertical slice before adding Live survey, flight, pose, and safety adapters.

**Tech Stack:** Python 3.11+, NiceGUI/FastAPI, SQLite WAL, vanilla ES modules, inline SVG, Node test runner, pytest, Playwright browser acceptance, existing SENTINEL survey/cube/pose packages.

---

## Scope, ownership, and prerequisite gate

This plan is intentionally linear even though it crosses four seams. Tasks 1–6 are LightCode's
UI/Rehearsal vertical slice. Tasks 7–10 adopt runtime interfaces and must not bypass missing
integration contracts. Tasks 11–12 compose and verify Live mode.

The PR branch currently contains the fixture console but not the active local integration work.
The operator must publish the approved `integration/survey-demo` tip before Live tasks begin.
LightCode may complete Tasks 1–6 against the fixture console while that publication is pending.

### Task 0: Update branch ancestry and prove the baseline

**Files:**
- Verify: `examples/survey-console/`
- Verify: `tests/survey_console/`
- Verify: `src/sentinel/survey/`
- Verify: `src/sentinel/cube/mission.py`
- Verify: `src/sentinel/pose/stream.py`

- [ ] **Step 1: Fetch and merge the published integration branch without rewriting the PR**

```powershell
rtk git fetch origin
rtk git merge --no-ff origin/integration/survey-demo
```

Expected: a merge commit or `Already up to date`; do not force-push and do not resolve conflicts
by dropping either the fixture console or integration runtime.

- [ ] **Step 2: Verify required source seams exist**

```powershell
rtk rg -n "class SurveyStore|def run_survey|class CubeMissionRunner|def run_pose_stream|def trigger_emergency" src/sentinel
```

Expected: matches in `survey/store.py`, `survey/loop.py`, `cube/mission.py`, `pose/stream.py`, and
`runner.py`. If one is absent, stop Live work and report the exact missing integration commit;
Tasks 1–6 may still proceed.

- [ ] **Step 3: Run the Python and existing UI baselines**

```powershell
rtk uv sync --all-extras
rtk proxy uv run pytest -q --ignore=fixtures
rtk node --test tests/survey_console/*.test.mjs
```

Expected: Python suite passes with only documented skips/warnings; survey console reports 45
passing tests and zero failures. Preserve the output in the PR handoff.

### Task 1: Add the local Mission Control host

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/sentinel/ui/__init__.py`
- Create: `src/sentinel/ui/__main__.py`
- Create: `src/sentinel/ui/app.py`
- Create: `tests/ui/__init__.py`
- Create: `tests/ui/test_app.py`

- [ ] **Step 1: Write the failing host test**

```python
# tests/ui/test_app.py
from fastapi.testclient import TestClient
from pathlib import Path

from sentinel.ui.app import build_app


class StubService:
    def session(self) -> dict:
        return {"mode": "REHEARSAL", "active_run_id": "run-demo"}


def test_host_serves_console_and_session(tmp_path):
    repository_root = Path(__file__).resolve().parents[2]
    app = build_app(StubService(), repository_root=repository_root)
    client = TestClient(app)
    assert client.get("/api/v1/session").json() == {
        "mode": "REHEARSAL",
        "active_run_id": "run-demo",
    }
    assert client.get("/").status_code == 200
```

- [ ] **Step 2: Run the test and confirm the missing package failure**

```powershell
rtk proxy uv run pytest tests/ui/test_app.py -q
```

Expected: FAIL because `sentinel.ui` does not exist.

- [ ] **Step 3: Add NiceGUI and the smallest host**

Add `"nicegui>=3.6,<4"` to project dependencies, run `rtk uv lock`, and implement:

```python
# src/sentinel/ui/app.py
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def build_app(service, *, repository_root: Path) -> FastAPI:
    api = FastAPI(title="SENTINEL Mission Control", docs_url=None, redoc_url=None)
    console = repository_root / "examples" / "survey-console"
    assets = repository_root / "assets" / "sentinel-ui"
    api.mount("/console", StaticFiles(directory=console), name="console")
    api.mount("/assets/sentinel-ui", StaticFiles(directory=assets), name="sentinel-assets")

    @api.get("/", include_in_schema=False)
    async def index():
        return FileResponse(console / "index.html")

    @api.get("/api/v1/session")
    async def session():
        return service.session()

    return api
```

`src/sentinel/ui/__main__.py` parses `--host`, `--port`, `--mode rehearsal|live`, and the immutable
startup paths required by later tasks, then runs the FastAPI app through NiceGUI's supported
`ui.run_with`/Uvicorn integration. Bind to `127.0.0.1` by default and never enable NiceGUI On Air.

- [ ] **Step 4: Run focused tests and import-boundary checks**

```powershell
rtk proxy uv run pytest tests/ui/test_app.py tests/coordinator/test_package_boundary.py -q
```

Expected: PASS; coordinator tests still prove coordinator code imports no NiceGUI.

- [ ] **Step 5: Commit**

```powershell
rtk git add pyproject.toml uv.lock src/sentinel/ui tests/ui
rtk git commit -m "feat(ui): add local mission control host"
```

### Task 2: Adopt a complete projection and event store

**Files:**
- Create: `src/sentinel/ui/models.py`
- Create: `src/sentinel/ui/store.py`
- Create: `src/sentinel/ui/projection.py`
- Create: `tests/ui/conftest.py`
- Create: `tests/ui/test_models.py`
- Create: `tests/ui/test_store.py`
- Create: `tests/ui/test_projection.py`
- Modify: `examples/survey-console/contract.mjs`
- Modify: `tests/survey_console/contract.test.mjs`

- [ ] **Step 1: Write failing Python contract tests**

```python
# tests/ui/test_models.py
from sentinel.ui.models import validate_snapshot


def test_snapshot_rejects_internal_paths(valid_snapshot):
    valid_snapshot["evidence"]["frames"][0]["image_path"] = "C:/secret/frame.jpg"
    try:
        validate_snapshot(valid_snapshot)
    except ValueError as exc:
        assert "image_path" in str(exc)
    else:
        raise AssertionError("internal path accepted")


def test_pose_health_requires_backend_age(valid_snapshot):
    del valid_snapshot["localization"]["age_ms"]
    try:
        validate_snapshot(valid_snapshot)
    except ValueError as exc:
        assert "age_ms" in str(exc)
    else:
        raise AssertionError("missing age accepted")
```

Create `tests/ui/conftest.py` with one complete V1 snapshot containing exactly the top-level units
from the design: `session`, `run`, `source`, `safety`, `chat`, `proposal`, `mission`,
`localization`, `evidence`, `results`, `report`, `events`, and `legal_actions`.

- [ ] **Step 2: Confirm the contract tests fail**

```powershell
rtk proxy uv run pytest tests/ui/test_models.py -q
```

Expected: FAIL because `validate_snapshot` does not exist.

- [ ] **Step 3: Implement strict projection validation**

In `models.py`, define frozen dataclasses for command/evidence results and use an exact-key
validator for snapshots. These types are also used by Task 3:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceCapability:
    status: str
    run_id: str
    artifact_id: str
    media_type: str | None = None
    payload: bytes | None = None
    reason_code: str | None = None


INTERNAL_KEYS = frozenset({"image_path", "storage_key", "payload_json", "raw_response_json"})


def _reject_internal(value, path="snapshot"):
    if isinstance(value, dict):
        forbidden = INTERNAL_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"{path} contains internal field {sorted(forbidden)[0]}")
        for key, child in value.items():
            _reject_internal(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_internal(child, f"{path}[{index}]")


def _exact(value: dict, keys: set[str], path: str):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{path} keys differ")


def validate_snapshot(snapshot: dict) -> dict:
    required = {
        "session", "run", "source", "safety", "chat", "proposal", "mission",
        "localization", "evidence", "results", "report", "events", "legal_actions",
    }
    if set(snapshot) != required:
        raise ValueError(f"snapshot keys differ: {sorted(set(snapshot) ^ required)}")
    _reject_internal(snapshot)
    _exact(snapshot["localization"], {
        "status", "age_ms", "stale_after_ms", "latest_report", "recent_stream",
        "containment", "pose_evidence",
    }, "localization")
    if snapshot["localization"]["status"] not in {"FRESH", "AGING", "STALE", "UNAVAILABLE"}:
        raise ValueError("localization.status")
    if snapshot["localization"]["age_ms"] is not None and snapshot["localization"]["age_ms"] < 0:
        raise ValueError("localization.age_ms")
    if snapshot["events"]["watermark"] < 0:
        raise ValueError("events.watermark")
    # Continue this exact-key pattern for every nested object in tests/ui/conftest.py.
    # Resolve frame, sighting, tag, stop, proposal, and report references before returning.
    return snapshot
```

Implement the recursive checks explicitly; do not serialize arbitrary dataclasses with
`__dict__` and do not pass through unknown runtime payloads.

- [ ] **Step 4: Write and implement the append-only UI store**

Tests create two SQLite connections to one temporary file and assert ordered, run-scoped events,
idempotent commands, persisted chat, and snapshot version monotonicity. Implement these tables in
`store.py`:

```sql
CREATE TABLE ui_events (
  run_id TEXT NOT NULL,
  sequence_no INTEGER NOT NULL,
  event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  public_payload_json TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  PRIMARY KEY (run_id, sequence_no)
);
CREATE TABLE ui_commands (
  command_id TEXT PRIMARY KEY,
  run_id TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  command_type TEXT NOT NULL,
  request_json TEXT NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE ui_messages (
  message_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  sequence_no INTEGER NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('OPERATOR','ASSISTANT','SYSTEM')),
  body TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (run_id, sequence_no)
);
```

Use WAL, `busy_timeout=5000`, parameterized SQL, explicit transactions, and a separate connection
per thread. `append_event` allocates `MAX(sequence_no)+1` inside `BEGIN IMMEDIATE`.

- [ ] **Step 5: Expand the browser validator to the same public shape**

Update `contract.mjs` to validate schema version 2 while keeping a `validateSurveyV1` export for
the untouched fixture tests until Task 3 migrates them. Add JavaScript tests that reject raw paths,
unknown fields, mismatched run IDs, invalid pose states, missing event watermarks, and unresolved
frame/sighting/tag references.

- [ ] **Step 6: Run both contract suites**

```powershell
rtk proxy uv run pytest tests/ui/test_models.py tests/ui/test_store.py tests/ui/test_projection.py -q
rtk node --test tests/survey_console/contract.test.mjs
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
rtk git add src/sentinel/ui tests/ui examples/survey-console/contract.mjs tests/survey_console/contract.test.mjs
rtk git commit -m "feat(ui): adopt mission control projection"
```

### Task 3: Build the complete Rehearsal service and HTTP client

**Files:**
- Create: `src/sentinel/ui/rehearsal.py`
- Create: `src/sentinel/ui/api.py`
- Create: `fixtures/mission-control/golden-run.json`
- Create: `tests/ui/test_rehearsal.py`
- Create: `tests/ui/test_api.py`
- Create: `examples/survey-console/http-client.mjs`
- Modify: `examples/survey-console/app.mjs`
- Modify: `examples/survey-console/controller.mjs`
- Modify: `tests/survey_console/client.test.mjs`

- [ ] **Step 1: Write failing Rehearsal API tests**

```python
def test_rehearsal_exposes_snapshot_events_and_bounded_evidence(client):
    snapshot = client.get("/api/v1/runs/golden-run/snapshot").json()
    assert snapshot["session"]["mode"] == "REHEARSAL"
    events = client.get(
        "/api/v1/runs/golden-run/events", params={"after_sequence": 0, "limit": 200}
    ).json()
    assert [row["sequence"] for row in events] == list(range(1, len(events) + 1))
    image = client.get("/api/v1/runs/golden-run/evidence/frame-1")
    assert image.headers["content-type"] in {"image/png", "image/jpeg"}
    assert "image_path" not in image.headers
```

- [ ] **Step 2: Run and confirm 404 failures**

```powershell
rtk proxy uv run pytest tests/ui/test_rehearsal.py tests/ui/test_api.py -q
```

Expected: FAIL because routes and service are absent.

- [ ] **Step 3: Implement the Rehearsal adapter**

`RehearsalService` loads `golden-run.json`, validates every supplied snapshot/event, owns only
fixture advancement, and implements the same service methods Live will implement:

```python
class MissionControlService(Protocol):
    def session(self) -> dict: ...
    def read_snapshot(self, run_id: str) -> dict: ...
    def read_events(self, run_id: str, after_sequence: int, limit: int) -> list[dict]: ...
    def read_evidence(self, run_id: str, artifact_id: str) -> EvidenceCapability: ...
    def submit_chat(self, run_id: str, command: dict) -> dict: ...
    def submit_decision(self, run_id: str, command: dict) -> dict: ...
    def request_land(self, run_id: str, command: dict) -> dict: ...
    def trigger_emergency(self, command: dict) -> dict: ...
```

Import `EvidenceCapability` from `sentinel.ui.models`; return `payload` only from the evidence
route and never include it in snapshot JSON.

The fixture contains request, proposal, approval, flight, fresh pose, aging pose, stale pose,
recovered pose, capture/analysis, uncertain count, follow-up, and report stages. Synthetic images
remain clearly labelled Rehearsal.

- [ ] **Step 4: Add typed FastAPI routes**

Implement the exact `/api/v1` routes from the design. Validate `limit` as 1–200, map missing runs
to 404, conflicts to 409, forbidden evidence to 403, and uncertain command outcomes to a 202 body
with `status: IN_DOUBT`. Never return a traceback or private exception text.

- [ ] **Step 5: Implement the browser HTTP client**

```javascript
// examples/survey-console/http-client.mjs
export function createHttpClient(base = "/api/v1") {
  const json = async (url, init) => {
    const response = await fetch(url, init);
    const body = await response.json();
    if (!response.ok) throw Object.assign(new Error(body.message), { code: body.code });
    return body;
  };
  return {
    read_survey: (runId) => json(`${base}/runs/${runId}/snapshot`),
    read_survey_events: (runId, after, limit) =>
      json(`${base}/runs/${runId}/events?after_sequence=${after}&limit=${limit}`),
    submit_decision: (runId, attempt) => json(`${base}/runs/${runId}/decisions`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(attempt),
    }),
  };
}
```

Add a visibility-aware 500 ms poller that never overlaps calls and invokes `controller.refresh()`.
Do not add SSE or WebSocket client code.

- [ ] **Step 6: Run Rehearsal service and client tests**

```powershell
rtk proxy uv run pytest tests/ui/test_rehearsal.py tests/ui/test_api.py -q
rtk node --test tests/survey_console/client.test.mjs tests/survey_console/controller.test.mjs
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
rtk git add src/sentinel/ui fixtures/mission-control tests/ui examples/survey-console tests/survey_console
rtk git commit -m "feat(ui): connect rehearsal mission service"
```

### Task 4: Restyle the stable field-command-ledger shell

**Files:**
- Modify: `assets/sentinel-ui/tokens/tokens.css`
- Add: `assets/sentinel-ui/fonts/IBMPlexSans-Variable.woff2`
- Add: `assets/sentinel-ui/fonts/IBMPlexMono-Regular.woff2`
- Add: `assets/sentinel-ui/fonts/OFL.txt`
- Modify: `examples/survey-console/index.html`
- Modify: `examples/survey-console/console.css`
- Modify: `examples/survey-console/shell.mjs`
- Modify: `scripts/ui_assets/verify_survey_console.cjs`

- [ ] **Step 1: Add failing browser assertions for the new regions**

Extend the verifier to require `CommandBar`, `ChatRail`, `MissionWorkspace`, `FactsRail`,
`EvidenceSpine`, and `AuditDrawer`; assert no horizontal overflow at 1440x900, 1920x1080, and
390x844; assert Land and Emergency remain visible at every fixture stage.

- [ ] **Step 2: Run the verifier and confirm region failures**

```powershell
rtk python -m http.server 8770 --bind 127.0.0.1
rtk node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8770
```

Expected: FAIL with missing mission-control region IDs.

- [ ] **Step 3: Implement the stable layout and tokens**

Use one CSS grid with fixed command bar, 300 px chat rail, flexible central workspace, 300 px
facts rail, and central evidence spine. Keep the geometry stable between phases. Define local
`@font-face` declarations and preserve 16 px body/14 px metadata minimums. Cyan is measured/live,
amber is aging/attention, and red is emergency/containment only.

The center contains two persistent children:

```html
<section id="mission-sheet" aria-label="Mission route and position"></section>
<section id="camera-stage" aria-label="Current camera and evidence"></section>
```

Add an off-canvas audit drawer opened by a labelled button; do not keep the full event log in the
first viewport.

- [ ] **Step 4: Verify accessibility and responsive states**

```powershell
rtk node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8770
```

Expected: PASS at all three viewports, reduced motion, keyboard-only navigation, and no external
font/network request.

- [ ] **Step 5: Commit**

```powershell
rtk git add assets/sentinel-ui examples/survey-console scripts/ui_assets/verify_survey_console.cjs
rtk git commit -m "feat(ui): create field command ledger shell"
```

### Task 5: Add the persistent text mission thread

**Files:**
- Create: `src/sentinel/ui/chat.py`
- Create: `tests/ui/test_chat.py`
- Create: `examples/survey-console/chat.mjs`
- Create: `tests/survey_console/chat.test.mjs`
- Modify: `examples/survey-console/app.mjs`
- Modify: `examples/survey-console/shell.mjs`
- Modify: `examples/survey-console/console.css`

- [ ] **Step 1: Write failing backend chat tests**

```python
def test_chat_persists_one_atomic_reply(service):
    command = {
        "command_id": "cmd-1", "idempotency_key": "chat-1", "expected_version": 3,
        "text": "How many computers have you seen?",
    }
    first = service.submit_chat("run-1", command)
    second = service.submit_chat("run-1", command)
    assert first == second
    messages = service.read_snapshot("run-1")["chat"]["messages"]
    assert [m["role"] for m in messages[-2:]] == ["OPERATOR", "ASSISTANT"]
    assert all(m["status"] == "COMPLETE" for m in messages[-2:])
```

Also test unsupported requests escalate or clarify, status/count questions cite snapshot versions,
provider failures preserve the operator message, and chat cannot approve a proposal.

- [ ] **Step 2: Confirm failures, then implement `ChatService`**

`ChatService` wraps the integrated bounded decision session when present. Rehearsal uses a
deterministic responder over the current projection. It writes the operator message first, calls
the bounded session once, writes one complete assistant/system result, and records public events.
It exposes no token stream and no private reasoning.

- [ ] **Step 3: Write failing frontend transcript tests**

Test one continuous run thread, preserved composer text after failure, suggested prompts, no
auto-scroll while history is being inspected, and a disabled duplicate send while the same
command is pending.

- [ ] **Step 4: Implement `chat.mjs`**

Export `createChatView(host, client, getSnapshot)` with `render(messages)`, `submit(text)`, and
`dispose()`. Generate command UUIDs with `crypto.randomUUID()`, retain the same identity on retry,
and render assistant Markdown as inert text for V1 (no raw HTML injection).

- [ ] **Step 5: Run and commit**

```powershell
rtk proxy uv run pytest tests/ui/test_chat.py -q
rtk node --test tests/survey_console/chat.test.mjs
rtk git add src/sentinel/ui/chat.py tests/ui/test_chat.py examples/survey-console tests/survey_console
rtk git commit -m "feat(ui): add persistent mission chat"
```

### Task 6: Connect proposal cards to a real approval broker

**Files:**
- Create: `src/sentinel/ui/approval.py`
- Create: `tests/ui/test_approval.py`
- Modify: `examples/survey-console/approval.mjs`
- Modify: `examples/survey-console/app.mjs`
- Modify: `tests/survey_console/approval.test.mjs`

- [ ] **Step 1: Write failing broker tests**

```python
def test_approval_unblocks_exact_waiter_only(broker):
    pending = broker.offer(run_id="run-1", proposal_id="p-1", proposal_sha256="abc", version=7)
    result = broker.decide({
        "run_id": "run-1", "proposal_id": "p-1", "proposal_sha256": "abc",
        "expected_version": 7, "decision": "APPROVE", "command_id": "c-1",
        "idempotency_key": "approve-1",
    })
    assert result["status"] == "ACKNOWLEDGED"
    assert pending.wait(timeout=0.1) == "APPROVE"


def test_replaced_proposal_conflicts(broker):
    broker.offer(run_id="run-1", proposal_id="p-2", proposal_sha256="new", version=8)
    assert broker.decide({
        "run_id": "run-1", "proposal_id": "p-1", "proposal_sha256": "old",
        "expected_version": 7, "decision": "APPROVE", "command_id": "c-2",
        "idempotency_key": "approve-old",
    })["status"] == "CONFLICT"
```

Cover browser refresh, duplicate delivery, reject, `IN_DOUBT` reconciliation, and backend restart
turning a Live waiter into `RECOVERY_REQUIRED`.

- [ ] **Step 2: Implement the broker and route binding**

The broker persists offers/attempts before publishing them, uses a thread-safe in-memory waiter
only for the current process, and checks all immutable identity fields before resolving it. It
never calls flight code from the HTTP request thread.

- [ ] **Step 3: Move working buttons into inline chat proposal cards**

Render the full candidate in the thread and keep the right facts rail as a read-only summary.
Both Approve and Reject call the same `submit_decision` method. Disable stale, ineligible, pending,
and settled cards with visible reasons. Preserve LightCode's existing retry identity behavior.

- [ ] **Step 4: Run and commit**

```powershell
rtk proxy uv run pytest tests/ui/test_approval.py -q
rtk node --test tests/survey_console/approval.test.mjs
rtk git add src/sentinel/ui/approval.py tests/ui/test_approval.py examples/survey-console tests/survey_console
rtk git commit -m "feat(ui): bind inline proposal approvals"
```

### Task 7: Project real survey state and mission progress

**Files:**
- Create: `src/sentinel/ui/survey_projection.py`
- Create: `src/sentinel/ui/mission_worker.py`
- Create: `tests/ui/test_survey_projection.py`
- Create: `tests/ui/test_mission_worker.py`
- Create: `examples/survey-console/mission-map.mjs`
- Create: `tests/survey_console/mission-map.test.mjs`
- Modify: `src/sentinel/survey/loop.py`
- Modify: `src/sentinel/survey/store.py`
- Modify: `src/sentinel/cube/mission.py`
- Modify: `examples/survey-console/shell.mjs`

- [ ] **Step 1: Write failing projection tests over a real `SurveyStore`**

Populate a temporary survey database with a run, proposed/flown sweep, frames, sightings,
objects, and report. Assert the projection returns exact counts, two-flight budget, frame status,
artifact IDs, route stops, commanded/measured distinction, and no `image_path`.

- [ ] **Step 2: Add public read methods rather than UI SQL**

Add focused methods to `SurveyStore`: `read_run`, `read_sweeps`, `read_frames_public`,
`read_objects_public`, `read_report`, and `read_event_page`. Each returns allowlisted dictionaries.
`survey_projection.py` composes those methods; browser and JavaScript code never query SQLite.

- [ ] **Step 3: Add event hooks at existing lifecycle boundaries**

Inject an optional `emit(event_type, public_payload)` callback into `run_survey` and
`CubeMissionRunner`. Emit proposal offered, decision applied, flight started, stop active,
localization result, frame captured, analysis completed, merge completed, report accepted/rejected,
and terminal outcome. Defaults preserve existing callers and behavior.

- [ ] **Step 4: Implement one serial mission worker**

```python
class MissionWorker:
    def __init__(self, run_callable, approval_broker, emit):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sentinel-mission")
        self._run_callable = run_callable
        self._approval_broker = approval_broker
        self._emit = emit

    def start(self, request):
        return self._executor.submit(self._run_callable, request, self._approval_broker, self._emit)
```

Tests prove the UI thread remains responsive while approval blocks and that a second mutating
mission is refused while one is active.

- [ ] **Step 5: Render the inline SVG mission sheet**

`mission-map.mjs` consumes only backend-provided map coordinates. Draw envelope, route, numbered
stops, active stop, commanded ghost, measured marker, and containment state. Do not convert pose
units in JavaScript and do not infer progress from stop order or time.

- [ ] **Step 6: Run focused and regression tests**

```powershell
rtk proxy uv run pytest tests/ui/test_survey_projection.py tests/ui/test_mission_worker.py tests/survey tests/cube/test_mission.py -q
rtk node --test tests/survey_console/mission-map.test.mjs tests/survey_console/*.test.mjs
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
rtk git add src/sentinel/ui src/sentinel/survey src/sentinel/cube tests/ui tests/survey tests/cube examples/survey-console tests/survey_console
rtk git commit -m "feat(ui): project live survey mission state"
```

### Task 8: Preserve pose evidence and derive localization health

**Files:**
- Create: `src/sentinel/pose/evidence.py`
- Create: `src/sentinel/ui/pose_projection.py`
- Create: `tests/pose/test_evidence.py`
- Create: `tests/ui/test_pose_projection.py`
- Modify: `src/sentinel/pose/detector.py`
- Modify: `src/sentinel/pose/stream.py`
- Modify: `src/sentinel/pose/persistence.py`

- [ ] **Step 1: Write failing detector-evidence tests**

Use rendered tag frames from existing pose test helpers. Assert the diagnostic contains normalized
four-corner polygons, `USED` versus `DETECTED_NOT_USED`, run/report/frame hash binding, and no raw
image bytes or path.

```python
def test_pose_evidence_binds_corners_to_report(rendered_frame, tag_map):
    result = estimate_pose_with_evidence(rendered_frame, tag_map, run_id="r", report_id="p")
    assert result.report.report_id == result.evidence.report_id == "p"
    assert all(len(tag.corners_xy_normalized) == 4 for tag in result.evidence.tags)
    assert all(0 <= value <= 1 for tag in result.evidence.tags for point in tag.corners_xy_normalized for value in point)
```

- [ ] **Step 2: Introduce an additive diagnostic return path**

Keep `estimate_pose(...) -> DronePoseReport` unchanged. Add
`estimate_pose_with_evidence(...) -> PoseEstimate` where `PoseEstimate` contains the report and
`PoseEvidenceFrame`. The stream uses the richer function only when an evidence sink is supplied.
Existing pose consumers and wire contracts remain unchanged.

Define the new types in `src/sentinel/pose/evidence.py`:

```python
@dataclass(frozen=True)
class PoseTagEvidence:
    tag_id: int
    corners_xy_normalized: tuple[tuple[float, float], ...]
    status: Literal["USED", "DETECTED_NOT_USED"]
    reason_code: str | None
    zone_id: str | None
    reprojection_error_px: float | None


@dataclass(frozen=True)
class PoseEvidenceFrame:
    run_id: str
    report_id: str
    artifact_id: str
    sha256: str
    captured_at: str
    width_px: int
    height_px: int
    source_kind: str
    tags: tuple[PoseTagEvidence, ...]


@dataclass(frozen=True)
class PoseStreamHealth:
    frames_seen: int
    reports_emitted: int
    no_tag_frames: int
    gated_frames: int
    timeouts: int
```

- [ ] **Step 3: Add an aggregated stream-health callback**

Extend `run_pose_stream` with optional `on_health(PoseStreamHealth)` and
`on_evidence(PoseEvidenceFrame)` callbacks. Once per second, publish counts for frames seen,
reports emitted, no-tag frames, gated frames, and timeouts. Callback failures disable only that
display callback and never stop flight/localization.

- [ ] **Step 4: Derive freshness on the backend**

`pose_projection.py` reads the latest run-bound report and returns `age_ms`, effective
`stale_after_ms`, and `FRESH` up to half-threshold, `AGING` up to threshold, `STALE` above it, or
`UNAVAILABLE`. Use an injected UTC clock in tests. Convert pose to mission-sheet coordinates via
the adopted site transform in Python.

- [ ] **Step 5: Run pose and UI tests**

```powershell
rtk proxy uv run pytest tests/pose/test_evidence.py tests/ui/test_pose_projection.py tests/pose/test_detector.py tests/pose/test_live_stream.py -q
```

Expected: PASS with existing `DronePoseReport` tests unchanged.

- [ ] **Step 6: Commit**

```powershell
rtk git add src/sentinel/pose src/sentinel/ui/pose_projection.py tests/pose tests/ui/test_pose_projection.py
rtk git commit -m "feat(pose): expose bounded localization evidence"
```

### Task 9: Make localization behavior legible in the browser

**Files:**
- Create: `examples/survey-console/localization.mjs`
- Create: `tests/survey_console/localization.test.mjs`
- Modify: `examples/survey-console/evidence.mjs`
- Modify: `examples/survey-console/mission-map.mjs`
- Modify: `examples/survey-console/shell.mjs`
- Modify: `examples/survey-console/console.css`

- [ ] **Step 1: Write failing state and geometry tests**

Test fresh, aging, stale, unavailable, and refreshed pose; tag corner letterboxing; separate
localization/survey layers; commanded/measured markers; no-fix ribbon segments; and reduced motion.
Assert stale coordinates remain fixed and labelled with age rather than drifting.

- [ ] **Step 2: Implement localization rendering**

`localization.mjs` exports pure render models before DOM functions:

```javascript
export function poseStyle(status, containment) {
  if (containment === "OUTSIDE") return "pose-outside";
  return {
    FRESH: "pose-fresh",
    AGING: "pose-aging",
    STALE: "pose-stale",
    UNAVAILABLE: "pose-unavailable",
  }[status];
}
```

Draw tag quadrilaterals as SVG polygons over the exact evidence image geometry. Labels include tag
ID and used/not-used state. Survey boxes and localization tags have independent labelled toggles;
localization is primary during flight and survey objects during analysis.

- [ ] **Step 3: Add the pose health block and ribbon**

Show state text, age, source kind, used tag IDs, heading, optional altitude, overall reprojection
error, containment, recent fix/no-fix segments, and last observation time. Color is accompanied by
solid/dashed shape and text.

- [ ] **Step 4: Run and commit**

```powershell
rtk node --test tests/survey_console/localization.test.mjs tests/survey_console/evidence.test.mjs tests/survey_console/mission-map.test.mjs
rtk git add examples/survey-console tests/survey_console
rtk git commit -m "feat(ui): visualize localization health and tags"
```

### Task 10: Adopt controlled Land and independent Emergency

**Files:**
- Create: `src/sentinel/ui/safety.py`
- Create: `tests/ui/test_safety.py`
- Create: `examples/survey-console/safety.mjs`
- Create: `tests/survey_console/safety.test.mjs`
- Modify: `src/sentinel/runner.py`
- Modify: `tests/test_runner.py`
- Modify: `examples/survey-console/shell.mjs`
- Modify: `examples/survey-console/console.css`

- [ ] **Step 1: Write runner-level controlled-land race tests**

Before exposing a UI action, add tests proving a public controlled-land request latches before
sending `land`, prevents any later normal command, is idempotent, and remains distinct from
`trigger_emergency`. Use `DryRunClient` and synchronization events; send no hardware command.

- [ ] **Step 2: Add a public runner seam only if those tests define safe behavior**

Implement `request_controlled_land()` on `FlightTestRunner` with its own latch and lock. Do not call
the private `_attempt_normal_landing` from UI code. The runner reports requested, acknowledged,
and completed separately and treats a land latch as a controlled terminal outcome, not emergency.

If integration has already supplied an equivalent tested method, adapt `SafetyControlPort` to it
and do not create a duplicate.

- [ ] **Step 3: Implement `SafetyControlPort` adapters**

```python
class SafetyControlPort(Protocol):
    def request_land(self) -> dict: ...
    def trigger_emergency(self) -> dict: ...


class RunnerSafetyAdapter:
    def __init__(self, runner, emergency_sender):
        self.runner = runner
        self.emergency_sender = emergency_sender

    def request_land(self) -> dict:
        self.runner.request_controlled_land()
        return {"status": "ACKNOWLEDGED"}

    def trigger_emergency(self) -> dict:
        sent = self.emergency_sender.send()
        return {"status": "ACKNOWLEDGED", "bytes_sent": sent}
```

Emergency uses the independent sender/runner emergency seam, never the mission executor queue.
Sanitize errors and preserve command identity in `ui_commands`.

- [ ] **Step 4: Implement browser safety controls**

Land is one click. Emergency requires pointer/keyboard hold for 1200 ms, has an accessible label,
and remains visible while ordinary controls disable. Show REQUESTED, ACKNOWLEDGED, COMPLETED, and
UNAVAILABLE accurately; never display “safe” merely because HTTP returned.

- [ ] **Step 5: Run race, adapter, and browser tests**

```powershell
rtk proxy uv run pytest tests/test_runner.py tests/ui/test_safety.py -q
rtk node --test tests/survey_console/safety.test.mjs
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
rtk git add src/sentinel/runner.py src/sentinel/ui/safety.py tests/test_runner.py tests/ui/test_safety.py examples/survey-console tests/survey_console
rtk git commit -m "feat(ui): connect controlled land and emergency"
```

### Task 11: Compose immutable Rehearsal and Live startup modes

**Files:**
- Create: `src/sentinel/ui/bootstrap.py`
- Create: `tests/ui/test_bootstrap.py`
- Modify: `src/sentinel/ui/__main__.py`
- Modify: `src/sentinel/ui/app.py`
- Modify: `src/sentinel/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `examples/survey-console/shell.mjs`

- [ ] **Step 1: Write startup-gate tests**

Test that Rehearsal starts without hardware, Live refuses without `--confirm`, site, tag map,
survey database, pose database, detector/provider configuration, consent, and emergency transport,
and Live never constructs a fixture adapter after a failed gate.

- [ ] **Step 2: Implement immutable configuration**

Add a `mission-control` CLI command with:

```text
--mode rehearsal|live
--host 127.0.0.1
--port 8080
--site PATH
--tag-map PATH
--survey-db PATH
--pose-db PATH
--pose off|passive|reanchor
--detector replay|moondream|gemini
--location TEXT
--consent-date YYYY-MM-DD
--confirm
```

Provider secrets remain environment-only and are never copied into `SessionConfig` projections.
The browser receives a read-only summary, not configuration controls.

- [ ] **Step 3: Compose adapters by mode**

`bootstrap.py` returns a `RehearsalService` or a `LiveMissionControlService`. Live composes the
survey projection, mission worker, approval broker, pose projection/evidence sink, and safety
adapter. Refuse startup if any required port is absent; never substitute the rehearsal service.

```python
def build_service(config: SessionConfig) -> MissionControlService:
    if config.mode == "REHEARSAL":
        return RehearsalService.from_fixture(config.rehearsal_fixture)
    validate_live_startup(config)
    return LiveMissionControlService(
        projection=build_live_projection(config),
        worker=build_mission_worker(config),
        approval=build_approval_broker(config),
        pose=build_pose_projection(config),
        safety=build_safety_adapter(config),
    )
```

- [ ] **Step 4: Run startup tests and CLI regressions**

```powershell
rtk proxy uv run pytest tests/ui/test_bootstrap.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
rtk git add src/sentinel/ui src/sentinel/cli.py tests/ui/test_bootstrap.py tests/test_cli.py examples/survey-console/shell.mjs
rtk git commit -m "feat(ui): compose rehearsal and live mission control"
```

### Task 12: Complete browser acceptance, documentation, and handoff

**Files:**
- Modify: `scripts/ui_assets/verify_survey_console.cjs`
- Modify: `examples/survey-console/README.md`
- Modify: `examples/survey-console/contract.md`
- Modify: `README.md`
- Create: `docs/handoffs/2026-09-21-mission-control.md`
- Modify: relevant tests under `tests/ui/` and `tests/survey_console/`

- [ ] **Step 1: Extend browser acceptance over the complete golden run**

The verifier must drive request, proposal, approval, route progress, fresh/aging/stale/recovered
pose, AprilTag overlays, commanded/measured markers, object evidence, uncertain count, follow-up,
report, Land availability, and Emergency availability. It also covers reject, conflict,
`IN_DOUBT`, forbidden evidence, event gap, disconnect/reconnect, and backend recovery-required.

- [ ] **Step 2: Assert presentation and network invariants**

At 1440x900, 1920x1080, and 390x844 assert no overflow, visible focus, reduced motion, no console
errors, no external requests, bounded frame requests, no overlapping event polls, and no raw path,
storage key, provider secret, or private prompt in DOM/network bodies.

- [ ] **Step 3: Run all focused suites**

```powershell
rtk node --test tests/survey_console/*.test.mjs
rtk proxy uv run pytest tests/ui tests/survey tests/pose tests/cube tests/test_runner.py tests/test_cli.py -q
```

Expected: all pass.

- [ ] **Step 4: Run the complete serial repository suite**

```powershell
rtk proxy uv run pytest -q --ignore=fixtures
```

Expected: all tests pass; only previously documented skips/warnings remain.

- [ ] **Step 5: Run browser verification from the real host**

```powershell
rtk uv run python -m sentinel.ui --mode rehearsal --host 127.0.0.1 --port 8770
rtk node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8770
```

Expected: verifier exits 0 and writes ignored screenshots/validation evidence under `artifacts/`.

- [ ] **Step 6: Document truthfully**

Update the README and handoff with exact implemented adapters, source commit, test counts, mode
labels, known unavailable signals, and the separate operator-authorized hardware gates. Do not
claim Live flight, Land completion, emergency success, or provider readiness from Rehearsal tests.

- [ ] **Step 7: Commit**

```powershell
rtk git add scripts/ui_assets examples/survey-console README.md docs/handoffs tests/ui tests/survey_console
rtk git commit -m "test(ui): complete mission control acceptance"
```

## Final hardware gate — operator-authorized, not part of automated execution

After all software verification passes, the operator separately authorizes and witnesses:

1. Live camera delivery with source/freshness labels.
2. AprilTag polygons bound to the same frame/report/hash as the pose.
3. Fresh → aging → stale → recovered pose behavior by temporarily occluding tags.
4. Commanded and measured positions on the same mission sheet.
5. Controlled Land from the UI, with requested/acknowledged/completed kept distinct.
6. Independent Emergency transport availability and its documented physical fallback.

Record observed evidence and exact run IDs in the handoff. A software acknowledgement alone never
proves physical completion.
