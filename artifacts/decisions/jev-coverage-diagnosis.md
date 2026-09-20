# Diagnosis: why is Jev never HIGH-confidence?

Investigated strictly offline: code, committed fixtures, and already-recorded
artifacts only. No model call of any kind was made for this diagnosis.

## The question

`artifacts/decisions/live-shadow-{jev,llm}.md` (a consented sweep of 48
judgment turns x 3 repeats = 144 calls per advisor, `act_at=0.80`):

| | Jev EVIDENCE_GATE | Jev SELECT_PLAN | LLM EVIDENCE_GATE | LLM SELECT_PLAN |
|---|---|---|---|---|
| coverage_high | 0.0 | 0.0139 | 0.958 | 0.639 |
| escalation_rate | 1.0 | 1.0 | 0.542 | 0.708 |
| selective_accuracy | 0.0 (n=0) | 1.0 (n=1) | 0.768 | 0.848 |
| false_act | 0 | 0 | 16 | 6 |

Architecture E needs Jev to act at HIGH confidence. Jev reached HIGH on 1 of
144 calls. Is that a measurement defect (E is not dead), real calibration
(E must be re-scoped, with a stated ceiling), or a threshold mismatch (0.80
is above Jev's operating range for this task shape, even though Jev itself
is fine)?

## What was examined

1. **`JevPolicy.decide`** (`src/sentinel/coordinator/policies.py:100-143`).
   `confidence = answer.get("confidence")`, falling back to
   `answer["probabilities"][label]` only when `confidence is None`.
   `act_at=0.80` (default), `review_below=0.50`. Band: HIGH >= 0.80, MID
   >= 0.50, else LOW. A raw `escalate` pick is honoured in ANY band (not a
   confidence fallback) — this matters for reading `escalation_rate`, see
   below. `criteria = {candidate.label: candidate.description for candidate
   in legal.candidates}` is built from the real legal set and passed through
   unconditionally.

2. **`JevJudge.choice` / `evaluate`** (`src/sentinel/judgment.py:131-184`).
   `choice()` accepts both `criteria=` and `options=` (the `376ec2f` defect —
   `LlmJudge.choice` rejecting `criteria=` and raising `TypeError` on every
   call — is already fixed on **both** adapters; read the current signatures,
   not just history). `evaluate()` builds the wire payload as
   `{"type": q["type"], "instructions": q["instruction"], "criteria": q["criteria"]}`
   whenever `q.get("criteria") is not None`; `choice()` always passes a dict
   (`{}` at worst, never `None`), so criteria — including candidate
   descriptions — always reaches the wire. For a `choice` answer:
   `answers[name] = {"value": ..., "probabilities": {...}, "confidence": answer.get("confidence")}`
   — read directly off the upstream JSON, no rescale, no temperature, no
   calibration step in this repo. **No defect found in this path** — it is
   also independently unit-tested end to end in
   `tests/test_judgment.py::test_jev_judge_evaluate_batches_all_questions_in_one_post`
   and in `tests/coordinator/test_policies.py` (HIGH/MID/LOW banding, the
   `escalate`-in-any-band rule, and the probabilities fallback are each
   pinned by a scripted judge).

3. **`fixtures/judgment/cases.v1.jsonl` / `cases.v2.jsonl`.** These do NOT
   contain judge output — they are hand-labelled ground truth
   (`hazard`/`needs_review`/`priority`/`rationale`) for a *different*
   judgment surface (`hazard_relevant` / `priority` / `human_review_needed`
   / `more_evidence_needed`, `sentinel.evaluation.QUESTION_SET`), not the
   `SELECT_PLAN`/`EVIDENCE_GATE` decision-candidate surface `JevPolicy`
   scores. There is no confidence distribution to extract from them
   directly. The real, comparable committed data is
   **`artifacts/jev/v2-live-jev.jsonl`** — 63 real live Jev answers (21
   cases x 3 repeats, `typesafe/jev-1.13-20260917` via OpenRouter,
   `/api/alpha/decisions`) already paid for and committed on 2026-09-19/20.
   Extracted distribution (pinned by
   `tests/test_jev_coverage_diagnosis.py`):

   | question (type) | n | min | mean | max | fraction >= 0.80 |
   |---|---|---|---|---|---|
   | `priority` (choice, 5 options) | 63 | 0.24 | 0.57 | **0.98** | 12/63 = **19.0%** |
   | `priority_scored` (score, 4 options) | 63 | 0.17 | 0.68 | **0.93** | 19/63 = **30.2%** |

   This single fact settles the "never populated" and "always near zero"
   hypotheses: on a comparable `choice`/`score` question shape, Jev's
   `confidence` field is present on every record, spans nearly the full
   0-1 range, and clears 0.80 on roughly a fifth to a third of picks. The
   `boolean`/`noul` questions in the same file (`hazard_relevant`,
   `human_review_needed`, `more_evidence_needed`) carry no `confidence`
   field at all (only `probability`) — that is documented, structural, and
   consistent across every adapter (`scripts/jev_v2_dry_run.py`'s own note:
   "Jev noul wire answers carry no self-confidence field"), not a bug.

4. **`scripts/jev_v2_dry_run.py` / `scripts/live_jev_v2_replay.py`.** Both
   replay the `evaluation.QUESTION_SET` surface above, not
   `SELECT_PLAN`/`EVIDENCE_GATE`. `jev_v2_dry_run.py`'s fake transport is
   scripted (not usable as evidence about the real model). `live_jev_v2_replay.py`
   is the script that produced `v2-live-jev.jsonl` (item 3), which is the
   most useful thing either script offers here, and it is already committed
   — no new call was made.

5. **The actual `SELECT_PLAN`/`EVIDENCE_GATE` sweep's raw data does not
   exist in this repo.** `scripts/shadow_sweep.py` is the script that
   produced `live-shadow-jev.md`/`live-shadow-llm.md`; commit `0275218`
   ("fix(eval): commit raw sweep rows...") added `RecordingJudge` and
   `write_records()` specifically so a future sweep's raw per-call
   `{confidence, probabilities}` could be re-derived and re-banded without
   paying for another live batch — its own commit message says this is
   "what the next sweep needs to settle whether Jev's ~0% HIGH-band
   coverage is real calibration." But that fix landed *after* the sweep
   whose aggregates `live-shadow-jev.md` reports; no
   `artifacts/decisions/live-shadow-jev.jsonl` (or `-llm.jsonl`) was ever
   generated or committed. **This diagnosis cannot see the actual per-turn
   confidence values Jev returned for `SELECT_PLAN`/`EVIDENCE_GATE`** — only
   the two aggregate numbers (`coverage_high`, `escalation_rate`, etc.) in
   the committed `.md` files, which cannot by themselves distinguish "Jev's
   raw pick was `escalate`" from "Jev picked a real candidate at MID/LOW
   confidence and `JevPolicy` overrode the *recorded* label to `escalate`"
   (`JevPolicy.decide` sets `candidate = legal.by_label(ESCALATE_LABEL)` for
   any non-HIGH, non-raw-escalate pick — the fallback pick, not Jev's raw
   pick, is what `escalation_rate` counts). Re-running the live sweep would
   settle this precisely, but that is a paid call and out of scope here
   (`SENTINEL_SHADOW_SWEEP_CONSENT` is required and this task is
   NO-PAID-CALLS).

6. **`fixtures/decisions/turns.v1.jsonl`** (the actual sweep fixture, 48
   judgment turns). `SELECT_PLAN`: 24 turns, always 3 candidates (2 plans +
   `escalate`), truth label `escalate` 18/24 (75%). `EVIDENCE_GATE`: 24
   turns, always 2 candidates (`create_task` + `escalate`), truth label
   `escalate` 18/24 (75%) — this matches `2026-09-20`'s own
   `live-comparison-2026-09-20.md`, which already flagged "18 of 24 labels
   at each point are `escalate`... A fixture whose majority answer is
   `escalate` cannot separate a good judge from an abstainer." Critically,
   EVIDENCE_GATE is not uniformly ambiguous: 6 of its 24 turns are
   unambiguous by the deterministic rule itself (`detection_score_band` in
   `{high, very_high}`, `detection_inside_plan=True`,
   `detection_duplicates_open_task=False` — a textbook `create_task`,
   pinned in `test_evidence_gate_fixture_includes_unambiguous_create_task_turns`).
   Yet `coverage_high` for EVIDENCE_GATE is exactly 0.0 across all 72
   calls — not even the 18 calls (6 turns x 3 repeats) over unambiguous
   inputs cleared 0.80.

## Verdict: (C) THRESHOLD MISMATCH

Jev's `confidence` field is real, reaches the adapter and the policy
correctly (no code defect — item 2), and is demonstrably capable of
clearing 0.80 (item 3: 19-30% of picks on a comparable `choice`/`score`
question). That rules out (A) as stated ("confidence is mis-read,
mis-scaled, or never populated") for the adapter/policy code in general.

But on the `SELECT_PLAN`/`EVIDENCE_GATE` task shape — a small (2-3 option)
"pick the action, or escalate" choice — Jev's confidence apparently never
clears 0.80, even on EVIDENCE_GATE turns the deterministic rule itself finds
unambiguous (item 6). If the fixture's difficulty were the whole story, the
6 easy turns should show at least some HIGH picks; they show none. That
points at the *task shape itself* — a bounded, safety-flavoured act-or-defer
choice — running Jev's self-reported confidence lower than a same-model
categorical classification (`priority`) does, rather than at every one of
these 144 packets being individually, uniformly hard.

**This is not a clean, fully-verified (C)** — see item 5: the one piece of
direct evidence that would confirm it (the actual per-turn confidence values
for these 144 calls) was never committed, so I cannot rule out that a
non-trivial share of those calls were MID-band (0.50-0.79) rather than
clustered near 0, nor can I rule out (B) real, warranted low confidence on
the 18/24 genuinely-ambiguous turns coexisting with an unexplained ceiling on
the 6 easy ones. **What I can say with the evidence in hand:** there is no
evidence of a code-level measurement defect (A); there is direct evidence
that 0.80 is not, in general, above Jev's confidence range (item 3); and
there is fixture-level evidence that 0.80 is above whatever Jev is actually
reporting for this specific 2-3-option task shape, including on its easiest
cases (item 6). I am establishing "I could not find evidence of a defect,
and I have positive evidence the field works correctly elsewhere" — not "I
have proven there is no defect in this exact code path for these exact 144
calls," because the raw rows to prove that were never captured.

### Observed ceiling and what `act_at` would need to be

No committed value of Jev's confidence on `SELECT_PLAN`/`EVIDENCE_GATE`
exceeds 0.80 except the single SELECT_PLAN HIGH pick (which was correct:
`selective_accuracy=1.0`, `false_act=0`). On the comparable `choice`/`score`
surface (item 3), 50% of picks already clear roughly 0.57-0.73
(`priority`/`priority_scored` medians), so an `act_at` around 0.55-0.60
would plausibly move a meaningful share of `SELECT_PLAN`/`EVIDENCE_GATE`
picks into an acting band *if* the same distribution holds there — but that
is an extrapolation from a different question shape, not a measurement of
the real one. Whether such a threshold would stay safe cannot be answered
from what is committed: `false_act` is 0 for Jev at `act_at=0.80`, but
`false_act` is scored only over the HIGH band, so lowering `act_at` could
only be judged safe after a live re-run at the lower threshold reports
`false_act` directly (the raw-row logging from `0275218` would make this
possible without re-paying for confidence itself — the same call's raw
`{confidence, probabilities}` can be re-banded offline at any threshold, per
`tests/test_shadow_sweep_records.py`).

## Implication for architecture E

E is not dead, but it is not validated at `act_at=0.80` on this task shape
either. The path forward is not "fix a bug" (none was found) but:

1. Re-run `scripts/shadow_sweep.py --advisor jev` (now that `0275218`'s
   raw-row logging exists) so the actual per-turn `SELECT_PLAN`/
   `EVIDENCE_GATE` confidence distribution can finally be inspected and
   re-banded offline at multiple thresholds without a second paid batch.
   This is the one step this diagnosis could not take (no paid calls
   permitted here).
2. Treat `0.80` as an unvalidated default for this task shape, not a fixed
   safety bar — re-derive it from the re-run's distribution against
   `false_act == 0`, the same way `wilson_lower_95` is already computed from
   the HIGH band's own hits.
3. Fix the fixture's label balance (already flagged in
   `live-comparison-2026-09-20.md`): a 75%-escalate fixture cannot
   distinguish "Jev correctly refuses to act on hard cases" from "Jev
   refuses to act on easy cases too."

## Regression coverage added

`tests/test_jev_coverage_diagnosis.py` (5 tests, all passing, no network):
pins that Jev's live `confidence` field is populated and varies widely
(falsifies "never populated"); pins the observed 19%/30% HIGH-band rate on
the comparable `choice`/`score` question (falsifies "0.80 is always above
Jev's range"); pins the committed `SELECT_PLAN`/`EVIDENCE_GATE` sweep's own
`coverage_high`/`escalation_rate`/`false_act` numbers so a future re-run
that changes them is caught and must update this document; pins the LLM
arm's much higher coverage over the identical 144 turns as the contrasting
baseline (rules out a scoring-pipeline-wide suppression); and pins that 6 of
EVIDENCE_GATE's 24 turns are unambiguous by the deterministic rule, the
fact this diagnosis's verdict rests on most heavily.

No existing test was modified. No fix was applied — none was found to
apply; this is a threshold/task-shape finding, not a code defect.
