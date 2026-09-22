# Room Agent Calibration and Live Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make room-agent runs reproducible and promote each physical capability only after calibrated replay, simulation and healthy-charge hardware evidence.

**Architecture:** Versioned manifests bind code, site, models, profiles, physical limits, provider deadlines and authorization before any side effect. Supervised fixed-route calibration produces profiles; independent holdouts promote them. A CLI supports preview, replay, simulation and explicit live execution while automated tests inject every external dependency.

**Tech Stack:** JSON/JSONL, JSON Schema, SQLite snapshots, pytest, existing consent/provider configuration, cube dry-run and real client factories.

**Approved design:** [Jev Agentic Room Session and Bounded Localization Recovery](../specs/2026-09-22-jev-agentic-room-design.md)

---

## File map

- `room/artifacts.py`: immutable authorization manifest plus append-only events and SQLite backup.
- `room/capabilities.py`: hashed disabled/trial/promoted grants consumed by live admission.
- `room/sim.py`: injected clock/battery/pose/provider/client fault simulator.
- `room/calibration.py` and `scripts/room_calibrate.py`: supervised trial ingestion and independent promotion.
- `room/connectivity.py`: motors-off drone/provider preflight and recorded freshness gate.
- `scripts/room_session.py`: preview/replay/simulate/live entrypoint with dependency factories.
- `scripts/room_verify_artifact.py`: deterministic terminal/hash/profile verifier used by the runbook.
- `docs/reports/evidence/room-agent/`: tracked sanitized evidence indexes and terminal summaries; raw/video remain under ignored artifacts.

### Task 1: Define manifests and append-only artifacts

**Files:**
- Create: `src/sentinel/room/artifacts.py`
- Create: `src/sentinel/room/capabilities.py`
- Create: `scripts/room_capability.py`
- Create: `docs/superpowers/specs/contracts/room-session-manifest-v1.schema.json`
- Create: `docs/superpowers/specs/contracts/room-capability-grant-v1.schema.json`
- Create: `fixtures/room_agent/v1/capability-grants.json`
- Create: `tests/room/test_artifacts.py`
- Create: `tests/room/test_capabilities.py`

- [ ] **Step 1: Write failing manifest/artifact tests**

Manifest fields bind code revision/dirty state, integration revision, mode, requested capability, capability-grant hash/state, request/goal-parser revision, site/tag-map/camera/profile hashes, aircraft/firmware, requested/pinned Jev and detector identities, physical duration/distance limits, recovery-attempt limits, per-wait provider deadlines, cost assumptions, home/termination policy, consent references and authorization digest. Compute `authorization_digest` over UTF-8 JSON with sorted keys, compact separators, finite numbers and the derived digest field omitted; live recomputes that exact projection from loaded bytes before comparing the supplied digest. There is no arbitrary total model-call cap; relevant-state progress, refusal suppression, wall-clock/physical bounds and no-progress termination govern the session. The immutable authorized manifest records resolved identities as `UNVERIFIED`; first provider observations append the actual resolved identity and fail closed on mismatch. Terminal/summary records report verified identities without rewriting the manifest.

Implement the Plan-01 `CapabilityGrant` registry as canonical JSON. Ordinary live mode admits only a matching unexpired `PROMOTED` grant. Acceptance mode admits exactly one matching `TRIAL` capability when the manifest also names the trial protocol and evidence destination; unrelated capabilities remain disabled. Tests prove a trial cannot authorize a second capability or an ordinary live session, and changing site, aircraft, profiles, evidence or expiry invalidates the grant hash.

`scripts/room_capability.py init --registry PATH` creates an empty canonical live registry. `issue-trial` requires capability, trial protocol, site, zero or more candidate profiles, evidence destination and registry path; it writes one narrowly scoped grant after displaying its digest. `promote` requires that trial grant plus a passing tracked evidence index and writes a new `PROMOTED` grant without mutating the trial. Synthetic fixture grants carry `SIMULATION_ONLY` and are rejected by live mode.

Test default preview before output/provider/client creation, explicit `--manifest-out` writing only the canonical manifest after local validation, byte-identical stdout/file digests, credential/header exclusion, append-only monotonic event IDs, flush/fsync, interrupted finalization, immutable terminal record, and quiescent SQLite-backup integrity with WAL enabled.

- [ ] **Step 2: Run the focused test and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_artifacts.py tests/room/test_capabilities.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection or assertions fail because the manifest schema and append-only artifact writer do not exist.

- [ ] **Step 3: Implement artifact layout**

```text
artifacts/room-agent/{manifest.run_id}/
  manifest.json
  raw.jsonl
  session.sqlite3
  terminal.json
  summary.json
  report.md
  frames/frames.jsonl
  detections.jsonl
  flight.h264
```

Create `flight.h264` only for authorized retained live video.

- [ ] **Step 4: Verify green and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_artifacts.py tests/room/test_capabilities.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/artifacts.py src/sentinel/room/capabilities.py scripts/room_capability.py docs/superpowers/specs/contracts/room-session-manifest-v1.schema.json docs/superpowers/specs/contracts/room-capability-grant-v1.schema.json fixtures/room_agent/v1/capability-grants.json tests/room/test_artifacts.py tests/room/test_capabilities.py
rtk git commit -m "feat(room): bind reproducible session artifacts"
```

Expected: all selected tests pass; default preview leaves no files, explicit manifest output contains only the canonical authorized bytes, and a verified SQLite backup opens without recovery.

### Task 2: Add replay and fault simulation

**Files:**
- Create: `src/sentinel/room/sim.py`
- Create: `tests/room/test_sim.py`
- Create: `fixtures/room_agent/v1/faults/manifest.json`
- Create: `fixtures/room_agent/v1/faults/events.jsonl`

- [ ] **Step 1: Write failing fault scenarios**

Cover zero-motion reads, count/return, closer confirmed/rejected, temporary tag loss, recovery success/failure, reserve return, provider stall, SDK completion unknown, duplicate/late detection, stale Jev decision, no-progress termination, aborted flight and failed report. Simulation controls clock, battery, pose, frames, detector/Jev results and command outcomes.

- [ ] **Step 2: Run the focused tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_sim.py tests/room/test_replay.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection or assertions fail because the deterministic simulator and fault fixtures do not exist.

- [ ] **Step 3: Implement deterministic simulator and replay comparison**

Same seed/input must produce byte-identical normalized events and terminal outcome. Missing report, home return or terminal artifact cannot score complete. Real providers/clients fail tests if constructed.

- [ ] **Step 4: Run green and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_sim.py tests/room/test_replay.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/sim.py tests/room/test_sim.py fixtures/room_agent/v1/faults/manifest.json fixtures/room_agent/v1/faults/events.jsonl
rtk git commit -m "test(room): simulate adaptive session faults"
```

Expected: the tests pass twice with byte-identical normalized events and no real dependency construction.

### Task 3: Generate and promote calibration profiles

**Files:**
- Create: `src/sentinel/room/calibration.py`
- Create: `scripts/room_calibrate.py`
- Create: `docs/superpowers/specs/contracts/room-calibration-profile-v1.schema.json`
- Create: `tests/room/test_calibration.py`
- Create: `fixtures/room_agent/v1/calibration/return-route.json`
- Create: `fixtures/room_agent/v1/calibration/tag-free-hover.json`
- Create: `fixtures/room_agent/v1/calibration/tracker-route.json`

- [ ] **Step 1: Write failing calibration tests**

Profiles bind site, aircraft, firmware, camera, tag map, speeds and source trials. Separate battery burn/timing, pose drift/recovery and tracker freshness profiles. `CALIBRATION` trials are supervised fixed routes and cannot enable autonomy. Promotion requires distinct holdout trial IDs; training/profile records cannot approve themselves. Synthetic profiles are `SIMULATION_ONLY`.

- [ ] **Step 2: Run the focused test and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_calibration.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection or assertions fail because profile generation, holdout promotion and the calibration CLI do not exist.

- [ ] **Step 3: Implement offline generator/promotion checks**

Battery bound uses worst observed consumption plus declared margin and quantization allowance. Pose envelope contains only sampled distance/time/yaw combinations. Tracker profile records measured update latency/loss behavior. Mismatched hashes or absent holdouts disable the affected capability.

The CLI contract is exact: `--mode preview|record|fit|promote`; preview requires `--target-mode record --trial-role training|holdout --split-id ID`, record requires `--execute --manifest --authorization-digest --route`, fit requires `--profile-kind flight|tracker --evidence-index --output`, and promote additionally requires `--candidate`. Preview binds the role and split ID into the canonical authorization digest, prints those canonical bytes and optionally writes only those exact bytes to an explicitly supplied `--manifest-out`. Record accepts the manifest-bound role without a caller override, uses only a controller-owned fixed calibration route and cannot invoke Jev. `fit` consumes only manifests predeclared as training and writes a `CALIBRATION` candidate profile that is legal only in a matching acceptance trial. Promote is offline, requires distinct manifests predeclared as holdout in the evidence index, and rejects role changes, duplicate run IDs, any holdout used during fitting, any flight candidate without forced-return plus tag-free/reanchor evidence, or any tracker candidate without measured freshness/loss holdouts. Tests cover each rejection.

- [ ] **Step 4: Run green and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_calibration.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add src/sentinel/room/calibration.py scripts/room_calibrate.py docs/superpowers/specs/contracts/room-calibration-profile-v1.schema.json tests/room/test_calibration.py fixtures/room_agent/v1/calibration/return-route.json fixtures/room_agent/v1/calibration/tag-free-hover.json fixtures/room_agent/v1/calibration/tracker-route.json
rtk git commit -m "feat(room): generate promotable calibration profiles"
```

Expected: all selected tests pass; synthetic or self-validating evidence cannot produce a `PROMOTED` profile.

### Task 4: Add session CLI with explicit modes

**Files:**
- Create: `scripts/room_session.py`
- Create: `scripts/room_verify_artifact.py`
- Create: `src/sentinel/room/connectivity.py`
- Create: `tests/room/test_cli.py`
- Create: `tests/room/test_verify_artifact.py`
- Create: `docs/room-agent-live-runbook.md`

- [ ] **Step 1: Write failing CLI tests**

The CLI uses `--mode preview|replay|simulate|live`; preview also requires `--target-mode simulate|live`, replay/simulate require `--fixture`, and live requires `--execute`, the exact preview manifest path, and the authorization digest printed for those bytes. Capability admission uses `--capability`, `--grant-registry`, repeatable `--profile`, and optional `--trial-protocol`; the last is legal only for a matching `TRIAL` grant. Preview always emits canonical manifest JSON to stdout; with explicit `--manifest-out`, it atomically writes exactly those bytes and computes the digest over the bytes live later loads. Without that option it creates nothing. Live accepts `--check-connectivity-only`, which must terminate `CONNECTIVITY_READY` without sending takeoff. This motors-off diagnostic is the sole capability-grant exemption and still requires manifest authorization, provider/filming consent and zero flight commands. Preview prints maximum physical duration/distance, recovery-attempt limits, provider deadlines and cost estimate, configuration, missing gates and authorization digest. It records no total model-call limit. Replay/simulate never construct network/drone clients. Every flight-capable live mode requires a matching `PROMOTED` grant, or exactly one manifest-bound `TRIAL` grant in acceptance mode, plus matching profiles, provider/filming consent, site assurance and execution authorization before run-artifact, provider or client initialization. After those gates but before takeoff, a motors-off connectivity preflight proves fresh drone telemetry and provider reachability concurrently, records both timestamps/identities, and refuses takeoff if either path fails or expires. One invocation authorizes the bounded session; no per-action prompts.

- [ ] **Step 2: Run the focused tests and verify red**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_cli.py tests/room/test_verify_artifact.py tests/room/test_artifacts.py tests/room/test_calibration.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: collection or assertions fail because the CLI, dual-path preflight and factory ordering are absent.

- [ ] **Step 3: Implement dependency-injected CLI**

Factories for Jev, detector, client, clock and store are injectable. Defaults are constructed only after authorization gates. `connectivity.py` performs the explicit motors-off dual-path check without creating a second flight client. `room_verify_artifact.py --manifest PATH --expect-terminal STATE --expect-capability NAME [--require-event EVENT_TYPE]` resolves the manifest's deterministic run ID, verifies manifest/terminal/profile/event hashes and exits nonzero on a missing field/event, mismatched identity, wrong terminal state or absent safe-termination evidence. Print the exact preview-derived live command for manual execution; never launch live mode from tests or this plan.

- [ ] **Step 4: Run green and commit**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_cli.py tests/room/test_verify_artifact.py tests/room/test_artifacts.py tests/room/test_calibration.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk git add scripts/room_session.py scripts/room_verify_artifact.py src/sentinel/room/connectivity.py tests/room/test_cli.py tests/room/test_verify_artifact.py docs/room-agent-live-runbook.md
rtk git commit -m "feat(room): add manifest-bound session CLI"
```

Expected: all selected tests pass; unauthorized, replay and simulation paths instantiate neither provider nor drone client, and connectivity-only sends no takeoff.

### Task 5: Execute staged non-hardware verification

**Files:**
- Update: `docs/reports/room-agent-count-readiness.md`
- Create per-stage evidence under `artifacts/room-agent/` without committing credentials/video.

- [ ] **Step 1: Run preview, replay and simulation scenarios**

Run these exact offline commands after the CLI implements the named options:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode simulate --request "count the computers and phones in the surveyed room, validate uncertain detections, then return home" --site fixtures/room_agent/v1/semantic-site.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode replay --fixture fixtures/room_agent/v1 --request "count the computers and phones in the surveyed room, validate uncertain detections, then return home"
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode simulate --fixture fixtures/room_agent/v1/manifest.json --request "count the computers and phones in the surveyed room, validate uncertain detections, then return home"
```

Expected: preview creates no artifact directory; replay/simulation terminate with their declared status and matching normalized hashes. No hardware/provider call is made.

- [ ] **Step 2: Run affected and full suites serially**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room tests/cube tests/survey/test_cube_bridge.py tests/survey/test_live_detect.py tests/survey/test_loop.py tests/survey/test_store.py tests/coordinator/test_jev_proposer.py tests/coordinator/test_jev_proposer_flow.py -q --tb=short -o addopts='' -p no:cacheprovider
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests -q --tb=short -o addopts='' -p no:cacheprovider
rtk git diff --check
rtk git status --short --branch
```

Expected: all pass, no automatic live calls, and only the declared report/evidence files remain modified before their exact commit step.

- [ ] **Step 3: Independent review/fix loop**

Dispatch separate specification and quality reviewers. Probe manifest/consent ordering, replay tampering, reserve arithmetic, pose-loss transitions, uncertain SDK completion, capability gating and accidental live construction. Fresh fix agents address important findings; repeat affected/full serial suites.

- [ ] **Step 4: Commit non-hardware verification evidence**

Create `docs/reports/evidence/room-agent/offline-v1.json` containing artifact hashes, source mode, terminal states and repository-relative retained fixture paths. Every report hash must resolve to a tracked fixture or sanitized terminal/manifest summary; ignored raw/video artifacts alone cannot support promotion.

```powershell
rtk git add docs/reports/room-agent-count-readiness.md docs/reports/evidence/room-agent/offline-v1.json fixtures/room_agent/v1/manifest.json fixtures/room_agent/v1/events.jsonl fixtures/room_agent/v1/faults/manifest.json fixtures/room_agent/v1/faults/events.jsonl
rtk git commit -m "test(room): record offline readiness evidence"
```

### Task 6: Promote hardware capabilities one at a time

**Files:**
- Update: `docs/room-agent-live-runbook.md`
- Update: `docs/reports/room-agent-count-readiness.md`
- Create: `docs/reports/evidence/room-agent/calibration-candidate-v1.json`
- Create: `docs/reports/evidence/room-agent/tracker-calibration-v1.json`
- Create: `docs/reports/evidence/room-agent/safety-v1.json`
- Create: `docs/reports/evidence/room-agent/closer-inspection-v1.json`
- Create: `docs/reports/evidence/room-agent/count-return-v1.json`
- Create after Plan 04 if measured: `docs/reports/evidence/room-agent/certified-landing-v1.json`
- Create after Plan 05 if measured: `docs/reports/evidence/room-agent/bounded-follow-v1.json`
- Store immutable trial artifacts under `artifacts/room-agent/{manifest.run_id}/`.

- [ ] **Step 1: Connectivity and supervised calibration**

Create the exact connectivity manifest:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py init --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability connectivity --request "verify room-agent connectivity with motors off" --site fixtures/room_agent/v1/semantic-site.json --grant-registry artifacts/room-agent/capability-grants.json --manifest-out artifacts/room-agent/authorizations/connectivity-preview.json
```

Execute only the exact live command printed by preview, adding its existing authorization digest through the documented consent mechanism. It includes `--check-connectivity-only`. Then run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_verify_artifact.py --manifest artifacts/room-agent/authorizations/connectivity-preview.json --expect-terminal CONNECTIVITY_READY --expect-capability connectivity
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode preview --target-mode record --trial-role training --split-id flight-return-training-v1 --route fixtures/room_agent/v1/calibration/return-route.json --site fixtures/room_agent/v1/semantic-site.json --manifest-out artifacts/room-agent/authorizations/calibration-return-training-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode preview --target-mode record --trial-role training --split-id flight-tag-free-training-v1 --route fixtures/room_agent/v1/calibration/tag-free-hover.json --site fixtures/room_agent/v1/semantic-site.json --manifest-out artifacts/room-agent/authorizations/calibration-tag-free-training-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode preview --target-mode record --trial-role holdout --split-id flight-return-holdout-v1 --route fixtures/room_agent/v1/calibration/return-route.json --site fixtures/room_agent/v1/semantic-site.json --manifest-out artifacts/room-agent/authorizations/calibration-return-holdout-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode preview --target-mode record --trial-role holdout --split-id flight-tag-free-holdout-v1 --route fixtures/room_agent/v1/calibration/tag-free-hover.json --site fixtures/room_agent/v1/semantic-site.json --manifest-out artifacts/room-agent/authorizations/calibration-tag-free-holdout-preview.json
```

Execute only each printed manifest-bound record command for supervised fixed routes at healthy charge, resetting the trial between the training and holdout runs. These routes cannot invoke Jev or enable adaptive autonomy. Abort on stale/missing telemetry, pose outside the site envelope, link degradation, unexpected occupancy, or any emergency latch. Do not drain a battery or induce link loss. Write `calibration-candidate-v1.json` with the immutable manifest roles, distinct training/holdout run IDs and raw bounds, then create the trial-only candidate from the predeclared training runs only:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode fit --profile-kind flight --evidence-index docs/reports/evidence/room-agent/calibration-candidate-v1.json --output artifacts/room-agent/profiles/room-flight-candidate-v1.json
```

Expected: the candidate has execution scope `CALIBRATION`; do not promote it yet.

- [ ] **Step 2: Safety mechanics**

Using a manifest-bound `TRIAL` grant and healthy charge, run the controller-owned forced-return protocol with a deliberately conservative test floor that makes objective 2 inadmissible while home remains affordable; expected terminal state is `INCOMPLETE_RETURNED` at/above the declared landing floor. Then run controlled tag occlusion at stationary hover with a cleared yaw volume and operator emergency control: coasting, recovery before exhaustion, reanchor, and safe return/land. Abort on containment alert, uncertain command completion, recovery deadline, stale battery or unexpected occupancy. Verify no objective resumes after return latch and no translation occurs in `LOST`.

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py issue-trial --capability adaptive_movement --trial-protocol forced-return-v1 --site fixtures/room_agent/v1/semantic-site.json --profile artifacts/room-agent/profiles/room-flight-candidate-v1.json --evidence-destination docs/reports/evidence/room-agent/safety-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py issue-trial --capability adaptive_movement --trial-protocol pose-recovery-v1 --site fixtures/room_agent/v1/semantic-site.json --profile artifacts/room-agent/profiles/room-flight-candidate-v1.json --evidence-destination docs/reports/evidence/room-agent/safety-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability adaptive_movement --trial-protocol forced-return-v1 --request "visit the front left and front right regions, then return home" --site fixtures/room_agent/v1/semantic-site.json --grant-registry artifacts/room-agent/capability-grants.json --profile artifacts/room-agent/profiles/room-flight-candidate-v1.json --manifest-out artifacts/room-agent/authorizations/forced-return-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability adaptive_movement --trial-protocol pose-recovery-v1 --request "hold position, recover localization from the declared tag views, then return home" --site fixtures/room_agent/v1/semantic-site.json --grant-registry artifacts/room-agent/capability-grants.json --profile artifacts/room-agent/profiles/room-flight-candidate-v1.json --manifest-out artifacts/room-agent/authorizations/pose-recovery-preview.json
```

Execute only the two commands emitted by these previews. Verify the first manifest with terminal `INCOMPLETE_RETURNED`; verify the second with terminal `COMPLETED` and required `RECOVERY_REQUIRED`, accepted reanchor and safe-home events.

After both protocols pass, append their run IDs and verifier results to `calibration-candidate-v1.json`, then promote offline:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode promote --profile-kind flight --candidate artifacts/room-agent/profiles/room-flight-candidate-v1.json --evidence-index docs/reports/evidence/room-agent/calibration-candidate-v1.json --output artifacts/room-agent/profiles/room-flight-v1.json
```

Expected: the profile is `PROMOTED` only when independent route holdouts, forced-return evidence and tag-free/reanchor evidence all pass. Record the profile hash and terminal checks in `safety-v1.json`, then run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py promote --capability adaptive_movement --evidence-index docs/reports/evidence/room-agent/safety-v1.json --profile artifacts/room-agent/profiles/room-flight-v1.json --registry artifacts/room-agent/capability-grants.json
```

No adaptive count flight starts before this promotion succeeds.

- [ ] **Step 3: Count and closer-view workflow**

First generate a standalone closer-inspection trial over a deliberately small static phone target, then execute only its printed live command:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py issue-trial --capability closer_inspection --trial-protocol closer-inspection-v1 --site fixtures/room_agent/v1/semantic-site.json --profile artifacts/room-agent/profiles/room-flight-v1.json --evidence-destination docs/reports/evidence/room-agent/closer-inspection-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability closer_inspection --trial-protocol closer-inspection-v1 --request "find the small phone, get closer if needed to validate the detection, then return home" --site fixtures/room_agent/v1/semantic-site.json --grant-registry artifacts/room-agent/capability-grants.json --profile artifacts/room-agent/profiles/room-flight-v1.json --manifest-out artifacts/room-agent/authorizations/closer-inspection-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_verify_artifact.py --manifest artifacts/room-agent/authorizations/closer-inspection-preview.json --expect-terminal COMPLETED --expect-capability closer_inspection --require-event CLOSER_INSPECTION_COMPLETED
```

The required event names the original/final hypothesis revisions, accepted swept-clearance/admission revision, checkpoint ID, measured pose/energy outcome and `CONFIRMED|REJECTED|AMBIGUOUS|LOST` result. Write `closer-inspection-v1.json` and promote only when that event and safe home termination verify:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py promote --capability closer_inspection --evidence-index docs/reports/evidence/room-agent/closer-inspection-v1.json --profile artifacts/room-agent/profiles/room-flight-v1.json --registry artifacts/room-agent/capability-grants.json
```

Then generate the count-and-return trial manifest and execute only its printed live command:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py issue-trial --capability count_return --trial-protocol count-return-v1 --site fixtures/room_agent/v1/semantic-site.json --profile artifacts/room-agent/profiles/room-flight-v1.json --evidence-destination docs/reports/evidence/room-agent/count-return-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability count_return --trial-protocol count-return-v1 --request "count the computers and phones in the surveyed room, validate uncertain detections, then return home" --site fixtures/room_agent/v1/semantic-site.json --grant-registry artifacts/room-agent/capability-grants.json --profile artifacts/room-agent/profiles/room-flight-v1.json --manifest-out artifacts/room-agent/authorizations/count-return-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_verify_artifact.py --manifest artifacts/room-agent/authorizations/count-return-preview.json --expect-terminal COMPLETED --expect-capability count_return
```

Require correct artifact chain, honest uncertainty/coverage, successful home termination and actual consumption within predicted bounds. If the count run itself uses closer inspection, require the same event fields; it cannot receive credit from the earlier run alone. Write `count-return-v1.json`, then run:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py promote --capability count_return --evidence-index docs/reports/evidence/room-agent/count-return-v1.json --profile artifacts/room-agent/profiles/room-flight-v1.json --registry artifacts/room-agent/capability-grants.json
```

A failed or incomplete run leaves the grant at `TRIAL` or `DISABLED`.

- [ ] **Step 4: Certified landing workflow**

After Plan 04, create `fixtures/room_agent/v1/flight3-measured-landing-site.json` only from recorded pad measurements/provenance. Use a stationary floor phone and that access-restricted pad. Generate the manifest, execute only its printed live command, and verify it:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py issue-trial --capability certified_landing --trial-protocol certified-landing-v1 --site fixtures/room_agent/v1/flight3-measured-landing-site.json --profile artifacts/room-agent/profiles/room-flight-v1.json --evidence-destination docs/reports/evidence/room-agent/certified-landing-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability certified_landing --trial-protocol certified-landing-v1 --request "find the phone next to you and land as close as possible at a certified safe landing zone" --site fixtures/room_agent/v1/flight3-measured-landing-site.json --grant-registry artifacts/room-agent/capability-grants.json --profile artifacts/room-agent/profiles/room-flight-v1.json --manifest-out artifacts/room-agent/authorizations/certified-landing-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_verify_artifact.py --manifest artifacts/room-agent/authorizations/certified-landing-preview.json --expect-terminal LANDED_AT_CERTIFIED_ZONE --expect-capability certified_landing
```

Revalidate clearance and target immediately before descent; abort to home on any stale/contradictory evidence. Require the matching zone revision and final-clearance evidence. Repeat at healthy charge; no arbitrary floor landing. Write `certified-landing-v1.json`, then promote only on pass:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py promote --capability certified_landing --evidence-index docs/reports/evidence/room-agent/certified-landing-v1.json --profile artifacts/room-agent/profiles/room-flight-v1.json --registry artifacts/room-agent/capability-grants.json
```

If the pad cannot be certified, record `BLOCKED` in that exact evidence file and do not create the measured fixture.

- [ ] **Step 5: Relation/yaw/follow workflow**

After Plan 05, use a consenting designated actor in a cleared corridor with operator emergency control. Generate separate immutable manifests for the fixed-route tracker training and holdout runs. Execute each printed command once; never reuse or overwrite either manifest. Record their distinct run IDs and roles in `tracker-calibration-v1.json`, then fit the trial-only tracker profile from only the runs marked `training`:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode preview --target-mode record --trial-role training --split-id tracker-training-v1 --route fixtures/room_agent/v1/calibration/tracker-route.json --site fixtures/room_agent/v1/semantic-site.json --manifest-out artifacts/room-agent/authorizations/calibration-tracker-training-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode preview --target-mode record --trial-role holdout --split-id tracker-holdout-v1 --route fixtures/room_agent/v1/calibration/tracker-route.json --site fixtures/room_agent/v1/semantic-site.json --manifest-out artifacts/room-agent/authorizations/calibration-tracker-holdout-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode fit --profile-kind tracker --evidence-index docs/reports/evidence/room-agent/tracker-calibration-v1.json --output artifacts/room-agent/profiles/room-tracker-candidate-v1.json
```

The holdout is collected in a separately reset trial with a new manifest/run ID and must remain excluded from fitting. The evidence index binds both manifests, route/site/aircraft/camera hashes, the training/holdout role, measured freshness/latency/loss fields and retained artifact hashes. Missing or repeated IDs block fitting and promotion.

Generate separate `TRIAL` manifests with these exact commands:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py issue-trial --capability relation_verification --trial-protocol relation-v1 --site fixtures/room_agent/v1/semantic-site.json --profile artifacts/room-agent/profiles/room-flight-v1.json --profile artifacts/room-agent/profiles/room-tracker-candidate-v1.json --evidence-destination docs/reports/evidence/room-agent/bounded-follow-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py issue-trial --capability yaw_tracking --trial-protocol yaw-v1 --site fixtures/room_agent/v1/semantic-site.json --profile artifacts/room-agent/profiles/room-flight-v1.json --profile artifacts/room-agent/profiles/room-tracker-candidate-v1.json --evidence-destination docs/reports/evidence/room-agent/bounded-follow-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py issue-trial --capability bounded_following --trial-protocol follow-step-v1 --site fixtures/room_agent/v1/semantic-site.json --profile artifacts/room-agent/profiles/room-flight-v1.json --profile artifacts/room-agent/profiles/room-tracker-candidate-v1.json --evidence-destination docs/reports/evidence/room-agent/bounded-follow-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability relation_verification --trial-protocol relation-v1 --request "find the designated man who is holding a smartphone and keep him in view" --site fixtures/room_agent/v1/semantic-site.json --grant-registry artifacts/room-agent/capability-grants.json --profile artifacts/room-agent/profiles/room-flight-v1.json --profile artifacts/room-agent/profiles/room-tracker-candidate-v1.json --manifest-out artifacts/room-agent/authorizations/relation-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability yaw_tracking --trial-protocol yaw-v1 --request "keep the designated man holding a smartphone in view using yaw only, then return home" --site fixtures/room_agent/v1/semantic-site.json --grant-registry artifacts/room-agent/capability-grants.json --profile artifacts/room-agent/profiles/room-flight-v1.json --profile artifacts/room-agent/profiles/room-tracker-candidate-v1.json --manifest-out artifacts/room-agent/authorizations/yaw-preview.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_session.py --mode preview --target-mode live --capability bounded_following --trial-protocol follow-step-v1 --request "find the designated man holding a smartphone, follow him for one safe step, then return home" --site fixtures/room_agent/v1/semantic-site.json --grant-registry artifacts/room-agent/capability-grants.json --profile artifacts/room-agent/profiles/room-flight-v1.json --profile artifacts/room-agent/profiles/room-tracker-candidate-v1.json --manifest-out artifacts/room-agent/authorizations/follow-step-preview.json
```

Execute in that order: relation verification, production local-tracker freshness/latency, yaw-only observation, then exactly one short follow step. Abort on identity ambiguity/loss, another person entering the corridor, pose recovery, reserve return, uncertain command completion or corridor violation. Verify each manifest with the expected capability and terminal state. Expected first follow terminal behavior is hover, fresh reacquisition, then declared return/land. Write `bounded-follow-v1.json`; append the independent holdout result and the accepted production-tracker freshness/loss result to `tracker-calibration-v1.json`, without changing its original run records. Promote the tracker candidate against that tracker-specific evidence, then promote only the capabilities whose evidence passed:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_calibrate.py --mode promote --profile-kind tracker --candidate artifacts/room-agent/profiles/room-tracker-candidate-v1.json --evidence-index docs/reports/evidence/room-agent/tracker-calibration-v1.json --output artifacts/room-agent/profiles/room-tracker-v1.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py promote --capability relation_verification --evidence-index docs/reports/evidence/room-agent/bounded-follow-v1.json --profile artifacts/room-agent/profiles/room-tracker-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py promote --capability yaw_tracking --evidence-index docs/reports/evidence/room-agent/bounded-follow-v1.json --profile artifacts/room-agent/profiles/room-tracker-v1.json --registry artifacts/room-agent/capability-grants.json
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/room_capability.py promote --capability bounded_following --evidence-index docs/reports/evidence/room-agent/bounded-follow-v1.json --profile artifacts/room-agent/profiles/room-flight-v1.json --profile artifacts/room-agent/profiles/room-tracker-v1.json --registry artifacts/room-agent/capability-grants.json
```

Keep longer following disabled.

For each step, record pass/fail/blocked evidence and update capability flags. A failed or unavailable hardware gate remains blocked rather than inferred from replay.

- [ ] **Step 6: Commit sanitized promotion evidence after each live stage**

Each named evidence file contains manifest/terminal/profile hashes, measured pass/fail fields and paths to retained sanitized summaries. Exclude credentials, raw frames and video. Commit only the stage completed:

```powershell
rtk git add docs/reports/evidence/room-agent/calibration-candidate-v1.json docs/reports/evidence/room-agent/safety-v1.json docs/reports/room-agent-count-readiness.md
rtk git commit -m "test(room): record calibrated flight safety evidence"
rtk git add docs/reports/evidence/room-agent/closer-inspection-v1.json docs/reports/room-agent-count-readiness.md
rtk git commit -m "test(room): record closer inspection evidence"
rtk git add docs/reports/evidence/room-agent/count-return-v1.json docs/reports/room-agent-count-readiness.md
rtk git commit -m "test(room): promote count and return capability"
rtk git add fixtures/room_agent/v1/flight3-measured-landing-site.json docs/reports/evidence/room-agent/certified-landing-v1.json docs/reports/room-agent-count-readiness.md
rtk git commit -m "test(room): record certified landing evidence"
rtk git add docs/reports/evidence/room-agent/tracker-calibration-v1.json docs/reports/evidence/room-agent/bounded-follow-v1.json docs/reports/room-agent-count-readiness.md
rtk git commit -m "test(room): record bounded following evidence"
```

Run only the commit pair for files produced by that stage. The shown landing command is only for a successful measured certification. If landing is blocked, stage only `certified-landing-v1.json` and the readiness report; do not create or stage the measured fixture. A blocked stage records `status=BLOCKED` and does not create a promotion grant.

### Task 7: Final integration report and branch gate

**Files:**
- Create: `docs/reports/jev-room-agent-integration.md`
- Update: `docs/superpowers/plans/2026-09-22-jev-room-agent-roadmap.md`

- [ ] **Step 1: Rebase checkpoint and full serial verification**

Use the roadmap rebase commands, resolve incoming API changes against the approved spec, and run the full suite once after the final rebase.

- [ ] **Step 2: Write evidence-backed report**

List every task/commit/test, upstream revision, artifact/profile hash, live run actually performed, enabled capability and blocked limitation. State that unrestricted room exploration, arbitrary landing and continuous pursuit remain unsupported unless their explicit promotion evidence exists.

- [ ] **Step 3: Commit**

```powershell
rtk git add docs/reports/jev-room-agent-integration.md docs/superpowers/plans/2026-09-22-jev-room-agent-roadmap.md docs/room-agent-live-runbook.md
rtk git commit -m "docs(room): report agentic room capability evidence"
```

Do not push or merge without the user's explicit request. Remind the user of any remaining roadmap plan or blocked live gate.
