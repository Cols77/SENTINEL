# Offline shadow sweep: fake vs static baselines

Shadow only. No advisor has authority. Promotion criteria live in P3 and require n ≥ 60 HIGH-band picks per decision point.

**Non-LLM, offline baselines only.** `fake` is a trivial pipeline check (always the alphabetically-first non-escalate candidate); `static` is a small hand-written rule set over the packet's facts, no model call. Neither is the real Jev/LLM shadow advisor -- those require the live, consented sweep (plan gate G-sweep, Task 5 Step 5), not run here.

**Labels are DRAFT.** `fixtures/decisions/README.md` records that gate `G-labels` (operator approval of the 48 hand labels these turns are scored against) has not yet run. This report exercises the scoring pipeline against non-LLM baselines only; it carries no promotion-relevant conclusion about any advisor and must be re-generated (and, if scoring an advisor with real authority implications, re-approved) once G-labels records approval.

48 judgment turns x 3 repeats x 2 advisors = 288 calls (all offline).

## fake

### SELECT_PLAN
| metric | value |
|---|---|
| `n` | 72 |
| `agreement` | 0.25 |
| `coverage_high` | 1.0 |
| `selective_accuracy` | 0.25 |
| `false_act` | 36 |
| `escalation_rate` | 0.0 |
| `error_rate` | 0.0 |
| `latency_ms_median` | 0.0 |
| `stability` | 1.0 |
| `wilson_lower_95` | 0.1644 |

### EVIDENCE_GATE
| metric | value |
|---|---|
| `n` | 72 |
| `agreement` | 0.3333 |
| `coverage_high` | 1.0 |
| `selective_accuracy` | 0.3333 |
| `false_act` | 48 |
| `escalation_rate` | 0.0 |
| `error_rate` | 0.0 |
| `latency_ms_median` | 0.0 |
| `stability` | 1.0 |
| `wilson_lower_95` | 0.2353 |

## static

### SELECT_PLAN
| metric | value |
|---|---|
| `n` | 72 |
| `agreement` | 0.625 |
| `coverage_high` | 1.0 |
| `selective_accuracy` | 0.625 |
| `false_act` | 9 |
| `escalation_rate` | 0.5833 |
| `error_rate` | 0.0 |
| `latency_ms_median` | 0.0 |
| `stability` | 1.0 |
| `wilson_lower_95` | 0.5095 |

### EVIDENCE_GATE
| metric | value |
|---|---|
| `n` | 72 |
| `agreement` | 0.9167 |
| `coverage_high` | 1.0 |
| `selective_accuracy` | 0.9167 |
| `false_act` | 0 |
| `escalation_rate` | 0.75 |
| `error_rate` | 0.0 |
| `latency_ms_median` | 0.0 |
| `stability` | 1.0 |
| `wilson_lower_95` | 0.8299 |

