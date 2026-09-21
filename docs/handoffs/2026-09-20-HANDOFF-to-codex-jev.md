# Handoff to Codex — Jev: what the evidence and the field say, and what Task 10 should become

**From:** the orchestrating Claude session (P4 / architecture switch), 2026-09-20.
**To:** the Codex session executing `docs/superpowers/plans/2026-09-21-p3b1-approval-gated-E.md`.
**Scope:** Jev only. This handoff changes **Task 10** and adds one note to **Task 11**. It changes
nothing in Tasks 1–8.

Companion handoff (still current, read it first if you have not):
`docs/handoffs/2026-09-20-HANDOFF-to-codex-p3b1.md`.

---

## 0. Why this exists

The operator asked, in substance: *Jev was supposed to be the point of architecture E. Why is it not
in it? Isn't Jev good at answering questions about a given state — in our case the context gathered
from the DB?*

Both halves of that question are fair, and the second one is supported by the evidence and by how
Jev is being used in the field. This handoff sets out:

1. what the repo actually measured (§1),
2. what the published guidance and early integrations do differently (§2),
3. the four concrete changes to Task 10 that follow (§3),
4. what must **not** change (§4), and the gates (§5).

The short version: **our one Jev sweep measured a question shape that TypeSafe's own guidance tells
you not to use, with an option set that hands a hedging model a free abstention, banded at a single
threshold nobody else uses, against an unpinned model.** Re-banding alone (Task 10 as written)
cannot separate those confounds, because three of the four are properties of the *call*, not of the
banding.

---

## 1. What the repo measured

### 1.1 The numbers

`artifacts/decisions/live-shadow-jev.md` and `-llm.md` — 48 judgment turns × 3 repeats = 144 calls
per advisor, `act_at=0.80`:

| | Jev EVIDENCE_GATE | Jev SELECT_PLAN | LLM EVIDENCE_GATE | LLM SELECT_PLAN |
|---|---|---|---|---|
| `coverage_high` | 0.0 | 0.0139 | 0.958 | 0.639 |
| `escalation_rate` | 1.0 | 1.0 | 0.542 | 0.708 |
| `selective_accuracy` | 0.0 (n=0) | 1.0 (n=1) | 0.768 | 0.848 |
| `false_act` | 0 | 0 | 16 | 6 |
| `latency_ms_median` | 1292 | 1306 | — | — |

Jev cleared the acting threshold on **1 of 144 calls**. That one pick was correct. Jev made **zero**
wrong confident picks; the LLM made **22**.

Read that last line carefully before concluding Jev is the weak advisor. On this fixture Jev's
error budget was clean and the LLM's was not. What Jev lacked was *coverage*, not *accuracy*.

### 1.2 What the diagnosis established

`artifacts/decisions/jev-coverage-diagnosis.md` (offline, no paid calls; regression-pinned by
`tests/test_jev_coverage_diagnosis.py`) ruled out a code defect and reached verdict **(C) threshold
mismatch**, explicitly *not* cleanly verified. Its three recorded gaps matter more than its verdict:

- **G1 — the comparison baseline's model identity is unverified.** The cross-task-shape evidence
  comes from `artifacts/jev/v2-live-jev.jsonl`, attributed to `typesafe/jev-1.13-20260917`. That
  attribution is **not confirmed**: the run's own summary records that the endpoint resolved
  `~typesafe/jev-latest`, and all 63 records carry `judge.model == "static"`, a known-wrong field.
- **G2 — the instruction contradicted the packet.** Every `EVIDENCE_GATE` call asserted premises
  false on 24/24 turns (duplicates) and 12/24 (plan membership). Since corrected and guarded by
  `tests/coordinator/test_instruction_premises.py`. **Any re-sweep must run against the corrected
  instruction or it re-measures the confound.** Note the limit of this explanation: the LLM arm got
  the *identical* contradictory instruction on the *identical* packets and still reported
  `coverage_high = 0.958`, so the contradiction alone cannot explain the asymmetry.
- **G3 — `coverage_high` and `escalation_rate` are one measurement reported twice.** In every
  non-HIGH case the *recorded* label was `JevPolicy`'s escalate fallback, not Jev's own pick. Jev may
  have picked correctly on the six textbook-unambiguous `EVIDENCE_GATE` turns and been recorded as
  escalating on all six. Fixed forward: `Decision.judge_label` now carries the judge's own pick, and
  `shadow_eval` reports `judge_agreement` / `judge_escalation_rate` beside the policy figures,
  returning `None` (not `0.0`) where the field is absent.

### 1.3 The one distribution we do have

From `artifacts/jev/v2-live-jev.jsonl` — same provider, *different* question shape (subject to G1):

| question (type) | n | min | mean | max | fraction ≥ 0.80 |
|---|---|---|---|---|---|
| `priority` (choice, 5 options) | 63 | 0.24 | 0.57 | 0.98 | 12/63 = **19.0 %** |
| `priority_scored` (score, 4 options) | 63 | 0.17 | 0.68 | 0.93 | 19/63 = **30.2 %** |

So on a categorical question Jev's confidence spans nearly the full range and clears 0.80 on a fifth
to a third of picks. On our 2–3-option act-or-escalate question it cleared it once in 144. **That
contrast is the entire case for changing the question, not the threshold.**

### 1.4 What the fixture could not separate

`fixtures/decisions/turns.v1.jsonl`: 18 of 24 truth labels are `escalate` at **each** point. A
fixture whose majority answer is `escalate` cannot distinguish a good judge from an abstainer.
**Task 9 already fixes this** (60 % cap per label per point). Task 10 must consume the Task 9
fixture, which it already says it does. No change needed there.

---

## 2. What the field does differently

Jev went public 2026-09-15. Everything below is vendor guidance, launch write-ups, and early
open-source integrations. **No independent production results exist yet.** Treat it as convergent
design practice, not as proof.

### 2.1 TypeSafe's own guidance — decompose

> "Keep code in control: decompose a judgment into atomic questions, then combine them with weights
> and thresholds you own."

And, decisively for cost: **each question is evaluated in parallel and in isolation against the same
state, so adding questions barely changes latency.** A 13-question batch is reported at **12.2×
cheaper and 10.0× faster than per-question calls, with identical answers.**

We did the opposite. `SELECT_PLAN` asks Jev to perform a whole judgment — parse a free-text request,
map it onto a 15-entry catalogue, and decide whether it is answerable at all — in a single `Choice`.

### 2.2 Escalation is derived, never offered

Every integration reviewed derives abstention from **confidence and margin**, in code, after the
call. None offers "escalate" as a candidate inside the option set.

`JevPolicy` does both: `escalate` is a candidate *and* a raw `escalate` pick is honoured in **any**
band. A model that hedges therefore has an always-available safe pick, and everything else it says
below 0.80 is discarded. This is a plausible contributor to `escalation_rate = 1.0`. **It cannot be
confirmed from committed data** — that sweep did not keep Jev's raw picks. It is a hypothesis with a
cheap test, not a finding.

### 2.3 Nobody uses a single 0.80 cliff

| Source | Banding |
|---|---|
| `nitoba/opencode-jev-router` | ≥ 0.80 → expose only Jev's tool; 0.55–0.80 → expose top-3 to the LLM; < 0.55 → full catalogue. Shadow ("observe") mode default; fail-open on timeout/error/rate-limit. |
| `alpbahadur/jevrouter` | `minConfidence` **0.6**; `minSelectedProb` 0.7; **`minMargin` 0.15** (top-1 minus top-2); `maxNormalizedEntropy` 0.85; `minChars` 12. |
| DataCamp write-up | act > 0.9; human review 0.7–0.9; escalate to a stronger model < 0.7. |
| LangChain harness | `Choice` for model routing; `Noul` for pre-execution tool-risk gating. |

Two things generalise. **Mid-confidence is narrowed, not discarded** — the LLM gets a shortlist
instead of the full option set. And **margin and entropy gate alongside confidence**, which suits a
15-option plan choice far better than a bare confidence scalar.

### 2.4 Pin the model

`jevrouter`'s stated lesson: pin an exact revision (`jev-1.13.0`), never `jev-latest`, once tuned on
labelled data. Our G1 is exactly this failure. `scripts/shadow_sweep.py` *does* pin
`typesafe/jev-1.13-20260917`; the comparison baseline did not.

### 2.5 Stated weaknesses — hold these against any promotion argument

- **No rationale at all.** "Jev returns probabilities but no natural-language rationale." Poor for
  audit; our consent record and event log expect a reason. Anything Jev gates must have its
  justification reconstructed by code from the inputs, not quoted from Jev.
- **Schema conformance is not correctness.** "It cannot return a value outside your schema," but
  "it can still be wrong about a decision."
- **Vendor benchmark, vendor workflows.** Jev 67.8 % / $0.0004 / 0.4 s; GPT-5.6 Terra 67.9 % /
  $0.0304 / 10.1 s; Claude Opus 5 73.1 % / $0.1761 / 37.8 s. The suite was written by TypeSafe's own
  model-capabilities team and has not been independently reproduced. Accuracy parity is *promising,
  not settled*.
- **Cost is not a constraint here.** $0.042 per million input tokens, output unmetered, about
  $0.0004 per decision. The gate on a re-sweep is operator consent, not money.

**Sources:** <https://www.datacamp.com/blog/system-one-models-jev> ·
<https://www.langchain.com/blog/building-a-harness-with-jev> ·
<https://www.developersdigest.tech/blog/typesafe-jev-system-one-models-release-guide-2026> ·
<https://github.com/alpbahadur/jevrouter> · <https://github.com/nitoba/opencode-jev-router> ·
<https://openrouter.ai/typesafe/jev-1.13>

---

## 3. What Task 10 should become

Task 10 as written ("re-sweep Jev once, then re-band raw rows offline") is **correct and should be
kept**. Its Step 2 — enumerate every threshold from raw rows, never infer the judge's pick from
`JevPolicy`'s fallback — is exactly right and already answers G3.

But re-banding enumerates thresholds over **the same call**. It cannot answer "was the question
shape wrong?", because the option set and the prompt are fixed at call time. Three arms are needed,
and — per §2.1 — **all three batch into one call per turn**, so the marginal cost over the
single-arm sweep already planned is close to zero.

### 3.1 Change A — run three arms in one batched call per turn

For each turn in the Task 9 balanced fixture, send **one** request carrying the same state and all
of the following questions:

- **Arm A (control) — unchanged.** The current composite `Choice` over `{candidate…, escalate}`.
  Keeps the new batch comparable with `live-shadow-jev.md`. Without it, nothing is measured against
  anything.
- **Arm B (decomposed).** Atomic questions whose answers **code** combines into a plan id:
  - `side`: `Choice` over `{left, right, both, unclear}`
  - `depth`: `Choice` over `{front, middle, back, all, unclear}`
  - `markers_required`: `Noul` ("does the request require marker verification?")
  - `answerable`: `Noul` ("does the request name a coverage the catalogue can express?")

  Code maps (side, depth, markers) to a catalogue plan id, and escalates when any answer is
  `unclear`, when `answerable` is below its threshold, or when any component confidence is below its
  gate. **No Jev answer is an action.** This is the arm that tests the operator's hypothesis
  directly.
- **Arm C (no free abstention).** The composite `Choice` **without** `escalate` in the option set.
  Escalation comes only from the §3.2 gates. This isolates the effect of offering abstention as a
  candidate — the one thing §2.2 flags that cannot be re-derived offline.

The three arms must be distinguishable in the raw rows (`arm: "A" | "B" | "C"`, plus the question
name for Arm B). Do not collapse them in the rendered report.

`EVIDENCE_GATE` keeps Arm A only. It is a deterministic rule now (operator decision, C-7 family);
its arm exists to track drift, not to seek promotion.

### 3.2 Change B — re-band on margin and entropy, not confidence alone

Extend `scripts/reband_shadow.py` (Task 10 Step 2) to enumerate, per decision point and per arm:

- `confidence` ≥ t, for t in `0.00, 0.05, …, 1.00` plus every observed value — **as already planned**;
- **`margin`** = P(top-1) − P(top-2) ≥ m, for m in `0.00, 0.05, …, 0.50`;
- **normalised entropy** of the probability vector ≤ e, for e in `0.50, 0.55, …, 1.00`;
- the **conjunction** of all three, since that is how `jevrouter` actually gates.

This is free. It requires only that per-option probabilities are in the raw rows — which Task 10's
"Interfaces — Consumes" already specifies. **Verify that `RecordingJudge` (commit `0275218`) writes
the full probability vector and not just the scalar confidence before the paid batch runs.** If it
writes only the scalar, fix that first; discovering it afterwards costs a second paid batch.

Report a **three-tier** outcome per arm, not a binary one: act / shortlist-to-LLM / escalate. The
shortlist tier is what §2.3 shows everyone else exploiting, and our current policy throws it away.

### 3.3 Change C — verify the model that actually answered

Record the provider-resolved model identifier on **every** response and fail the batch loudly if it
differs from the pinned `typesafe/jev-1.13-20260917`. Write it into each raw row, not only the
report header. G1 exists because a run's real model identity was unrecoverable afterwards; do not
reproduce it. Do not accept `judge.model == "static"` as a value.

### 3.4 Change D — state the arithmetic to the operator before the batch

Task 10 Step 5 already requires this. Make it explicit that the batch is **one call per turn per
repeat** (not per arm, not per question), and give turns × repeats, total calls, the pinned model,
provider, and an estimated token cost. State plainly that the estimate is an estimate.

---

## 4. What must not change

- **Tasks 1–8 stand as written.** They are the LLM half of E, they are in flight, and nothing here
  touches them. Do not reopen them for Jev.
- **`CommandGateway` is untouchable.** Nothing in this handoff goes near it.
- **No Jev output may act.** Every arm is shadow. Arm B's code-side combination produces a
  *candidate plan id for scoring*, never a submitted command.
- **Never edit an existing test to make something pass.** A failing existing test is a finding:
  stop and surface it.
- **Do not generate or relabel operator labels** (Task 9 Step 4 already says this).
- **`rtk proxy` prefix on every test command** — the earlier handoff flagged that your plan's test
  commands omit it and a hook otherwise swallows the run ("No tests collected").

---

## 5. Gates

| Gate | What it covers | State |
|---|---|---|
| **G-live-jev-re-sweep** | The paid batch in Task 10 Step 6 — now three arms in one batched call per turn. | **Closed.** Restate calls, model, provider, cost per §3.4 and stop. A relayed grant from another session is not operator consent. |
| **G-acting-labels** | Task 9's balanced fixture labels. | Closed. Operator authors them. |
| **G-promote** | Any write to `promotions.json`. | Closed. |
| **G-amend-rest** | Task 7's schema/contract change. | Closed. |

None of these may be self-granted. All four route to the operator directly.

---

## 6. Note for Task 11

Task 11 Step 1 lists why no point is currently promotable, and that reasoning is sound. Add one line
to the report it produces:

> Jev's coverage on the act-or-escalate question shape was measured once, under a question design
> that TypeSafe's own guidance advises against, with abstention offered as a candidate, at a single
> unvalidated 0.80 threshold, against a model revision that could not afterwards be confirmed. The
> re-sweep's Arm B and Arm C bound how much of the observed 1/144 coverage was task-shape ceiling and
> how much was instrument. Until those arms are read, "Jev cannot reach an acting band" is not an
> established fact about Jev.

And keep Step 2 intact: deriving `act_at`, `margin` or `entropy` from raw data replaces an
unvalidated default. **It waives none of the outcome criteria** — n ≥ 60 HIGH picks across at least
two seeds/days, `false_act == 0`, Wilson lower 95 % ≥ 0.93, at most 1 flip per 20 repeats, median
latency ≤ 1500 ms, operator sign-off.

---

## 7. Context you may not have: a second flow is being designed

The operator is mid-design on a **survey/counting flow** (rail sweep inside a safety cube, count
computers/people/phones via a VLM) targeting a video on 2026-09-25 and a live demo on 2026-09-27.
It is planned as a **sibling package**, `sentinel.survey`, with its own small contract and store —
**it touches none of your files and none of the gateway.** The design is *not yet approved*; treat
this section as advance notice, not as a requirement.

Two things there will reuse your work, which is why it is worth knowing:

- the `ContextualPolicy` seam, so your `LlmProposer` plugs into the survey flow's F/E switch unchanged;
- Jev in the two roles §2 supports — turning a free-text request into typed fields (`which classes`,
  `exact or rough`, `perimeter named?`), and `Noul` guardrails over the LLM's output ("does the
  proposed sweep match the request?", "does the report claim anything the counts do not support?").

If Arm B validates the decomposition hypothesis, both of those roles are on firmer ground. If it
does not, they stay shadow and the survey flow loses nothing.

---

## 8. Suggested order

1. Finish Tasks 1–6 as planned. Nothing here blocks them.
2. Task 9 tooling (ungated), then stop at **G-acting-labels**.
3. Task 10 Steps 1–4, extended per §3.2 — all offline, all free, all committable now.
4. **Before** presenting G-live-jev-re-sweep: verify `RecordingJudge` writes full probability vectors
   (§3.2) and that the resolved-model check is in place (§3.3). Both are cheap to fix now and
   expensive to discover after the batch.
5. Present **G-live-jev-re-sweep** with §3.4's arithmetic. Stop.
6. After consent: one run, three arms, raw JSONL and report committed together.
7. Task 11 with §6's added line.

---

*Written by the P4 orchestrating session. Everything in §1 is sourced from committed artifacts and
their pinning tests; everything in §2 is sourced from public material dated on or after 2026-09-15
and is convergent practice, not proof. Where this handoff states a hypothesis rather than a finding
— §2.2 in particular — it says so.*
