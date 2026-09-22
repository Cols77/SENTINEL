# Jev Room Agent Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute every plan in this roadmap. Each task gets a fresh implementer, specification review, quality review, and fix loop before the next dependent task.

**Goal:** Deliver a Jev-directed room agent incrementally, ending with grounded room queries, adaptive inspection/counting, conservative return, certified-zone landing, and bounded designated-person following.

**Architecture:** A new `sentinel.room` session kernel owns goals, evidence, candidates and replay. A connected cube executor and deterministic supervisor own flight, pose recovery, energy and terminal behavior. Capability modules add counting, closer inspection, certified landing and following only after their own evidence gates pass.

**Tech Stack:** Python 3.11+, dataclasses, SQLite, JSON Schema, pytest, existing Jev judge, survey perception/store, cube runner, AprilTag pose stack, OpenRouter Gemini detector.

**Approved design:** [Jev Agentic Room Session and Bounded Localization Recovery](../specs/2026-09-22-jev-agentic-room-design.md)

---

## Current base and branch policy

- Feature branch: `feat/jev-agentic-room-loop`.
- Initial reviewed upstream: `integration/survey-demo` at `4aa9e19`.
- Initial feature head after rebase: `2b28df1`.
- Rebase only from a clean worktree. Record the resolved upstream revision and rerun the affected serial suite after each rebase.
- Do not push, merge into integration, or execute hardware automatically.

## Required execution order

1. [Plan 01 — room kernel and grounded reads](2026-09-22-jev-room-agent-01-kernel.md)
2. [Plan 02 — adaptive flight safety](2026-09-22-jev-room-agent-02-flight-safety.md)
3. [Plan 03 — count, closer inspection and return](2026-09-22-jev-room-agent-03-count-closer-return.md)
4. [Plan 06 — calibration, replay and first live rollout](2026-09-22-jev-room-agent-06-live-rollout.md), through count-and-return promotion
5. [Plan 04 — closest certified-zone landing](2026-09-22-jev-room-agent-04-certified-landing.md)
6. Resume Plan 06 through certified-zone landing promotion
7. [Plan 05 — person-phone relation and bounded following](2026-09-22-jev-room-agent-05-bounded-following.md)
8. Finish Plan 06 through yaw tracking and one-step follow promotion

Every plan is mandatory. A blocked hardware gate records blocked coverage and leaves the implementation capability-disabled; it does not delete or silently pass the plan.

## Capability promotion matrix

| Capability | First implemented | Enabled in real mode only after |
|---|---|---|
| Room/drone reads | Plan 01 | deterministic query/replay tests |
| Adaptive movement | Plan 02 | calibrated pose and energy profiles |
| Count and return | Plan 03 | Plan 06 count-and-return rehearsal |
| Closer inspection | Plan 03 | swept-clearance and live static-object rehearsal |
| Land near phone | Plan 04 | certified pad and final-clearance rehearsal |
| Person-phone relation | Plan 05 | held-out relation examples and consent |
| Yaw tracking | Plan 05 | measured tracker freshness/latency |
| Translational following | Plan 05 | cleared corridor and one-step hardware acceptance |

Disabled capabilities still parse into a goal with an explicit unavailable reason. They never appear as executable candidates.

## Approved-design traceability

| Approved design concern | Implemented and proved by |
|---|---|
| Jev fixed-choice judgment, explicit escalation, typed tool feedback | Plan 01 Tasks 1, 3 and 5 |
| Deterministic grounded room/drone answer generation with no flight or perception-provider call | Plan 01 Tasks 2, 4 and 5 |
| Semantic cube navigation over all 27 regions | Plan 03 Task 1 |
| Correlated frame/detection transport and stable evidence | Plan 01 Tasks 2 and 4; Plan 03 Tasks 2 and 3 |
| Connected flight, localization coasting/reanchor, smart reserve and return | Plan 02 Tasks 1-5 |
| Count, closer inspection, incomplete-result honesty and home return | Plan 03 Tasks 2-5 |
| Closest certified safe landing near a phone | Plan 04 Tasks 1-3; Plan 06 landing promotion |
| Designated person holding a phone, yaw observation and one-step follow | Plan 05 Tasks 1-5; Plan 06 follow promotion |
| Consent, reproducibility, simulation, calibration and live evidence | Plan 06 Tasks 1-7 |

Every row has deterministic tests before implementation, a capability flag that defaults disabled for real execution, and an evidence gate before promotion.

## Rebase checkpoints

Run after Plan 01, after Plan 02's executor extraction, before live CLI work, and before final integration review:

```powershell
rtk git status --short --branch
rtk git log --left-right --oneline HEAD...integration/survey-demo
rtk git rebase integration/survey-demo
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/cube tests/survey/test_providers.py tests/survey/test_live_detect.py tests/survey/test_cube_bridge.py tests/survey/test_dry_run_chain.py -q --tb=short -o addopts='' -p no:cacheprovider
```

Expected: clean rebase and all selected tests pass. If integration changes contracts, update the affected plan and review the update before continuing.

## Review/fix protocol

For each task, use a fresh implementation agent. The following review/fix protocol is part of every task even when the individual task does not repeat these five lines; plan boundaries add one aggregate review over the complete plan:

1. After the task implementation commit, a fresh specification reviewer maps that task and the approved design to code/tests.
2. A separate fresh quality reviewer looks for stale-state actions, unsafe physical transitions, inaccurate claims, accidental live calls and weak tests.
3. Fresh fix agents address confirmed important findings, partitioned by independent file ownership.
4. Repeat review while important findings remain.
5. Rerun focused tests serially after fixes and commit exact fix paths; run the affected suite plus an aggregate specification/quality review at the plan boundary.

At every plan completion, remind the user of the next plan in this roadmap.

## Retained earlier plans

- `2026-09-21-proposer-explicit-escalation.md`: completed; retain regression coverage.
- `2026-09-21-bounded-inspection-decision-session.md`: outstanding inspection-domain work; this room session does not mark it complete.
- `2026-09-21-bounded-multiturn-demo-qa.md`: amend during Plan 03 so room/drone questions use deterministic room reads.
- `2026-09-21-integrated-survey-evaluation.md`: deferred backlog; retain it explicitly.
- `2026-09-22-trajectory-first-evaluation-design.md`: defer broad model comparison while retaining replay/correctness gates.

The final report includes the disposition of every retained plan and reminds the user of the next outstanding item.

## Final evidence

The final branch report lists every plan/task/commit, upstream revisions, focused and full-suite results, replay artifacts, calibration profile hashes, live trials actually performed, capabilities enabled, and blocked coverage. Historical evaluation reports remain unchanged and separately labelled.
