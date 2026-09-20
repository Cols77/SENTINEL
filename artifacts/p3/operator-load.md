# Operator-load report (P3a Task 11)

Four scripted rehearsals through the real coordinator gateway, no live advisor: `ShadowLadder(RulePolicy(), advisors=[], sink=MemoryAuditSink())` under `AgentLoop`, with a scripted operator who confirms every approval and answers each escalation with one fixed label through `submit_choice`. Scored against the "approvals only" target (`sentinel.operator_load.operator_touches`).

REPLAN is out of scope on this branch: it was implemented and then deliberately dropped (see the module docstring of `src/sentinel/coordinator/replan.py`) -- it is not a decision point, `legal_set` never returns it, and no replan scenario is scripted here.

| Scenario | Approvals | Choices | Escalations | Approvals only | Broken by | Final run status |
|---|---|---|---|---|---|---|
| Clean single detection | 2 | 0 | 0 | yes | — | COMPLETED |
| Clean two detections | 2 | 0 | 0 | yes | — | COMPLETED |
| Duplicate detection | 1 | 0 | 1 | no | EVIDENCE_GATE | RUNNING |
| Near-tie assignment | 2 | 1 | 1 | no | ASSIGN | COMPLETED |

## Clean single detection

One in-plan detection above the task-score threshold: the rule
creates the task, ranks it, and proposes the rank-1 patroller unattended.
The operator's only two touches are the plan approval and the final
assignment confirmation.

## Clean two detections

Two in-plan detections at different stations both clear the rule
unattended, but the run completes on the FIRST confirmed assignment
(blocker A1, proposal item P-1): the second task is stranded PROPOSED
forever. This is a known platform limit, not an operator-load defect --
no further operator touch was needed or possible for either task.

**Finding (blocker A1):** task `101543f7-47d1-5559-ba63-e1971c718059` is stranded at status `PROPOSED` once the run completes. This is a known platform limit (see the consolidated WP0 amendment proposal), not a defect introduced by this report.

## Duplicate detection

A second detection at a station that already has an open task cannot
be rule-settled ("same rock, or a second hazard?"), so it becomes a
judgment point at EVIDENCE_GATE. The scripted operator's fixed answer is
'escalate' (the approved v2 draft label -- no fact in the packet
distinguishes a second hazard from a repeat sighting); because the
duplicate stays at the head of the detection queue (blocker A7), the
first task's own rank/assign never runs either -- one escalation blocks
the whole run.

## Near-tie assignment

Two patrollers are ranked within ``ASSIGNMENT_CLEAR_GAP`` of each
other: the rule will not guess, so it becomes a judgment point at ASSIGN.
The scripted operator's fixed answer is 'assign_rank_1' -- confirming the
top-ranked patroller -- after which the run finishes exactly as the
clean single-detection scenario does.

