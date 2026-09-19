# Jev Experiment Note for SENTINEL

> Status: exploratory / non-blocking  
> Target window: before 25 September 2026  
> Goal: evaluate whether TypeSafe AI's Jev can improve bounded semantic decisions in SENTINEL without becoming a dependency of the core demo.

## 1. Why Jev is relevant to SENTINEL

SENTINEL already separates several kinds of intelligence:

- **Perception**: understand what the drone/camera observed.
- **State management**: maintain detections, hazards, assignments, mission state, and history.
- **Reasoning / planning**: propose what to do next.
- **Governance / policy**: decide what actions are permitted and when human confirmation is required.
- **Execution**: dispatch a drone, assign a patroller, update state, etc.

Jev is potentially relevant to the layer between state and policy: **bounded semantic judgment**.

TypeSafe describes Jev as a "System One Model": it consumes shared state plus predefined questions and returns typed probabilistic decisions rather than free-form generated text. Its current decision primitives include boolean judgments, choices among fixed alternatives, and scores over ordered scales.

This is a good fit for questions such as:

- Is this detection operationally relevant?
- Is this observation likely to represent a trail-opening hazard?
- Does this situation require human review?
- What operational priority should this observation receive?
- Should the system ignore, monitor, inspect, escalate, or assign?
- Is the evidence sufficient to advance the workflow?

These are not good reasons to use Jev:

- image/object detection itself;
- drone control or collision avoidance;
- deterministic mission-envelope checks;
- exact state-machine transitions;
- schema validation;
- anything that can be proved mechanically;
- open-ended planning or explanation.

The intended architecture is therefore:

```text
Drone / Camera
      |
      v
Vision model / VLM
      |
      v
Structured observation
      |
      v
Mountain State / Mission State
      |
      +------------------------------+
      |                              |
      v                              v
Generative LLM                  Jev experiment
planning / explanation         bounded judgments
      |                              |
      +---------------+--------------+
                      |
                      v
             Deterministic policy
                      |
             human confirmation
             where required
                      |
                      v
                  Actions
```

Jev should provide **evidence to SENTINEL policy**, not own the policy.

## 2. Current availability

As of 17 September 2026:

- TypeSafe announced Jev in early access in mid-September 2026.
- Direct TypeSafe access is still described as early access / waitlist-based.
- Jev is also available through **Vercel AI Gateway** under the model identifier:

```text
typesafe-ai/jev
```

For the hackathon experiment, Vercel AI Gateway is currently the lowest-friction path because it avoids waiting for direct TypeSafe onboarding.

Jev is exposed in AI SDK 7 through the experimental evaluation API.

Current Vercel documentation indicates that `ai@7.0.105` or newer supports this API.

## 3. Recommended access path for the experiment

### Option A — Vercel AI Gateway

Recommended for SENTINEL because access is already exposed publicly through the gateway.

High-level setup:

1. Create or use a Vercel account.
2. Enable AI Gateway / obtain an AI Gateway API key.
3. Add the credential to the SENTINEL environment.
4. Install/update the Vercel AI SDK.
5. Call `typesafe-ai/jev` using the evaluation API.

Example environment convention:

```bash
AI_GATEWAY_API_KEY=<secret>
```

Use the exact environment variable expected by the installed Vercel AI SDK / gateway provider in the implementation environment rather than hard-coding credentials.

Example package setup:

```bash
pnpm add ai@latest
```

Minimal TypeScript shape:

```ts
import { experimental_evaluate as evaluate } from 'ai';

const result = await evaluate({
  model: 'typesafe-ai/jev',
  state: {
    detection: {
      type: 'rock',
      confidence: 0.91,
      approximate_size: 'large',
      trail_relation: 'on_trail',
    },
    mission: {
      phase: 'opening_inspection',
    },
  },
  questions: {
    hazardRelevant: {
      type: 'boolean',
      instructions:
        'Is this observation relevant to trail-opening safety?',
    },

    priority: {
      type: 'choice',
      options: ['ignore', 'low', 'medium', 'high'],
      instructions:
        'What operational priority should this observation receive?',
    },

    needsHumanReview: {
      type: 'boolean',
      instructions:
        'Should a patroller review this observation before an intervention is taken?',
    },
  },
});
```

Treat the exact API shape as version-sensitive because the Vercel API is currently marked experimental.

### Option B — Direct TypeSafe API

TypeSafe also provides official Python and JavaScript SDKs.

The Python SDK uses a `TYPESAFE_API_KEY` environment variable and exposes the same general System One concepts.

Example:

```bash
uv add typesafe-sdk
```

```bash
export TYPESAFE_API_KEY=<secret>
```

Conceptual example:

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

with TypeSafeClient() as client:
    response = client.system_one(
        state={
            "detection": {
                "type": "rock",
                "confidence": 0.91,
                "trail_relation": "on_trail",
            }
        },
        questions={
            "hazard_relevant": Noul(
                instructions="This detection represents an operational trail-opening hazard."
            ),
            "priority": Choice(
                instructions="Select the operational priority.",
                criteria={
                    "ignore": None,
                    "low": None,
                    "medium": None,
                    "high": None,
                },
            ),
        },
    )
```

Direct TypeSafe access may require early-access approval, so it should not block the experiment.

## 4. Proposed SENTINEL integration boundary

Do **not** put Jev inside the flight-control path.

A safe experiment boundary is:

```text
VLM / detector output
        |
        v
normalize observation
        |
        v
Mountain State DB
        |
        v
Jev evaluation
        |
        v
judgment object
        |
        v
SENTINEL deterministic policy
        |
        +--> ignore
        +--> collect more evidence
        +--> request human confirmation
        +--> propose patroller assignment
        +--> escalate
```

Suggested interface:

```ts
type HazardJudgment = {
  hazardRelevant: {
    value: boolean;
    probability: number;
  };

  priority: {
    value: 'ignore' | 'low' | 'medium' | 'high';
    probabilities: Record<string, number>;
  };

  needsHumanReview: {
    value: boolean;
    probability: number;
  };
};
```

Then keep policy code explicit:

```ts
if (!judgment.hazardRelevant.value) {
  return IGNORE;
}

if (judgment.needsHumanReview.probability > HUMAN_REVIEW_THRESHOLD) {
  return REQUEST_HUMAN_REVIEW;
}

return PROPOSE_ASSIGNMENT;
```

Thresholds should be configuration, not embedded inside prompts.

## 5. First experiment

The first experiment should be intentionally small.

### Hypothesis

For bounded operational judgments over structured SENTINEL state, Jev may provide:

- lower latency than a general-purpose LLM;
- lower cost;
- more predictable typed outputs;
- useful confidence/probability information;
- simpler integration into deterministic policy.

### Candidate judgment task

Given a normalized observation plus mission context:

1. Is the observation relevant to trail-opening safety?
2. What priority should it receive?
3. Should a human review it before action?
4. Is more evidence required?

Example input:

```json
{
  "observation": {
    "source": "drone_1",
    "object": "rock",
    "perception_confidence": 0.87,
    "size": "medium",
    "position_relation": "inside_trail_corridor",
    "duplicate_of_existing_detection": false
  },
  "mission": {
    "workflow": "daily_opening_inspection",
    "trail_status": "closed_pending_inspection"
  },
  "operations": {
    "patroller_available": true
  }
}
```

Expected Jev output should remain bounded:

```text
hazard_relevant      -> boolean + probability
priority             -> ignore | low | medium | high + distribution
human_review_needed  -> boolean + probability
more_evidence_needed -> boolean + probability
```

No natural-language explanation is required for the first test.

## 6. Comparison baseline

Do not evaluate Jev in isolation.

Run the same cases through:

1. the current/general-purpose LLM approach;
2. Jev;
3. optionally a deterministic rule baseline where feasible.

Example:

```text
               same SENTINEL state
                       |
        +--------------+--------------+
        |              |              |
        v              v              v
 deterministic     LLM judge         Jev
    rules
        |              |              |
        +--------------+--------------+
                       |
                       v
                  evaluation
```

## 7. Test cases

Start with approximately 20-50 manually defined situations. Expand only if the signal is interesting.

Include at least:

### Clear negatives

- tiny pebble outside the trail;
- known static object already marked safe;
- duplicate detection already resolved;
- low-confidence visual artifact.

### Clear positives

- large rock blocking the trail;
- fallen branch across the trail;
- object inside a critical opening-inspection zone;
- hazard not yet assigned to a patroller.

### Ambiguous cases

- medium rock near the trail boundary;
- low-confidence rock inside the trail;
- apparently dangerous object with poor image quality;
- conflicting observations from two passes;
- hazard whose position is uncertain;
- object already assigned but not confirmed resolved.

### Workflow-sensitive cases

- trail still closed during opening inspection;
- trail already open;
- patroller already en route;
- no patroller currently available;
- observation outside the mission envelope.

## 8. Metrics

The most important metrics are not raw benchmark accuracy alone.

Track:

### Decision quality

- agreement with a human/reference label;
- false-safe rate;
- false-escalation rate;
- confusion matrix by decision type.

For SENTINEL, **false-safe decisions should be treated as more serious than unnecessary review**.

### Calibration

Jev's main architectural claim is not just classification but meaningful probabilities.

For predictions around 90% confidence, approximately 90% should be correct over a sufficiently large and representative sample.

Useful metrics:

- Brier score;
- expected calibration error;
- reliability diagram;
- accuracy by confidence bucket.

The initial hackathon experiment will probably be too small to establish strong statistical calibration claims, but the instrumentation should already capture the data.

### Systems metrics

Record:

- end-to-end latency;
- request failure rate;
- input size;
- cost;
- output validity;
- consistency across repeated calls.

## 9. Experiment logging

Persist every comparison.

Suggested record:

```json
{
  "case_id": "rock_017",
  "timestamp": "...",
  "state": {},
  "reference": {
    "hazard_relevant": true,
    "priority": "high",
    "human_review_needed": true
  },
  "jev": {
    "model": "typesafe-ai/jev",
    "latency_ms": 0,
    "answers": {}
  },
  "llm_baseline": {
    "model": "...",
    "latency_ms": 0,
    "answers": {}
  }
}
```

This allows the experiment to become a reusable evaluation harness rather than a one-off demo integration.

## 10. Demo integration rule

Jev must remain **non-blocking** for the 25 September demo.

Recommended strategy:

```text
production/demo path
        |
        +--> established SENTINEL logic

shadow experiment
        |
        +--> Jev
        |
        +--> log comparison
```

Once confidence in the integration is sufficient, Jev may optionally influence a non-safety-critical branch.

Do not make the success of the drone demo dependent on:

- TypeSafe availability;
- Jev availability;
- gateway latency;
- experimental SDK behavior;
- probability thresholds that have not been tested.

A network or provider failure must degrade cleanly to the existing SENTINEL path.

## 11. Potential demo story if results are good

If the experiment succeeds, the architecture can be described as:

```text
Perception model
    "What is happening?"

Generative reasoning model
    "What might we do?"

Jev / bounded decision model
    "How strongly does the current state satisfy these operational conditions?"

SENTINEL policy
    "What are we allowed to do?"

Physical system / human operator
    "Execute."
```

This reinforces the product story that SENTINEL is not simply an LLM connected to a drone.

It is an operational system that separates:

- perception;
- reasoning;
- probabilistic semantic judgment;
- deterministic governance;
- physical execution.

## 12. Risks and caveats

### Jev is new

Independent evidence is currently limited. TypeSafe's published performance numbers are vendor-reported and should not be treated as established third-party benchmark results.

### Typed output does not imply semantic correctness

Jev can avoid invalid output types while still choosing the wrong valid answer.

For example:

```text
priority ∈ {low, medium, high}
```

may be guaranteed structurally, while choosing `low` instead of `high` remains a model error.

### Calibration must be tested on our domain

Good aggregate calibration does not imply calibration on:

- mountain operations;
- VLM-generated observations;
- unusual hazards;
- distribution shift;
- sparse or ambiguous evidence.

### Upstream perception still matters

Jev receives state; it does not make bad perception good.

Garbage or overconfident VLM detections can still lead to incorrect downstream decisions.

### Do not use it for hard real-time control

The model belongs in high-level workflow judgment, not stabilization, collision avoidance, geofencing, or low-level motion control.

## 13. Nice-to-have abstraction

Avoid coupling SENTINEL directly to Jev-specific concepts in core business logic.

Potential abstraction:

```ts
interface BoundedJudge {
  boolean(
    state: unknown,
    instruction: string
  ): Promise<{
    value: boolean;
    probability: number;
  }>;

  choice<T extends string>(
    state: unknown,
    instruction: string,
    options: readonly T[]
  ): Promise<{
    value: T;
    probabilities: Record<T, number>;
  }>;
}
```

Adapters could then be implemented for:

```text
BoundedJudge
   |
   +-- Jev
   +-- structured-output LLM
   +-- deterministic test double
```

This would let the experiment survive even if Jev itself is not adopted.

## 14. Proposed definition of done

The Jev experiment is considered useful if, before demo freeze:

- [ ] A Jev request can be made from the SENTINEL dev environment.
- [ ] At least one bounded operational judgment is implemented.
- [ ] The same judgment can be evaluated with the existing LLM baseline.
- [ ] At least 20 representative cases are replayable.
- [ ] Latency, output probabilities, and decisions are logged.
- [ ] False-safe cases can be inspected explicitly.
- [ ] Provider failure has a clean fallback.
- [ ] Jev is not required for the core drone demo.
- [ ] A go/no-go decision is recorded based on evidence.

## 15. Decision after experiment

Possible outcomes:

### Adopt in the demo

Only if the integration is stable and the result visibly improves the product or engineering story.

### Keep as shadow evaluation

Likely the safest outcome for the hackathon: demonstrate or report the experiment without putting Jev on the critical execution path.

### Defer

If access, SDK stability, model behavior, or experiment results are weak, retain the evaluation harness and revisit after the hackathon.

The experiment is valuable even if Jev is rejected, because it forces SENTINEL to define a clean boundary between:

```text
deterministic facts
semantic bounded judgments
open-ended reasoning
policy authority
```

That boundary is useful independently of any specific model.

## References

- TypeSafe AI — Introducing System One Models and Jev: https://typesafe.ai/blog/introducing-system-one-models-and-jev
- TypeSafe AI documentation: https://docs.typesafe.ai/
- TypeSafe Python SDK: https://github.com/typesafe-ai/typesafe-sdk-python
- TypeSafe System One LLM adapter: https://github.com/typesafe-ai/system-one-adapter-python
- Vercel — Jev on AI Gateway: https://vercel.com/changelog/typesafe-ai-jev-now-available-on-ai-gateway
- Vercel AI Gateway model page: https://vercel.com/ai-gateway/models/jev

## 16. Experiment status and go/no-go (2026-09-18)

Recorded as of 18 September 2026, before demo freeze (25 September 2026).

### Adapter status

- `BoundedJudge` port implemented (`src/sentinel/judgment.py`), with:
  - `StaticJudge` — deterministic test double;
  - `JevJudge` — adapter with fail-closed behaviour (missing API key env, unparseable
    response, or choice value outside options -> `JudgeUnavailable`).
- `state_snapshot` — pure versioned `judgment_context.v1` projection for System One
  context, so judgment inputs are explicit and reproducible.
- Replay harness `run_cases` (`src/sentinel/evaluation.py`) logs every comparison to
  JSONL and counts `false_safe`, `false_escalation`, `unavailable` explicitly.
- Contract `jev-adapter-config.v1` with `path_role` locked to
  `SHADOW_EVALUATION_ONLY`.
- Finding from the live smoke: Jev is NOT exposed through chat/completions; it is
  served on a dedicated decisions endpoint with noul/choice/score types
  (`/api/alpha/decisions`). The adapter's response parsing still assumes a
  chat-completions shape, so the happy path does not yet match the real endpoint.
  A fix card (t_807e8b3f) is open. No adapter code has touched any production or
  demo path.

### Static-baseline replay (21 cases, section-7 fixtures)

`StaticJudge(hazard_relevant=(True, 0.9))` — a deliberately escalation-biased
double — over the 21 judgment evaluation cases:

```text
total: 21   false_safe: 0   false_escalation: 5   unavailable: 0
log: artifacts/jev/static-baseline.jsonl (21 records)
```

This validates the harness and the metric definitions (a false-safe is counted
only when the reference label is `hazard_relevant=True` and the judge says
`False`; escalation-bias produces false_escalations, never false_safes), and
reproduces the section-8 rule that false-safe decisions are the more serious
failure class. It says nothing about Jev's real accuracy — that requires the
live path.

### Live result (consented smoke, single case)

Operator consent was recorded in chat on 2026-09-18; one paid live call was made:

```text
endpoint: /api/alpha/decisions (OpenRouter)
model:    typesafe/jev-1.13-20260917
hazard_relevant: 0.57 noul
priority: low (0.59); distribution {ignore 0.18, low 0.59, medium 0.14, high 0.09}
confidence: 0.45; latency 671 ms; ~1.54e-05 USD; 367 in / 65 out tokens
```

The call returned valid typed output (boolean + probability, choice +
distribution), confirming the System One output contract on our domain payload.
The single case is far too small for any accuracy or calibration claim (section 8).
Full 21-case live replay and the general-LLM baseline comparison (section 6) have
NOT been run — they remain gated behind explicit operator consent and the adapter
endpoint fix.

### Decision (section 15): Keep as shadow evaluation

- Jev stays off the demo critical path; `path_role=SHADOW_EVALUATION_ONLY` is
  enforced by contract, and a provider/network failure degrades cleanly to the
  existing SENTINEL path (section 10 satisfied).
- Go/no-go on live replay vs. baseline is deferred until the adapter fix
  (t_807e8b3f) lands and a consented full replay can run.
- The experiment already yields value independently of Jev adoption: the
  `BoundedJudge` port, versioned judgment context, and false-safe/false-escalation
  harness make the boundary between deterministic facts, bounded judgments, and
  policy authority explicit and testable (section 15).

### Adapter update: batched evaluate() landed (2026-09-18, later)

The fix card (t_807e8b3f) has landed: the adapter now implements the
research-verified batched design. `BoundedJudge.evaluate(state, questions)` sends
ONE POST to `/api/alpha/decisions` per judgment batch, instead of one request per
question:

- booleans with criteria are mapped to noul; the priority question uses a
  Choice+Score pair (operator decision); `human_review_needed` and
  `more_evidence_needed` also map to noul;
- boolean values are threshold policy: the boolean threshold comes from the
  `boolean_threshold` config (default 0.5), not embedded in prompts;
- confidence is logged alongside the probabilities, so the go/no-go can weigh
  TypeSafe's three-band routing against plain thresholds;
- `QUESTION_SET` is a module-level configuration constant in the evaluation
  module.

TypeSafe documentation facts underpinning this design: questions in a batch are
evaluated in parallel (near-zero marginal latency per extra question); Jev has no
boolean primitive (hence noul); P(noul) and 1-P(negated noul) are not
complementary, so each direction must be judged on its own noul probability; and
state should be kept minimal per call to avoid context rot.

Final offline verification: 129 tests passed (`uv run pytest`), and the static
replay with the batched evaluate per case returned:

```text
total: 21   false_safe: 0   false_escalation: 5   unavailable: 0
```

## 17. Consented live dual replay — Jev vs LLM baseline (2026-09-19)

Consented by the operator (2026-09-19; ~45 paid live calls, est. < 0.01 USD).
Both arms replayed the IDENTICAL 21-case fixture (`fixtures/judgment/cases.v1.jsonl`),
same state payloads, same `QUESTION_SET`; one batched call per case per arm.
Jev via `typesafe/jev-1.13-20260917` (OpenRouter `/api/alpha/decisions`), baseline
LLM `z-ai/glm-5.3-flash` via OpenRouter chat/completions JSON mode.
Artifacts: `artifacts/jev/jev-replay.jsonl`, `artifacts/jev/llm-replay.jsonl`,
`artifacts/jev/dual-replay-summary.json` (not committed).

### Judgment-suite comparison (21 cases)

| Metric | Jev | LLM (glm-5.3-flash) | Δ (LLM − Jev) |
|---|---|---|---|
| Cases replayed | 21 | 21 | — |
| Unavailable | 0 | 0 | 0 |
| False-safe (ref True → judged False) | 9 | 6 | −3 |
| False-escalation (ref False → judged True) | 0 | 0 | 0 |
| Mean latency (ms, per batched case) | 428 | 17 015 | +16 587 |

Per-question agreement (LLM vs Jev answers, identical questions):
`hazard_relevant` 2/21 (0.095), `priority` 0/21, `priority_scored` 0/21,
`human_review_needed` 0/21, `more_evidence_needed` 0/21. Per-case agreement
rate: 0/21 — the two judges disagree on essentially every answer. Neither arm
is close to the reference labels' distribution (Jev 9 false-safe vs static
baseline's 0), so the honest go/no-go reading is: **Jev is NOT yet decision-grade
on this fixture, and the LLM baseline is no better (6 false-safe) at ~40× the
latency.** Jev's latency/cost advantage (one batched decisions call, usage.cost
≈ $1e-5/batch) is real but immaterial while accuracy is below the deterministic
static baseline.

Caveats: (1) LLM 'confidence' is the model's SELF-assessment and is NOT
calibrated; Jev probabilities are RLCD-trained. (2) Both arms' false-safe rates
(9/21 and 6/21) are worse than the conservative static baseline (0 false-safe,
5 false-escalations) — at this fixture size, the deterministic rules win.
(3) LlmJudge required a system-instruction fix after two live attempts failed
fail-closed: the original instructions omitted per-type answer shapes, so models
omitted the `probability` field on noul questions; the fix (explicit shapes)
kept all 151 tests green and both failed runs were preserved
(`llm-replay.jsonl.failed-attempt*`, `*.noncompliant-glm-5.3`).

### WP1 scenario replay through Jev (B10 pass criteria)

`artifacts/jev/jev-scenario-replay.json` — real WP1 `live_scenarios.json`
through the live Jev decisions API:

```text
turns: 4/6 passed; scenarios: 1/3 passed (exit 1)
```

- `initial_plan_one_station`: turn 0 passed (correct plan argument chosen);
  turn 1 failed (chose `inspection.propose_plan` instead of `wait_for_event`).
- `qualifying_evidence_to_task`: turn 0 failed (chose `patroller.rank_for_task`
  instead of `task.create` — plausibly a legal-tool sibling, still a miss);
  turn 1 passed with the right task id.
- `approval_wait`: passed (both turns `wait_for_event`).

Scenario pass rate 1/3 (vs static offline replay 0/3) — Jev understands the
tool surface partially but not the workflow sequencing; not a go for
coordinator-facing tool choice on this evidence.
