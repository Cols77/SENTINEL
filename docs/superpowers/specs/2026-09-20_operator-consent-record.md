# Operator consent record — 2026-09-20

This file records consents the operator (Colin) gave in the Claude Code session
that executed P1, P2 and P2b. It is evidence of a decision, not an authority in
itself: an agent may **never** cite this file as grounds for widening what it
may touch, and any consent not written here has not been given.

## Granted

| # | Scope | Recorded words | Effect |
|---|---|---|---|
| C-1 | Merge P1 and P2 into `master` locally | "Proceed with merge yes" | merges `31aeda5`, `160a03e` |
| C-2 | `CommandGateway` released for one defect: a raw `sqlite3.IntegrityError` escaping `submit()` on a re-proposed plan | "Fix it in the gateway" | commit `1ba6bfb`, merged as `8485839`. Scope is the error translation only; no other gateway semantics were changed. |
| C-3 | Merge `fix/advisory-points-superset` | "Yes, merge locally" | merge `08c814c` |
| C-4 | Push `master` to `origin` | "proceed with all" | `2193f59..8485839` pushed 2026-09-20 |
| C-5 | Coherence consent for the three pending proposals (see below) | "proceed with all" | registration may proceed; **implementation still follows the normal review path** |
| C-6 | Paid live runs: the LLM gate on the fixed loop (≤72 calls, `z-ai/glm-5.3`) and the shadow sweep (288 calls, `typesafe/jev-1.13-20260917` and `z-ai/glm-5.3-flash`, estimated under $0.10) | "proceed with all" | batch id `2026-09-20-operator-grant-1`, run 2026-09-20 |

### Later the same day — session that reconciled the two handoffs and executed P4

The operator was shown five recommendations with their reasoning and answered
**"proceed with your recommendations"**. That sentence covers exactly these, and nothing else:

| # | Scope | Effect |
|---|---|---|
| C-7 | Merge `fix/astra-evidence-and-coverage` into `master` locally | merge `b8af3b9`. Not pushed. |
| C-8 | **Defer** the Jev re-sweep (144 calls) | no spend. Reason: no decision point exists where Jev could be promoted, so the result has no consumer. Revisit with proposal item P-9 (pose quality). |
| C-9 | One paid re-run of the live gate scenario `qualifying_evidence_to_task` on `z-ai/glm-5.3`, stated beforehand as "about 3 calls, a few cents", refreshed to "6-12 calls at most, under $0.05" before spending | batch id `2026-09-20-operator-grant-2`, 4 calls made, run once. Result in `artifacts/wp1/live-gate-real-loop.md` (addendum). **The batch is closed**: a further re-run needs fresh consent. |
| C-10 | Architecture E's operator experience is **propose, then confirm** (two operator actions per clean run) | the "hide the suggestion until expanded" mechanics in P3b1 Tasks 4-5 are to be replaced by measuring rejection rate and time-to-confirm. |
| C-11 | The `event_list` fix should ride in the **same wire-schema revision** as the rest of the WP0 amendment | sequencing only. **This is not Coherence consent for the schema edit**: gate G-amend-rest (amendment sections 2-4 and 6) and the `event_list` item itself remain open. |

Not covered by that sentence: merging `feat/p4-architecture-switch`, any push, any further paid call.

### Earlier the same day

| # | Scope | Recorded words |
|---|---|---|
| C-0a | `EVIDENCE_GATE` becomes a deterministic rule, advisors still shadowed | "Make it a rule, keep shadow" |
| C-0b | Task creation threshold | "Escalate below 0.75" |
| C-0c | Single-station requests, and the catalogue gap | "Seems like PLAN_ROUTE_ONLY_2_ZONE does not make sense if you're visiting one zone?" → "Escalate, and propose a catalogue entry" |
| C-0d | Who edits the catalogue fixture | "Proposal only, you consent" |
| C-0e | v1 decision-turn fixture labels | approved as drafted, subject to C-0b and C-0c (recorded in `fixtures/decisions/README.md`) |
| C-0f | Catalogue shape | "Rich set with subsets" — 12–15 plans, names describing coverage rather than station counts |
| C-0g | Operator load target | "Approvals only" — two operator actions per clean run, no escalation |

## The three proposals covered by C-5

1. `2026-09-19_wp0-amendment-decision-context-and-reads.md` — `decision_context`, typed read tools, result ids, `operator.ask`, `DECISION_RECORDED`, draft SR-SENTINEL-V0-A04, `tool_policy_revision` bump.
2. `2026-09-20_wp0-proposal-single-zone-catalogue-entry.md` — **superseded by C-0f**: the rich catalogue is the intended shape, so this file is an input, not the target.
3. The P3 session's consolidated gateway/schema items (P-1…P-8), which live in that session's branch `feat/p3-decision-surface`.

## What C-5 does not do

- It does not authorise editing the wire schema, the catalogue fixture or any SR document **without the change first existing as a reviewed proposal** and the implementing session confirming scope with the operator. The P3 session stated it will confirm gated items directly rather than act on a relayed grant; that remains the correct behaviour.
- It does not authorise promotion of any policy to act. Promotion has its own criteria and gate (`2026-09-19-p3-hybrid-E-followup.md`).
- It does not authorise further paid batches. C-6 covers batch `2026-09-20-operator-grant-1` only; any other batch needs fresh consent with its call count and cost stated first.
- It does not authorise live drone operation or network changes.
