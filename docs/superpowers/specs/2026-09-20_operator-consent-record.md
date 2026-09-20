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
