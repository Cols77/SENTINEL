# Brief for Codex — plan architecture E, starting with the half that needs no evidence

**Written:** 2026-09-20 by the Claude session that executed P3a (`sentinel-b0`). **For:** Codex, planning only.
**You are writing a plan, not code.** Do not implement. Do not merge, push, or run anything that costs money.

---

## 1. What this project is, in one paragraph

SENTINEL flies a Tello drone around a six-zone indoor alley, photographs stations, detects rocks, and raises patrol tasks for a human operator to approve. A **coordinator** runs an agent loop: it reads state, enumerates the legally available actions, picks one, and submits it through a single `CommandGateway`. Every mutation goes through that gateway. The operator approves the survey plan and the patroller assignment; everything else should run unattended.

## 2. The architecture question you are planning against

Two architectures were defined:

- **F (shipped).** Rules execute everything. The operator decides at genuine judgment points. Jev (a small classifier-style judge) and an LLM answer **in shadow**: recorded, never shown before the operator decides, never acted on.
- **E (the goal).** The LLM *proposes* and *investigates*; Jev *gates* at high-frequency decisions; code still executes. Models gain authority only by passing pre-registered **promotion criteria**.

P1, P2 and P3a are merged to local `master` (suite: 586 passed, 3 skipped, 0 failed; nothing pushed). **The operator now wants E sooner.** That is your planning objective.

## 3. The insight your plan should be built on

**E has two halves, and only one of them needs promotion evidence.**

The promotion criteria (n ≥ 60 HIGH-confidence picks per decision point, `false_act == 0`, Wilson lower bound ≥ 0.93, ≤ 1 flip per 20 repeats, median latency ≤ 1500 ms, operator sign-off) exist to answer one question: *when may a model act without a human?*

Two E components never act:

- **`LlmProposer` at `SELECT_PLAN`** emits `inspection.propose_plan`. The gateway puts the run into `WAITING_FOR_APPROVAL` and the operator confirms or rejects. The model's output is a **proposal**, and a wrong proposal costs a rejection, not an incident.
- **`EscalationInvestigator`** reads state and ends in exactly one `operator.ask` with a recommendation. It cannot call a mutating tool — that is enforced in the legal tool set, not in the prompt.

Neither needs the promotion criteria at all. The operator gate *is* the safety property. The existing P3 plan already half-recognises this ("latency does not apply to approval-gated proposals"); take it further and say so explicitly.

**This is the fastest honest path to E, and your plan should lead with it.**

### Two qualifications that are part of the argument, not caveats on it

The session that owns the promotion criteria reviewed this framing and agreed with the core — the criteria exist to bound the cost of a *wrong autonomous action*, and a proposal that cannot reach the gateway without an operator confirm has a different failure mode. It tightened it in two places, and both are binding on your plan:

1. **Approval-gated is not consequence-free — it is anchoring.** A proposal reorders what the operator considers first; a recommendation attached to `operator.ask` more so. If the proposer ships without the presentation rule enforced and tested, "the operator gate is the safety property" quietly becomes false, because the gate is only as good as the attention behind it. So: no promotion criteria, **but the presentation rule is load-bearing and must be a hard acceptance item, not a nicety.**
2. **A weak proposer is still a failure, just a different one.** It does not cause an unsafe action; it causes rejections and extra escalations — measurable against the operator's "approvals only" target of two actions per clean run. So the approval-gated half **still needs a quality measurement**, just not the promotion machinery: proposal acceptance rate and operator touches per run (reuse P3a Task 11's `operator_touches`), scored against **held-out request texts the operator wrote**, not generator phrasings. Cheap, and it is the number that says whether the proposer is earning its place.

And one correction for the implementation, which is a safety property rather than a preference: **the investigator's inability to mutate must be enforced by the `legal_tools` set in the request, never by its prompt.** A prompt-only restriction is not a safety property.

## 4. Why the acting half is currently blocked — do not plan around this, plan a branch point

A consented live sweep (48 judgment turns × 3 repeats) produced:

| Advisor | Point | coverage_high | escalation_rate | selective_accuracy | Wilson lower | median latency |
|---|---|---|---|---|---|---|
| Jev | EVIDENCE_GATE | **0.0** | 1.0 | 0.0 | 0.0 | 1292 ms |
| Jev | SELECT_PLAN | **0.0139** | 1.0 | 1.0 | 0.207 | 1306 ms |
| LLM | EVIDENCE_GATE | 0.958 | 0.542 | 0.768 | 0.656 | 7482 ms |
| LLM | SELECT_PLAN | 0.639 | 0.708 | 0.848 | 0.718 | 9297 ms |

Read that carefully:

- **Jev reaches HIGH confidence on essentially nothing** and escalates 100% of the time. It cannot reach n ≥ 60 HIGH picks, so it cannot be promoted on this data. E's original premise — *promote Jev at the evidence gate* — is currently unsupported.
- **The LLM has coverage but fails the bar to act**: `false_act` 16 at EVIDENCE_GATE, Wilson lower 0.656 against a 0.93 requirement, and 7.5–9.3 s median latency against a 1500 ms requirement. As an *acting* policy it is out. **As an approval-gated proposer, none of those three bars apply.**
- **The original target decision no longer exists.** P2b made `EVIDENCE_GATE` a deterministic rule (in the approved plan and score ≥ 0.75 → create the task). There is nothing left to promote there.

**The diagnosis has reported** (`artifacts/decisions/jev-coverage-diagnosis.md`, commit `01096a9`; an independent review is in flight). Verdict: **(C) threshold / task-shape mismatch — no code defect.** Jev's confidence field is real, reaches the policy correctly, and clears 0.80 on 19–30% of picks on a comparable question shape (max 0.98) in the already-committed `artifacts/jev/v2-live-jev.jsonl`. On the 2–3-option act-or-defer shape of `SELECT_PLAN`/`EVIDENCE_GATE` it never clears 0.80 — not even on the 6 of 24 `EVIDENCE_GATE` turns the deterministic rule itself finds unambiguous.

**Two measurement artifacts it surfaced change how the table above should be read, and your plan must not repeat the mistake:**

- **`escalation_rate = 1.0` does not mean Jev answered "escalate".** `JevPolicy` records its own *fallback* pick for any non-HIGH answer, so a MID-confidence **correct** pick is counted as an escalation. Jev's raw picks on those 144 calls were never recorded, so its true behaviour is unknown.
- **The fixture is 75% `escalate` at both points**, which cannot separate a good judge from an abstainer.

So the acting half is **not refuted — it is unmeasured**. The honest next step is one consented re-sweep now that raw-row logging exists (`0275218`): the same paid batch can then be re-banded offline at *any* `act_at`, so the threshold is derived from data against `false_act == 0` rather than assumed. Treat `0.80` as an unvalidated default for this task shape, not a safety bar. That re-run is a paid operator gate — plan it as gated, do not assume it.

## 5. Hard constraints — these are not style preferences

1. **`CommandGateway` is untouchable.** One narrow exception was granted to another session for one defect; it does not generalise. If a capability needs a gateway or schema change, the plan **produces a proposal and stops at a gate** — it does not build.
2. **Operator gates are never self-granted.** Any schema or contract-fixture change (Coherence consent), any paid model call, any merge or push, any new hand-labelled fixture, and any promotion of a policy to act. State the gate; stop there.
3. **Proposal status, as of 2026-09-20 — check before assuming:**
   - **P1's WP0 amendment is GRANTED.** The operator approved it and another session is implementing typed result ids now (schema, contracts, `agent_loop`, tests). `EscalationInvestigator` depends on this, so it is unblocked — but it lands *after* that implementation, so plan it as sequenced behind work you do not own, and coordinate rather than duplicating.
   - **Still pending, do not assume:** the rich survey catalogue (12–15 coverage-named plans), and the consolidated P3a amendment items P-1…P-5, P-7, P-8, P-9 (`docs/superpowers/specs/2026-09-20_wp0-proposal-p3-decision-surface-amendments.md`).
   - `LlmProposer` depends on **none** of these. Put it first.
4. **Never edit an existing test to make something pass.** If one fails, that is a finding: stop and surface it.
5. **Where the platform blocks something, pin the current behaviour with a characterization test** naming the proposal item that would unblock it. This project does that deliberately, and it works: a scheduled conflict fired exactly as documented when two branches merged.
6. **No live provider call without consent**, with the call count, models and cost estimate stated first.

## 6. What to read, in this order

1. `docs/superpowers/plans/2026-09-19-p3-hybrid-E-followup.md` — the original E plan. **Its promotion criteria remain valid. Its task list is superseded.** Reuse T4 (`LlmProposer`), T5 (`EscalationInvestigator`), T6 (the Jev-agent experiment); discard the rest.
2. `docs/superpowers/plans/2026-09-20-p3a-decision-surface.md` and `HANDOFF-P3.md` — what P3a built, and the close-out.
3. `docs/superpowers/specs/2026-09-20_wp0-proposal-p3-decision-surface-amendments.md` — blockers A1–A13, proposals P-1…P-9.
4. `src/sentinel/coordinator/` — `legal_tools.py` (the decision points and `needs_judgment`), `policies.py` (`RulePolicy`, `JevPolicy`), `shadow.py` (`ShadowLadder`), `policy.py` (the seam), `operator_choice.py`, `decision_state.py`.
5. `artifacts/decisions/live-shadow-{jev,llm}.md` and `artifacts/p3/operator-load.md`.

## 7. The plan you should write

**File:** `docs/superpowers/plans/2026-09-21-p3b1-approval-gated-E.md`

**Follow the house plan format**, which the existing plans demonstrate: a header with goal, architecture, tech stack and spec links; a **Global Constraints** section; then numbered tasks, each with exact file paths, an **Interfaces** block naming what it consumes and produces, and bite-sized TDD steps — write the failing test, run it and see it fail, implement, see it pass, run the suite, commit. **Executors are cheaper models that see only their own task**, so every task must be self-contained: real code in the steps, no "TBD", no "similar to Task N", no placeholder.

Scope it as:

- **Task group A — `LlmProposer` at `SELECT_PLAN`, approval-gated.** No ungranted dependency; this is the part that can start immediately. It implements `ContextualPolicy` at `SELECT_PLAN` only and escalates elsewhere; emits `inspection.propose_plan` or `escalate` with a required `reason`; the run goes to `WAITING_FOR_APPROVAL`; a rejected proposal returns to `SELECT_PLAN`. Test with a fake adapter that it can never call a tool outside `{inspection.propose_plan, finish}` — enforced in the legal tool set, not the prompt. Evaluate against the v2 decision fixture (`fixtures/decisions_v2/turns.v2.jsonl`: 78 scored turns, 56 of them `SELECT_PLAN`) plus the 24 operator-authored request texts still pending at gate G-labels-v2. Argue explicitly in the plan **why the promotion criteria do not gate this**, so nobody later applies them by reflex.
- **Task group B — the anti-automation-bias presentation.** Whatever the model recommends, the operator sees options in fixed catalogue order, never reordered by the suggestion; the suggestion appears only behind an explicit "show assistant analysis"; the reason is shown with the facts it cites; and the operator's choice is recorded **together with whether the analysis had been expanded**, so the anchoring effect can be measured later. This is a real requirement, not a nicety — it is what keeps an approval gate meaningful rather than a rubber stamp.
- **Task group C — `EscalationInvestigator`, conditional on the WP0 amendment.** Mark it clearly as blocked on that consent. Bounded to ≤ 4 typed reads, a 6-turn / 60-second budget, ending in exactly one `operator.ask`; on budget expiry the coordinator sends a templated ask with `suggested = null`.
- **Task group D — the acting half, gated on a consented re-sweep.** The diagnosis (section 4) already rules out a code defect, so do NOT plan a bug hunt. Plan instead: re-run the sweep with raw-row logging under operator consent; re-band the resulting rows offline across a range of `act_at` values; derive the threshold from the observed distribution against `false_act == 0`; and only then apply the promotion criteria. Also fix the fixture's 75%-`escalate` imbalance first, or the re-run inherits a fixture that cannot distinguish a good judge from an abstainer. State plainly which decision points could support promotion at all, given that `EVIDENCE_GATE` is now a deterministic rule and the remaining judgment points are information-thin.

**Also state, in the plan's own words, what E will NOT be.** P3a's remaining judgment points — duplicate detections and near-tie rankings — yield only 8 and 6 distinct packets respectively, because the decision packet carries coarse bands rather than raw values (blocker A12). No promotion study reaching n ≥ 60 on distinct states is possible there today. The first information-rich judgment point in this system will be pose quality (proposal item P-9: continuous reprojection error, tag geometry, staleness), which the operator has already approved as post-P3 work. Say so, so the next person does not rediscover it.

## 8. How to hand the plan back

Save it at the path above and report: the task count, which tasks are unblocked today versus gated, and which gates the operator must open for the rest. Do not start implementation, and do not create the worktree — the executing session does that as its own Task 0.
