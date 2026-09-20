# Jev integration architecture (agreed in grill, 2026-09-19)

Supersedes the "shadow hazard-label" framing as the primary Jev goal. Companion to `2026-09-19_jev-fixture-rebuild-plan.md` (fixture/harness rebuild stays valid; it now also feeds the fast-path evaluation below).

## Diagnosis of the previous direction

- Jev was tested as a hazard labeller on prose (truth lives in the image; Jev is text-only) and as an LLM replacement for WP1 tool choice (no legal-set restriction, fixture bugs).
- Confidence and three-band routing, Jev's main product, were collapsed to a 0.5 binary.
- Static "baseline" was a constant `True`; results are void (see fixture plan).
- Documented Jev roles (TypeSafe docs, LangChain harness): routing, tool selection, confidence-gated escalation, tool-risk gating. Prior art (AerialClaw, MAVLink-MCP, PEACE): LLM proposes named skills; deterministic layer validates and vetoes.

## Agreed design

### Decision ladder per turn
1. **RULE**: deterministic state machine computes the legal-tool set. One legal tool -> execute, no model.
2. **Jev fast path**: 2..N legal tools -> Jev `Choice` over the legal set, from a compact structured state projection (`state_snapshot v1`, includes WP5 pose/staleness). N is a measured config, not a fixed cap.
   - High confidence -> act (gateway still validates). Mid or gateway-rejected -> LLM turn. Low/odd state -> operator.
   - Arguments (UUIDs, `after_sequence`, `limit`) are filled deterministically from state. Jev may pick `catalogue_plan_id` only from fixed options.
3. **LLM turn**: gateway rejection -> bounded corrective retry (architecture-review item 2).
4. **ASK-HUMAN turn**: after retries are exhausted, the LLM writes a question via a new typed tool `operator.ask`. The coordinator appends the rejected call, error code and state (grounded facts); a templated fallback question is emitted if the LLM cannot produce a valid call. One ask per stuck point, with a deadline; expiry fails closed.

### LLM is the primary operator interface at all times
- Operator replies are **free text**, logged verbatim (length-capped) and given to the LLM as operator intent; the LLM decides using it.
- Any operator message forces the next turn onto the LLM (bypasses RULE/Jev).
- Fast-path turns get asynchronous narration (LLM or template) so the operator always sees what happened, with no added latency.
- If the reply cannot be mapped to a legal tool: gateway rejects, ask once more, then fail closed.
- **Chat steers, never authorizes.** Emergency stop, assignment confirm/reject, envelope activation remain explicit operator UI actions, not inferred from chat. LLM may propose/explain only.
- **Every goal change is a new plan proposal** (`inspection.propose_plan` -> existing approval flow); the old plan stays active until approved. May be relaxed later for narrowing-only changes.

### Audit
Each turn event records `chooser` (RULE | JEV | LLM | OPERATOR), Jev confidence, model version, legal set, and rejection code if any.

### Out of scope / ruled out
Jev for continuous flight control, image classification, or argument generation. Pose stays evidence/read-model only in V0 (WP5); pose-driven capture-point choice would need a WP5 amendment.

## Cards (new, after the fixture-rebuild cards J1-J4; board `jev-shadow`)

- **F1. Legal-tool state machine + labelled turn generator.** Derive (state -> legal set -> correct tool) pairs for the full workflow, offline. Acceptance: exhaustive enumeration test; every generated turn valid against the current gateway.
- **F2. Jev/LLM/RULE chooser + audit fields (hermetic).** Injected transport; fake Jev. Ladder logic incl. forced-LLM on operator message. Acceptance: ladder unit tests; audit fields present; no path bypasses the gateway.
- **F3. Offline calibration sweep on generated turns (fake/static first).** Harness for accuracy vs confidence threshold and vs legal-set size (2, 3, 5, 8); selective accuracy at coverage. Acceptance: report format tested with a synthetic judge.
- **F4. WP0 amendment proposal: `operator.ask` + OPERATOR_QUERY_REQUESTED/ANSWERED.** Proposal only; must go through the Coherence workflow (SR consent is a human gate; never grant consent). Do NOT touch WP1 cards `t_63e568fc`, `t_4c014301`, `t_45e8629b`; land those first, then reconcile `finish(NEEDS_REVIEW)` ESCALATED semantics.
- **F5. BLOCKED: consented live sweep** of Jev on the generated turns (same consent rules as J5; state call count and cost first).

## Open items needing the operator

1. ~~Approve/edit draft labels in the fixture plan~~ — APPROVED by operator 2026-09-19.
2. ~~Live consent~~ — GRANTED 2026-09-19 for J5 only (126 calls, < ~$0.02, see fixture plan). F5 is NOT covered: restate call count and cost and get fresh consent before running it.
3. ~~Static veto~~ — CONFIRMED 2026-09-19: static remains the fail-closed veto on safety-relevant decisions; Jev's authority is limited to choosing among legal tools.
4. ~~Goal changes~~ — CONFIRMED 2026-09-19: every goal change goes through plan approval (narrowing included).
4. Is a `closer_look`/revisit choreography available by 25 Sept? Only matters for a later Jev follow-up chooser; not on this plan's critical path.

## Success criteria (write before any live run)

- High-confidence Jev choices are correct on the generated turns at the chosen N; otherwise fast path stays disabled for that legal-set size and falls back to the LLM.
- Fast-path median turn latency well under the LLM's ~17 s, without a rise in gateway rejections.
