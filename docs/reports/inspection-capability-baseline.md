# Inspection capability baseline — 2026-09-21

The real inspection workflow produced **12/15 successful declared outcomes for F and 10/15 each for the LLM and Jev arms**, using one repeat of 15 synthetic cases. There were **zero fresh provider calls**. These results demonstrate wiring, failure retention and scoring behavior; they do not measure model quality or justify release thresholds.

All arms used the same approved two-plan catalogue, fresh SQLite databases, the repository, `AgentLoop` and gateway. F uses existing rules plus a scripted operator policy for judgment. The other arms use the actual `LlmProposer` and `JevProposer` behind architecture E with scripted provider-shaped responses. Operator confirmation and rejection are simulated approval tests; their counts do not measure human effort.

| Metric | F | LLM behind E | Jev behind E |
|---|---:|---:|---:|
| Declared outcome success / cases | 12/15 (80%) | 10/15 (66.7%) | 10/15 (66.7%) |
| Raw COMPLETED terminal states / cases | 9/15 | 10/15 | 10/15 |
| Correct accepted proposals / accepted proposals | N/A (0/0) | 9/10 | 9/10 |
| Accepted proposals / proposal attempts (coverage) | N/A (0/0) | 10/15 | 10/15 |
| Successful expected escalation / escalation cases | 3/3 | 2/3 | 2/3 |
| Unsupported-request false proposals / unsupported cases | 0/1 | 1/1 | 1/1 |
| Rejected gateway calls / gateway attempts | 1/108 | 1/117 | 1/117 |
| Observed approval-boundary violations / checked accepted effects | 0/18 | 0/20 | 0/20 |
| Recovery to completed state after rejected call / cases | 1/15 | 1/15 | 1/15 |
| Policy turns | 51 | 54 | 54 |
| Retries after rejected result / policy turns | 1/51 | 1/54 | 1/54 |
| Additional proposal attempts beyond the first in a case | 0 | 1 | 1 |
| Simulated adapter attempts / fresh provider calls | 0 / 0 | 15 / 0 | 15 / 0 |
| Total proposal-attempt latency / attempts | N/A (0/0) | 6,255 ms / 15 | 643 ms / 15 |
| Complete reason-and-facts records / recommendations | N/A (0/0) | 10/10 | 10/10 |
| Unique simulated confirmations / rejections / duplicate recoveries | 19 / 1 / 3 | 21 / 1 / 3 | 21 / 1 / 3 |

Outcome success requires the declared terminal state and persisted, accepted effects, including matching plans, evidence, tasks, assignment targets and event order. Escalation and approval-wait cases can therefore succeed without a COMPLETED state. Conversely, recovery to COMPLETED cannot erase a rejected command or transport failure. The approval-boundary count checks accepted task creation against prior approval of its evidence's plan, and assignment confirmation against an explicit CONFIRM targeting a previously proposed task. Missing effects on failed executions are scored as failures, not counted as accepted approval violations.

The latency values measure local scripted adapter and audit work, not network or model latency. Reason-and-facts presence does not establish factual support: **rationale factual support remains unassessed**. The LLM has 0/15 and Jev 2/15 literal `escalate` raw choices; these are not credited as valid proposer abstentions because the current proposer contracts do not accept those shapes. Successful escalation outcomes in this run include the scripted fallback operator. Five of 15 proposal attempts in each E arm were not accepted.

Both supported plan-selection cases completed with qualifying evidence producing the required task and assignment. All three pending-approval executions made zero proposal calls and zero policy turns. Rejection followed by a different legal plan, duplicate recovery and audit-write interruption/retry recovered successfully in every arm. Malformed or illegal proposals were retained and rejected by the proposer, then recovered through the scripted operator; successful workflow recovery does not give those proposals quality credit.

Missing evidence, exhausted turn budgets and stale-evidence gateway rejection failed all arms. Injected provider failure additionally failed both E arms, even where the workflow recovered. A confident supported-plan proposal for unsupported computer counting failed both E arms. These deliberate negative cases explain the percentages; they are not population estimates or evidence that F is a better model.

The fixture loader also inspected existing hand-labelled decision data. The v1 fixture has 24/53 eligible request adaptations and 29/53 rows outside SELECT_PLAN. The v2 fixture has **0/102 eligible rows**: 80 use an incompatible or draft candidate catalogue, including escalation rows, and 22 are outside SELECT_PLAN. These independent labels were not executed in this synthetic baseline. Their full original decision contexts are not asserted equivalent to the adapted new-run requests. Draft and unlabelled data cannot support quality claims.

Execution evidence is retained locally in [the original run](../../artifacts/proposal-eval/inspection-scripted-v1/manifest.json), with per-case requests, results, tool outcomes, audits, final state and SQLite snapshots. The original reports remain historical and unchanged. Their earlier success counts included recovery after a rejected gateway call. The canonical values above come from [a separate retained-outcome rescore](../../artifacts/proposal-eval/inspection-rescored-v2/report.md) with [summary](../../artifacts/proposal-eval/inspection-rescored-v2/summary.json) and [manifest](../../artifacts/proposal-eval/inspection-rescored-v2/manifest.json). These ignored artifacts remain in the worktree; they are not embedded in the Git commit.

| Provenance | Value |
|---|---|
| Execution revision / dirty | `63e59ce6ea0375dd9675ec3c7472612b81f7a763` / false |
| Scoring revision / dirty | `d285d5552eab62dd1c8acfb2f5b0e253023b69dc` / true; uncommitted reporting work |
| Execution manifest SHA-256 | `662a37fd4bea68f9d58975331a7ca71a973760278be945a4f46966fc47d1afee` |
| Rescore manifest SHA-256 | `994c3e2ae70e8bccfea179399303ea8e3bb7d9cad6cd4a992c39e27e58ca0d0b` |
| Scorer SHA-256 | `bb128611f8f761ed72a06c5d25396c20b4599e3924091c505cb474ecff0f2aa6` |
| Fixture SHA-256 | `1e51973a45aa2c1993fee7ed1f37f2973cb59405080f3b6c2288ca250ad487a4` |
| Approved catalogue SHA-256 | `62c4540aed6a573b90213e7362e712b601de4786b84a59cc1965a3ffa30422b6` |

The rescore validates the complete 45-outcome matrix and unchanged catalogue, hashes every authoritative input, and retains append-only rescore records. It runs no policies or providers. Reproduce it into a fresh destination:

```powershell
rtk proxy C:/coding/sentinel/.venv/Scripts/python.exe scripts/rescore_proposal_report.py --source artifacts/proposal-eval/inspection-scripted-v1 --output artifacts/proposal-eval/inspection-rescored-next
```

The execution manifest retains model configuration, prompt revisions, Jev thresholds (0.6 confidence, 0.15 margin), a 626-attempt ceiling and one repeat. The run consumed 30 simulated attempts, with no budget exhaustion. Requested identities were LLM `offline` and Jev `typesafe/jev-1.13-20260917`; resolved identities remain unverified because no provider was called. Historical direct-adapter reports stay a separate regression suite and are not pooled here.

Fresh model capability measurement remains pending an independently labelled case selection and manifest-bound live authorization, with three repeats per case and explicit budgets. No live batch, autonomous-action promotion or hardware flight was performed. Computer counting remains outside this inspection catalogue; see the separate [survey readiness baseline](survey-readiness-evaluation-baseline.md).
