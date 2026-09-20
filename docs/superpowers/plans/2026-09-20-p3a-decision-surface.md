# P3a — The Decision Surface Implementation Plan (rev 2, executor-ready)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. In Claude Code the parent runs this with the `Workflow` tool: per task **dev → fresh review → fix → suite gate**, on Sonnet. Tasks marked **(parent)** and every operator gate stay **outside** the workflow. Steps use checkbox syntax for tracking.
>
> **Rev 2 (2026-09-20)** replaces rev 1. Rev 1 left five "verify first" steps to the executor; they were all resolved by a code reading plus a gateway probe, and the answers changed the task list. A dev subagent sees **only its own task** plus "Global Constraints" and "Ground truth": everything it needs is written there. Do not re-investigate what "Ground truth" already states.

**Goal:** Extend the coordinator so a run contains decisions that rules cannot settle — several detections per run, duplicate detections, near-tie patroller rankings, and replanning on an out-of-plan detection — while the operator's load stays at "approvals only", and without touching `CommandGateway` or any contract.

**Architecture:** Every new decision is added the way P1/P2 added the existing ones: `read_decision_state` exposes facts, `legal_set` enumerates fully-argued candidates, a rule settles what facts can settle (`rule_choice`), and only the remainder is a judgment point executed by rules + operator while Jev and the LLM answer in shadow. **P3a gives no model any authority** (that is P3b, behind the promotion criteria in `2026-09-19-p3-hybrid-E-followup.md`). Whatever the gateway cannot do today is **not built**: it is pinned by a characterization test and listed in one consolidated proposal.

**Tech Stack:** Python 3.12+, sqlite3, pytest, jsonschema, `uv`.

**Spec:** `docs/superpowers/plans/HANDOFF-P3.md` (operator decisions, gates, file ownership) and `docs/superpowers/specs/2026-09-20_wp0-proposal-p3-decision-surface-amendments.md` (blockers A1–A13, proposals P-1…P-8).

## Global Constraints

- Worktree `C:/coding/sentinel/.worktrees/p3-decision-surface`, branch `feat/p3-decision-surface`. Run every command from that directory.
- Suite gate, after every task: `uv run python -m pytest tests/ -q -o addopts=''` → **0 failed**. The 3 skipped tests are the opt-in live gate; leave them skipped.
- **Never edit:** `src/sentinel/coordinator/command_gateway.py`, `tasks.py`, `approval.py`, `plans.py`, `catalogue.py`, `sql/schema.sql`, anything under `src/sentinel/contracts/`, anything under `docs/superpowers/specs/contracts/`, `fixtures/decisions/*`, `artifacts/**` outside `artifacts/p3/`, `src/sentinel/chat.py`, `scripts/rehearsal_run.py`, `scripts/shadow_sweep.py`, `tests/coordinator/turn_generator.py`, `tests/coordinator/harness.py`.
- **Never edit an existing P1/P2 test to make it pass.** If one fails, stop and report the test name and the assertion; the parent decides.
- No live provider call of any kind. No network.
- Every state mutation in a test goes through `CommandGateway.submit`. **One exception**, used only where this plan says so: `SimulatedPatroller(repo).rank_for_task(task_id, scores)` to create multi-patroller rankings (the gateway cannot carry scores; blocker A3). Never `INSERT`/`UPDATE` rows directly.
- `DecisionState.to_packet()` output must stay **byte-identical** (the v1 fixture's golden test and its `additionalProperties:false` schema pin it). New facts go in `to_packet_v2()`.
- The decision-point string stays `"EVIDENCE_GATE"` (rev 1's rename to `DETECTION_TRIAGE` would break the v1 fixture and three scripts). `DETECTION_TRIAGE` exists only as a Python alias of the same string.
- No packet (v1 or v2) may contain a UUID.
- Commit after each task with the message given. Do not push. Do not merge.
- The static veto `EVIDENCE_SCORE_THRESHOLD = 0.5` is unchanged.

## Ground truth (verified 2026-09-20 on `620e8ac` by reading + probing; do not re-derive)

1. **Several detections per run are reachable.** Four `PERCEPTION_INGEST` in a row succeed; several `TASK_CREATE` succeed.
2. **The run goes `COMPLETED` on the first confirmed assignment** (`command_gateway.py:414-418`). After that every command is rejected with `INVALID_STATE_TRANSITION: terminal runs reject ordinary commands`. A second task stays `PROPOSED` forever. (A1)
3. **`ASSIGNMENT_PROPOSE` accepts only rank 1** → `INVALID_STATE_TRANSITION: assignment must bind to the persisted rank-1 proposal`. (A2)
4. **`TASK_RANK` with a `scores` key fails schema validation** (`INVALID_CONTRACT`); through the gateway a ranking is always `[PATROLLER_1: 0.0]`. (A3)
5. **A mid-run `AGENT_INTENT_PROPOSE` is accepted**: the run goes `WAITING_FOR_APPROVAL` with the new plan pending; `REJECT` returns it to `RUNNING`.
6. **Read-model bug (A13):** after that rejection, `read_decision_state` reports the *rejected* plan as the approved plan (it takes the latest `plans` row and `any(PLAN_APPROVED)`). Task 3 fixes it.
7. **Re-proposing a catalogue entry already proposed in the run raises raw `sqlite3.IntegrityError: UNIQUE constraint failed: plans.plan_sha256`** — not a `CoordinatorError`, so it escapes `AgentLoop`. (A9) Task 3 exposes the already-proposed entries so Task 8 can exclude them.
8. **The v1 catalogue schema can express exactly two plans** (A4), the DB and wire schema pin `mode` (A5), and the gateway hardcodes the v1 fixture (A6). A draft catalogue can feed `legal_set` through `load_catalogue_plans(path=…)` (which does not validate) but can **never** be proposed through the gateway.
9. `finish` accepts `status ∈ {DONE, NEEDS_REVIEW, FAILED}` only.
10. Timestamps tie: harness payloads use the constant `NOW`. **Every `ORDER BY created_at` you write must add `, rowid`.**
11. Task statuses in use: `PROPOSED`, `ASSIGNMENT_PROPOSED`, `ASSIGNED` (`CLOSED`/`REJECTED` appear only in SQL filters).
12. Reusable gateway-driving helpers live in `tests/coordinator/turn_generator.py`: `_run_id_for`, `_Sequencer`, `_submit`, `_state_version`, `_create_run`, `_propose_and_approve_route_plan`, `_perception_payload(seed, plan_id, plan_sha256, station_id, raw_score, status="DETECTED")`, `_ingest_perception`, `_create_task`. `TASK_CREATE` needs no preflight or dispatch.

## Two waves

| Wave | Tasks | Needs P2b (`rule_choice`)? |
|---|---|---|
| 1 | 0–5 | **No.** Start now, from the current tip of `feat/p2-shadow-advisors`. |
| 2 | 6–11 | **Yes.** Task 6 is a mechanical gate + rebase + interface pin. |

Wave 1 touches one shared file (`decision_state.py`), additively. Everything else in Wave 1 is a new file.

**Wave 1 was dry-run on 2026-09-20**: the code and tests of Tasks 1–5, applied verbatim to a throwaway checkout of `620e8ac`, gave **30 new tests passed** (read from the summary line) and a full suite with **0 failures**. The dry-run checkout's total (502) does not reconcile with this worktree's 492 baseline plus 30, so trust **your own** suite gate, not either total. A Wave 1 dev whose tests fail has most likely mis-applied a snippet (watch the indentation of the snippets that go inside `DecisionState` and inside `read_decision_state`), not found a plan error. Wave 2's test bodies are outlines by necessity (they depend on P2b's final names) and were **not** dry-run.

## Operator gates (parent brings these; never self-grant)

| Gate | When | Decision |
|---|---|---|
| G-amend | after Task 0 (any time) | consent per item P-1…P-8 of the consolidated proposal; includes **G-closer-look** (P-7) |
| G-catalogue | after Task 1 | is the 14-plan draft the right set? (content only; adopting it is P-3) |
| G-labels-v2 | after Task 10 | approve/edit the hand labels; write the 24 operator request texts |
| G-merge | Final | merge into `master`, push |

---

## WAVE 1

### Task 0 (parent): worktree, documents, baseline

- [ ] **Step 1:** Create the worktree from the current P2 tip.

```bash
cd C:/coding/sentinel
git worktree add .worktrees/p3-decision-surface -b feat/p3-decision-surface feat/p2-shadow-advisors
```

- [ ] **Step 2:** Copy the three documents (they are untracked in the main checkout) into the worktree, same relative paths: `docs/superpowers/plans/2026-09-20-p3a-decision-surface.md`, `docs/superpowers/plans/HANDOFF-P3.md`, `docs/superpowers/specs/2026-09-20_wp0-proposal-p3-decision-surface-amendments.md`.
- [ ] **Step 3:** A fresh worktree has no dev extras: run `uv sync --extra dev` first (without it: `No module named pytest` / `jsonschema`). Then baseline: `uv run python -m pytest tests/ -q -o addopts=''`. **Measured 2026-09-20 in this worktree: 495 collected → 492 passed, 3 skipped, 0 failed** (the handoff's earlier "472" was stale). The suite takes ~3 minutes. (If the `rtk` hook prints "No tests collected", run it as `rtk proxy uv run python -m pytest …`.)
- [ ] **Step 4:** Commit: `docs(p3): P3a plan rev 2, handoff, consolidated WP0 amendment proposal`.
- [ ] **Step 5:** Open **G-amend** with the operator (it does not block Wave 1 or Wave 2).

### Task 1: Draft rich catalogue (content only; never loaded in production)

**Files:**
- Create: `scripts/p3/build_catalogue_draft.py`, `fixtures/catalogue_draft/survey-catalogue-v2.draft.json` (generated), `fixtures/catalogue_draft/README.md`
- Test: `tests/coordinator/test_catalogue_draft.py`

**Interfaces:**
- Consumes: `load_catalogue_plans(path: Path | None) -> tuple[CataloguePlan, ...]` from `sentinel.coordinator.legal_tools`.
- Produces: `DRAFT_CATALOGUE_PATH = <repo>/fixtures/catalogue_draft/survey-catalogue-v2.draft.json`, importable as `tests.coordinator.test_catalogue_draft.DRAFT_CATALOGUE_PATH`; 14 plan ids listed below (Tasks 8 and 10 use them).

- [ ] **Step 1: Write the failing test** `tests/coordinator/test_catalogue_draft.py`:

```python
"""The draft rich catalogue is content for operator review. No production path may read it."""
from __future__ import annotations

import json
import re
from pathlib import Path

from sentinel.coordinator.legal_tools import load_catalogue_plans

ROOT = Path(__file__).resolve().parents[2]
DRAFT_CATALOGUE_PATH = ROOT / "fixtures/catalogue_draft/survey-catalogue-v2.draft.json"
STATIONS = ("LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK",
            "RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK")
EXPECTED_IDS = {
    *(f"PLAN_ROUTE_{s}" for s in STATIONS),
    "PLAN_ROUTE_LEFT_ALLEY", "PLAN_ROUTE_RIGHT_ALLEY",
    "PLAN_ROUTE_FRONT_PAIR", "PLAN_ROUTE_MIDDLE_PAIR", "PLAN_ROUTE_BACK_PAIR",
    "PLAN_VERIFIED_LEFT_ALLEY", "PLAN_VERIFIED_RIGHT_ALLEY", "PLAN_VERIFIED_FULL_SITE",
}


def test_draft_loads_through_the_production_loader_and_has_the_14_plans():
    plans = load_catalogue_plans(DRAFT_CATALOGUE_PATH)
    assert {p.plan_id for p in plans} == EXPECTED_IDS


def test_no_plan_id_contains_a_bare_count():
    for plan in load_catalogue_plans(DRAFT_CATALOGUE_PATH):
        assert not re.search(r"_\d+(_|$)", plan.plan_id), plan.plan_id


def test_every_description_names_its_stations():
    doc = json.loads(DRAFT_CATALOGUE_PATH.read_text(encoding="utf-8"))
    by_id = {p.plan_id: p for p in load_catalogue_plans(DRAFT_CATALOGUE_PATH)}
    for entry in doc["entries"]:
        for step in entry["station_steps"]:
            assert step["zone_id"] in by_id[entry["id"]].description


def test_verified_plans_carry_markers_and_route_plans_do_not():
    doc = json.loads(DRAFT_CATALOGUE_PATH.read_text(encoding="utf-8"))
    for entry in doc["entries"]:
        markers = [step.get("marker_id") for step in entry["station_steps"]]
        if entry["mode"] == "MARKER_VERIFIED":
            assert all(markers)
        else:
            assert entry["mode"] == "ROUTE_ONLY" and not any(markers)


def test_draft_is_marked_draft_and_production_default_is_still_v1():
    doc = json.loads(DRAFT_CATALOGUE_PATH.read_text(encoding="utf-8"))
    assert doc["revision"] == "DRAFT_REV_2"
    assert {p.plan_id for p in load_catalogue_plans()} == {"PLAN_MARKER_VERIFIED_6", "PLAN_ROUTE_ONLY_2_ZONE"}


def test_no_source_file_references_the_draft():
    offenders = [str(p) for p in (ROOT / "src").rglob("*.py") if "catalogue_draft" in p.read_text(encoding="utf-8")]
    assert offenders == []
```

- [ ] **Step 2:** Run `uv run python -m pytest tests/coordinator/test_catalogue_draft.py -q -o addopts=''` → FAIL (file not found).
- [ ] **Step 3: Write the builder** `scripts/p3/build_catalogue_draft.py`, then run it (`uv run python scripts/p3/build_catalogue_draft.py`):

```python
"""Build the DRAFT rich survey catalogue (P3a Task 1). Content for operator review only:
it cannot validate under survey-catalogue-v1.schema.json and no production path loads it."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "docs/superpowers/specs/contracts/fixtures/survey-catalogue-v1.fixture.json"
OUT = ROOT / "fixtures/catalogue_draft/survey-catalogue-v2.draft.json"

MARKERS = {"LEFT_ALLEY_FRONT": "TAG_LEFT_FRONT", "LEFT_ALLEY_MIDDLE": "TAG_LEFT_MIDDLE",
           "LEFT_ALLEY_BACK": "TAG_LEFT_BACK", "RIGHT_ALLEY_FRONT": "TAG_RIGHT_FRONT",
           "RIGHT_ALLEY_MIDDLE": "TAG_RIGHT_MIDDLE", "RIGHT_ALLEY_BACK": "TAG_RIGHT_BACK"}
LEFT = ("LEFT_ALLEY_FRONT", "LEFT_ALLEY_MIDDLE", "LEFT_ALLEY_BACK")
RIGHT = ("RIGHT_ALLEY_FRONT", "RIGHT_ALLEY_MIDDLE", "RIGHT_ALLEY_BACK")

PLANS: list[tuple[str, str, tuple[str, ...]]] = [
    *[(f"PLAN_ROUTE_{s}", "ROUTE_ONLY", (s,)) for s in LEFT + RIGHT],
    ("PLAN_ROUTE_LEFT_ALLEY", "ROUTE_ONLY", LEFT),
    ("PLAN_ROUTE_RIGHT_ALLEY", "ROUTE_ONLY", RIGHT),
    ("PLAN_ROUTE_FRONT_PAIR", "ROUTE_ONLY", ("LEFT_ALLEY_FRONT", "RIGHT_ALLEY_FRONT")),
    ("PLAN_ROUTE_MIDDLE_PAIR", "ROUTE_ONLY", ("LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE")),
    ("PLAN_ROUTE_BACK_PAIR", "ROUTE_ONLY", ("LEFT_ALLEY_BACK", "RIGHT_ALLEY_BACK")),
    ("PLAN_VERIFIED_LEFT_ALLEY", "MARKER_VERIFIED", LEFT),
    ("PLAN_VERIFIED_RIGHT_ALLEY", "MARKER_VERIFIED", RIGHT),
    ("PLAN_VERIFIED_FULL_SITE", "MARKER_VERIFIED", LEFT + RIGHT),
]


def build() -> dict:
    v1 = json.loads(V1.read_text(encoding="utf-8"))
    template = {("MARKER_VERIFIED" if e["mode"] == "MARKER_VERIFIED" else "ROUTE_ONLY"): e for e in v1["entries"]}
    entries = []
    for plan_id, mode, stations in PLANS:
        base = template[mode]
        entries.append({
            "id": plan_id, "revision": "DRAFT_REV_2", "sha256": "0" * 64, "mode": mode,
            "station_steps": [{"index": i, "zone_id": s, "marker_id": MARKERS[s] if mode == "MARKER_VERIFIED" else None}
                              for i, s in enumerate(stations, 1)],
            "capture_headings_deg": base["capture_headings_deg"], "capture_count": base["capture_count"],
            "segment_heights_m": [{"zone_id": s, "height_m": 1.2} for s in stations],
            "route_bounds": base["route_bounds"], "timeout": base["timeout"], "command_budget": base["command_budget"],
        })
    return {"contract_version": "sentinel.survey-catalogue.v1", "catalogue_id": "SURVEY_CATALOGUE_V2_DRAFT",
            "revision": "DRAFT_REV_2", "sha256": "0" * 64, "entries": entries}


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(PLANS)} plans)")
```

  `fixtures/catalogue_draft/README.md`: five lines — what it is, that `sha256` values are placeholders, that `route_bounds`/`timeout`/`command_budget` are copied from v1 and need the operator's review per plan, that adoption is proposal item P-3, and the migration map (`PLAN_ROUTE_ONLY_2_ZONE → PLAN_ROUTE_MIDDLE_PAIR`, `PLAN_MARKER_VERIFIED_6 → PLAN_VERIFIED_FULL_SITE`).
- [ ] **Step 4:** Test file PASS, then the suite gate.
- [ ] **Step 5:** Commit `feat(p3): draft rich survey catalogue, 14 coverage-named plans (not loaded in production)`.

### Task 2: `DecisionState` sees several detections and several tasks

**Files:**
- Create: `tests/coordinator/p3_helpers.py`
- Modify: `src/sentinel/coordinator/decision_state.py`
- Test: `tests/coordinator/test_decision_state_multi.py`

**Interfaces — Produces** (Tasks 3, 4, 7–10 rely on these exact names):
- `DetectionView(result_id: str, artifact_id: str, station_id: str, status: str | None, raw_score: float | None, canonical_class: str | None)` — frozen dataclass.
- `TaskView(task_id: str, station_id: str, status: str, perception_result_id: str | None, ranked_patrollers: tuple[str, ...], ranking_scores: tuple[float, ...])` — frozen dataclass.
- `DecisionState.detections: tuple[DetectionView, ...] = ()` — perception results of the run that no task references, **oldest first**.
- `DecisionState.tasks: tuple[TaskView, ...] = ()` — every task of the run, oldest first.
- `DecisionState.current_task -> TaskView | None` (property): the oldest task whose status is not in `{"ASSIGNED","CLOSED","REJECTED"}`; if none, the newest task; if no task, `None`.
- `DecisionState.detection_facts(d: DetectionView) -> dict` with keys `score_band`, `inside_plan`, `duplicates_open_task`, `repeat_of_handled`.
- `DecisionState.to_packet_v2() -> dict`.
- **Unchanged contract:** the existing fields `detection`, `task_id`, `task_status`, `ranked_patrollers`, `open_task_station_ids`, and `facts()`, `to_packet()`, `ids()`. `detection` becomes the dict form of `detections[0]`; `task_id/task_status/ranked_patrollers` come from `current_task`. For a run with one detection and one task these equal the old values.
- `tests/coordinator/p3_helpers.py`: class `P3Run` (below).

- [ ] **Step 1: Write the helper** `tests/coordinator/p3_helpers.py`:

```python
"""Gateway-driving helper for P3 tests. Every mutation goes through CommandGateway.submit."""
from __future__ import annotations

import json
from pathlib import Path

from sentinel.coordinator.decision_state import read_decision_state

from . import turn_generator as tg
from .harness import open_harness

LEFT_MID, RIGHT_MID, LEFT_FRONT = "LEFT_ALLEY_MIDDLE", "RIGHT_ALLEY_MIDDLE", "LEFT_ALLEY_FRONT"  # LEFT_FRONT is outside the route plan


class P3Run:
    def __init__(self, tmp_path: Path, name: str, request: str = "route-only survey of the alley stations"):
        self.repo, self.gateway = open_harness(tmp_path)
        self.run_id = tg._run_id_for(f"p3:{name}")
        self.seq = tg._Sequencer()
        self.reference: dict | None = None
        tg._create_run(self.gateway, self.run_id, self.seq, request)

    def approve_route_plan(self) -> dict:
        """Propose and CONFIRM PLAN_ROUTE_ONLY_2_ZONE (LEFT_ALLEY_MIDDLE, RIGHT_ALLEY_MIDDLE)."""
        self.reference = tg._propose_and_approve_route_plan(self.gateway, self.repo, self.run_id, self.seq)
        return self.reference

    def ingest(self, seed: str, station_id: str, raw_score: float | None, status: str = "DETECTED") -> dict:
        payload = tg._perception_payload(f"{self.run_id}:{seed}", self.reference["plan_id"],
                                         self.reference["plan_sha256"], station_id, raw_score, status)
        tg._ingest_perception(self.gateway, self.run_id, self.seq, payload)
        return payload

    def create_task(self, perception: dict) -> str:
        tg._create_task(self.gateway, self.run_id, self.seq, perception)
        row = self.repo.connection.execute(
            "SELECT task_id FROM tasks WHERE perception_result_id = ?", (perception["result_id"],)).fetchone()
        return row["task_id"]

    def submit(self, command_type: str, payload: dict, *, actor_type: str = "AGENT", with_version: bool = False) -> dict:
        expected = tg._state_version(self.repo, self.run_id) if with_version else None
        return tg._submit(self.gateway, self.run_id, self.seq, command_type, payload,
                          actor_type=actor_type, expected=expected)

    def rank(self, task_id: str) -> None:
        self.submit("TASK_RANK", {"task_id": task_id})

    def propose_plan(self, catalogue_plan_id: str) -> None:
        self.submit("AGENT_INTENT_PROPOSE", {"intent": "REQUEST_OPERATOR_APPROVAL", "catalogue_plan_id": catalogue_plan_id})

    def decide_latest_plan(self, decision: str) -> None:
        row = self.repo.connection.execute(
            "SELECT plan_id, catalogue_id, catalogue_revision, plan_sha256, station_ids_json FROM plans "
            "WHERE run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (self.run_id,)).fetchone()
        reference = {"plan_id": row["plan_id"], "catalogue_id": row["catalogue_id"],
                     "catalogue_revision": row["catalogue_revision"], "plan_sha256": row["plan_sha256"],
                     "station_ids": json.loads(row["station_ids_json"])}
        self.submit("OPERATOR_DECIDE", {"decision": decision, "target": {"kind": "PLAN", "plan_reference": reference}},
                    actor_type="OPERATOR", with_version=True)

    def state(self):
        return read_decision_state(self.repo, self.run_id)

    def close(self) -> None:
        self.repo.close()
```

- [ ] **Step 2: Write the failing test** `tests/coordinator/test_decision_state_multi.py`:

```python
from __future__ import annotations

import json
import re

import pytest

from sentinel.coordinator.decision_state import DecisionState, DetectionView, TaskView

from .p3_helpers import LEFT_FRONT, LEFT_MID, RIGHT_MID, P3Run

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@pytest.fixture
def run(tmp_path):
    r = P3Run(tmp_path, "multi")
    r.approve_route_plan()
    yield r
    r.close()


def test_three_detections_are_listed_oldest_first(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    b = run.ingest("b", LEFT_MID, 0.92)
    c = run.ingest("c", RIGHT_MID, 0.95)
    state = run.state()
    assert [d.result_id for d in state.detections] == [a["result_id"], b["result_id"], c["result_id"]]
    assert state.tasks == ()
    assert state.detection["result_id"] == a["result_id"]          # compat accessor = head of the queue
    assert state.task_id is None


def test_tasking_the_first_detection_moves_it_out_of_the_queue(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    b = run.ingest("b", LEFT_MID, 0.92)
    run.ingest("c", RIGHT_MID, 0.95)
    task_id = run.create_task(a)
    state = run.state()
    assert len(state.detections) == 2 and state.detections[0].result_id == b["result_id"]
    assert [t.task_id for t in state.tasks] == [task_id]
    assert state.tasks[0].status == "PROPOSED" and state.tasks[0].ranked_patrollers == ()
    assert state.task_id == task_id and state.task_status == "PROPOSED"   # compat accessors = current_task


def test_detection_facts_flag_a_duplicate_of_an_open_task(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    run.ingest("b", LEFT_MID, 0.92)
    run.ingest("c", RIGHT_MID, 0.95)
    run.create_task(a)
    state = run.state()
    dup, other = state.detections
    assert state.detection_facts(dup) == {"score_band": "very_high", "inside_plan": True,
                                          "duplicates_open_task": True, "repeat_of_handled": False}
    assert state.detection_facts(other)["duplicates_open_task"] is False


def test_outside_plan_detection_is_flagged(run):
    run.ingest("x", LEFT_FRONT, 0.90)
    state = run.state()
    assert state.detection_facts(state.detections[0])["inside_plan"] is False


def test_current_task_is_the_oldest_unsettled_task_and_ranking_scores_are_exposed(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    c = run.ingest("c", RIGHT_MID, 0.95)
    first, second = run.create_task(a), run.create_task(c)
    run.rank(first)
    state = run.state()
    assert state.current_task.task_id == first
    assert state.tasks[0].ranked_patrollers == ("PATROLLER_1",) and state.tasks[0].ranking_scores == (0.0,)
    assert state.tasks[1].task_id == second and state.tasks[1].ranked_patrollers == ()


def test_repeat_of_handled_on_a_constructed_state():
    """An ASSIGNED task alongside a pending detection is unreachable through the gateway today
    (the run completes on the first confirmed assignment, blocker A1), so this fact is unit-tested
    on a constructed state."""
    handled = TaskView("t", LEFT_MID, "ASSIGNED", "r0", ("PATROLLER_1",), (0.0,))
    again = DetectionView("r1", "a1", LEFT_MID, "DETECTED", 0.9, "rock")
    state = DecisionState(run_id="run", state_version=1, run_status="RUNNING", agent_phase="PLAN",
                          emergency_latched=False, request_text=None, plan_id="p", plan_mode="ROUTE_ONLY_2_ZONE",
                          plan_station_ids=(LEFT_MID, RIGHT_MID), plan_approved=True, detection=None,
                          open_task_station_ids=(LEFT_MID,), task_id="t", task_status="ASSIGNED",
                          detections=(again,), tasks=(handled,))
    facts = state.detection_facts(again)
    assert facts["repeat_of_handled"] is True and facts["duplicates_open_task"] is False


def test_packets_carry_no_uuid_and_v1_packet_shape_is_unchanged(run):
    a = run.ingest("a", LEFT_MID, 0.90)
    run.ingest("b", LEFT_MID, 0.92)
    run.create_task(a)
    state = run.state()
    assert set(state.to_packet()) == {"context_version", "run", "operator_request", "facts"}
    assert state.to_packet()["context_version"] == "decision_packet.v1"
    v2 = state.to_packet_v2()
    assert v2["context_version"] == "decision_packet.v2"
    assert v2["queue"] == {"pending_detections": 1, "open_tasks": 1}
    assert v2["detection"]["duplicates_open_task"] is True
    assert not UUID_RE.search(json.dumps(v2)) and not UUID_RE.search(json.dumps(state.to_packet()))
```

- [ ] **Step 3:** Run it → FAIL (`ImportError: DetectionView`).
- [ ] **Step 4: Implement** in `decision_state.py`. Add above `DecisionState`:

```python
_SETTLED_TASK = frozenset({"ASSIGNED", "CLOSED", "REJECTED"})
_OPEN_TASK = frozenset({"PROPOSED", "ASSIGNMENT_PROPOSED"})
_HANDLED_TASK = frozenset({"ASSIGNED", "CLOSED"})


@dataclass(frozen=True)
class DetectionView:
    result_id: str
    artifact_id: str
    station_id: str
    status: str | None
    raw_score: float | None
    canonical_class: str | None


@dataclass(frozen=True)
class TaskView:
    task_id: str
    station_id: str
    status: str
    perception_result_id: str | None
    ranked_patrollers: tuple[str, ...] = ()
    ranking_scores: tuple[float, ...] = ()
```

  Append two fields to `DecisionState` **after** `ranked_patrollers` (defaults keep every existing constructor call valid):

```python
    detections: tuple[DetectionView, ...] = field(default_factory=tuple)
    tasks: tuple[TaskView, ...] = field(default_factory=tuple)
```

  Add to `DecisionState`:

```python
    @property
    def current_task(self) -> TaskView | None:
        for task in self.tasks:
            if task.status not in _SETTLED_TASK:
                return task
        return self.tasks[-1] if self.tasks else None

    def detection_facts(self, d: DetectionView) -> dict:
        same_station = [t for t in self.tasks if t.station_id == d.station_id]
        return {"score_band": score_band(d.raw_score),
                "inside_plan": (d.station_id in self.plan_station_ids) if self.plan_id else None,
                "duplicates_open_task": any(t.status in _OPEN_TASK for t in same_station),
                "repeat_of_handled": any(t.status in _HANDLED_TASK for t in same_station)}

    def to_packet_v2(self) -> dict:
        head = self.detections[0] if self.detections else None
        return {"context_version": "decision_packet.v2",
                "run": {"status": self.run_status, "phase": self.agent_phase},
                "operator_request": self.request_text,
                "facts": self.facts(),
                "queue": {"pending_detections": len(self.detections),
                          "open_tasks": sum(1 for t in self.tasks if t.status in _OPEN_TASK)},
                "detection": self.detection_facts(head) if head else None}
```

  In `read_decision_state`, **replace** the single-`task` query, the single-`ref` query and the `ranked` block with:

```python
    det_rows = c.execute("""SELECT pr.result_id, pr.artifact_id, pr.result_json, o.station_id
                            FROM perception_references pr JOIN observations o USING (observation_id)
                            WHERE pr.run_id = ? AND pr.result_id NOT IN
                                  (SELECT COALESCE(perception_result_id, '') FROM tasks)
                            ORDER BY pr.created_at, pr.rowid""", (run_id,)).fetchall()
    detections = []
    for row in det_rows:
        result = json.loads(row["result_json"])
        detections.append(DetectionView(row["result_id"], row["artifact_id"], row["station_id"],
                                        result.get("status"), result.get("raw_score"), result.get("canonical_class")))
    tasks = []
    for row in c.execute("SELECT task_id, station_id, status, perception_result_id FROM tasks "
                         "WHERE run_id = ? ORDER BY created_at, rowid", (run_id,)).fetchall():
        ranking = c.execute("SELECT patroller_id, score FROM task_rankings WHERE task_id = ? ORDER BY rank_no",
                            (row["task_id"],)).fetchall()
        tasks.append(TaskView(row["task_id"], row["station_id"], row["status"], row["perception_result_id"],
                              tuple(r["patroller_id"] for r in ranking), tuple(float(r["score"]) for r in ranking)))
    head = detections[0] if detections else None
    detection = None if head is None else {
        "result_id": head.result_id, "artifact_id": head.artifact_id, "station_id": head.station_id,
        "status": head.status, "raw_score": head.raw_score, "canonical_class": head.canonical_class}
    current = next((t for t in tasks if t.status not in _SETTLED_TASK), tasks[-1] if tasks else None)
```

  and in the final `DecisionState(...)` call pass `detection=detection`, `task_id=current.task_id if current else None`, `task_status=current.status if current else None`, `ranked_patrollers=current.ranked_patrollers if current else ()`, `detections=tuple(detections)`, `tasks=tuple(tasks)`. Leave `open_task_station_ids`, `facts()`, `to_packet()`, `ids()` untouched.
- [ ] **Step 5:** Test file PASS, then the suite gate. The v1 golden test (`tests/coordinator/test_turn_fixture.py`) must pass untouched.
- [ ] **Step 6:** Commit `feat(coordinator): decision state models several detections and tasks per run`.

### Task 3: Approved-plan read-model fix (A13) and already-proposed entries (A9)

**Files:**
- Modify: `src/sentinel/coordinator/decision_state.py`
- Test: `tests/coordinator/test_decision_state_plans.py`

**Interfaces:**
- Consumes: `P3Run` (Task 2).
- Produces: `DecisionState.proposed_catalogue_plan_ids: tuple[str, ...] = ()` — every catalogue entry id proposed in this run, in order. New semantics of existing fields: `plan_id / plan_mode / plan_station_ids` = the **latest CONFIRMed** plan; if none is confirmed, the latest plan with **no decision yet**; if every plan was rejected, `None / None / ()`. `plan_approved` = "that plan is a confirmed one".

- [ ] **Step 1: Write the failing test:**

```python
from __future__ import annotations

import sqlite3

import pytest

from .p3_helpers import LEFT_MID, RIGHT_MID, P3Run

ROUTE, FULL = "PLAN_ROUTE_ONLY_2_ZONE", "PLAN_MARKER_VERIFIED_6"


@pytest.fixture
def run(tmp_path):
    r = P3Run(tmp_path, "plans")
    yield r
    r.close()


def test_rejected_mid_run_proposal_leaves_the_old_plan_approved(run):
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("REJECT")
    state = run.state()
    assert state.plan_station_ids == (LEFT_MID, RIGHT_MID) and state.plan_approved is True
    assert state.run_status == "RUNNING"


def test_confirmed_mid_run_proposal_becomes_the_plan(run):
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("CONFIRM")
    state = run.state()
    assert len(state.plan_station_ids) == 6 and state.plan_approved is True


def test_pending_first_plan_is_visible_but_not_approved(run):
    run.propose_plan(ROUTE)
    state = run.state()
    assert state.plan_id is not None and state.plan_approved is False


def test_rejected_only_plan_returns_the_run_to_no_plan(run):
    run.propose_plan(ROUTE)
    run.decide_latest_plan("REJECT")
    state = run.state()
    assert state.plan_id is None and state.plan_station_ids == () and state.plan_approved is False


def test_proposed_catalogue_entries_are_listed(run):
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("REJECT")
    assert run.state().proposed_catalogue_plan_ids == (ROUTE, FULL)


def test_characterization_reproposing_an_entry_raises_a_raw_integrity_error(run):
    """Blocker A9 (proposal item P-6). When P-6 is granted this test flips; do not 'fix' it here."""
    run.approve_route_plan()
    run.propose_plan(FULL)
    run.decide_latest_plan("REJECT")
    with pytest.raises(sqlite3.IntegrityError):
        run.propose_plan(FULL)
```

- [ ] **Step 2:** Run → FAIL (first test: 6 stations reported; `proposed_catalogue_plan_ids` missing).
- [ ] **Step 3: Implement.** Append the field `proposed_catalogue_plan_ids: tuple[str, ...] = field(default_factory=tuple)` to `DecisionState`. In `read_decision_state` replace the `plan = …` query and the `approved = any(…)` line with:

```python
    plan_rows = c.execute(
        """SELECT p.plan_id, p.mode, p.station_ids_json,
                  (SELECT pa.action FROM plan_approvals pa
                   WHERE pa.run_id = p.run_id AND pa.plan_id = p.plan_id
                   ORDER BY pa.created_at DESC, pa.rowid DESC LIMIT 1) AS action
           FROM plans p WHERE p.run_id = ? ORDER BY p.created_at, p.rowid""", (run_id,)).fetchall()
    confirmed = [r for r in plan_rows if r["action"] == "CONFIRM"]
    undecided = [r for r in plan_rows if r["action"] is None]
    plan = confirmed[-1] if confirmed else (undecided[-1] if undecided else None)
    approved = bool(confirmed)
    proposed = tuple(e["payload"]["catalogue_plan_id"] for e in events
                     if e["event_type"] == "PLAN_PROPOSED" and "catalogue_plan_id" in e["payload"])
```

  and pass `proposed_catalogue_plan_ids=proposed`.
- [ ] **Step 4:** Test file PASS, then the suite gate. **If an existing P1/P2 test fails because it relied on "a rejected plan still counts as the plan", stop and report it** (Global Constraints).
- [ ] **Step 5:** Commit `fix(coordinator): read model reports the confirmed plan, not the latest proposed one`.

### Task 4: Near-tie facts (pure functions + packet v2)

**Files:**
- Create: `src/sentinel/coordinator/assignment_policy.py`
- Modify: `src/sentinel/coordinator/decision_state.py` (`to_packet_v2` only)
- Test: `tests/coordinator/test_assignment_policy.py`

**Interfaces:**
- Consumes: `TaskView.ranking_scores`, `DecisionState.current_task`, `to_packet_v2` (Task 2); `SimulatedPatroller(repo).rank_for_task(task_id, scores)` (the one permitted non-gateway write).
- Produces: `ASSIGNMENT_CLEAR_GAP = 0.10`; `rank_gap(scores: Sequence[float]) -> float | None`; `gap_band(gap: float | None) -> str` ∈ `{"single","tie","very_close","close","clear"}`; `is_near_tie(scores) -> bool`; `assignment_facts(scores) -> dict` = `{"patrollers_ranked": n, "rank_gap_band": str, "near_tie": bool}`; `to_packet_v2()["assignment"]` = those facts for `current_task` when it is ranked, else `None`.

- [ ] **Step 1: Write the failing test:**

```python
from __future__ import annotations

import pytest

from sentinel.coordinator.assignment_policy import (
    ASSIGNMENT_CLEAR_GAP, assignment_facts, gap_band, is_near_tie, rank_gap)
from sentinel.coordinator.tasks import SimulatedPatroller

from .p3_helpers import LEFT_MID, P3Run


@pytest.mark.parametrize("scores,gap,band,near", [
    ((0.0,), None, "single", False),
    ((0.80, 0.80), 0.0, "tie", True),
    ((0.81, 0.79), 0.02, "very_close", True),
    ((0.85, 0.79), 0.06, "close", True),
    ((0.90, 0.80), 0.10, "clear", False),      # 0.9 - 0.8 == 0.0999… in floats: rounding must make it clear
    ((0.95, 0.60, 0.59), 0.35, "clear", False),
])
def test_gap_bands(scores, gap, band, near):
    assert rank_gap(scores) == gap
    assert gap_band(rank_gap(scores)) == band
    assert is_near_tie(scores) is near
    assert assignment_facts(scores) == {"patrollers_ranked": len(scores), "rank_gap_band": band, "near_tie": near}


def test_clear_gap_constant_is_declared():
    assert ASSIGNMENT_CLEAR_GAP == 0.10


def test_packet_v2_carries_assignment_facts_for_the_current_task(tmp_path):
    run = P3Run(tmp_path, "near-tie")
    try:
        run.approve_route_plan()
        task_id = run.create_task(run.ingest("a", LEFT_MID, 0.9))
        assert run.state().to_packet_v2()["assignment"] is None            # not ranked yet
        # The gateway cannot carry scores (blocker A3); this service call is the one permitted exception.
        SimulatedPatroller(run.repo).rank_for_task(task_id, {"PATROLLER_1": 0.81, "PATROLLER_2": 0.79})
        assert run.state().to_packet_v2()["assignment"] == {
            "patrollers_ranked": 2, "rank_gap_band": "very_close", "near_tie": True}
    finally:
        run.close()
```

- [ ] **Step 2:** Run → FAIL (module missing).
- [ ] **Step 3: Implement** `assignment_policy.py`:

```python
"""Patroller-choice facts. A near-tie between rank 1 and rank 2 is a judgment point;
a clear gap is settled by rule. Today the gateway only ever produces a single patroller
(blocker A3) and only accepts rank 1 (A2); see the consolidated WP0 proposal, item P-2."""
from __future__ import annotations

from typing import Sequence

ASSIGNMENT_CLEAR_GAP = 0.10
_GAP_BANDS = ((ASSIGNMENT_CLEAR_GAP, "clear"), (0.05, "close"), (0.01, "very_close"))


def rank_gap(scores: Sequence[float]) -> float | None:
    return None if len(scores) < 2 else round(scores[0] - scores[1], 6)


def gap_band(gap: float | None) -> str:
    if gap is None:
        return "single"
    for floor, name in _GAP_BANDS:
        if gap >= floor:
            return name
    return "tie"


def is_near_tie(scores: Sequence[float]) -> bool:
    gap = rank_gap(scores)
    return gap is not None and gap < ASSIGNMENT_CLEAR_GAP


def assignment_facts(scores: Sequence[float]) -> dict:
    return {"patrollers_ranked": len(scores), "rank_gap_band": gap_band(rank_gap(scores)),
            "near_tie": is_near_tie(scores)}
```

  In `DecisionState.to_packet_v2` add the key (import `assignment_facts` inside the method to avoid an import cycle if one appears; a top-level import is fine otherwise):

```python
                "assignment": (assignment_facts(self.current_task.ranking_scores)
                               if self.current_task and self.current_task.ranking_scores else None),
```

- [ ] **Step 4:** Test file PASS, the Task 2 test file still PASS (its `to_packet_v2` assertions use `[...]` lookups, not whole-dict equality), then the suite gate.
- [ ] **Step 5:** Commit `feat(coordinator): near-tie facts for patroller choice`.

### Task 5: Operator-load measurement (function only)

**Files:**
- Create: `src/sentinel/operator_load.py`
- Test: `tests/test_operator_load.py`

**Interfaces — Produces:** `operator_touches(audit_records: Iterable[dict], events: Iterable[dict]) -> dict` with keys `approvals`, `choices`, `escalations`, `total`, `approvals_only`, `broken_by`.
Input shapes (already produced by P2; do not change them): audit records are the dicts written by `ShadowLadder` (`{"record": "judgment", "decision_point": …, "executor": {"chooser": "OPERATOR"|"RULE"|"JEV", "label": …}, …}`, `{"record": "rule", …}`) and by `submit_choice` (`{"record": "operator_choice", "decision_point": …, "label": …}`); events are repository events (`{"event_type": …}`).

- [ ] **Step 1: Write the failing test:**

```python
from sentinel.operator_load import operator_touches

APPROVE_PLAN = {"event_type": "PLAN_APPROVED"}
CONFIRM_ASSIGN = {"event_type": "PATROLLER_ASSIGNMENT_CONFIRMED"}


def judgment(point, chooser):
    return {"record": "judgment", "decision_point": point, "executor": {"chooser": chooser, "label": "x"}}


def test_clean_run_is_approvals_only_with_two_touches():
    audit = [{"record": "rule", "decision_point": "RANK", "label": "rank"}, judgment("EVIDENCE_GATE", "RULE")]
    assert operator_touches(audit, [APPROVE_PLAN, CONFIRM_ASSIGN, {"event_type": "TASK_CREATED"}]) == {
        "approvals": 2, "choices": 0, "escalations": 0, "total": 2, "approvals_only": True, "broken_by": []}


def test_an_escalation_and_the_operator_choice_that_answers_it_break_the_target():
    audit = [judgment("SELECT_PLAN", "OPERATOR"),
             {"record": "operator_choice", "decision_point": "SELECT_PLAN", "label": "PLAN_ROUTE_ONLY_2_ZONE"}]
    result = operator_touches(audit, [APPROVE_PLAN, CONFIRM_ASSIGN])
    assert (result["approvals"], result["choices"], result["escalations"], result["total"]) == (2, 1, 1, 3)
    assert result["approvals_only"] is False and result["broken_by"] == ["SELECT_PLAN"]


def test_rejections_count_as_operator_touches_and_escalate_choice_is_not_a_choice():
    audit = [judgment("EVIDENCE_GATE", "OPERATOR"),
             {"record": "operator_choice", "decision_point": "EVIDENCE_GATE", "label": "escalate"}]
    result = operator_touches(audit, [{"event_type": "PLAN_REJECTED"}, APPROVE_PLAN,
                                      {"event_type": "OPERATOR_ASSIGNMENT_REJECTED"}])
    assert result["approvals"] == 3 and result["choices"] == 0 and result["escalations"] == 1
    assert result["broken_by"] == ["EVIDENCE_GATE"]
```

- [ ] **Step 2:** Run → FAIL. **Step 3: Implement:**

```python
"""Operator-load measurement against the 'approvals only' target (P3a)."""
from __future__ import annotations

from typing import Iterable

_APPROVAL_EVENTS = frozenset({"PLAN_APPROVED", "PLAN_REJECTED",
                              "PATROLLER_ASSIGNMENT_CONFIRMED", "OPERATOR_ASSIGNMENT_REJECTED"})


def operator_touches(audit_records: Iterable[dict], events: Iterable[dict]) -> dict:
    """approvals: approval-flow decisions. choices: judgment-point picks other than 'escalate'.
    escalations: judgment turns the executor handed to the operator. total = approvals + choices.
    approvals_only: no escalation and no choice. broken_by: decision points that escalated, in order, deduplicated."""
    approvals = sum(1 for e in events if e.get("event_type") in _APPROVAL_EVENTS)
    choices, escalations, broken_by = 0, 0, []
    for record in audit_records:
        if record.get("record") == "operator_choice" and record.get("label") != "escalate":
            choices += 1
        if record.get("record") == "judgment" and (record.get("executor") or {}).get("chooser") == "OPERATOR":
            escalations += 1
            if record["decision_point"] not in broken_by:
                broken_by.append(record["decision_point"])
    return {"approvals": approvals, "choices": choices, "escalations": escalations,
            "total": approvals + choices, "approvals_only": escalations == 0 and choices == 0,
            "broken_by": broken_by}
```
- [ ] **Step 4:** PASS + suite gate. **Step 5:** Commit `feat(eval): operator-touch counting against the approvals-only target`.

---

## WAVE 2 — requires P2b

### Task 6 (parent): P2b gate, rebase, interface pin

- [ ] **Step 1 — gate.** `git -C C:/coding/sentinel/.worktrees/p2-shadow-advisors grep -n "rule_choice" -- src/sentinel/coordinator/legal_tools.py`. **No output ⇒ P2b has not landed: stop Wave 2** and tell the operator. (Do not implement `rule_choice` here: the P2 session owns it, and two implementations in `legal_tools.py`/`policies.py` would collide.)
- [ ] **Step 2 — rebase.** `git rebase feat/p2-shadow-advisors` in the P3 worktree. Expected conflicts: none, or `decision_state.py` only. Resolve by keeping both sides' additions; re-run the suite gate.
- [ ] **Step 3 — interface pin.** Read P2b's `legal_tools.py`, `policies.py`, `shadow.py`, `operator_choice.py`, `turn_generator.py` and write `docs/superpowers/plans/p3a-interface-pin.md` answering, with file:line:

| # | This plan assumes | Actual |
|---|---|---|
| 1 | `LegalSet(decision_point, candidates, rule_choice: str \| None = None)`; `rule_choice` is a **label** | |
| 2 | `EVIDENCE_TASK_SCORE = 0.75` is importable from `sentinel.coordinator.legal_tools` | |
| 3 | At `EVIDENCE_GATE`: `rule_choice = "create_task"` iff inside the approved plan and score ≥ 0.75, else `"escalate"` | |
| 4 | At single-candidate points `rule_choice` is that candidate's label (or `None` — record which) | |
| 5 | `RulePolicy` acts on `rule_choice` when it is not `None`; otherwise single candidate acts, else escalate | |
| 6 | How `ShadowLadder.decide`, `pending_decision`, `submit_choice` decide "this turn needs a human": by `decision_point in JUDGMENT_POINTS`, or by `rule_choice is None`? | |
| 7 | What `ADVISORY_POINTS` contains and who reads it | |
| 8 | The helper names in Ground truth §12 still exist in `turn_generator.py` | |

- [ ] **Step 4 — reconcile.** Where "Actual" differs from the assumption, edit Tasks 7–10 of this plan (names only) **before** dispatching them, and commit the pin + the edited plan: `docs(p3): P2b interface pin`.
- [ ] **Step 5:** Suite gate; record the new baseline in `HANDOFF-P3.md`.

### Task 7: Work queue in `legal_set` — oldest-first triage, duplicates become the judgment point

**Files:**
- Modify: `src/sentinel/coordinator/legal_tools.py`, `src/sentinel/coordinator/policies.py` (`INSTRUCTIONS["EVIDENCE_GATE"]` text only)
- Test: `tests/coordinator/test_legal_tools_queue.py`

**Interfaces:**
- Consumes: `DecisionState.detections`, `.tasks`, `.detection_facts()` (Task 2); P2b's `LegalSet.rule_choice`, `EVIDENCE_TASK_SCORE` (pin rows 1–3).
- Produces: `DETECTION_TRIAGE = EVIDENCE_GATE` (alias, same string); `needs_judgment(legal: LegalSet) -> bool` = `legal.rule_choice is None and len(legal.candidates) > 1` (skip if the pin shows P2b already has an equivalent — then use theirs everywhere this plan says `needs_judgment`); queue order below.

**Queue order (deterministic):**
1. `TERMINAL`, `WAIT_APPROVAL`, `SELECT_PLAN` exactly as today.
2. If `state.detections`: triage **`detections[0]`** (oldest).
   - not `DETECTED`, no score, or score < `evidence_threshold` → `EVIDENCE_VETO`, single `escalate` (unchanged).
   - else `EVIDENCE_GATE` with `(create_task, escalate)` built from **that detection's ids**, and `rule_choice`:
     - `"escalate"` if `inside_plan` is not `True` or score < `EVIDENCE_TASK_SCORE`;
     - `None` if `duplicates_open_task` or `repeat_of_handled` (**the judgment point**: same rock, or a second hazard?);
     - `"create_task"` otherwise.
3. Else the oldest task with status `PROPOSED` and no ranking → `RANK` for it.
4. Else the oldest task with status `PROPOSED` (ranked) → `ASSIGN` for it (rank 1, as today; Task 8 extends it).
5. Else if there is no task → `WAIT_OBSERVATION`; else `DONE` (unchanged wait).

Known limit, do **not** work around it: a vetoed or escalated detection stays at the head forever (blocker A7, proposal P-4). No `RUN_WRAP_UP` point: it is unreachable while the run completes on the first confirmed assignment (A1).

- [ ] **Step 1: Write the failing tests** (names and assertions are binding; build states with `P3Run`; `catalogue = load_catalogue_plans()`; `legal = legal_set(run.state(), catalogue)`):

```python
def test_clean_detection_is_settled_by_rule(run):            # ingest LEFT_MID 0.90
    assert legal.decision_point == EVIDENCE_GATE and legal.rule_choice == "create_task"
    assert legal.by_label("create_task").arguments["perception_result_id"] == a["result_id"]

def test_queue_triages_the_oldest_detection_first(run):       # ingest a(LEFT_MID), c(RIGHT_MID)
    # candidate ids are a's; after run.create_task(a) they are c's

def test_duplicate_of_an_open_task_is_a_judgment_point(run):  # a, b at LEFT_MID 0.90/0.92; create_task(a)
    assert legal.decision_point == EVIDENCE_GATE and legal.rule_choice is None
    assert legal.labels == ("create_task", "escalate") and needs_judgment(legal)

def test_out_of_plan_detection_escalates_by_rule(run):        # LEFT_FRONT 0.90  (Task 9 changes this one test)
    assert legal.rule_choice == "escalate"

def test_inside_plan_below_task_score_escalates_by_rule(run): # LEFT_MID 0.62
def test_below_static_threshold_is_still_the_veto(run):       # LEFT_MID 0.35 -> EVIDENCE_VETO, one candidate

def test_rank_then_assign_walk_tasks_oldest_first(run):
    # a(LEFT_MID), c(RIGHT_MID); create both -> RANK with first task_id; rank(first) -> RANK with second;
    # rank(second) -> ASSIGN with first task_id

def test_two_clean_detections_run_unattended_until_the_first_assignment(run):
    # Drive AgentLoop(CoordinatorBoundary(repo, gateway), bind_context(RulePolicy(), repo, catalogue),
    #                 repository=repo, catalogue=catalogue, max_turns=8).run(run_id, AGENT_RUN_ID, "…")
    # Expect: 2 TASK_CREATED events, both tasks ranked, run_status == "WAITING_FOR_APPROVAL",
    # pending approval is the FIRST task, and the loop never returned NEEDS_REVIEW.

def test_characterization_second_task_is_stranded_by_run_completion(run):
    """Blocker A1 (proposal item P-1). When P-1 is granted this test flips; do not work around it."""
    # continue from the state above: OPERATOR_DECIDE CONFIRM on the first task ->
    # run_status == "COMPLETED", legal_set(...).decision_point == TERMINAL, second task still "PROPOSED".

def test_every_queue_candidate_is_accepted_by_the_gateway(run):
    # for the create_task, rank and assign_rank_1 candidates reached above, execute each through AgentLoop
    # with a one-shot policy (copy `_agent_result_for` / `_run_candidate` from tests/coordinator/test_legal_tools.py)
    # and assert the returned tool result status is "ACCEPTED".
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** by replacing, in `legal_set`, everything after the `SELECT_PLAN` branch with the queue above. Build the create candidate from the `DetectionView`:

```python
def _triage(state: DecisionState, d, evidence_threshold: float) -> LegalSet:
    if d.status != "DETECTED" or d.raw_score is None or d.raw_score < evidence_threshold:
        return LegalSet(EVIDENCE_VETO, (_escalate("evidence not DETECTED or below threshold"),))
    facts = state.detection_facts(d)
    create = Candidate("task.create", {"perception_result_id": d.result_id, "section_id": d.station_id,
                                        "evidence_artifact_id": d.artifact_id},
                       "create_task", "create a patrol task for this detection")
    if facts["inside_plan"] is not True or d.raw_score < EVIDENCE_TASK_SCORE:
        rule = ESCALATE_LABEL
    elif facts["duplicates_open_task"] or facts["repeat_of_handled"]:
        rule = None
    else:
        rule = "create_task"
    return LegalSet(EVIDENCE_GATE, (create, _escalate("detection needs operator review before tasking")), rule_choice=rule)
```

  Keep P2b's candidate descriptions and labels byte-identical where they already exist (the v1 golden test compares them). Replace the `EVIDENCE_GATE` instruction text in `policies.py` with: *"The detection passed the score rules and lies inside the approved plan, but a task already exists at the same station. Choose 'create_task' if this is a second, distinct hazard. Choose 'escalate' if it is the same hazard seen again or you cannot tell."*
- [ ] **Step 4:** Test file PASS + suite gate (v1 golden test untouched and green).
- [ ] **Step 5:** Commit `feat(coordinator): oldest-first detection queue; duplicates become the judgment point`.

### Task 8: `ASSIGN` near-tie and SELECT_PLAN without already-proposed entries

**Files:**
- Modify: `src/sentinel/coordinator/legal_tools.py`, `policies.py` (add `INSTRUCTIONS["ASSIGN"]`), and — only if pin row 6 says membership in `JUDGMENT_POINTS` is what triggers the human path — `shadow.py` and `operator_choice.py` to use `needs_judgment(legal)` instead
- Test: `tests/coordinator/test_assignment_choice.py`

**Interfaces:**
- Consumes: `is_near_tie`, `gap_band`, `rank_gap` (Task 4); `TaskView.ranking_scores`; `DecisionState.proposed_catalogue_plan_ids` (Task 3); `needs_judgment` (Task 7).
- Produces: at `ASSIGN`, a clear gap or a single patroller → exactly today's single candidate `assign_rank_1`; a near-tie → `(assign_rank_1, escalate)` with `rule_choice=None`. Rank-2/3 candidates are **not** offered (the gateway rejects them, A2). At `SELECT_PLAN`, catalogue entries in `proposed_catalogue_plan_ids` are omitted.

- [ ] **Step 1: Failing tests** (binding names):
  - `test_single_patroller_is_unchanged_from_p2` — gateway `rank()`; one candidate `assign_rank_1`; `needs_judgment` False.
  - `test_clear_gap_is_settled_by_rule` — `SimulatedPatroller(...).rank_for_task(task_id, {"PATROLLER_1": 0.90, "PATROLLER_2": 0.60})`; one candidate.
  - `test_near_tie_is_a_judgment_point` — scores 0.81 / 0.79; labels `("assign_rank_1", "escalate")`; `rule_choice is None`; the `assign_rank_1` description contains `"very_close"`.
  - `test_near_tie_candidate_is_accepted_by_the_gateway` — execute `assign_rank_1` through `AgentLoop` → `ACCEPTED`, run `WAITING_FOR_APPROVAL`.
  - `test_rule_policy_escalates_a_near_tie_and_operator_can_choose` — `RulePolicy` → `finish(NEEDS_REVIEW)`; `pending_decision(...)` returns the two options; `submit_choice(..., "assign_rank_1", ...)` succeeds.
  - `test_pending_decision_is_none_on_a_clear_gap`.
  - `test_characterization_rank_2_is_rejected_by_the_gateway` — `ASSIGNMENT_PROPOSE` with `PATROLLER_2` raises `CoordinatorError` code `INVALID_STATE_TRANSITION` (A2 / P-2).
  - `test_select_plan_omits_entries_already_proposed_in_this_run` — propose `PLAN_ROUTE_ONLY_2_ZONE`, `REJECT`; `SELECT_PLAN` labels == `("PLAN_MARKER_VERIFIED_6", "escalate")`.
  - Extend the non-interference proof: copy the pattern of `tests/coordinator/test_shadow.py`'s "advisor cannot change execution" test for a near-tie `ASSIGN` turn (an advisor that picks `assign_rank_1` while the executor escalates ⇒ the result is the executor's).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. `INSTRUCTIONS["ASSIGN"]`: *"Two or more patrollers are ranked within a near-tie. Choose 'assign_rank_1' if the top-ranked patroller is an acceptable assignment. Choose 'escalate' if the operator should pick."* **Step 4:** PASS + suite gate. **Step 5:** Commit `feat(coordinator): near-tie rankings become a judgment point; no re-proposal of a proposed plan`.

### Task 9: `REPLAN` on a qualifying out-of-plan detection

**Files:**
- Create: `src/sentinel/coordinator/replan.py`
- Modify: `legal_tools.py` (`CataloguePlan` gains `station_ids: tuple[str, ...] = ()`, filled by `load_catalogue_plans`; one branch in `_triage`), `policies.py` (`INSTRUCTIONS["REPLAN"]`)
- Test: `tests/coordinator/test_replan.py`

**Design (operator decision, binding):** a goal change is a **new plan proposal** through the existing approval flow; the old plan stays the plan until the new one is confirmed (Task 3 makes the read model say so). Only the detection trigger is built; the operator-message trigger needs P-5, and `keep_current_plan` needs P-4 (a no-op cannot be persisted, so the same turn would repeat).

- `replan.covering_plans(catalogue, station_id, current_station_ids, already_proposed) -> tuple[CataloguePlan, ...]`: plans whose `station_ids` ⊇ `current_station_ids ∪ {station_id}`, minus `already_proposed`, fewest stations first then by id.
- In `_triage`: if the detection is `DETECTED`, score ≥ `EVIDENCE_TASK_SCORE`, `inside_plan is False`, and `covering_plans(...)` is non-empty → `LegalSet("REPLAN", (one inspection.propose_plan candidate per covering plan) + (escalate,), rule_choice=None)`. Otherwise Task 7's behaviour.
- Add `REPLAN` to `JUDGMENT_POINTS` (and wherever pin row 6/7 requires).

- [ ] **Step 1: Failing tests:** `covering_plans` unit cases (v1 catalogue: `LEFT_FRONT` over the route plan → only `PLAN_MARKER_VERIFIED_6`; after that entry was proposed → empty; draft catalogue → `PLAN_VERIFIED_FULL_SITE` is the only superset of both middles + `LEFT_ALLEY_FRONT`); out-of-plan 0.90 → `REPLAN`, labels `("PLAN_MARKER_VERIFIED_6", "escalate")`; choosing the plan through `AgentLoop` → `WAITING_FOR_APPROVAL` **and `run.state().plan_station_ids` is still the two middle stations**; `CONFIRM` → the same detection is now `EVIDENCE_GATE` with `rule_choice == "create_task"`; `REJECT` → the detection escalates by rule (no covering plan left) and the old plan is still approved; out-of-plan 0.62 → never `REPLAN`. **Update** Task 7's `test_out_of_plan_detection_escalates_by_rule` to use score 0.62 (this is the one sanctioned edit of a P3 test).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS + suite gate. **Step 5:** Commit `feat(coordinator): replanning as an approval-gated proposal on an out-of-plan detection`.

### Task 10: Decision-turn fixture v2

**Files:**
- Create: `tests/coordinator/turn_generator_v2.py`, `fixtures/decisions_v2/turns.v2.jsonl`, `fixtures/decisions_v2/turns.v2.schema.json`, `fixtures/decisions_v2/README.md`
- Test: `tests/coordinator/test_turn_fixture_v2.py`

Same method as v1 (`tests/coordinator/turn_generator.py`, `test_turn_fixture.py`: copy their structure): drive the real gateway, capture `state.to_packet_v2()` + candidates, draft-label, commit the JSONL, golden-test the regeneration. Record = v1's fields with `packet` validated as `decision_packet.v2`, plus `rule_choice`.

**Honest sizing (rev 1's 80/80/60/60 targets are not reachable):** packets carry bands, not raw values, so distinct states are few (blocker A12). **Every `(decision_point, packet, candidates)` triple must be unique** — never pad with identical turns.

| Point | How states are built | Expected distinct turns |
|---|---|---|
| `SELECT_PLAN` vs the **draft** catalogue (`legal_set(state, load_catalogue_plans(DRAFT_CATALOGUE_PATH))`, never proposed through the gateway) | `RUN_CREATE` with 56 agent-written request texts: for each of the 14 plans, 3 phrasings that name its coverage (42); 5 ambiguous; 5 out-of-catalogue; 4 contradictory. Plus **24 empty slots** `label_source: "operator_pending"`, `operator_request: null`, excluded from every count and from scoring | 56 + 24 pending |
| `EVIDENCE_GATE` duplicate (`rule_choice is None`) | new-detection score {0.78, 0.90, 0.99} × station {L-mid, R-mid} × earlier task {unranked, ranked} × another pending detection {no, yes} | ≤ 24; keep the distinct ones |
| `ASSIGN` near-tie | gaps {0.00, 0.02, 0.06} × patrollers {2, 3} via `SimulatedPatroller.rank_for_task` | ≤ 6 |
| `REPLAN` | outside station {4 non-middle stations} × score {0.78, 0.90, 0.99} | ≤ 12; keep the distinct ones |
| RULE turns (`label_source: "rule"`, label = `rule_choice`) | one per rule outcome: clean create, out-of-plan low, in-plan < 0.75, veto, rank, clear-gap assign, wait ×2 | 8 |

Draft labels: `SELECT_PLAN` → the named plan / `escalate`; duplicate → `escalate` (rationale: "no fact in the packet distinguishes a second hazard from a repeat sighting"); near-tie → `assign_rank_1`; `REPLAN` → the covering plan. Every hand label has a rationale.

- [ ] **Step 1:** Failing tests: schema validity; label ∈ candidates; rationale on hand labels; uniqueness of the triple; per-point minimums (`SELECT_PLAN` ≥ 56 scored, duplicates ≥ 8, near-tie ≥ 4, `REPLAN` ≥ 4); no UUID in any packet; golden regeneration; pending slots flagged and excluded.
- [ ] **Step 2:** FAIL. **Step 3:** Implement, generate, write the README (counts actually achieved, the A12 limit, the 24 pending slots). **Step 4:** PASS + suite gate. Commit `feat(fixtures): decision turns v2 (draft labels; distinct reachable states only)`.
- [ ] **Step 5 — gate G-labels-v2 (parent):** the operator approves/edits labels and writes the 24 request texts. Tell the operator plainly: with this many distinct turns, P3b's "n ≥ 60 HIGH picks per point" is reachable for `SELECT_PLAN` only; the other points need repeats across seeds (criterion 1 allows that) or richer facts (P-8).

### Task 11: Operator-load report

**Files:**
- Create: `scripts/operator_load_report.py`, `artifacts/p3/operator-load.md`
- Test: `tests/test_operator_load_report.py`

Scripted rehearsals, no live advisor: `ShadowLadder(RulePolicy(), advisors=[], sink=MemoryAuditSink())` under `AgentLoop`, a scripted operator who confirms every approval and answers each escalation with a fixed label through `submit_choice`. Scenarios: clean single detection; clean two detections (stops at A1 — report the stranded task as a finding, not a failure); duplicate; near-tie; replan. For each: `operator_touches(...)`, whether it meets "approvals only", and `broken_by`. The test asserts the clean single-detection scenario yields `{"approvals": 2, "choices": 0, "escalations": 0}` and that the duplicate scenario is `broken_by == ["EVIDENCE_GATE"]`.

- [ ] Steps: failing test → implement → generate `artifacts/p3/operator-load.md` → suite gate → commit `feat(eval): operator-load report against the approvals-only target`.

### Final (parent)

- [ ] Fresh whole-branch review: P1/P2 suites green; nothing under `src/` references `catalogue_draft`; `git diff feat/p2-shadow-advisors --stat` shows **no** change to any file in the "Never edit" list; every new judgment turn is covered by a non-interference test; the three characterization tests (A1, A2, A9) exist and name their proposal item.
- [ ] Update `HANDOFF-P3.md`: what was built, the gates still open (G-amend items, G-catalogue, G-labels-v2), the measured operator load.
- [ ] **G-merge.**

## What comes next

- **P3a-2** (write after G-amend): one short task per granted item; each starts by flipping its characterization test. P-1 → a real second assignment and `RUN_WRAP_UP` (`finish` status `DONE`); P-2 → rank-2/3 candidates; P-3 → the draft becomes the catalogue; P-4 → `dismiss_duplicate`, `keep_current_plan`, no head-of-line blocking; P-5 → message-triggered `REPLAN`; P-7 → `REVISIT`.
- **P3b**: architecture E on this surface; tasks and promotion criteria from `2026-09-19-p3-hybrid-E-followup.md`; advisors switch to `to_packet_v2()`.

## Self-review

- **Operator's four decision types:** multi-detection (T2, T7), patroller choice (T4, T8 — judgment exists, the alternative patrollers wait for P-2), replanning (T3, T9 — detection trigger; message trigger waits for P-5), closer look (proposal P-7 + G-closer-look; nothing buildable today). Rich catalogue: T1 content, P-3 adoption. Approvals-only: T5, T11.
- **Differences from rev 1, all forced by Ground truth:** no `DETECTION_TRIAGE` rename; `to_packet()` frozen, `to_packet_v2()` added; no `RUN_WRAP_UP`, no `finish(COMPLETED)`; no rank-2/3 candidates; no `keep_current_plan`; fixture sizes are what is distinct and reachable; five per-task proposals became one document; the read-model bug A13 became a task.
- **Type consistency:** `DetectionView`, `TaskView`, `current_task`, `detection_facts`, `to_packet_v2`, `proposed_catalogue_plan_ids`, `assignment_facts`, `needs_judgment`, `covering_plans`, `P3Run`, `DRAFT_CATALOGUE_PATH` are each defined in exactly one task and used under the same name afterwards.
