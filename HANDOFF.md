# SENTINEL handoff

## State

- This isolated worktree applies the reviewed-zone/defer-only update on top of the existing V0 specification branch. The original shared checkout remains untouched; the branch is pushed as `docs/sentinel-v0-defer-only` and tracked by GitHub PR #6.
- This branch defines the reworked V0 specification set under `docs/superpowers/specs/` for the six-zone indoor-alley survey: the canonical V0 coherence spec, an approved indoor-alley survey design (the V0 decision record), WP0 shared contracts, and WP1–WP4 component specs, plus `docs/safety.md`.
- No push to `main` was performed. No real-drone or USB-adapter validation was claimed.

## V0 profile

- Fixed six-zone alley route (`LEFT_ALLEY_FRONT/MIDDLE/BACK`, `RIGHT_ALLEY_FRONT/MIDDLE/BACK`) in an indoor room safety envelope, with an operator-selected layout mode (marker mode default; `ROUTE_ONLY_2_ZONE` explicit fallback). A missing expected marker in marker mode is a failure.
- Immutable `SurveyPlanReference` + `approval_reference` + exact `PreflightReport` bound to dispatch; operator approves the exact plan version before `DRONE_DISPATCH_REQUESTED`.
- Provider gate: named joint Colin+Dzivha approval over a versioned, reproducible test-image-set hash (approval/review shared; execution of provider work is Dzivha's). No candidate list, no fixed 0.90 threshold.
- Terminal run outcomes: `COMPLETED`, `FAILED`, `EMERGENCY`; `NEEDS_REVIEW` is a review-hold resolved by operator review of the current zone result; no automatic revisit occurs in V0. `PLAN_REJECTED` is event-only and returns to plan refinement/re-proposal; a zone-result `REJECT` is recorded by `ZONE_RESULT_REVIEWED` and advances to the next fixed zone or completes the run without revisit.
- Preflight is exactly the 8 named checks (`WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH`); operator authorization is the plan's `approval_reference` evaluated at dispatch, not a preflight check.
- Real execution is DJITelloPy-only, fresh-process with fresh preflight and `--real --confirm`. V0 uses live frame capture plus `VideoStatus`; `SafeVideoStreamHandle` and served browser video are retained but deferred to V0.1. The `EMERGENCY` latch is terminal and non-overridable; force-run after a failed preflight is operator-only `FORCE_RUN_CREATE` naming a new run/plan, never a resume, never under an active latch, always with fresh preflight and `--real --confirm`.

## Python implementation handoff

The authoritative Python package boundaries and protocol signatures are in [`WP0 §6.1`](docs/superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md#61-python-implementation-handoff). This section is the routing summary; it does not add a second contract.

- **Colin / WP0–WP1–WP4:** owns `sentinel.contracts`, canonical fixtures, `sentinel.coordinator`, the `CoordinatorPort`/`AgentReadToolPort`/`WorkerPort`/`PerceptionPort` declarations, SQLite/state semantics, the deterministic fake worker, replay/restart harness and the physical worker.
- **Alan / WP1–WP2:** owns `sentinel.adapter.llm` and `sentinel.ui`. He implements `AgentModelPort`, the parser/config/fake agent, and an injected `CoordinatorClient` test double. The UI sends complete `CommandEnvelope` values and consumes read models; it does not define a second coordinator protocol or access SQLite.
- **Dzivha / WP3:** owns `sentinel.perception`, `PerceptionPort` implementation, provider-neutral normalization, fake perception responses and `fixtures/perception/`. The adapter returns only `PerceptionResult`; it does not mutate coordinator state or control the worker.
- **Composition:** the coordinator receives the three ports by injection. Fakes are `SIMULATED`, require no credentials/network, and are rejected from judged use; replay fixtures are `REPLAYED` and are also rejected. All shared wire payloads are validated through `sentinel.contracts` against the named WP0 schema anchor; the only explicit Python-only exceptions are the `AgentToolContext` capability context and `EvidenceRead`/`EvidenceContentHandle` defined in WP0 §6.
- `EvidenceRead` is the Python-only result of run-scoped `read_evidence(run_id, artifact_id)`: a typed `ArtifactRef` plus an opaque coordinator-created capability. It permits only re-authorized JPEG/PNG reads bounded to 10 MiB and the configured timeout, expires/revokes fail closed, and never exposes a filesystem path, `storage_key`, credentials, unbounded URL or raw video bytes.
- **Agent tools:** V0 remains proposal-only for state mutation and physical action, but the agent may choose exactly three coordinator-mediated, read-only tools: `read_state`, `read_mountain` and bounded `read_events`, each receiving an immutable coordinator-created `AgentToolContext` bound to `agent_run_id`, `run_id`, state version, request identity, allowed references and deadline. Direct SQLite, SQL, filesystem, provider, perception or worker access remains prohibited; evidence/file reads, perception requests and operation proposals require a later contract amendment.
- **Deferred capability guard:** a later operation proposal is non-executing and must resolve to the canonical immutable catalogue plan → operator `PlanApproval` → fresh preflight → `PENDING` → at-most-one dispatch → reconciliation flow; a later perception tool must construct/validate the canonical `PerceptionRequest`, enforce hash/source/profile/threshold provenance, and persist only validated `PerceptionResult` data through coordinator ingress. The agent never receives `WorkerPort`, emergency, force-run, operator-decision or redispatch authority.
- Reviewed-zone decision — `OPERATOR_DECIDE` accepts an active `ZONE_RESULT` target and persists `ZONE_RESULT_REVIEWED`; `TASK_CREATE`, `TASK_RANK`, patroller/assignment lifecycle, target aggregation, precise hazard geometry and automatic revisit remain retained V0.1 contracts and are not emitted by V0.
- Actor authorization — the coordinator rejects every actor/command pair outside the WP0 matrix with `FORBIDDEN_ACTOR_COMMAND`; operator decisions/force-run are operator-only, worker facts/results use typed ports, and system commands are limited to low-level emergency-latch ingress.

A contributor may change code only inside the owned boundary unless the change is a WP0 contract amendment agreed before implementation. The package checks are `uv run python -m pytest tests/contracts -q`, `uv run python -m pytest tests/coordinator tests/agent -q`, `uv run python -m pytest tests/ui -q`, and `uv run python -m pytest tests/perception -q`; passing one package does not accept another package.

## Gate status (G0–G4)

- **G0 Contract** (Colin): pending — schema/fixture validation, DDL constraint manifest, contract tests.
- **G1 Coordinator simulation** (Colin + Alan): pending — deterministic fake-agent replay, duplicate and `IN_DOUBT` tests.
- **G2 Human surface** (Alan): pending — NiceGUI smoke fixture, click-through evidence, Confirm/Reject stale-version test.
- **G3 Perception** (execution Dzivha; approved/reviewed Colin + Dzivha): **BLOCKED** — only the `UNAPPROVED` template is checked in; requires a named joint approval over a versioned test-image-set hash plus reproducible evaluation-manifest evidence.
- **G4 Physical** (Colin): pending — supervised one-Tello rehearsal using WP0 operation/evidence contracts; cannot be substituted by dry-run success.

## Verification

From this branch:

```text
uv run python -m pytest -q
105 passed

uv run pytest tests/test_indoor_alley_contract_probe.py -q
11 passed
```

The V0 contract root is `docs/superpowers/specs/contracts/sentinel-v0-contract.schema.json`, with the `oneOf` port registry at `sentinel-v0-wire.schema.json`. The live yaw-scan fixture is `fixtures/perception/live/yaw-scan-20260916.h264` (plus `.manifest.json`/`.mp4`).

## Hardware next step

Install the package on the target machine, connect the USB Wi-Fi adapter to the Tello SSID, verify the main adapter still owns the internet route, then run one supervised real test with `--confirm`. Use an explicit `--local-ip` for the USB adapter if automatic selection is ambiguous. Keep a second terminal ready with `python -m sentinel emergency --local-ip <usb-ip>`; pressing `E` in the run terminal sends the same terminal motor-stop command.

Each real run must be started as a fresh process because the pinned DJITelloPy receiver threads are process-global. Full flight details, force-run semantics and the terminal emergency latch are in `docs/safety.md`.

## Follow-up completion

- Superseded direct flight-test material is archived under
  `docs/superpowers/specs/superseded/` with replacement headers, a replacement
  map, and links to the active V0 contracts. The broad historical worker
  specification remains in place as non-authoritative support, as required by
  the V0 design.