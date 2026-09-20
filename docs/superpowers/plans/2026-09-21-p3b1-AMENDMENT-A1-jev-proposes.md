# Amendment A1 to the P3b1 plan — Jev proposes too

> **Amends:** `docs/superpowers/plans/2026-09-21-p3b1-approval-gated-E.md`
> **Status:** operator-directed, 2026-09-20, added after Codex committed Task 5.
> **Effect:** adds **Task 13**, and supersedes **one sentence** in *"What Architecture E Will Not Be
> in P3b1"*. It changes nothing in Tasks 0–12. Do not reopen work already committed.
> **Evidence behind it:** `docs/handoffs/2026-09-20-HANDOFF-to-codex-jev.md`.

**Why this exists.** The operator asked: *"Jev was supposed to be the point of architecture E. Why
is it not in it? Isn't it good at answering questions about a given state?"* Both halves are fair,
and the plan as written does not answer them.

---

## A1.1 The argument this plan already makes, applied to Jev

The plan's own section *"Why Acting-Policy Promotion Does Not Gate Tasks 1-8"* says:

> `LlmProposer` cannot approve its proposed plan; the existing coordinator moves the run to
> `WAITING_FOR_APPROVAL`. A wrong output can anchor or burden the operator, but it cannot cross the
> operator gate by itself.

**That argument is about the operator gate, not about which model sits behind it.** It therefore
applies to Jev unchanged. A Jev pick at `SELECT_PLAN` is a proposal, parks in
`WAITING_FOR_APPROVAL`, and costs the operator one rejection when it is wrong.

Admitting the LLM to the proposing role while excluding Jev is not a conclusion this plan's own
reasoning supports. It is an inconsistency, and Task 13 removes it.

**Superseded sentence.** In *"What Architecture E Will Not Be in P3b1"*, the closing clause

> architecture E means: the LLM proposes and investigates, the operator decides, and code executes

is replaced by

> architecture E means: **an advisor proposes** — the LLM or Jev, selectable per run — the operator
> decides, and code executes. Neither advisor may act without an operator gate, and neither claims
> promotion evidence.

Everything else in that section stands, **including** the refusal to promote Jev merely by lowering
`act_at`, and the refusal to treat a 75 %-`escalate` fixture as evidence that a judge distinguishes
acting from abstaining.

## A1.2 Why Jev can do this at all

Jev cannot generate a UUID, a sentence, or any value outside the caller's schema. It **can** pick
among options the caller enumerates. This project's own design already draws exactly that line
(`docs/superpowers/specs/2026-09-19_jev-integration-architecture.md` §2):

> Jev `Choice` over the legal set … **arguments are filled deterministically from state.**

`legal_set()` already returns precisely that shape: a `LegalSet` of
`Candidate(tool_name, arguments, label, description)` in which **every argument is already filled**.
No new machinery is required to make the candidates Jev-pickable — they already are. That is why
this task is small.

---

## Task 13: Approval-gated `JevProposer` at `SELECT_PLAN`

**Status:** Unblocked. No schema change, no gateway change, no paid call, no promotion.

**Files:**

- Modify: `src/sentinel/coordinator/proposer.py` — add a class beside `LlmProposer`; **do not alter
  `LlmProposer`**
- Create: `tests/coordinator/test_jev_proposer.py`
- Modify: `src/sentinel/cli.py` — accept `--proposer jev`

**Interfaces — Consumes:** `PolicyContext`, `LegalSet`, `Candidate`, `AuditSink`, and the existing
`JevJudge.choice(instruction=, state=, criteria=)` surface.

**Interfaces — Produces:** `JevProposer(judge, sink, act_at=0.60, min_margin=0.15)` exposing
`decide(request, context) -> dict`, and `derive_reason(label, context) -> str`.

- [ ] **Step 1: Write the failing tests**

Six behaviours, each a direct consequence of what the old sweep could not measure:

```python
def test_jev_is_never_offered_escalate_as_a_candidate():
    """Escalation is derived from the gates. Offering it inside the option set
    hands a hedging model a free abstention and throws away everything else it
    said below the threshold -- the likeliest single contributor to
    escalation_rate = 1.0 in artifacts/decisions/live-shadow-jev.md."""


def test_a_thin_margin_escalates_even_at_high_confidence():
    """Top-1 minus top-2 below min_margin is a near-tie: exactly the case the
    operator should see. A bare confidence scalar cannot express it, which is
    why every published integration gates on margin as well."""


def test_a_label_outside_the_legal_set_escalates():
    """Jev cannot return an out-of-schema value, but a stale catalogue can
    still make a returned label illegal for this turn."""


def test_a_judge_that_raises_or_times_out_fails_closed_to_escalate():
    ...


def test_the_recorded_row_carries_jevs_own_pick_beside_the_policy_label():
    """G3 in artifacts/decisions/jev-coverage-diagnosis.md: the old sweep
    recorded JevPolicy's escalate fallback rather than the judge's answer, so
    coverage_high and escalation_rate were one measurement reported twice."""


def test_the_operator_facing_reason_is_derived_from_the_candidate_not_from_jev():
    """Jev returns no natural-language rationale at all. A derived reason
    cannot be wrong about itself, which is a better approval surface than
    model prose."""
```

- [ ] **Step 2: Run them and watch them fail**

```powershell
rtk proxy uv run pytest tests/coordinator/test_jev_proposer.py -q
```

Expected: FAIL, `ImportError: cannot import name 'JevProposer'`.

- [ ] **Step 3: Implement `JevProposer`**

Mirror `LlmProposer`'s fail-closed structure exactly. The differences that matter:

- build `criteria` from `context.legal.candidates` **excluding** `ESCALATE_LABEL`;
- accept only when `confidence >= act_at` **and** `P(top-1) − P(top-2) >= min_margin`;
- reject any label not in the legal set;
- wrap the judge call so that **any** exception escalates;
- write the audit row carrying **Jev's own `value`** in `judge_label` beside the policy's final
  label, so the two can never collapse into a single number again;
- set the proposal's `reason` from `derive_reason`, which renders the chosen `Candidate`'s
  `description` and the decision point — never a Jev string, because there is none.

**Defaults: `act_at = 0.60`, not `0.80`.** `0.80` was never validated for this task shape, and every
published integration uses a floor in the 0.55–0.70 band **with a margin gate beside it**. Both
thresholds are constructor arguments, read from one config dict rather than scattered as literals,
so Task 10's offline re-banding can re-derive them from real rows without a code change.

- [ ] **Step 4: Run the tests**

```powershell
rtk proxy uv run pytest tests/coordinator/test_jev_proposer.py tests/coordinator/test_proposer.py -q
```

Expected: PASS. **`test_proposer.py` must pass unmodified.** If it does not, `LlmProposer` was
altered — stop and surface it rather than editing the test.

- [ ] **Step 5: Wire `--proposer jev` and prove the lifecycle**

Extend the Task 3 lifecycle test to run the same scenario through `JevProposer` with a scripted
judge, and assert it also reaches `WAITING_FOR_APPROVAL` with the plan pending. Same gateway path,
same operator gate, same terminal state.

- [ ] **Step 6: Full suite, then commit**

```powershell
rtk proxy uv run pytest -q
rtk git add src/sentinel/coordinator/proposer.py tests/coordinator/test_jev_proposer.py src/sentinel/cli.py
rtk git commit -m "feat(coordinator): approval-gated Jev proposer at SELECT_PLAN"
```

---

## A1.3 What Task 13 does NOT claim

- **It is not a promotion.** `promotions.json` is untouched. Task 11's criteria are unchanged and
  still govern any point where a model would act **without** an operator gate.
- **It does not make Jev the default.** `--proposer` selects it per run; the default is unchanged.
  Making Jev the default would be a separate operator decision.
- **It does not settle whether Jev is good at this.** Task 10's three-arm re-sweep does that. Task 13
  makes Jev *usable* under a gate; Task 10 measures whether it *should* be used.

## A1.4 Relationship to Task 10

Task 10's **Arm B** (decomposed atomic questions) and **Arm C** (no `escalate` candidate) are the
measurement; Task 13 is the production path Arm C's design implies. Build Task 13's gate logic so
Task 10's re-banding can drive it directly: `act_at`, `min_margin` and any entropy ceiling all come
from one config dict.

If Arm B wins decisively, a follow-up task should decompose `SELECT_PLAN` into typed fields (side,
depth, markers-required, answerable) with code mapping them to a catalogue plan. **Do not build that
before the measurement** — it is the hypothesis Task 10 exists to test.

## A1.5 Revised gate summary row

| Tasks | State today | Gate |
|---|---|---|
| 13 | **Unblocked** | None to implement. Making Jev the *default* proposer is a separate operator decision. |

There are now **14 tasks (0–13)**.

## A1.6 A note worth keeping

Twice now Jev has been blamed for what was an instrument problem: once when a sweep measured a
question shape the vendor's own guidance advises against, and once here, when it was excluded from a
role this plan's own reasoning admits it to. Neither was a finding about Jev. When Task 10's results
arrive, check the instrument before the advisor.

---

## A1.7 Parallel change in the survey flow, for consistency

The survey/counting flow (`docs/superpowers/specs/2026-09-20-survey-counting-design.md` §7, plan
`docs/superpowers/plans/2026-09-20-survey-counting.md` Task 6) makes the identical change and should
stay in step:

| | P3b1 (alley) | Survey |
|---|---|---|
| Seam | `ContextualPolicy` / `PolicyContext` | `SurveyPolicy` / `SurveyContext` |
| Modes | `--proposer rule \| llm \| jev` | `--mode F \| E \| J` |
| Candidates | `legal_set()` | `survey.candidates` |
| Jev gates | `act_at` 0.60, `min_margin` 0.15 | identical |
| Escalate offered to Jev? | never | never |
| Reason for the operator | derived from the `Candidate` | derived from the `Candidate` |

The two flows share no code (`PolicyContext.state` is typed to the alley's `DecisionState`, so the
classes cannot be reused across them), but they must not drift in behaviour. If either side changes
a gate default, change both and say so.
