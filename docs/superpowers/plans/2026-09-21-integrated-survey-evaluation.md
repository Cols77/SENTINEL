# Integrated Survey Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the integrated survey loop reproducibly evaluable through strict option-bound replay, one canonical scene/scorer contract, explicit scripted/recorded/live modes, and stage-separated capability reports.

**Architecture:** `sentinel.survey_eval` owns scene-v2 validation, independent projection, truth matching, and scoring. Production replay boundaries enforce selected option/frame/response identity and raise typed unevaluable failures; `sentinel.survey.eval` remains an explicit compatibility/execution wrapper. The runner executes F/E/J on fresh stores with identical public scenes/options and writes a manifest, append-only stream, episode snapshots, summary, and report without silently substituting unavailable live adapters.

**Tech Stack:** Python 3.11, dataclasses, SQLite, SHA-256 manifests, pytest, existing survey loop/store/flight/detector ports.

**Dependencies and execution order:** The integrated checkout must remain the current source; never import the old `feat/survey-counting` worktree. After the bounded inspection trajectory gate passes, complete this plan's evaluator/strict-replay Tasks 1–5 before any survey production-policy adaptation. Then complete Demo Conversational Outcomes Tasks 1–4, return here for Task 6, complete Demo Tasks 5–7, and finish this plan with Tasks 7–8. Task 6 depends on the shared bounded selector/session and typed response finalizer; Tasks 1–5 do not.

---

## File map

- Modify `src/sentinel/survey_eval.py`: canonical scene-v2 loader, independent projection, frame/object matching, stage metrics.
- Modify `src/sentinel/survey/eval.py`: explicit legacy compatibility wrappers and production-loop episode execution.
- Modify `src/sentinel/survey/flight.py`: typed `ReplayUnavailable` and strict option/stop/look/artifact binding.
- Modify `src/sentinel/survey/detector.py`: strengthen the existing `ReplayDetector` with strict request/frame/response binding.
- Modify `scripts/survey_sweep.py`: canonical scripted/recorded/live orchestration and non-overwriting artifacts.
- Modify `scripts/survey_run.py`: use canonical score semantics and honest F/E/J mode/provider labels.
- Create `docs/superpowers/specs/contracts/survey-scene-v2.schema.json` and reusable scene-v2 fixtures under `fixtures/survey_eval/`.
- Modify `tests/test_survey_eval.py`, `tests/test_survey_sweep.py`, `tests/survey/test_eval.py`, `tests/survey/test_loop.py`, `tests/survey/test_detector.py`, and `tests/survey/test_consent.py`.
- Update `docs/reports/survey-readiness-evaluation-baseline.md` after verified runs.

### Task 1: Define and validate scene manifest v2

**Files:**
- Create: `docs/superpowers/specs/contracts/survey-scene-v2.schema.json`
- Modify: `src/sentinel/survey_eval.py`
- Modify: `tests/test_survey_eval.py`
- Create: `fixtures/survey_eval/scene-v2-close-objects.json`
- Create: `fixtures/survey_eval/scene-v2-occlusion-followup.json`

- [ ] **Step 1: Add failing scene-v2 tests**

Require schema/version, provenance/label authority, unique truth IDs/classes/3D surface positions/physical dimensions, surveyed bounds, camera intrinsics/calibration hash, exact public `SweepOption` fields, option/stop/look-indexed frames, status/pose/source/artifact/response hashes, occlusion truth, request text, and expected evaluability. Reject duplicate identities, missing hashes for materialized files, undeclared option IDs, truth outside bounds, and ambiguous label authority.

- [ ] **Step 2: Add a policy-visibility noninterference test**

Load a scene, then change/delete hidden truth objects while holding public options fixed. Assert policy-visible sweep candidates are byte-identical. This prevents truth depth/class from changing option range or resolvability metadata.

- [ ] **Step 3: Run loader tests and verify v1-only behavior fails**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_survey_eval.py
```

- [ ] **Step 4: Implement the v2 model and JSON Schema**

Use explicit dataclasses:

```python
@dataclass(frozen=True)
class TruthObject:
    object_id: str
    class_name: str
    x_cm: float
    y_cm: float
    surface_z_cm: float
    width_cm: float
    height_cm: float

@dataclass(frozen=True)
class SceneFrame:
    option_id: str
    stop_index: int
    look_index: int
    status: str
    pose: dict[str, Any]
    pose_source: str
    artifact_path: str | None
    artifact_sha256: str
    response_path: str | None
    response_sha256: str | None
    occluded_object_ids: Sequence[str]
    truth_boxes: Sequence[dict[str, Any]]

@dataclass(frozen=True)
class Scene:
    schema_version: int
    scene_id: str
    provenance: str
    label_authority: str
    request_text: str
    expected_evaluable: bool
    bounds_cm: dict[str, float]
    camera: dict[str, Any]
    options: Sequence[dict[str, Any]]
    objects: Sequence[TruthObject]
    frames: Sequence[SceneFrame]
```

Validate file bytes against declared hashes at load time. Synthetic records without image files must hash a canonical `synthetic_frame` object and must not claim an image-file hash.

- [ ] **Step 5: Run scene tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_survey_eval.py
```

Expected: all scene-v2 loader and noninterference tests pass.

- [ ] **Step 6: Commit scene v2**

```powershell
rtk git add docs/superpowers/specs/contracts/survey-scene-v2.schema.json src/sentinel/survey_eval.py tests/test_survey_eval.py fixtures/survey_eval
rtk git commit -m "feat: add independent survey scene v2 manifests"
```

### Task 2: Enforce strict option/frame/detector replay

**Files:**
- Modify: `src/sentinel/survey/flight.py`
- Modify: `src/sentinel/survey/detector.py`
- Modify: `tests/survey/test_loop.py`
- Modify: `tests/survey/test_detector.py`
- Modify: `tests/test_survey_sweep.py`

- [ ] **Step 1: Add failing flight replay tests**

Cover selected frames with missing option ID, wrong option, duplicate option/stop/look identity, missing selected frame, `UNANALYSED` status, missing artifact, and artifact hash mismatch. Every case must raise:

```python
class ReplayUnavailable(RuntimeError):
    def __init__(self, code: str, safe_summary: str) -> None:
        self.code = code
        self.safe_summary = safe_summary[:512]
        super().__init__(self.safe_summary)
```

Missing nonselected options remain allowed. An empty selected sweep is never success.

- [ ] **Step 2: Add failing detector replay tests**

Require each recorded response to match the selected frame identity and image hash plus a normalized detector-request digest. Reject response substitution, request mismatch, missing response, exhaustion, duplicate response, and unused required response.

- [ ] **Step 3: Run replay tests and verify permissive substitution**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/survey/test_loop.py tests/survey/test_detector.py tests/test_survey_sweep.py
```

- [ ] **Step 4: Implement strict `ReplayFlight`**

Index frames by `(option_id, stop_index, look_index)`, validate uniqueness during construction, and resolve only the selected option. Rehash materialized artifacts before returning them. Raise stable codes such as `MISSING_SELECTED_FRAME`, `WRONG_OPTION`, `UNANALYSED_SELECTED_FRAME`, and `ARTIFACT_HASH_MISMATCH`.

- [ ] **Step 5: Strengthen the existing `ReplayDetector`**

Normalize the exact detector request, compare its digest and frame/image hashes to the recording, consume each response once, and expose `assert_exhausted()` after the episode. Never import evaluator code from production modules.

- [ ] **Step 6: Catch replay failures as unevaluable episodes**

The runner records the typed code and partial evidence, marks the episode unevaluable, and does not convert it to escalation, completion, or an empty report.

- [ ] **Step 7: Run replay tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/survey/test_loop.py tests/survey/test_detector.py tests/test_survey_sweep.py
```

Expected: all pass.

- [ ] **Step 8: Commit strict replay**

```powershell
rtk git add src/sentinel/survey/flight.py src/sentinel/survey/detector.py tests/survey/test_loop.py tests/survey/test_detector.py tests/test_survey_sweep.py
rtk git commit -m "fix: make survey replay option and artifact strict"
```

### Task 3: Make one canonical projection and stage scorer

**Files:**
- Modify: `src/sentinel/survey_eval.py`
- Modify: `tests/test_survey_eval.py`

- [ ] **Step 1: Add failing independent projection tests**

Cover close objects in one frame, off-axis observations, occlusion, behind-camera objects, minimum resolvable width, explicit truth boxes, and camera calibration changes. Compute expected pixel centres/boxes from hand-checked geometry rather than production merge/projection functions.

- [ ] **Step 2: Add failing one-to-one matching tests**

Assert several predictions cannot match one truth object; two truth objects merged into one prediction is a separate collapse error; assignment is maximum-cardinality and deterministic under tie-breaking. Pin class compatibility and manifest-bound centre-distance/IoU thresholds.

- [ ] **Step 3: Add failing stage-metric tests**

Require explicit numerators/denominators or `not_applicable` for:

```text
workflow: completion, escalation, refusal, report presence/validity, calls,
          retries, latency, illegal/rejected calls, approvals, flights/budget
detection: per-frame visible/resolvable truth, TP/FP/FN, precision/recall
merge: duplicate splits, truth collapses, conditional merge recall, depth spread
count: interval containment, exact error only for exact bounds, lower-bound validity
localisation: matched distances mean/median/p95 and threshold recall
uncertainty: confirmed/uncertain, overconfident false hypotheses, unresolved truth
planning: distinct visible/resolvable truth, follow-up additions/redundancy,
          cumulative and whole-scene coverage within flight budget
attribution: unanalysed, class map/provider, geometry, detector, merge, policy
pose: availability and commanded-vs-measured errors only when measured pose exists
```

No report means report validity is `not_applicable`. Empty database, absent raw records, rejected final decision, exhausted budget, or missing report cannot be success.

- [ ] **Step 4: Run scorer tests and verify the current aggregate is insufficient**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_survey_eval.py
```

- [ ] **Step 5: Implement projection, matching, and `score_episode`**

Keep the evaluator independent of `survey.geometry`. Project physical width and height through camera intrinsics unless explicit truth boxes exist. Use maximum-cardinality class-compatible matching with deterministic distance/IoU tie-break. Return a nested result with one object per stage, each containing counts and denominators.

- [ ] **Step 6: Preserve count-bound semantics**

Computer/phone intervals use finite lower/upper bounds; people lower bounds use `upper=None`. Report exact error only for `lower == upper`. Report containment for every bound. Do not transform a lower bound into an exact count.

- [ ] **Step 7: Run scorer tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_survey_eval.py
```

Expected: all pass.

- [ ] **Step 8: Commit canonical scoring**

```powershell
rtk git add src/sentinel/survey_eval.py tests/test_survey_eval.py
rtk git commit -m "feat: score survey episodes by pipeline stage"
```

### Task 4: Make `sentinel.survey.eval` an explicit compatibility/execution layer

**Files:**
- Modify: `src/sentinel/survey/eval.py`
- Modify: `tests/survey/test_eval.py`
- Modify: `tests/test_survey_eval.py`

- [ ] **Step 1: Add failing compatibility tests**

Pin every existing caller of `sentinel.survey.eval.Scene`, `load_scene`, `score_episode`, and `compare_modes`. Require either the old signature with an explicit deprecation wrapper that converts to canonical evidence or the new signature migrated in the same commit. Assert both scripts use `sentinel.survey_eval.score_episode` semantics.

- [ ] **Step 2: Run tests and confirm divergent semantics**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/survey/test_eval.py tests/test_survey_eval.py
```

- [ ] **Step 3: Delegate scoring explicitly**

Keep production-loop execution helpers in `sentinel.survey.eval`, but construct a canonical episode evidence packet and call `sentinel.survey_eval.score_episode`. Do not silently re-export an incompatible function. Name compatibility functions `score_legacy_store_episode` and `compare_policy_modes` if their signatures remain production-store oriented.

- [ ] **Step 4: Run evaluation compatibility tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/survey/test_eval.py tests/test_survey_eval.py tests/test_survey_sweep.py
```

Expected: all pass and both runners resolve modules from current integrated HEAD.

- [ ] **Step 5: Commit the canonical boundary**

```powershell
rtk git add src/sentinel/survey/eval.py tests/survey/test_eval.py tests/test_survey_eval.py
rtk git commit -m "refactor: centralize survey evaluation semantics"
```

### Task 5: Implement reproducible scripted and recorded survey runs

**Files:**
- Modify: `scripts/survey_sweep.py`
- Modify: `scripts/survey_run.py`
- Create: `docs/superpowers/specs/contracts/survey-recording-v1.schema.json`
- Modify: `docs/evaluation.md`
- Modify: `tests/test_survey_sweep.py`
- Modify: `tests/survey/test_survey_cli.py`

- [ ] **Step 1: Add failing dry-run and artifact-layout tests**

Assert dry run validates every scene/recording and prints a canonical manifest digest, maximum selector/detector calls, flight budget, provider/model configuration, thresholds, approval script, and cost assumptions. It must create no provider, database, output directory, or artifact. Executed runs must refuse an existing output directory and produce:

```text
manifest.json
raw.jsonl
summary.json
report.md
episodes/<case-id>/<arm>/<repeat>/episode.sqlite3
episodes/<case-id>/<arm>/<repeat>/snapshot.json
```

- [ ] **Step 2: Add failing recording-integrity tests**

Assert normalized selector/detector request digest mismatch, exhaustion, extra required response, wrong identity, and incomplete episode are reported. Recorded mode makes zero network calls. Scripted/recorded F/E/J use fresh stores and identical scene/options. The recording manifest binds source run/manifest hashes, source revision/dirty state, requested/resolved identities, prompt/tool revisions, ordered scene/arm/episode/call indices, normalized request/response/frame digests, archive hash, capture time, and sanitization revision.

- [ ] **Step 3: Run runner tests and verify gaps**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_survey_sweep.py tests/survey/test_survey_cli.py tests/survey/test_consent.py
```

- [ ] **Step 4: Build the immutable manifest**

Bind commit/dirty/source hashes; scene, option, calibration, image, and response hashes; arm/mode; selector/detector providers and requested/resolved identities; prompt/tool revisions; Jev/observability/merge/matching thresholds; repeats; separate selector/detector/flight budgets; approval script; replay/hardware mode; cost assumptions; consent metadata; and bounded decision/demo-context revisions. Missing resolved identities are `unverified`.

- [ ] **Step 5: Write complete append-only raw records**

Record episode start/completion, model requests/results, reads/results, deterministic refusals, approvals, options, frames, detector requests/results, persistence references, reports, scores, and sanitized failures. Exclude secrets and image bytes. Flush after every row.

Add an explicit capture/sanitization command that accepts a completed source artifact, verifies each selector/detector request and frame against its source manifest, strips credentials/transport headers, and emits an immutable archive plus `survey-recording-v1` manifest. Refuse reordered, invented, missing, unused, or identity-inconsistent responses. Never hand-author responses to make recorded coverage pass; if no valid source artifact exists, retain recorded mode as blocked coverage.

- [ ] **Step 6: Make policy and provider labels orthogonal**

Keep architecture and execution/provider mode as separate fields. Allow `F/scripted`, `E/scripted`, and `J/scripted` for wiring; allow `E/recorded` and `J/recorded` for strict response replay; allow a live selector or detector pair only after its real adapter/factory and resolved identity are verified and consented. Until then reject that exact live pair. Never substitute a scripted policy/provider while retaining a live label.

- [ ] **Step 7: Run runner tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_survey_sweep.py tests/survey/test_survey_cli.py tests/survey/test_consent.py
```

Expected: all pass.

- [ ] **Step 8: Commit reproducible offline modes**

```powershell
rtk git add scripts/survey_sweep.py scripts/survey_run.py docs/superpowers/specs/contracts/survey-recording-v1.schema.json docs/evaluation.md tests/test_survey_sweep.py tests/survey/test_survey_cli.py
rtk git commit -m "feat: add reproducible survey evaluation modes"
```

### Task 6: Connect bounded survey reads and enforce approval/flight budgets

**Files:**
- Modify: `src/sentinel/survey/candidates.py`
- Modify: `src/sentinel/survey/loop.py`
- Modify: `src/sentinel/survey/store.py`
- Modify: `src/sentinel/survey/sql/schema.sql`
- Modify: `src/sentinel/survey/proposer.py`
- Modify: `src/sentinel/coordinator/decision_responses.py`
- Modify: `src/sentinel/decision_prompt.py`
- Modify: `scripts/survey_sweep.py`
- Modify: `tests/survey/test_loop.py`
- Modify: `tests/survey/test_store.py`
- Modify: `tests/survey/test_proposer.py`
- Modify: `tests/survey/test_report.py`
- Modify: `tests/coordinator/test_decision_responses.py`
- Modify: `tests/test_survey_sweep.py`

- [ ] **Step 1: Add failing read-dispatch tests**

When the packet declares a site gap, assert `survey.get_site` dispatches and its typed current result reaches the next policy decision. When it declares a result gap after an approved sweep, assert `survey.get_results` dispatches with current run/result/registry identity. Suppress both when equivalent facts are already current. Missing selected replay frames remain unevaluable and cannot be repaired by another option’s observations.

- [ ] **Step 2: Add failing approval/budget tests**

Assert approval occurs before each sweep, no selector/detector/flight call happens after proposal and before the synchronous approval callback returns, rejected approval causes no flight, and no more than two flights occur. A follow-up is legal only when it addresses a named uncertainty within the remaining budget.

- [ ] **Step 3: Add failing shared-policy and report-finalization tests**

Run survey Jev and LLM policies through the shared candidate registry/selectors and assert identical active candidate semantics; retain `RuleProposer` for F. Prebuild a kind `report` candidate from an exact run ID, results version/data digest, deterministic body hash, and option-registry hash. Assert matching current results persist the prebuilt body and return `DecisionFinalization(disposition="REPORTED", response_id=expected_report_id, response=expected_report_packet)`. A stale result version, changed body hash, changed option registry, or different run returns typed rejection and writes no report. An identical retry returns the same report ID with one logical audit record; a conflicting existing finalization is rejected. Inject interruption before insert, after report insert before finalization insert, after both inserts before commit, and after commit before audit projection; retry must yield one report row and one logical `survey_report_finalized` record. Test both a fresh store and an existing store upgraded in place with `PRAGMA foreign_keys = ON`, execute a real finalization insert in each, and assert the `reports` and `survey_runs` foreign keys reject orphan identities. A matching historical report without finalization provenance is rejected as `LEGACY_REPORT_UNATTESTED`; never fabricate an audit record for it.

- [ ] **Step 4: Run integration tests and verify missing dispatch**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/survey/test_loop.py tests/survey/test_proposer.py tests/survey/test_report.py tests/coordinator/test_decision_responses.py tests/test_survey_sweep.py
```

- [ ] **Step 5: Add site/results read candidates and shared selectors**

Build exact controller-owned calls with current option registry and results version. Feed results through the shared bounded session transcript. Rebuild legal candidates after each read. Report every read and source version in the raw stream. Replace survey-only choice validation with the shared bounded Jev/LLM selectors and survey candidate builder; keep architecture F's `RuleProposer` as its independent arm. Extend only the neutral shared prompt revision with the two approved survey domain rules and test exact Jev/LLM revision/text parity. Do not claim live E/J support until the runner verifies real provider adapters and identities.

- [ ] **Step 6: Implement deterministic report finalization and preserve flight authority**

Move report-body construction to a deterministic controller function over current `class_counts`. The kind `report` candidate stores its identity fields and body hash. At finalization, re-read results and registry metadata, recompute the body/hash, and compare every identity field. Add this append-only table to the fresh schema and create it transactionally from `SurveyStore.open` for existing databases:

```sql
CREATE TABLE report_finalizations (
    finalization_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    results_digest TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    option_registry_sha256 TEXT NOT NULL,
    audit_record_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES reports(report_id),
    FOREIGN KEY (run_id) REFERENCES survey_runs(run_id),
    UNIQUE (run_id, results_digest, body_sha256, option_registry_sha256)
);
```

Implement `store.finalize_report(report_record, finalization_record)` as one SQLite transaction that inserts the complete report and matching finalization row or returns an existing identical joined pair; reject conflicts. Existing report rows remain untouched. If an existing report has no finalization row, return `LEGACY_REPORT_UNATTESTED` and do not synthesize provenance. Make `report_finalizations` the authoritative audit source and derive `survey_report_finalized` during the same idempotent raw-stream projection used for decision responses, keyed by `audit_record_id`, so a projection failure after commit is recoverable without a second logical event. A report does not use the coordinator mutation gateway and does not request a second approval after an already approved sweep.

Only an approved kind `proposal` candidate can invoke the flight port. Reads, answers, suggestions, clarifications, reports, and escalations cannot. The model never dispatches the aircraft directly.

- [ ] **Step 7: Run survey integration tests**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/survey/test_candidates.py tests/survey/test_loop.py tests/survey/test_store.py tests/survey/test_proposer.py tests/survey/test_report.py tests/coordinator/test_decision_responses.py tests/test_survey_sweep.py
```

Expected: all pass.

- [ ] **Step 8: Commit bounded survey dispatch**

```powershell
rtk git add src/sentinel/survey/candidates.py src/sentinel/survey/loop.py src/sentinel/survey/store.py src/sentinel/survey/sql/schema.sql src/sentinel/survey/proposer.py src/sentinel/coordinator/decision_responses.py src/sentinel/decision_prompt.py scripts/survey_sweep.py tests/survey/test_loop.py tests/survey/test_store.py tests/survey/test_proposer.py tests/survey/test_report.py tests/coordinator/test_decision_responses.py tests/test_survey_sweep.py
rtk git commit -m "feat: evaluate bounded survey reads and approvals"
```

### Task 7: Run offline baseline and publish honest readiness

**Files:**
- Update: `docs/reports/survey-readiness-evaluation-baseline.md`
- Create at execution: a fresh directory under `artifacts/survey-eval/`

- [ ] **Step 1: Run focused survey evaluation tests serially**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short tests/test_survey_eval.py tests/test_survey_sweep.py tests/survey/test_eval.py tests/survey/test_loop.py tests/survey/test_detector.py tests/survey/test_consent.py
```

Expected: all pass.

- [ ] **Step 2: Run the full serial suite**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short
```

Expected: exit code 0; record exact revision, test counts, skips, warnings, and duration.

- [ ] **Step 3: Generate and inspect the scripted dry-run manifest**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/survey_sweep.py --mode scripted --arms F E J --repeats 1 --dry-run
```

Expected: no artifact write or provider call; public options do not depend on truth.

- [ ] **Step 4: Execute scripted and recorded baselines**

Use fresh non-overwriting output directories and identical scenes/options per arm. Run recorded mode only with complete strict recordings whose source manifest and request digests match.

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/survey_sweep.py --mode scripted --arms F E J --repeats 1 --execute --output artifacts/survey-eval/integrated-scripted-20260921
```

Run recorded mode only when the capture command from Task 5 has emitted and validated concrete archive/manifest paths; use the exact replay command printed by that validation. If no valid source exists, do not invent a path or execute recorded mode.

Expected: every episode has a fresh database/snapshot and complete raw trail. Any absent valid recording is reported as blocked coverage rather than replaced.

- [ ] **Step 5: Publish stage-separated results**

Update the survey readiness report with workflow, detection, merge, count, localisation, uncertainty, planning, pose, and attribution metrics with denominators. Separate synthetic harness evidence, recorded reproducibility, and fresh live measurements. Mark real-scene accuracy, live detector identity/evidence, cube bridge, and hardware trials blocked unless independently measured.

- [ ] **Step 6: Keep live and hardware gates explicit**

Before any live selector/detector run, generate a new manifest, verify the real adapter/provider identity, obtain manifest-bound authorization and provider-specific consent, and use three repeats per labelled case. Hardware additionally requires the typed cube bridge, calibration, independently labelled measured scene, location/filming consent, and separate flight authorization. No such run is implied by this plan.

### Task 8: Iterative specification and quality review

**Files:**
- Review all files changed by this plan and the final report/artifacts.

- [ ] **Step 1: Dispatch an independent specification reviewer**

Require a requirement-to-test map for scene v2, oracle noninterference, strict replay, modes/consent, artifact completeness, every scoring stage, read dispatch, approval/flight budgets, and capability limits. The reviewer must inspect retained artifacts, not only tests.

- [ ] **Step 2: Dispatch an independent quality reviewer**

Ask a separate reviewer to probe path/hash handling, recording exhaustion, database isolation, matching determinism, empty denominators, report-success gaming, cross-worktree imports, identity claims, and accidental hardware/provider invocation.

- [ ] **Step 3: Fix confirmed important findings**

Assign each independent finding to a fresh fix agent, one bounded domain per agent. Review each diff, run its focused tests, then run the combined focused suite. Commit fixes by domain.

- [ ] **Step 4: Repeat review until clear**

Send the updated implementation, test results, manifests, raw streams, summary, and report to fresh reviewers. Continue review/fix cycles while any important issue remains.

- [ ] **Step 5: Run final full verification**

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest -q --tb=short
rtk git diff --check
rtk git status --short --branch
```

Expected: test exit 0, no whitespace errors, and only intended committed changes before integration review.
