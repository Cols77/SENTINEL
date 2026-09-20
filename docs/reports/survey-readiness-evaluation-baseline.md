# Survey readiness and evaluation baseline — 2026-09-21

The actual survey loop now runs against evaluator-owned synthetic observations
through all three production policy classes. This baseline exposes incomplete
counting and approval behavior. It does not establish reliable room counting,
provider perception quality, or live model decision quality.

The measured evidence is [survey-scripted-v1.json](../../artifacts/proposal-eval/survey-scripted-v1.json).
Its retained [episode stream](../../artifacts/proposal-eval/survey-run-0apvd3cn/episodes.jsonl)
and F/E/J SQLite databases preserve decisions, requests, current results, legal
candidates, approvals, frames, sightings, objects and reports. Artifacts are local
run outputs; source control retains this report and the reproducible harness.

## Execution and provenance

One synthetic scene was run once per F/E/J arm. F used the production rule
policy; E and J used the actual survey proposer classes with scripted advisor
responses. The report writer and detector responses were deterministic doubles.
There were **zero paid provider calls and zero hardware flights**. Each sweep
received an explicit scripted synthetic approval; this measures neither human
effort nor real filming consent.

```powershell
rtk proxy C:/coding/sentinel/.venv/Scripts/python.exe scripts/survey_sweep.py --survey-root C:/coding/sentinel/.worktrees/survey-counting --scene fixtures/survey_eval/synthetic-off-axis-occlusion-closer-followup.json --output artifacts/proposal-eval/survey-scripted-v1.json --approve-sweeps --max-flights 2
```

The surveyed production checkout reported commit
`a59a3afd15a56406d5ddc2b8f4c9e55d15a57e70` with **an uncommitted change to
`src/sentinel/survey/loop.py`**. Before/after dirty state and source hashes matched
(`source_stable=true`). Consequently, the commit alone cannot reproduce this
run. Its recorded loop SHA256 is
`5d7d590dd8ade039eddfecc2e531750b490c4bbbb7576e727464f0ea1394426d`;
the artifact contains the complete source hash maps. This measured dirty state
supersedes the earlier clean-checkout observation in the handoff.

The scene SHA256 is
`df86c177b538df468277a2ee50001a731c9515629a6471d973ddc4d8cfa0ba24`.
Its independently declared truth contains one phone and one occluded computer.
The far camera is at `(0,0,55)` cm; the near camera physically advances to
`(0,300,55)` cm. The phone at `(150,600)` becomes resolvable only in the near
frame. The computer remains occluded in both frames.

The bridge supplies manifest-derived `SweepOption` objects and selected-option
frames. **Production `generate_options` is not exercised.** Missing or unanalysed
selected observations are unevaluable; another option's frames are never
substituted. This run verifies production loop/store/proposer seams under those
explicit adapters.

## Observed results

| Metric, per arm | F | E, scripted LLM | J, scripted Jev |
|---|---:|---:|---:|
| Emitted and checked reports / episodes | 1/1 | 1/1 | 1/1 |
| Escalated episodes / episodes | 0/1 | 0/1 | 0/1 |
| Selected options | far | far, near | far, near |
| Simulated sweeps / allowed budget | 1/2 | 2/2 | 2/2 |
| Approved sweeps / flown sweeps | 1/1 | 2/2 | 2/2 |
| Nonblank approval descriptions / approvals | 1/1 | 1/2 | 1/2 |
| Policy decisions | 2 | 2 | 2 |
| Illegal tool names / issued calls | 0/2 | 0/2 | 0/2 |
| Matched objects / stored hypotheses (precision) | 0/1 | 1/2 | 1/2 |
| Matched / visible, resolvable truth (recall) | N/A: 0/0 | 1/1 | 1/1 |
| Visible, resolvable truth / whole-scene objects | 0/2 | 1/2 | 1/2 |
| Localised matches / whole-scene objects | 0/2 | 1/2 | 1/2 |
| Automatic site reads; results reads | 1; 2 | 1; 3 | 1; 3 |

Precision and recall concern stored object hypotheses matched one-to-one within
50 cm; they are not bounding-box detection metrics. E/J's follow-up reveals one
additional independently visible and resolvable truth object. Their matched
phone is stored near `(134.24,568.87)` cm instead of `(150,600)` cm, so a match
within tolerance does not establish unbiased geometry. No whole-scene coverage
credit is awarded for the occluded computer.

All reports pass production count/text consistency checks (`report_valid=true`),
yet all assert an exact computer interval `[0,0]`, excluding the one computer in
whole-scene truth: computer count error is **−1 in every arm**. Report validity
therefore does not mean factual count accuracy or adequate coverage. F's phone
bound is `[0,∞)`; E/J report `[0,2]`. These bounds contain the true phone count of
one but do not establish an exact count. Aggregate exact unique-count error is
N/A for all arms.

E/J retain two uncertain phone hypotheses from one actual phone: the initial
unlocalised sighting and the later localised sighting. The spatial duplicate
metric is zero because it cannot match the unlocalised hypothesis. It must not
be read as evidence that cross-frame duplicates were resolved. The reported
`decision_quality=1.0` in all arms is a production field, not an independently
measured camera-planning score; independent coverage above shows the difference.

Each database contains a report and flown sweeps, but its `survey_runs.status`
remains `NEW` while the loop returns `REPORTED`. Persisted terminal-state
consistency is therefore an outstanding production defect, not a passed gate.

## Readiness limits and handoff

Automatic `get_site` and `get_results` calls are exercised, with current stored
results visible in the next policy context. Model-issued read-tool dispatch is
absent from production legal candidates and dispatch branches:
`read_dispatch_supported=false`. Invocation counts do not establish dispatchability.
The illegal-call metric above checks names; it is not comprehensive argument or
approval-boundary validation.

The [production handoff](../handoffs/2026-09-20-survey-evaluation.md) contains
source-reviewed reproductions for fictitious close-look range, off-axis axial
depth interpretation, same-frame object merging, overstated follow-up coverage,
range-spread degradation and malformed advisor shapes. The loop's fixed 55 cm
surface-height assumption restricts scenes the bridge can evaluate. Follow-up
approvals lose their candidate descriptions; both E/J runs reproduce that gap.
Evaluator changes do not repair these production defects.

Independent synthetic manifests and scorer tests cover missing captures,
unanalysed frames, close objects, repeated sightings, occlusion, rotated geometry,
uncertainty and physically meaningful follow-ups. This measured table covers
only the single declared scene; other acceptance cases are not additional
executed production episodes. No numerical model-release threshold is introduced.

Blocked coverage comprises authentic independently labelled scenes, provider
detection precision/recall, fresh model comparisons and repeat variation,
production-generated camera planning, model-dispatched read tools, complete
persisted terminal-state and approval presentation checks, and hardware trials.
The future supported claim remains counting visible supported objects within an
approved surveyed area; unrestricted exploration has not been evaluated.
