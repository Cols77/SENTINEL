# Inspection and survey evaluation

`scripts/proposal_sweep.py` is the canonical inspection capability entry point.
It runs the real coordinator policies, proposer classes, AgentLoop, gateway and
SQLite repository with simulated worker ports. The direct-adapter tests and old
shadow sweeps remain separate legacy regression suites. Historical reports are
unchanged and must not be pooled with this baseline.

The three arms are existing rules with a scripted operator policy (`F`), the
actual `LlmProposer` (`llm`), and the actual pinned `JevProposer` (`jev`). The
operator policy and confirmations are simulation inputs, not measured human
effort. Every case has a fresh database and policy instance.

## Run modes

All examples use the existing Windows repository environment. Tests and runs
are serial. The default command is a dry run; it creates no output directory
and initializes no provider.

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --dry-run --repeats 1
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --mode scripted --execute --repeats 1 --output artifacts/proposal-eval/my-scripted-run
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --mode recorded --execute --repeats 1 --recording artifacts/proposal-eval/my-scripted-run/raw.jsonl --output artifacts/proposal-eval/my-recorded-run
```

Scripted runs test wiring and scoring through real proposer classes with fake
transports. Recorded runs replay captured responses and validate their requests;
neither is a fresh model-quality measurement. Output directories must be new,
so a rerun cannot overwrite an earlier manifest or report.

A live dry run uses the existing LLM configuration loader and pinned Jev model.
Live execution requires three repeats, `SENTINEL_PROPOSAL_EVAL_CONSENT` equal to
the printed manifest digest, and each selected provider's existing consent token
(`SENTINEL_JEV_PROPOSER_CONSENT` or `SENTINEL_LLM_PROPOSER_CONSENT`). Changing
source, fixtures, configuration, repeats or budget changes the digest. No tool
sets these consent values automatically. The optional `--cost-per-call-usd`
is an operator-supplied estimate; otherwise estimated cost is explicitly unknown.
Budgeted adapter attempts include errors; actual fresh HTTP calls are counted
separately. Provider SDK retries are disabled; further attempts consume budget.

## Evidence and interpretation

Each run retains `manifest.json`, append-only `raw.jsonl`, `summary.json` and
`report.md`. Each case retains its real SQLite database, captured gateway
commands/outcomes and audit records. `outcome.json` is the intermediate
coordinator outcome; `final-outcome.json` is authoritative after transport
failures, replay integrity, budget checks and provider telemetry are applied.
Unknown resolved model identity is reported as unverified, even if a requested
model name is pinned.

Scoring checks persisted plan content, evidence/task/assignment targets, accepted
commands, approval identity and event order. Rejected commands, wrong plans,
missing evidence, incomplete execution and missing records fail their gates.
An operator-recovered task does not prove the model selected a correct proposal.
Invalid or unavailable answers cannot establish model-quality escalation success.
Rationale completeness is separate from factual support, which is unassessed.

The default is the fixed 12-case
`fixtures/proposal_eval/generated-requests-v1.json` pack. Its reviewed literal
SHA-256 pin is
`1b1089694fd74a82924c020bacfefc2e7a8b905077f05b8bbe01135777822f70`.
The runner computes the file digest at runtime and requires both the exact
canonical path and that literal digest; a matching filename or a self-consistent
manifest value is never treated as pin evidence. The cases are explicitly
synthetic. The optional existing hand-label
importer preserves labels and describes its request-only adaptation; it excludes
incompatible context, draft candidates, pending labels and other decision points
with reasons. It does not manufacture independently labelled observations.
No release threshold, model-default change or autonomous-action promotion follows
from this baseline.

## Reviewed live gate

Both model arms bind the shared SELECT_PLAN semantic revision
`select_plan.complete_constraints.v4`. It treats catalogue entries as future
work, so empty pre-execution plan, task, detection, and result fields are not
reasons to escalate. Jev's candidate criteria describe plan capabilities as
scheduled future actions, including marker-verification steps derived from the
catalogue, and make escalation conditional on no complete match,
contradictory constraints, or insufficient request detail. Confidence and
margin gates still apply uniformly to plan and escalation choices. The LLM arm additionally requires
`SENTINEL_LLM_PROMPT_REVISION=prompt-v4-complete-constraints` and
`SENTINEL_LLM_TOOL_POLICY_REVISION=legal-tools-v2`. Jev's policy thresholds are
recorded separately as `act_at=0.60` and `min_margin=0.15`.
Reports keep accepted explicit escalations separate from raw escalation and
plan choices. Proposal coverage uses every provider attempt on supported cases,
so provider failures, malformed answers, and model escalation cannot disappear
from its denominator.
The Jev manifest retains the historical `configurations.jev.prompt_revision`
field for report-reader compatibility; it has the same value as the explicit
`decision_prompt_revision`. The runner rejects any runtime drift from the
required semantic revision before authorization, provider construction, or
output creation. The manifest schema version remains `proposal-eval-run.v1`.

Each live arm is authorized separately. First run a dry run with the exact
provider identity reviewed by the operator and a concise decision note:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/proposal_sweep.py --mode live --arms jev --max-calls 36 --cost-per-call-usd 0.025 --expected-resolved-model jev=typesafe/jev-1.13-20260917 --identity-decision-note "jev=reviewed pinned Jev release" --dry-run
```

Copy the printed `authorization_sha256` into
`SENTINEL_PROPOSAL_EVAL_CONSENT`, set the existing provider-specific consent,
then repeat the identical arguments with `--execute` and a new `--output` path.
The LLM form uses `--arms llm`, for example
`--expected-resolved-model llm=z-ai/glm-5.3` and a matching
`--identity-decision-note llm=...`. Identity comparison is exact:
`~typesafe/jev-latest` does not verify `typesafe/jev-1.13-20260917`, and
`z-ai/glm-5.3-flash` is not pooled with `z-ai/glm-5.3`.

The pass/fail gate applies only to a fresh live run of exactly one model arm on
the pinned path and digest, with three repeats, 36 terminal trials, one provider
request/result pair and one proposal attempt/outcome per trial, and exactly 18
supported plus 18 escalation-required trials. It reports supported correctness,
safe escalation and unsupported false proposals with fixed `/18` denominators;
architecture-F recovery receives zero gate credit. Any observed identity
mismatch fails an otherwise applicable gate. Scripted, recorded, partial,
multi-arm or alternate-fixture runs are always marked non-applicable and cannot
claim a passing live gate.

The manifest binds requested and expected identities plus the review note before
authorization. Observed identities are append-only run evidence and never rewrite
that manifest. Reports retain provider calls, nullable prompt/completion/total
tokens, latency, and actual-call cost estimates. Missing usage stays null. Cost
remains an operator estimate rather than provider billing evidence, and model
identity metadata is provider-asserted rather than cryptographic attestation.

## Survey scope

The current offline results are published separately in the
[inspection capability baseline](reports/inspection-capability-baseline.md) and
[survey readiness/evaluation baseline](reports/survey-readiness-evaluation-baseline.md).
The inspection report distinguishes execution from subsequent scoring revisions;
the original artifacts remain historical and unchanged.

Initial implementation verification: **857 passed, 3 skipped** in the serial pytest
suite on 2026-09-21. The retained local log is
`artifacts/proposal-eval/full-pytest.log`; offline capability outcomes and
production survey limits remain distinct from regression test results.

The subsequent [integration review](reports/2026-09-21-evaluation-integration-review.md)
records corrected replay, validation and failure-retention boundaries plus the
current verification. The [12 generated approved-catalogue requests](../fixtures/proposal_eval/generated-requests-v1.md)
can be selected with `--fixtures fixtures/proposal_eval/generated-requests-v1.json`.
The separate [24 completed v2 requests](../fixtures/decisions_v2/README.md)
retain generated provenance and depend on the draft catalogue; they are not
eligible for the current production inspection benchmark.

`sentinel.survey_eval` owns independent synthetic truth, projection, matching and
strict option-specific replay. Its arithmetic tests do not prove production
policy quality. Whole-scene coverage, observed-object accuracy, completion,
escalation and report validity are distinct. Missing reports have no validity
score; lower bounds do not become exact count errors.

The bridge can run the separate survey worktree's actual loop with synthetic
frames and scripted advisors, retaining each arm's database and source hashes:

```powershell
rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe scripts/survey_sweep.py --survey-root C:/coding/sentinel/.worktrees/survey-counting --scene fixtures/survey_eval/synthetic-off-axis-occlusion-closer-followup.json --output artifacts/proposal-eval/my-survey-run.json --approve-sweeps --max-flights 2
```

This command simulates approval for each replayed sweep. It uses independently
declared scene options, so it does not validate production option generation.
Automatic site/results reads are captured and visible to later policy decisions;
model-issued read-tool dispatch remains blocked coverage. The report records
dirty source state as well as revision and before/after hashes.

See [the survey handoff](handoffs/2026-09-20-survey-evaluation.md) for source
revisions, reproduced production limitations and the integration seam. Real
scene labels, hardware trials and unrestricted room exploration remain outside
this implementation. Hardware trials require separate authorization.
