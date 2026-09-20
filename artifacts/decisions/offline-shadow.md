# Offline shadow sweep: fake vs static baselines

Shadow only. No advisor has authority. Promotion criteria live in P3 and require n ≥ 60 HIGH-band picks per decision point.

**Non-LLM, offline baselines only.** `fake` is a trivial pipeline check (always the alphabetically-first non-escalate candidate); `static` is a small hand-written rule set over the packet's facts, no model call. Neither is the real Jev/LLM shadow advisor -- those require the live, consented sweep (plan gate G-sweep, Task 5 Step 5), not run here.

**Labels are APPROVED (2026-09-20).** `fixtures/decisions/README.md` records that gate `G-labels` (operator approval of the 48 hand labels these turns are scored against) has run: the operator approved all draft labels as drafted, subject to two relabellings -- `sp-001`..`sp-006` (single-station requests) now `escalate`, and the `EVIDENCE_GATE` score cut is `>= 0.75`. This report is generated against that approved fixture, not the earlier draft.

**`EVIDENCE_GATE` now measures agreement with a deterministic rule, not an independent judgment benchmark.** Per the operator's 2026-09-20 decision ("Make it a rule, keep shadow"), `legal_tools.legal_set()` decides `create_task` vs `escalate` deterministically (`LegalSet.rule_choice`: `create_task` iff the detection is inside the approved plan and `raw_score >= 0.75`, `escalate` otherwise) -- advisors are still called in shadow at that point and recorded, they just no longer gate execution. Because the fixture's `EVIDENCE_GATE` hand labels mirror that same rule exactly (see `fixtures/decisions/README.md`), scoring an advisor's `EVIDENCE_GATE` picks below is a **rule-agreement check**, not a judgment benchmark -- `static`'s `_evidence_gate` rule happens to mirror the deterministic rule, so its near-1.0 numbers below reflect that overlap, not independent judgment skill. `SELECT_PLAN` remains the judgment benchmark: it has no `rule_choice`, and the operator alone decides it (P2 Task 3).

48 judgment turns x 3 repeats x 2 advisors = 288 calls (all offline).

## fake

### SELECT_PLAN
| metric | value |
|---|---|
| `n` | 72 |
| `agreement` | 0.25 |
| `coverage_high` | 1.0 |
| `selective_accuracy` | 0.25 |
| `false_act` | 54 |
| `escalation_rate` | 0.0 |
| `error_rate` | 0.0 |
| `latency_ms_median` | 0.0 |
| `stability` | 1.0 |
| `wilson_lower_95` | 0.1644 |

### EVIDENCE_GATE
| metric | value |
|---|---|
| `n` | 72 |
| `agreement` | 0.25 |
| `coverage_high` | 1.0 |
| `selective_accuracy` | 0.25 |
| `false_act` | 54 |
| `escalation_rate` | 0.0 |
| `error_rate` | 0.0 |
| `latency_ms_median` | 0.0 |
| `stability` | 1.0 |
| `wilson_lower_95` | 0.1644 |

## static

### SELECT_PLAN
| metric | value |
|---|---|
| `n` | 72 |
| `agreement` | 0.8333 |
| `coverage_high` | 1.0 |
| `selective_accuracy` | 0.8333 |
| `false_act` | 12 |
| `escalation_rate` | 0.5833 |
| `error_rate` | 0.0 |
| `latency_ms_median` | 0.0 |
| `stability` | 1.0 |
| `wilson_lower_95` | 0.7309 |

### EVIDENCE_GATE
| metric | value |
|---|---|
| `n` | 72 |
| `agreement` | 1.0 |
| `coverage_high` | 1.0 |
| `selective_accuracy` | 1.0 |
| `false_act` | 0 |
| `escalation_rate` | 0.75 |
| `error_rate` | 0.0 |
| `latency_ms_median` | 0.0 |
| `stability` | 1.0 |
| `wilson_lower_95` | 0.9493 |
