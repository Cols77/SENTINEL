# P1 — Agent Loop Foundations (architectures A + B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In Claude Code this plan is executed by the `Workflow` tool: each task runs dev → fresh review → fix, followed by parent verification (diff scope + full suite).

**Goal:** Make the coordinator agent loop capable of informed decisions. It should see real state, get a computed legal set, receive gateway rejections as feedback, keep a bounded history, and accept any policy (RULE, Jev, LLM) behind one interface. It should also give the LLM agent (architecture B) a fair, consented re-evaluation.

**Architecture:** A read-only `DecisionState` read model and a deterministic `legal_set()` replace the "all 8 tools, event types only" context. `AgentLoop` takes a `Policy` (any callable that maps an `agent_request` to an `agent_result`), so the LLM adapter, the rule ladder and a Jev policy are interchangeable and share one gateway path. Everything in P1 fits the current `sentinel.v0` wire schema. Anything that needs new tools or fields is written up as a WP0 amendment proposal (Task 6) and implemented in P3 only after Coherence consent.

**Tech Stack:** Python 3.12+, sqlite3, pytest, `uv`, the existing `sentinel.coordinator`, `sentinel.adapter.llm` and `sentinel.judgment`.

**Specs (copied into `docs/superpowers/specs/` by Task 0):** `2026-09-19_wp1-architecture-review.md`, `2026-09-19_jev-integration-architecture.md`, `2026-09-19_jev-fixture-rebuild-plan.md`. Decisions below amend them.

**Plan family:** P1 (this) → P2 `2026-09-19-p2-shadow-advisors-F.md` (demo, before the 2026-09-25 freeze) → P3 `2026-09-19-p3-hybrid-E-followup.md` (after the freeze).

## Decisions recorded 2026-09-19 (operator)

1. **Demo architecture is F.** Execution is rules plus operator. Jev and the LLM run as *shadow advisors*: their answers are recorded and never shown or acted on. The LLM is the operator chat interface.
2. **E is the follow-up (P3).** The LLM proposes (plan interpretation, escalation investigation) and never acts directly. Jev may act at the evidence gate once promoted by data.
3. **Architectures A and B get fixed and fairly re-evaluated (this plan).** The six failed WP1 live runs graded models that could not see the database.
4. The static veto (`EVIDENCE_SCORE_THRESHOLD`, gateway validation) is unchanged, and every mutation goes through `CommandGateway.submit`.

## Defects this plan resolves (verified in `src/sentinel/coordinator/agent_loop.py` on `feat/wp1-gate-fixes`)

| # | Defect | Where | Task |
|---|---|---|---|
| D1 | `event_context.summary` is only the event type; payloads are dropped | `:98-99` | 3 |
| D2 | `evidence_context` is always `[]` | `:51` | 3 |
| D3 | `legal_tools` is always all 8 tools; legality is never computed | `:14`, `:51`, `:126` | 2, 3 |
| D4 | `state.get_summary` returns only status/phase/version | `:102-104` | 3 |
| D5 | Mutation results are `{"kind": "task"}` with no ids; the gateway reply is discarded; status is always `ACCEPTED` | `:121-127` | 3 (ids via event summaries), 6 (typed ids) |
| D6 | A gateway `CoordinatorError` escapes `run()` instead of being fed back | `:122` | 3 |
| D7 | Unmapped tool name → `command` unbound → `NameError` | `:113-122` | 3 |
| D8 | No history; the model sees only `previous_result` | `:27`, `:86` | 4 |
| D9 | The loop is LLM-shaped; no policy seam for RULE/Jev | whole file | 4, 5 |
| D10 | The live-gate scenarios smuggle ids into event summaries to compensate for D1/D5 | `tests/agent/live_scenarios.json` | 7 |
| D11 | Jev `choice` criteria are sent as `None`, so Jev sees bare labels | `judgment.py:180` | 5 |

## Global Constraints

- Worktree `C:/coding/sentinel/.worktrees/wp1-gate-fixes`, branch `feat/wp1-gate-fixes`. Test command: `uv run python -m pytest tests/ -q -o addopts=''`. Baseline: 393 passed, 0 failed, 3 skipped.
- No live provider calls except Task 7 Step 4, which needs fresh operator consent with call count and cost.
- Never read or print `.env` values; names only.
- No wire-schema edits in P1 (`docs/superpowers/specs/contracts/sentinel-v0-wire.schema.json` is read-only here).
- `summary` strings ≤ 512 chars and `user_request` ≤ 2048 chars (schema limits). `event_context` and `evidence_context` hold ≤ 32 items each.
- Each commit touches only the files its task lists.

## Operator gates

| Gate | Task | Decision |
|---|---|---|
| G-merge | 0 | local merge of the existing 8 commits to master (push is asked separately) |
| G-amend | 6 | Coherence consent for the WP0 amendment (never self-granted) |
| G-live-B | 7 | consent to one live LLM gate run on the fixed loop (count and cost stated) |

---

### Task 0: Land the existing branch and fix J5 provenance

**Files:**
- Commit: `uv.lock` (currently missing `openai` and its dependencies, which `adapter/llm/openai_compatible.py` imports)
- Create: `docs/superpowers/specs/2026-09-19_wp1-architecture-review.md`, `docs/superpowers/specs/2026-09-19_jev-integration-architecture.md`, `docs/superpowers/specs/2026-09-19_jev-fixture-rebuild-plan.md`. These are verbatim copies from `C:/coding/sentinel/.hermes/plans/`.
- Create: `scripts/rescore_j5_bands.py`, `artifacts/jev/v2-live-band-rescore.md`, `tests/test_rescore_j5_bands.py`
- Modify: `scripts/live_jev_v2_replay.py` (provenance only), `artifacts/jev/v2-live-summary.md` (append a provenance note)

**J5 defects (verified 2026-09-19):**
- Every record in `v2-live-jev.jsonl` and `v2-live-llm.jsonl` has `"judge": {"model": "static"}`.
- The Jev model was recorded as `~typesafe/jev-latest`, whereas consent named `typesafe/jev-1.13-20260917`.
- Band routing is `unknown` ×108 because `noul` answers carry no confidence.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rescore_j5_bands.py
from scripts.rescore_j5_bands import band_of, rescore

def test_band_of_uses_uncertain_interval():
    assert band_of(0.48, uncertain=(0.40, 0.60)) == "UNCERTAIN"
    assert band_of(0.84, uncertain=(0.40, 0.60)) == "CONFIDENT_TRUE"
    assert band_of(0.14, uncertain=(0.40, 0.60)) == "CONFIDENT_FALSE"

def test_rescore_escalates_uncertain_instead_of_false_safe():
    rows = [
        {"case_id": "pos-002", "repeat": 2, "reference": {"hazard_relevant": True},
         "answers": {"hazard_relevant": {"value": False, "probability": 0.48}}},
        {"case_id": "neg-001", "repeat": 0, "reference": {"hazard_relevant": False},
         "answers": {"hazard_relevant": {"value": False, "probability": 0.14}}},
        {"case_id": "amb-001", "repeat": 0, "reference": {"hazard_relevant": None},
         "answers": {"hazard_relevant": {"value": False, "probability": 0.29}}},
    ]
    out = rescore(rows, uncertain=(0.40, 0.60))
    assert out == {"false_safe": 0, "false_escalation": 0, "escalated": 1, "confident_correct": 1, "scored": 2}
```

If `scripts` is not importable as a package, add an empty `scripts/__init__.py` in this task.

- [ ] **Step 2:** Run `uv run python -m pytest tests/test_rescore_j5_bands.py -q -o addopts=''`. Expected: FAIL (ImportError).
- [ ] **Step 3: Implement `scripts/rescore_j5_bands.py`**

```python
"""Offline re-score of committed J5 logs by probability band. No network."""
from __future__ import annotations
import json, sys
from pathlib import Path

def band_of(p: float, *, uncertain: tuple[float, float]) -> str:
    lo, hi = uncertain
    if lo <= p <= hi:
        return "UNCERTAIN"
    return "CONFIDENT_TRUE" if p > hi else "CONFIDENT_FALSE"

def rescore(rows: list[dict], *, uncertain: tuple[float, float]) -> dict:
    out = {"false_safe": 0, "false_escalation": 0, "escalated": 0, "confident_correct": 0, "scored": 0}
    for r in rows:
        ref = r["reference"].get("hazard_relevant")
        if ref is None:
            continue
        out["scored"] += 1
        band = band_of(r["answers"]["hazard_relevant"]["probability"], uncertain=uncertain)
        if band == "UNCERTAIN":
            out["escalated"] += 1
        elif (band == "CONFIDENT_TRUE") == ref:
            out["confident_correct"] += 1
        elif ref:
            out["false_safe"] += 1
        else:
            out["false_escalation"] += 1
    return out

def main() -> int:
    base = Path("artifacts/jev")
    lines = ["# J5 offline band re-score", "",
             "No new calls. Input: committed `v2-live-*.jsonl`. n is tiny (15 scored cases x 3 repeats); directional only.", "",
             "| arm | uncertain band | scored | confident correct | escalated | false-safe | false-escalation |",
             "|---|---|---|---|---|---|---|"]
    for arm in ("jev", "llm"):
        text = (base / f"v2-live-{arm}.jsonl").read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        for band in ((0.45, 0.55), (0.40, 0.60), (0.35, 0.65)):
            m = rescore(rows, uncertain=band)
            lines.append(f"| {arm} | {band[0]:.2f}-{band[1]:.2f} | {m['scored']} | {m['confident_correct']} | "
                         f"{m['escalated']} | {m['false_safe']} | {m['false_escalation']} |")
    (base / "v2-live-band-rescore.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4:** Run the test (expect PASS), then `uv run python scripts/rescore_j5_bands.py`.
- [ ] **Step 5: Fix provenance in `scripts/live_jev_v2_replay.py`.** Each record's `judge.model` must be the resolved model id of its arm (`JevJudge._model` for Jev, the LLM model string for the LLM). The Jev default is `typesafe/jev-1.13-20260917`, overridable by `SENTINEL_SYSTEM_ONE_MODEL`, and never `latest`. Append this note to `artifacts/jev/v2-live-summary.md`: "Provenance: committed J5 logs predate the fix; their `judge.model` field reads `static` for both arms and is wrong; the Jev endpoint resolved `~typesafe/jev-latest`, not the consented pinned revision." Do not rewrite the logs.
- [ ] **Step 6:** Run the full suite (expect 0 failed). Commit with the message `chore: lock openai deps, commit specs, J5 band re-score + provenance fix`.
- [ ] **Step 7 (parent, not a worker):** On board `sentinel-wp1-kanban`, mark `t_388d8f65` and `t_24b25ade` as superseded by P1 Task 7. On `jev-shadow`, archive F1–F5 with a pointer to P1/P2/P3. **Gate G-merge:** ask the operator, then `git -C C:/coding/sentinel merge --no-ff feat/wp1-gate-fixes`. Do not push without asking.

### Task 1: `DecisionState` read model with derived categorical facts

**Files:**
- Create: `src/sentinel/coordinator/decision_state.py`
- Test: `tests/coordinator/test_decision_state.py`

**Interfaces:**
- Produces:
  - `DecisionState` (frozen dataclass, fields below)
  - `read_decision_state(repository, run_id: str) -> DecisionState`
  - `DecisionState.facts() -> dict[str, str | bool | None]`: categorical derived facts only, no raw floats
  - `DecisionState.to_packet() -> dict`: the projection sent to any policy (no UUIDs)
  - `DecisionState.ids() -> dict[str, str | None]`: the UUIDs, kept out of the packet and used only for argument filling

```python
# src/sentinel/coordinator/decision_state.py
"""Read-only decision read model: what any policy may know at a decision point."""
from __future__ import annotations
import json
from dataclasses import dataclass, field

SCORE_BANDS = ((0.9, "very_high"), (0.75, "high"), (0.6, "moderate"), (0.5, "marginal"))

def score_band(score: float | None) -> str | None:
    if score is None:
        return None
    for floor, name in SCORE_BANDS:
        if score >= floor:
            return name
    return "below_threshold"

@dataclass(frozen=True)
class DecisionState:
    run_id: str
    state_version: int
    run_status: str
    agent_phase: str
    emergency_latched: bool
    request_text: str | None
    plan_id: str | None
    plan_mode: str | None
    plan_station_ids: tuple[str, ...]
    plan_approved: bool
    detection: dict | None   # {"result_id","artifact_id","station_id","status","raw_score","canonical_class"}
    open_task_station_ids: tuple[str, ...]
    task_id: str | None
    task_status: str | None
    ranked_patrollers: tuple[str, ...] = field(default_factory=tuple)

    def facts(self) -> dict:
        det = self.detection or {}
        station = det.get("station_id")
        return {
            "plan_exists": self.plan_id is not None,
            "plan_mode": self.plan_mode,
            "plan_station_count": len(self.plan_station_ids) if self.plan_id else None,
            "plan_approved": self.plan_approved,
            "detection_present": bool(det),
            "detection_status": det.get("status"),
            "detection_score_band": score_band(det.get("raw_score")),
            "detection_class": det.get("canonical_class"),
            "detection_inside_plan": (station in self.plan_station_ids) if det and self.plan_id else None,
            "detection_duplicates_open_task": (station in self.open_task_station_ids) if det else None,
            "task_exists": self.task_id is not None,
            "task_status": self.task_status,
            "patrollers_ranked": len(self.ranked_patrollers),
        }

    def to_packet(self) -> dict:
        return {"context_version": "decision_packet.v1",
                "run": {"status": self.run_status, "phase": self.agent_phase},
                "operator_request": self.request_text,
                "facts": self.facts()}

    def ids(self) -> dict:
        det = self.detection or {}
        return {"run_id": self.run_id, "plan_id": self.plan_id, "task_id": self.task_id,
                "perception_result_id": det.get("result_id"), "artifact_id": det.get("artifact_id"),
                "station_id": det.get("station_id")}

def read_decision_state(repository, run_id: str) -> DecisionState:
    c = repository.connection
    run = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(run_id)
    events = repository.read_events(run_id, 0, 256)
    request_text = next((e["payload"].get("request_text") for e in events
                         if e["event_type"] == "USER_MESSAGE_RECEIVED"), None)
    plan = c.execute("SELECT plan_id, mode, station_ids_json FROM plans WHERE run_id = ? "
                     "ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
    approved = any(e["event_type"] == "PLAN_APPROVED" for e in events)
    task = c.execute("SELECT task_id, status FROM tasks WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                     (run_id,)).fetchone()
    open_stations = tuple(r["station_id"] for r in c.execute(
        "SELECT station_id FROM tasks WHERE run_id = ? AND status NOT IN ('CLOSED','REJECTED')", (run_id,)))
    ref = c.execute("""SELECT pr.result_id, pr.artifact_id, pr.result_json, o.station_id
                       FROM perception_references pr JOIN observations o USING (observation_id)
                       WHERE pr.run_id = ? AND pr.result_id NOT IN
                             (SELECT COALESCE(perception_result_id, '') FROM tasks)
                       ORDER BY pr.created_at DESC LIMIT 1""", (run_id,)).fetchone()
    detection = None
    if ref is not None:
        result = json.loads(ref["result_json"])
        detection = {"result_id": ref["result_id"], "artifact_id": ref["artifact_id"],
                     "station_id": ref["station_id"], "status": result.get("status"),
                     "raw_score": result.get("raw_score"), "canonical_class": result.get("canonical_class")}
    ranked = ()
    if task is not None:
        ranked = tuple(r["patroller_id"] for r in c.execute(
            "SELECT patroller_id FROM task_rankings WHERE task_id = ? ORDER BY rank_no", (task["task_id"],)))
    return DecisionState(
        run_id=run_id, state_version=run["state_version"], run_status=run["status"],
        agent_phase=run["agent_phase"], emergency_latched=bool(run["emergency_latched"]),
        request_text=request_text,
        plan_id=plan["plan_id"] if plan else None, plan_mode=plan["mode"] if plan else None,
        plan_station_ids=tuple(json.loads(plan["station_ids_json"])) if plan else (),
        plan_approved=approved, detection=detection, open_task_station_ids=open_stations,
        task_id=task["task_id"] if task else None, task_status=task["status"] if task else None,
        ranked_patrollers=ranked)
```

Implementer notes:
- Verify these three assumptions against the code before relying on them: that the dicts returned by `repository.read_events` carry `payload` as a dict (see `coordinator/events.py`), the task status names (see `coordinator/tasks.py`), and that `observations` stores the `station_id` used by `perception_result.station_capture`.
- Adjust only the lines that read those values. The tests are the ground truth.

- [ ] **Step 1: Write the failing tests.** Drive the real flow with `tests/coordinator/harness.py` (`open_harness`, `envelope`, `FakeWorker`), following the command order in `tests/coordinator/test_one_station_flow.py`. Assert:
  - After `RUN_CREATE {"request_text": "inspect one station"}`: `request_text == "inspect one station"`, `facts()["plan_exists"] is False`, and `to_packet()` contains no UUID (regex `[0-9a-f]{8}-[0-9a-f]{4}-` absent from `json.dumps(packet)`).
  - After plan proposal, approval, preflight, dispatch and perception: `facts()["detection_status"] == "DETECTED"`, `detection_score_band == "very_high"` (the harness `raw_score` is 0.99), `detection_inside_plan is True`, `detection_duplicates_open_task is False`, and `ids()["perception_result_id"] == RESULT_ID`.
  - `score_band`: 0.99→`very_high`, 0.8→`high`, 0.6→`moderate`, 0.5→`marginal`, 0.3→`below_threshold`, None→None.
- [ ] **Step 2:** Run the tests (expect FAIL). **Step 3:** Implement. **Step 4:** Run them (expect PASS), plus the full suite.
- [ ] **Step 5:** Commit with the message `feat(coordinator): decision read model with categorical derived facts`.

### Task 2: `legal_set()` — deterministic legal actions with described options

**Files:**
- Create: `src/sentinel/coordinator/legal_tools.py`
- Test: `tests/coordinator/test_legal_tools.py`

**Interfaces:**
- Consumes: `DecisionState` from Task 1, and `EVIDENCE_SCORE_THRESHOLD` from `command_gateway.py`.
- Produces:
  - `Candidate(tool_name: str, arguments: dict, label: str, description: str)`
  - `LegalSet(decision_point: str, candidates: tuple[Candidate, ...])`
  - `legal_set(state, catalogue: Sequence[CataloguePlan], *, evidence_threshold=EVIDENCE_SCORE_THRESHOLD) -> LegalSet`
  - `CataloguePlan(plan_id: str, description: str)`
  - `load_catalogue_plans() -> tuple[CataloguePlan, ...]`, which reads `docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json`. Each description is built from the fixture's fields (mode, station list, marker requirement).
  - The constants `TERMINAL, WAIT_APPROVAL, SELECT_PLAN, WAIT_OBSERVATION, EVIDENCE_GATE, EVIDENCE_VETO, RANK, ASSIGN, DONE, ESCALATE_LABEL = "escalate"`, and `JUDGMENT_POINTS = frozenset({SELECT_PLAN, EVIDENCE_GATE})`.

```python
# src/sentinel/coordinator/legal_tools.py
"""Deterministic legal-action state machine: DecisionState -> fully-argued, described candidates."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from .command_gateway import EVIDENCE_SCORE_THRESHOLD
from .decision_state import DecisionState

TERMINAL, WAIT_APPROVAL, SELECT_PLAN, WAIT_OBSERVATION = "TERMINAL", "WAIT_APPROVAL", "SELECT_PLAN", "WAIT_OBSERVATION"
EVIDENCE_GATE, EVIDENCE_VETO, RANK, ASSIGN, DONE = "EVIDENCE_GATE", "EVIDENCE_VETO", "RANK", "ASSIGN", "DONE"
ESCALATE_LABEL = "escalate"
JUDGMENT_POINTS = frozenset({SELECT_PLAN, EVIDENCE_GATE})

@dataclass(frozen=True)
class Candidate:
    tool_name: str
    arguments: dict
    label: str
    description: str

@dataclass(frozen=True)
class LegalSet:
    decision_point: str
    candidates: tuple[Candidate, ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(c.label for c in self.candidates)

    def by_label(self, label: str) -> Candidate:
        return next(c for c in self.candidates if c.label == label)

@dataclass(frozen=True)
class CataloguePlan:
    plan_id: str
    description: str

def load_catalogue_plans(path: Path | None = None) -> tuple[CataloguePlan, ...]:
    path = path or Path(__file__).resolve().parents[3] / "docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    plans = []
    for p in doc["plans"]:
        stations = p.get("station_ids") or [s.get("station_id") for s in p.get("stations", [])]
        plans.append(CataloguePlan(p["id"], f"mode={p.get('mode')}; stations={len(stations)} ({', '.join(map(str, stations))}); "
                                            f"marker verification={'required' if 'MARKER' in str(p.get('mode')) else 'not required'}"))
    return tuple(plans)

def _escalate(summary: str) -> Candidate:
    return Candidate("finish", {"status": "NEEDS_REVIEW", "summary": summary[:256]}, ESCALATE_LABEL,
                     "hand the decision to the operator with the reason")

def _wait(reason: str) -> Candidate:
    return Candidate("wait_for_event", {"reason": reason}, "wait", reason)

def legal_set(state: DecisionState, catalogue: Sequence[CataloguePlan], *,
              evidence_threshold: float = EVIDENCE_SCORE_THRESHOLD) -> LegalSet:
    if state.emergency_latched or state.run_status in {"EMERGENCY", "FAILED", "COMPLETED"}:
        return LegalSet(TERMINAL, ())
    if state.run_status == "WAITING_FOR_APPROVAL":
        return LegalSet(WAIT_APPROVAL, (_wait("operator approval pending"),))
    if state.plan_id is None:
        plans = tuple(Candidate("inspection.propose_plan", {"catalogue_plan_id": p.plan_id}, p.plan_id, p.description)
                      for p in catalogue)
        return LegalSet(SELECT_PLAN, plans + (_escalate("no catalogue plan fits the operator request"),))
    if state.task_id is None:
        det = state.detection
        if det is None:
            return LegalSet(WAIT_OBSERVATION, (_wait("awaiting station observation"),))
        score = det.get("raw_score")
        if det.get("status") != "DETECTED" or score is None or score < evidence_threshold:
            return LegalSet(EVIDENCE_VETO, (_escalate("evidence not DETECTED or below threshold"),))
        create = Candidate("task.create", {"perception_result_id": det["result_id"], "section_id": det["station_id"],
                                           "evidence_artifact_id": det["artifact_id"]},
                           "create_task", "create a patrol task for this detection")
        return LegalSet(EVIDENCE_GATE, (create, _escalate("detection needs operator review before tasking")))
    if not state.ranked_patrollers:
        return LegalSet(RANK, (Candidate("patroller.rank_for_task", {"task_id": state.task_id}, "rank",
                                         "rank patrollers for the task"),))
    if state.task_status not in {"PROPOSED", "ASSIGNED"}:
        return LegalSet(ASSIGN, (Candidate("assignment.propose", {"task_id": state.task_id,
                         "patroller_id": state.ranked_patrollers[0], "reason": "deterministic rank-1"},
                         "assign_rank_1", "propose the rank-1 patroller"),))
    return LegalSet(DONE, (_wait("assignment decision pending"),))
```

Implementer notes:
- Before finalising, check the catalogue fixture's real keys (`plans`, `id`, `mode`, stations) and the argument schemas for `wait_for_event`/`finish` (`AgentWaitArguments`, `AgentFinishArguments` in the wire schema).
- Also check the task status strings in `tasks.py`, and the status the gateway sets after `ASSIGNMENT_PROPOSE`.

- [ ] **Step 1: Write the failing tests.**
  - (a) Drive the one-station flow with the harness. At each step assert the decision point sequence `SELECT_PLAN → WAIT_APPROVAL → WAIT_OBSERVATION → EVIDENCE_GATE → RANK → ASSIGN → WAIT_APPROVAL`. At every point except `SELECT_PLAN` and `EVIDENCE_GATE`, assert exactly one candidate.
  - (b) At each point, execute the chosen candidate (the only one, or at `SELECT_PLAN` the `PLAN_ROUTE_ONLY_2_ZONE` candidate, and at `EVIDENCE_GATE` `create_task`) through `AgentLoop` with a one-shot policy. The gateway must accept it. This proves every generated candidate is gateway-valid.
  - (c) A perception variant with `raw_score=0.3` gives `EVIDENCE_VETO` with a single `escalate` candidate.
  - (d) `load_catalogue_plans()` returns `PLAN_MARKER_VERIFIED_6` and `PLAN_ROUTE_ONLY_2_ZONE`, each with a non-empty description.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus the full suite.
- [ ] **Step 5:** Commit with the message `feat(coordinator): deterministic legal-action state machine with described options`.

### Task 3: Loop context and feedback fixes within the current schema (D1–D7)

**Files:**
- Modify: `src/sentinel/coordinator/agent_loop.py`
- Test: `tests/coordinator/test_agent_loop_context.py` (new); keep `tests/coordinator/test_agent_loop.py` green

**Interfaces:**
- Consumes: `read_decision_state`, `legal_set`, `load_catalogue_plans`.
- Produces:
  - `AgentLoop(port, policy, *, max_turns=8, repository=None, catalogue=None)`. The keyword `model=` stays accepted as an alias for `policy` (backwards compatibility with the existing tests).
  - When `repository` is given, requests carry the computed `legal_tools` (the distinct tool names of the candidates, plus `state.get_summary` and `state.get_recent_events`). Without a repository the legacy behaviour is kept.

**Changes:**
1. **D1:** `_event_summary(event)` becomes `f"{event_type}: {json.dumps(_redacted(event['payload']), sort_keys=True)}"[:512]`. `_redacted` keeps ids and scalar fields and drops nested blobs larger than 200 characters. `summary` must be non-empty.
2. **D2:** `evidence_context` is filled from `perception_references` for the run (≤ 32, most recent first): `{"artifact_id", "source_kind", "status", "summary": f"station={station} class={cls} score_band={band} result_id={result_id}"}`. `source_kind` is `"SIMULATED"` for the harness, taken from `perception_result.artifact_source_kind`.
3. **D3:** `legal_tools` is computed from `legal_set()` when a repository is present. It is never empty: at `TERMINAL` the loop returns before building a request.
4. **D4:** the `state.get_summary` result stays schema-bound (`kind`, `run_status`, `phase`, `state_version`). The richer facts arrive through D1/D2, and the typed payload is deferred to the Task 6 amendment.
5. **D5 (within schema):** after a mutation, the next request's `event_context` includes the new event with its payload summary (for example `TASK_CREATED: {"task_id": "..."}`), so ids become visible. `safe_summary` of the result is set to a bounded description of the gateway result: `f"{command_type} {status}; ids: {ids}"[:512]`.
6. **D6:** wrap `self.port.submit(command)` in `try/except CoordinatorError as exc`. Return `AgentToolResult` with `status="REJECTED"`, `error_code=exc.code` (normalised to `^[A-Z][A-Z0-9_]{2,63}$`) and `safe_summary=str(exc)[:512]`. Gateway rejections count against the same corrective budget (2), after which `{"status": "FAILED", "error_code": "GATEWAY_REJECTED"}` is returned.
7. **D7:** an unmapped tool name returns a `REJECTED` result with `error_code="UNKNOWN_TOOL"` and never calls `submit`.

- [ ] **Step 1: Write the failing tests** in `tests/coordinator/test_agent_loop_context.py`:
  - (a) After perception is ingested, the request's `event_context` contains an entry whose summary starts with `DETECTION_RECORDED:` and contains `LEFT_ALLEY_MIDDLE`.
  - (b) `evidence_context` has one item with `status == "DETECTED"` and `score_band=very_high` in the summary.
  - (c) With a repository, `legal_tools` at `EVIDENCE_GATE` equals `["finish", "state.get_recent_events", "state.get_summary", "task.create"]` (sorted comparison).
  - (d) A policy that calls `task.create` with a wrong `section_id` gets `status == "REJECTED"` and a non-null `error_code` in the next request's `previous_result`, and `run()` does not raise.
  - (e) Three consecutive gateway rejections give `error_code == "GATEWAY_REJECTED"`.
  - (f) After `task.create` succeeds, the next request's `event_context` contains the created `task_id`.
  - (g) Every request validates with `validate_document`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus the full suite (the existing `test_agent_loop.py`, `test_one_station_flow.py` and `test_vertical_slice.py` must stay green).
- [ ] **Step 5:** Commit with the message `fix(coordinator): agent loop sees real payloads, computed legal set, gateway rejections as feedback`.

### Task 4: Policy seam and bounded adapter-side history (D8, D9)

**Files:**
- Create: `src/sentinel/coordinator/policy.py`
- Modify: `src/sentinel/adapter/llm/openai_compatible.py` (history only), `src/sentinel/adapter/llm/prompt.py` (render the decision packet and the option descriptions)
- Test: `tests/coordinator/test_policy.py`, `tests/agent/test_history.py`

**Interfaces:**
- Produces:
  - `Policy = Callable[[dict], dict]` (an agent_request dict to an agent_result dict)
  - `PolicyContext(state: DecisionState, legal: LegalSet)`
  - `ContextualPolicy(Protocol)`, with `decide(request: dict, context: PolicyContext) -> dict`
  - `bind_context(policy: ContextualPolicy, repository, catalogue) -> Policy`, which reads the state and legal set per request and calls `decide`
  - `one_call(request: dict, tool_name: str, arguments: dict) -> dict`, which builds a schema-valid `agent_result` with exactly one tool call
- LLM adapter:
  - `OpenAICompatibleAgent(..., history_turns: int = 4)` keeps per-`agent_run_id` the last `history_turns` pairs of (assistant tool call, tool result). It replays them as chat messages before the current request.
  - The history lives only in memory, is capped by count and by characters (12,000), and is cleared when the loop returns.
  - The prompt renders `to_packet()` and each candidate's `label: description` when they are provided via `PolicyContext`. It still falls back to the plain request.

- [ ] **Step 1: Write the failing tests:**
  - (a) `one_call(req, "wait_for_event", {"reason": "x"})` validates as `AgentModelResult`.
  - (b) A `ContextualPolicy` fake receives a `PolicyContext` whose `legal.decision_point` matches the harness state.
  - (c) An adapter with a fake transport gets, on turn 3, a messages list that contains turns 1–2's tool calls and results. With `history_turns=1`, only turn 2 is present.
  - (d) The prompt for `SELECT_PLAN` contains both plan descriptions and the operator request, and contains no UUIDs.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus the full suite.
- [ ] **Step 5:** Commit with the message `feat: policy seam for RULE/Jev/LLM + bounded adapter-side history`.

### Task 5: RULE policy and Jev policy (D11)

**Files:**
- Create: `src/sentinel/coordinator/policies.py`
- Modify: `src/sentinel/judgment.py`, a one-line change so that `JevJudge.choice` accepts `criteria: Mapping[str, str | None]` and passes the descriptions through. `options` stays supported.
- Test: `tests/coordinator/test_policies.py`

**Interfaces:**
- Consumes: `ContextualPolicy`, `PolicyContext`, `one_call`, `JUDGMENT_POINTS`, `ESCALATE_LABEL`.
- Produces:
  - `Decision(chooser: str, label: str | None, confidence: float | None, band: str | None, legal_labels: tuple[str, ...], model: str | None, error: str | None, latency_ms: int | None)`
  - `RulePolicy()`. It acts only when there is one candidate. At judgment points it escalates, labelling the decision `OPERATOR`, and this is the F executor. Its `.last: Decision` attribute holds the most recent decision.
  - `JevPolicy(judge, *, act_at=0.80, review_below=0.50, instructions=INSTRUCTIONS)`:
    - For a single candidate it returns RULE and never calls Jev.
    - Otherwise it calls `judge.choice(packet, instruction, criteria={label: description}, name=point.lower())`.
    - Confidence is `answer["confidence"]` if present, otherwise `answer["probabilities"][answer["value"]]`.
    - A pick of `escalate` is honoured in any band.
    - HIGH (≥ `act_at`) acts. MID and LOW escalate, labelled OPERATOR.
    - `JudgeUnavailable` or any exception escalates, labelled OPERATOR, with `error` set.
    - `.last: Decision` holds the most recent decision.
  - `INSTRUCTIONS: dict[str, str]`, keyed by `SELECT_PLAN` and `EVIDENCE_GATE`:

```python
INSTRUCTIONS = {
    "SELECT_PLAN": "Choose the survey plan that fully satisfies the operator request with the fewest stations and "
                   "least verification burden. Choose 'escalate' if no plan fits or the request is contradictory.",
    "EVIDENCE_GATE": "The detection already passed the static score threshold. Choose 'create_task' if it warrants a "
                     "patrol task. Choose 'escalate' if it duplicates an open task, lies outside the approved plan, "
                     "or otherwise needs operator review.",
}
```

- [ ] **Step 1: Write the failing tests** with a scripted fake judge:
  - A single candidate never calls the judge (the fake raises if called).
  - `create_task` at 0.91 → JEV acts (`tool_name == "task.create"`).
  - At 0.62 → OPERATOR escalate, `band == "MID"`.
  - `escalate` at 0.3 → JEV escalate.
  - `JudgeUnavailable` → OPERATOR with `error` set.
  - With `confidence=None`, the probabilities are used.
  - The criteria passed to the judge contain the plan descriptions from Task 2.
  - `RulePolicy` at `SELECT_PLAN` → escalate/OPERATOR, and at `RANK` → `patroller.rank_for_task`.
  - The full one-station flow with `RulePolicy` stops at `SELECT_PLAN` with `NEEDS_REVIEW`.
  - The full flow with a fake Jev (route-only at 0.95, `create_task` at 0.9) reproduces the event list asserted in `test_one_station_flow.py`, with operator approvals submitted by the test.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, plus the full suite.
- [ ] **Step 5:** Commit with the message `feat(coordinator): RULE and Jev policies over described legal candidates, fail-closed bands`.

### Task 6: WP0 amendment proposal (docs only; implemented in P3)

**Files:**
- Create: `docs/superpowers/specs/2026-09-19_wp0-amendment-decision-context-and-reads.md`

**The proposal must specify (as a schema diff in the document; the schema file itself is not touched):**
1. `AgentRequest.decision_context` (optional object): `{decision_point, packet (decision_packet.v1), candidates: [{label, tool_name, description}] ≤ 8}`.
2. Typed read tools: `state.read_plan`, `state.read_detection`, `state.read_task`, `state.read_rankings`, `state.read_evidence`. Each has fixed or UUID-only arguments and returns a bounded typed payload. `legal_tools.maxItems` rises from 8 to 16.
3. Result payload ids: `{"kind": "task", "task_id"}`, `{"kind": "plan", "plan_id"}`, `{"kind": "ranking", "patroller_ids"}`, `{"kind": "assignment", "task_id", "patroller_id"}`.
4. `operator.ask`, templated by the coordinator: `{question ≤ 512, decision_point, options: [label], suggested: null | {label, confidence}, deadline}`. Expiry yields `finish(NEEDS_REVIEW)`. The operator answers by choosing from `options` in the UI, never in free text.
5. Events: `OPERATOR_QUERY_REQUESTED`, `OPERATOR_QUERY_ANSWERED`, and `DECISION_RECORDED` (chooser, confidence, band, legal labels, model, error), which moves the audit into the event log.
6. A draft `SR-SENTINEL-V0-A04` and a `tool_policy_revision` bump per SR-A03.
7. Reconciliation: `finish(NEEDS_REVIEW)` stays the contradiction channel, and `operator.ask` replaces "escalate with the question in the summary".

- [ ] **Step 1:** Write the proposal. **Step 2:** A fresh reviewer checks it against `requirements/SR-SENTINEL-V0-A03.md` and `docs/COHERENCE.md`.
- [ ] **Step 3:** Commit with the message `docs(wp0): amendment proposal — decision_context, typed reads, result ids, operator.ask`.
- [ ] **Step 4, gate G-amend (parent):** present the proposal to the operator. Never self-grant it.

### Task 7: Fair re-evaluation of the LLM agent (architecture B)

**Files:**
- Modify: `tests/agent/test_live_openai_compatible.py`, `tests/agent/live_scenarios.json`
- Create: `tests/agent/test_live_gate_real_loop.py`
- Delete: `scripts/run_live_llm_acceptance.py` (pre-existing operator-run live-acceptance
  runner, fully superseded by Step 4's `test_live_gate_real_loop.py`; hardcoded the
  old `live_scenarios.json` shape that Step 2 rewrites, so it would raise a bare
  `KeyError` before any network call once Step 2 lands. Folded into this task's file
  list per review round 1 — see deviation note below.)

**Why:** the six failed live runs graded models that saw only event types (D1) and ids smuggled into the fixtures (D10). B has to be judged on the fixed loop.

**Deviation (review round 2):** commit `9e4c348` ("fix(T7): address review round 1")
deleted `scripts/run_live_llm_acceptance.py` without listing it in this task's Files
section or calling it out in the round-1 deviations report. The deletion itself was
verified correct (superseded, would now crash before any network call, zero remaining
references anywhere in the repo) and is left in place; this entry retroactively folds
the file into Task 7's scope and discloses it explicitly, closing the process gap the
round-1 review flagged. No code or test changes were needed.

- [ ] **Step 1: Write the failing test** (hermetic, fake adapter). `run_real_loop_scenario(name, policy)` drives the harness gateway through `AgentLoop(repository=..., catalogue=...)` for the three scenarios. Assert that with a scripted correct policy all three pass, and that no scenario fixture contains a UUID inside an `event_context.summary` string.
- [ ] **Step 2:** Rewrite `live_scenarios.json` so the scenarios specify only `user_request`, the starting harness steps and the expected tool sequence. Context now comes from the real loop, and no ids are smuggled.
- [ ] **Step 3:** Run the full suite (expect PASS). Commit with the message `test(agent): live gate runs on the real loop; no smuggled context`.
- [ ] **Step 4, gate G-live-B (parent):** state the call count (3 scenarios × ≤ 8 turns × 3 repeats ≤ 72 calls), the model (`z-ai/glm-5.3` unless the operator changes it) and the cost estimate. With consent, run `SENTINEL_RUN_LIVE_LLM_ACCEPTANCE=1 uv run python -m pytest tests/agent/test_live_gate_real_loop.py -q -o addopts=''` and commit the summary to `artifacts/wp1/live-gate-real-loop.md`. **The result feeds P3's promotion decision for the LLM. It is not a demo dependency.**

## Self-review

- **Coverage:** D1–D11 each map to a task, and A and B are both addressed (A's defects in Tasks 3–4, B's fair evaluation in Task 7). The old F4 is folded into Task 6. Pose and weather facts are absent because they have no data source (WP5 is pending and there is no weather table). They join `facts()` when those sources land.
- **Types:** `DecisionState`, `Candidate`, `LegalSet`, `CataloguePlan`, `PolicyContext`, `Decision`, `RulePolicy` and `JevPolicy` are defined once and consumed by name in P2 and P3.
