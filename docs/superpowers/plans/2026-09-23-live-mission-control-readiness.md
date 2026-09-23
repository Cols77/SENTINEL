# Live Mission Control Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the localhost Live Mission Control UI in an inert, unarmed state and expose safe, actionable readiness guidance while retaining flight, provider, consent, and emergency gates at capability use.

**Architecture:** Add a pure readiness evaluator plus inert/deferred Live adapters. Host composition never connects to Tello or a provider; planning is admitted from safe local configuration, while flight preflight, emergency transport initialization, and detector admission occur at dispatch. Project the readiness facts as an optional schema-v3 extension and render one accessible remediation panel.

**Tech Stack:** Python 3.11, pytest, NiceGUI/FastAPI, vanilla ES modules, Node test runner, JSON Schema.

---

## File map

- Create `src/sentinel/ui/readiness.py`: typed public checks, safe remediation strings, and pure Live configuration evaluation.
- Modify `src/sentinel/ui/live.py`: split safe planning preparation from deferred operational admission; remove startup drone preflight/provider construction.
- Modify `src/sentinel/ui/bootstrap.py`: always compose a Live service, using inert adapters when planning inputs are incomplete.
- Modify `src/sentinel/ui/service.py`, `chat.py`, `runtime.py`, and `projection.py`: own readiness, refuse unavailable proposals before worker start, and tolerate an empty route/options set.
- Modify `src/sentinel/ui/models.py` and `schema.py`: add the optional schema-v3 readiness projection and reference validation.
- Modify `examples/survey-console/mission-schema.mjs`, `mission-contract.mjs`, `view-model.mjs`, `mission-app.mjs`, and `console.css`: validate and render the readiness panel.
- Modify Python and Node tests plus Mission Control documentation.

### Task 1: Pure readiness model and safe remediation

**Files:**
- Create: `src/sentinel/ui/readiness.py`
- Modify: `tests/ui/test_bootstrap.py`
- Test: `tests/ui/test_models.py`

- [ ] **Step 1: Write failing tests for a bare Live configuration**

Add tests asserting that `evaluate_live_readiness(SessionConfig(mode='LIVE'))` returns four ordered checks (`planning`, `flight`, `detection`, `emergency`), overall `NOT_READY`, stable reason codes, and remediation that names required flags without containing environment values. Inject sentinel API secrets and assert they are absent from `json.dumps(readiness.public())`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_bootstrap.py tests/ui/test_models.py -q`

Expected: failure because `sentinel.ui.readiness` and the readiness projection do not exist.

- [ ] **Step 3: Implement the typed evaluator**

Create immutable `ReadinessCheck` and `LiveReadiness` dataclasses. Expose only:

```python
def evaluate_live_readiness(config: SessionConfig) -> LiveReadiness: ...

@dataclass(frozen=True)
class ReadinessCheck:
    capability: Literal['planning', 'flight', 'detection', 'emergency']
    ready: bool
    reason_code: str | None
    remediation: str | None

@dataclass(frozen=True)
class LiveReadiness:
    state: Literal['NOT_READY', 'READY_TO_PLAN', 'READY_TO_DISPATCH', 'ACTIVE']
    checks: tuple[ReadinessCheck, ...]
    command_template: str
    def public(self) -> dict: ...
```

Evaluation may inspect local paths, parse site/tag-map/profile documents, inspect environment-variable presence, and read consent. It must not construct a drone client, bind/send emergency transport, construct an HTTP provider session, or include secret values. The command template uses placeholders such as `<LOCAL_IP>` and environment-variable names.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_bootstrap.py tests/ui/test_models.py -q`

- [ ] **Step 5: Commit**

Commit message: `feat: model live mission readiness`

### Task 2: Deferred Live composition and fail-closed admission

**Files:**
- Modify: `src/sentinel/ui/bootstrap.py`
- Modify: `src/sentinel/ui/live.py`
- Modify: `src/sentinel/ui/service.py`
- Modify: `src/sentinel/ui/chat.py`
- Modify: `src/sentinel/ui/runtime.py`
- Test: `tests/ui/test_bootstrap.py`
- Test: `tests/ui/test_chat.py`
- Test: `tests/ui/test_runtime.py`

- [ ] **Step 1: Replace startup-refusal tests with inert-host tests**

Write tests proving that `build_service(SessionConfig(mode='LIVE', data_dir=tmp_path))` succeeds, that snapshot/session reads work, and that injected `make_client`, provider constructors, `check_emergency_transport`, and Rehearsal adapter factories are never called. Add a chat test proving a count request returns an actionable `REFUSE`/readiness response and does not start `MissionWorker` when planning is unavailable.

- [ ] **Step 2: Write dispatch-boundary tests**

For a locally valid Live configuration, assert host construction still makes zero client/provider/transport calls. Approve a proposal with `confirm=False` and assert dispatch fails closed before `make_client`. With `confirm=True`, inject a dry-run client and assert preflight happens only after approval. Assert detector construction/supervisor start is refused before provider authorization and recorded consent, without a Rehearsal fallback.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_bootstrap.py tests/ui/test_chat.py tests/ui/test_runtime.py -q`

Expected: current Live startup raises `StartupError` and performs preflight during composition.

- [ ] **Step 4: Implement inert and deferred adapters**

Split `live.validate_config/compose` into safe planning preparation and operational admission. A bare Live service uses empty options, `RuleProposer`, an inert flight boundary, no provider transport, and `source_kind='LIVE'`. When planning is ready, create route options and the real policy/camera, but keep flight and detector construction deferred. Move `FlightTestRunner(...).preflight()` and independent emergency initialization out of `compose()` and into the dispatch path. Check `confirm` immediately before client creation. Check provider env/profile, paid-call authorization, and consent immediately before detection-supervisor startup. Preserve explicit `StartupError`/public reason codes and never select Rehearsal adapters.

- [ ] **Step 5: Guard chat before worker start**

When `SurveyDecisionSession` returns `PROPOSE`, `ChatService` checks `service.readiness` planning admission first. If unavailable, settle the same idempotent command as `ACKNOWLEDGED`, `kind='REFUSE'`, with allowlisted remediation; do not start the worker. Status chat remains available.

- [ ] **Step 6: Make empty options safe**

`ProjectionService` and `SurveyRuntime.facts()` must return an empty route, `envelope=None`, zero counts, and no proposal when Live planning is unavailable. They must not index `options[0]` or require a policy provenance that is absent.

- [ ] **Step 7: Run focused tests and confirm GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_bootstrap.py tests/ui/test_chat.py tests/ui/test_runtime.py -q`

- [ ] **Step 8: Commit**

Commit message: `feat: defer live hardware admission`

### Task 3: Schema-v3 readiness projection

**Files:**
- Modify: `src/sentinel/ui/schema.py`
- Modify: `src/sentinel/ui/models.py`
- Modify: `src/sentinel/ui/projection.py`
- Modify: `examples/survey-console/mission-schema.mjs`
- Modify: `examples/survey-console/mission-contract.mjs`
- Modify: `examples/survey-console/view-model.mjs`
- Test: `tests/ui/test_models.py`
- Test: `tests/ui/test_api.py`
- Test: `tests/survey_console/mission-contract.test.mjs`

- [ ] **Step 1: Write contract tests**

Add Python and JavaScript cases for all four readiness states, ordered unique capability checks, stable reason/remediation pairing, secret-free serialization, and malformed/cross-type data. Confirm existing schema-v3 fixtures without `readiness` remain valid so this is an optional v3 extension.

- [ ] **Step 2: Run contract tests and confirm RED**

Run: `.venv\Scripts\python.exe -m pytest tests/ui/test_models.py tests/ui/test_api.py -q`

Run: `node --test tests/survey_console/mission-contract.test.mjs`

- [ ] **Step 3: Extend both validators together**

Add an optional top-level `readiness` object with `state`, `checks`, and `command_template`. Every emitted backend snapshot includes it; validators still accept older schema-v3 snapshots that omit it. `toSurveyView()` supplies a safe default when absent.

- [ ] **Step 4: Project controls from capability readiness**

Set proposal/chat legality from planning readiness, Land availability from an active admitted runner, and Emergency availability only from an initialized independent transport. Do not infer readiness from UI timers or button state.

- [ ] **Step 5: Run contract tests and confirm GREEN**

Run both commands from Step 2.

- [ ] **Step 6: Commit**

Commit message: `feat: project live readiness facts`

### Task 4: Accessible readiness panel

**Files:**
- Modify: `examples/survey-console/mission-app.mjs`
- Modify: `examples/survey-console/view-model.mjs`
- Modify: `examples/survey-console/console.css`
- Test: `tests/survey_console/status.test.mjs`
- Test: `tests/survey_console/mission-controller.test.mjs`
- Modify: `scripts/ui_assets/verify_mission_control.cjs`

- [ ] **Step 1: Write DOM/view-model tests**

Cover Live `NOT_READY`, partial `READY_TO_PLAN`, and configured `READY_TO_DISPATCH` snapshots. Assert the panel identifies Live/unarmed state, lists only failed checks, exposes the copyable command template as text, and gives disabled controls accessible reasons. Rehearsal must not show Live remediation.

- [ ] **Step 2: Run Node tests and confirm RED**

Run: `node --test tests/survey_console/status.test.mjs tests/survey_console/mission-controller.test.mjs`

- [ ] **Step 3: Implement the panel**

Render a compact `<section aria-labelledby="live-readiness-title">` near the command-bar status. Use text nodes only. Show capability labels, remediation, and a `<code>` command template; do not add configuration inputs or clipboard permissions. Maintain narrow-layout behavior, reduced motion, and existing chat/safety controls.

- [ ] **Step 4: Extend real-host acceptance**

Start a bare Live host on a temporary data directory, verify `/console/` renders `NOT_READY`, the command template is visible, chat status works, count requests are refused without dispatch, safety controls are disabled, and no injected hardware/provider boundary was called. Retain all existing Rehearsal scenarios and viewport checks.

- [ ] **Step 5: Run Node tests and browser acceptance**

Run: `node --test tests/survey_console/*.test.mjs`

Run: `node scripts/ui_assets/verify_mission_control.cjs`

- [ ] **Step 6: Commit**

Commit message: `feat: show live readiness guidance`

### Task 5: Documentation and final verification

**Files:**
- Modify: `examples/survey-console/README.md`
- Modify: `examples/survey-console/contract.md`
- Modify: `docs/handoffs/2026-09-21-mission-control.md`

- [ ] **Step 1: Document the two-phase Live workflow**

State that bare Live starts unarmed, remediation never contains secrets, restart is required after changing CLI configuration, and approval is distinct from flight confirmation/preflight/provider consent. Retain hardware-only limitations.

- [ ] **Step 2: Run focused and full verification**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/ui -q
node --test tests/survey_console/*.test.mjs
.venv\Scripts\python.exe scripts/ui_assets/check_assets.py
.venv\Scripts\python.exe -m pytest -q --ignore=fixtures
node scripts/ui_assets/verify_mission_control.cjs
git diff --check
```

Expected: all commands exit zero. If asset validation rewrites generated platform artifacts, inspect and restore only those generated changes before committing.

- [ ] **Step 3: Review the requirement checklist**

Confirm: bare Live host serves; no startup drone/provider calls; actionable secret-free message; no fallback; planning/dispatch/detection/emergency gates remain independent; existing routes and schema version remain stable; Rehearsal behavior is unchanged.

- [ ] **Step 4: Commit**

Commit message: `docs: explain unarmed live mission control`

