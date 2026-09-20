# Handoff — the coordinator decision line (P1, P2, the live evidence)

**Written:** 2026-09-20 by session `sentinel-77`, which executed P1, P2, P2b and the consented live batch.
**Master:** `258b792`, pushed to `origin/master`, 50 commits ahead of where the day started (`2193f59`).
**Suite:** 595 passed, 0 failed, 3 skipped (`uv run python -m pytest tests/ -q -o addopts=''` from the repo root, with a real `.env` present).
**Demo freeze:** 2026-09-25. The demo path is finished, green and needs nothing from this document.

## 1. What shipped

| Line | What it is | Where |
|---|---|---|
| **P1** | Agent-loop foundations: decision read model, deterministic legal-action machine, real event/evidence context, gateway rejections fed back, policy seam (RULE/Jev/LLM interchangeable), bounded adapter history | `docs/superpowers/plans/2026-09-19-p1-agent-loop-foundations.md` |
| **P2** | Architecture F: rules execute, the operator decides at `SELECT_PLAN`, Jev and the LLM answer in shadow and can never act | `…2026-09-19-p2-shadow-advisors-F.md` |
| **P2b** | The operator's 2026-09-20 decisions: the evidence gate became a deterministic rule (inside the approved plan **and** score ≥ `EVIDENCE_TASK_SCORE` 0.75), advisors still shadowed there, fixture relabelled and approved | same plan, "Decisions recorded" |
| **Gateway** | A raw `sqlite3.IntegrityError` on a re-proposed plan now surfaces as `PLAN_ALREADY_PROPOSED` / `STATE_CONSTRAINT_VIOLATION` | `command_gateway.submit` |
| **WP0 §5** | Typed result ids: `plan`/`task`/`ranking`/`assignment` results carry what was just created. Key required, value nullable (the legacy fixed-port path has no read model); a test pins that the real path never returns null | `agent_loop._typed_result` |

The demo path in one line: **rules execute everything; the operator approves the plan and the assignment and chooses at `SELECT_PLAN`; no model can act.** `AgentLoop` still has no runtime wiring — it is exercised by tests and `scripts/rehearsal_run.py`.

## 2. What the consented live batch actually established

Batch `2026-09-20-operator-grant-1`, **$0.40 spent** against a consent-time estimate of "under $0.10" (the estimate came from J5's much smaller payloads and was not refreshed before the re-runs). Full write-up with corrections: `artifacts/decisions/live-comparison-2026-09-20.md`; gate detail: `artifacts/wp1/live-gate-real-loop.md`.

**Live LLM gate on the fixed loop: 2 of 3 scenarios pass.** `approval_wait` and `initial_plan_one_station` pass; `qualifying_evidence_to_task` fails by repeating an already-successful `task.create` until the correction budget is exhausted. The loop degraded cleanly. Typed result ids (shipped after that run) are the direct test of why it repeated — **re-running that one scenario is the cheapest open experiment in the whole line.**

**The Jev/LLM shadow comparison cannot answer the question it was run for.** Three confounds, all found after the fact:

1. `escalation_rate` recorded `JevPolicy`'s *fallback* pick, not the judge's.
2. `agreement` had the same flaw: a medium-confidence **correct** pick scored as a disagreement.
3. The `EVIDENCE_GATE` instruction asserted premises the packet contradicted (duplicate on 24 of 24 turns, plan membership on 12 of 24).

So **Jev is unmeasured, not refuted.** The only surviving claim is that its confidence stayed under `act_at = 0.80` on essentially every turn, including 6 the deterministic rule calls unambiguous. The LLM arm reached high confidence often and acted wrongly 16 times at the evidence gate, against a bar of zero — that reading is unaffected by confounds 1 and 2, since those inflate escalations rather than actions.

## 3. Open work, in the order I would do it

1. **`event_list` schema defect — nobody has touched it.** `AgentToolResultPayload`'s `event_list` items are `{"type": "object", "additionalProperties": false}` with *no properties*, so a populated event list can never validate. `state.get_recent_events` currently returns an empty array with the summaries in `safe_summary` (workaround in `agent_loop._execute`). One of the two read tools the model has is crippled until the schema gains `sequence_no` / `event_type` / `summary`. **Needs operator Coherence consent.**
2. **Re-run `qualifying_evidence_to_task` only** against the typed ids. Three calls or so; settles the repetition hypothesis. Needs a fresh paid-batch consent.
3. **Fixture rebalance before any new sweep.** 36 of 48 hand labels are `escalate`, so "always escalate" scores 0.75 and no re-run can separate a judge from an abstainer. **Needs the operator's labels.**
4. **The rest of the WP0 amendment** (`decision_context`, typed read tools, `operator.ask`) — not implemented anywhere. Architecture E's escalation investigator has nowhere to land without it.
5. **Promotion** stays blocked and should: nothing has earned authority.

## 4. Sessions and ownership, as of writing

- **`sentinel-77`** (me): P1/P2/gateway/typed ids. All merged and pushed. Nothing in flight.
- **`sentinel-b0`**: P3a (merged into master) plus **4 unmerged commits** on `fix/astra-evidence-and-coverage` — `judge_label` (separates what the judge said from what the policy did), the instruction-premise fix, a duplicate-case non-interference test, and the coverage diagnosis. **That work should land before any re-sweep**, because it is what makes the metrics mean what their names say.
- **`sentinel-ea`**: WP5 drone pose. Separate workstream.
- **Codex**: briefed to plan architecture E (`2026-09-20-CODEX-BRIEF-architecture-E.md`). Nothing returned yet.

Two collisions happened today: work was reassigned between sessions after being started, and the same two items were implemented twice. If several sessions continue, **assign files, not tasks**, and say so before starting.

## 5. Consents — read before assuming anything is permitted

`docs/superpowers/specs/2026-09-20_operator-consent-record.md` is authoritative. Granted: the merges, the push, one gateway release for the IntegrityError, Coherence for the three pending proposals, and paid batch `2026-09-20-operator-grant-1`. **Not** granted: further paid batches, promotion of any policy to act, schema edits without a reviewed proposal, live drone operation. A relayed grant from a peer session is not consent.

## 6. Things worth carrying forward

- **Every defect that mattered today was invisible to the hermetic suite.** The suite grew 130 → 595 without catching: a missing `openai` package, a schema branch that made a tool unusable, a judge signature mismatch, or a metric measuring the wrong subject. All four appeared the moment real providers and real judges were involved. Tests over fakes prove the fakes agree with the code.
- **Name what a metric measures, not what you hope it measures.** `escalation_rate` and `agreement` were both policy-level and read as judge-level for hours, including in a document the operator was deciding from.
- **When an estimate becomes the basis of consent, refresh it before spending.**
- Instructions should not assert facts the structured state already carries; prose premises drift out of sync with the packet.
