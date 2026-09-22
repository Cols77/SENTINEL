# Jev agentic room session

Status: design for review; production behavior is not implemented by this document.

## Intent and delivery boundary

Provide a prompt-driven room agent on `feat/jev-agentic-room-loop`, created from integration commit `bca315f`. Rebase onto `integration/survey-demo` at integration checkpoints, recording the upstream revision and rerunning serial checks. Do not change the shared integration worktree or push/merge this branch as part of writing this specification.

Jev interprets requests and selects meaningful next actions. Deterministic code owns state queries, answers, geometric resolution, movement validation, localization recovery, energy reserve and landing. One invocation authorizes the bounded execution session; there is no confirmation before each action. Existing provider and filming consent mechanisms remain applicable.

Deliver capabilities progressively, retaining all as explicit roadmap items:

1. Read room/drone state; explore the surveyed area, count supported devices, validate uncertain detections and return home.
2. Find a stationary phone and land at the closest certified safe landing zone.
3. Find a consenting person visibly holding a phone and maintain observation using yaw.
4. Follow that person through short validated steps in a cleared surveyed corridor.

The first implementation must support the interfaces needed by later stages without advertising those stages as operational. General unknown-room navigation, arbitrary landing-surface certification and unrestricted person following remain outside the initial hardware claim. Hardware acceptance is measured separately from simulation and provider success.

## Architecture

Use one room-session controller with capability modules, a local safety supervisor and a step-capable flight executor. Avoid separate safety loops per use case and avoid one universal unconstrained tool interface.

The executor connects once and supports start, execute validated checkpoint, publish observation, return and land. It retains command ownership until terminal shutdown. The current full-mission runner is reused through extraction of existing movement/pose primitives; repeatedly invoking its complete run lifecycle is not the adaptive-flight interface.

The safety supervisor remains active while model/perception work is pending. It owns pose health, containment, action deadlines, energy and terminal transitions. Remote calls never hold its command lock. Only the executor sends flight commands; safety preemption cancels pending decisions and invalidates their snapshot versions. Existing emergency behavior takes precedence over every other action.

The executor capability profile declares maximum atomic movement duration, acknowledgment deadlines and supported interruption semantics. Admission budgets the complete noninterruptible interval and resulting drift. Expiring a caller timeout does not prove the aircraft stopped: a command with uncertain completion forbids issuing a replacement movement and invokes the validated landing/emergency contingency. Exercise supervision during SDK, model and perception stalls. Do not assume a generic stop or cancellation command exists on the deployed firmware.

Claude's live detection process remains a perception producer. An ingestion bridge associates its output with session, action, observation and frame IDs, timestamps, content hash, actual pose provenance and detector identity. Delayed, duplicate, malformed and failed results are recorded. Late results may improve historical evidence but cannot authorize movement using an obsolete target or pose.

## Goals, evidence and Jev boundary

Represent requests as an ordered goal ledger with kind (`ANSWER_STATE`, `COUNT`, `FIND_OBSERVE`, `FOLLOW`, `LAND_NEAR`), supported target class or class group, optional supported relationship, spatial scope and terminal condition. Preserve user order and grouping. Ambiguous classes such as "devices" resolve to an explicit supported set or a clarification; do not silently promise every electronic device. The present detector vocabulary includes computer, person, phone and pen. A request specifying "man" must not silently become any person: retain the qualifier and clarify or identify a designated actor when perception cannot substantiate it.

Jev's fixed-choice interface selects controller-owned candidates. Interpret compound requests hierarchically: goal family, parameters and ordered objectives, followed by consistency validation against the original request. Validate this path with compound-language examples before claiming reliable interpretation. Existing inspection-choice results do not establish room-agent performance.

Snapshots separate goal progress, pose health, home/route state, energy, coverage, static objects, moving targets, landing zones and tool outcomes. Keep detection class confidence, metric location uncertainty, identity confidence and relationship evidence distinct. No unvalidated description or proximity alone establishes that a person holds a phone.

Expose typed reads for drone status, observations, counts, target evidence and coverage. Deterministic queries and templates produce grounded answers with observation time, scope and uncertainty. Existing fresh evidence can answer without flight. Jev never authors SQL, raw SDK commands, coordinates or unsupported factual claims.

Candidates include read, observe semantic region, validate detection, verify relationship, reacquire target, bounded follow step, approach certified landing zone, return, land, answer, clarify and escalate. Candidates carry stable IDs, snapshot version, evidence references, prerequisites, predicted benefit and conservative physical cost. Mandatory pose recovery and reserve return are supervisor actions, not optional Jev choices.

## Turn and progress contract

At every checkpoint:

1. Ingest completed events and project a versioned state snapshot.
2. Apply mandatory safety transitions before requesting a model decision.
3. Generate legal candidates for the active goal and available capabilities.
4. Let Jev select a candidate; retain choice, probabilities, confidence, latency and gate outcome.
5. Validate decision, evidence freshness, snapshot version and concrete command plan immediately before execution.
6. Execute one checkpoint and persist accepted effects, refusals and errors.
7. Update evidence and goals, then deliberate again or terminate.

Low confidence is not proof that more data exists. Offer useful reads or observations when they can resolve uncertainty; otherwise clarify or escalate. Preserve explicit escalation. Invalid choices and deterministic refusals become typed feedback for the next decision, with repeated rejected actions suppressed.

There is no arbitrary total model-call cap. Reads repeat only after relevant state changes; do not treat audit writes, timestamps or refusal events as substantive progress. Same action against the same relevant state cannot repeat. Physical duration, distance, recovery attempts and provider deadlines remain bounded. No-progress terminates with an honest reason. Provider waits have a bounded cost and timeout; if the wait plus safe termination cannot fit the reserve, return first. Validate simultaneous provider connectivity and drone connectivity before enabling airborne remote deliberation.

## Semantic navigation and closer inspection

Use left/center/right, rear/middle/front and bottom/middle/top plus surveyed landmarks. Front means the side closest to the tables/targets; top/bottom mean altitude. The 27 canonical regions express user intent, not a fixed exhaustive waypoint catalogue. Resolve them deterministically within the surveyed volume. Counts requested at a viewpoint concern objects visible from that viewpoint, not objects assigned to a physical quadrant.

Generate object-relative observation candidates dynamically from estimated target location and uncertainty. Options may change distance, bearing or height, or collect a bounded additional hover capture. Predict resolvability and expected new evidence; choose an alternate view when moving closer would not address ambiguity. Preserve pending user objectives and requested corners.

Validate the entire path and swept clearance, not only the endpoint. Account for position uncertainty, movement error, people/object standoff, tag recovery feasibility and return reserve. Cube containment alone does not prove obstacle clearance. Do not infer range to held/table objects using the current fixed-plane projection. A missing metric estimate permits a safe surveyed viewpoint or yaw observation, not a guessed approach.

Each validation observation may confirm, reject, leave ambiguous or lose a detection. Confidence is not forced upward. Prevent repeated ineffective viewpoints and account for every hover/capture in physical budgets.

## Localization, tag-free viewing and reanchoring

AprilTags need not remain visible in every object frame. Maintain a last trusted measured pose, heading, monotonic measurement time, pose-source provenance, propagated uncertainty and motion since the last fix. Known commands and telemetry propagate position only within a validated drift model; elapsed time alone is not a sufficient confidence measure. Distinguish camera age, fix age, estimator failure and intentional looking away.

Use four states:

- `ANCHORED`: fresh accepted fix; commands remain within its uncertainty envelope.
- `COASTING`: tags temporarily absent, but conservative propagated uncertainty and heading remain sufficient for the proposed step and a recovery opportunity. Permit validated short actions and object observations.
- `RECOVERY_REQUIRED`: projected uncertainty, fix age, travel since fix, repeated quality failures or contradictory evidence would make continued goal movement inadmissible. Suspend target pursuit and start bounded recovery while there is still adequate clearance and energy.
- `LOST`: position/heading uncertainty or integrity no longer supports a certified maneuver. Prohibit translation and autonomous exploratory yaw; invoke the site's validated controlled-landing contingency.

Do not wait until completely stale localization to seek tags. Trigger recovery before the next action would exhaust the uncertainty/clearance allowance. A missing tag in one frame does not force recovery. Fresh opportunistic accepted fixes may reanchor earlier at stable checkpoints without interrupting the task.

For V1, recovery is stationary: settle and, if necessary, scan a finite set of yaw headings selected from the mapped tag geometry and last trusted uncertainty region. Validate rotational swept clearance, heading integrity, drift during hover, energy and a recovery deadline. No translation to hunt for tags. If already facing usable tags, acquire a stationary burst without unnecessary rotation. The coverage predictor must evaluate actual observation/recovery headings and uncertainty, not assume canonical heading everywhere.

Accept mapped joint-tag recovery evidence only after sufficient fresh distinct reports, bounded spread, plausible displacement/heading, containment and height consistency checks. Existing eight-report/10 cm spread checks are starting constraints, not a complete uncertainty model. Numerical age, drift, heading and recovery limits belong in a versioned calibration profile backed by replay and hardware evidence. Missing validated parameters disable autonomous tag-free movement in real mode; test fixtures may supply explicit synthetic profiles.

Reanchor pose and heading atomically, reset propagation history and invalidate cached routes/candidates. Validate restoration of the mission heading and re-run route/energy checks before resuming. Reacquire a moving target from fresh evidence after pose recovery; never assume identity survived the scan. Recovery exhaustion or integrity failure enters the landing contingency. Never blind-return from a lost pose.

## Battery reserve and termination

Battery percentage is a quantized proxy for remaining energy. Require a versioned real-flight calibration profile with conservative burn/timing bounds, telemetry allowance, landing floor and validity conditions. No guessed production defaults. Local consumption observations may increase the conservative bound but not relax it below calibration.

Bootstrap calibration through a separate supervised, fixed-route trial using existing takeoff checks and conservative short-flight limits. This mode records/shadows estimates and does not enable autonomous extensions or claim reserve protection. Promote a profile only after independent representative holdout flights remain within its predicted consumption/timing bounds and healthy-charge forced returns reach the verified home tolerance above its landing floor. Apply the same separation to pose-drift calibration: supervised bounded trials establish the tag-free envelope before the autonomous session may use it.

Before each leg, look/capture, recovery action and airborne provider wait, compute the upper-bound cost of the complete action plus a validated route to safe termination and landing. Use the same axis-ordered movement decomposition as execution, including minimum moves, settling, worst allowed trims and localization. Use monotonic elapsed time; motor-time telemetry is a cross-check.

Continue only when current conservative battery estimate minus that cost remains at or above the landing floor. Otherwise latch return while pose and route remain trustworthy. Read telemetry at checkpoints and monitor deadlines during blocking actions. Stale/invalid telemetry cannot authorize continuation. Return/landing preempts Jev; no new mission work or provider requests after the terminal transition. Pending results cannot resume the session.

Home is an explicit measured pose with a verified landing area and route; never silently clamp home to another coordinate. A site must declare a contingency for unavailable navigation or return energy. A reserve breach is reported; it does not manufacture a claim that arbitrary landing or return is safe. Retain existing emergency/containment abort semantics where additional movement would be unsafe.

Distinguish completed mission, incomplete returned safely, goal blocked, recovery failure, abort and provider failure. Partial captures remain evidence but cannot turn an interrupted sweep or incomplete coverage into success. Fix the existing bridge's ignored runner status and unconditional flown status before using it in the new path.

## Capability-specific completion

### Count and return

Track surveyed-area coverage separately from detections and unique-object count. Accumulate static objects with evidence and uncertainty; do not merge moving people by static position. Report confirmed and uncertain objects, observed-person lower bounds and unobserved/occluded coverage. An unseen object is not proven absent. Completion requires declared coverage criteria or an explicit incomplete result, followed by successful return/landing when requested. Failed return cannot be reported as overall success.

### Find and land near a phone

Localize the stationary target using validated geometry and consistent observations. Select the minimum target-to-zone distance among currently certified clear, reachable zones satisfying localization uncertainty, rotor/object/person clearance, approach/descent safety and energy. Require a deterministic tie-break. If location uncertainty makes the ordering indeterminate, report that limitation and seek evidence or choose a safe zone without claiming proven closest.

A certified zone is a surveyed polygon/pad with size, clear approach/descent volume, allowed conditions, certification provenance and validity/revocation state. The current forward camera does not certify arbitrary floor patches. Unexpected occupancy or stale clearance invalidates the zone. With no admissible zone, observe from a safe point, explain the refusal and return home. Certification and target association are checked again before descent.

Clearance records must name their evidence source, observation/assurance time and expiration rule. The final descent check requires a still-valid source and no contradictory observations. Where sensors cannot establish clearance, use explicit session-scoped site/operator assurance for a controlled, access-restricted pad; this is part of session setup rather than repeated action approval. Expired or contradicted assurance disables that landing candidate. A bounding-box-free image is not evidence of an empty descent volume.

### Person holding phone and following

Add structured relation evidence with person/phone references, observation time and uncertainty. Use a separate moving-target tracker with stable session identity, freshness, ambiguity and loss handling. No switch to another person when the selected target disappears. Generic person boxes do not satisfy a specific actor qualifier.

Start with a consenting designated actor in a cleared surveyed corridor, yaw-only observation, then short individually validated translation steps with fresh target acquisition after each. Maintain validated standoff and account for target movement during observation/decision delay. A local tracker/controller is required for later continuous following; cloud Jev does not control individual steering corrections. Follow goals have an explicit physical duration/extent and terminal behavior. Target or pose loss suspends pursuit; bounded recovery, return or landing follows the applicable safety state.

## Verification and live rollout

Run tests serially. Use independent specification and quality reviews with fixes before delivery. Keep paid Jev verification distinct from replay and from physical flight; no LLM comparison is required. Do not describe prior inspection results as evidence for tracking or navigation capability.

Required tests include:

- Natural-language goal order, qualifiers, class scope and unsupported requests.
- Read-only answers cause no flight; counts and uncertainty come from persisted evidence.
- Duplicate/delayed observations and stale decisions cannot cause duplicate or obsolete movement.
- Closer-view candidates respect uncertainty, object/person clearance and physical budgets; a disproved detection is removed as an action target.
- Tag-free observations continue within a validated coasting envelope; one missed tag does not scan. Projected uncertainty crossing the limit triggers recovery before the next translation.
- Recovery with no rotation when tags are already visible; bounded yaw failure then success; rejection of stale, single-tag, inconsistent or implausible fixes; lost state never translates or scans blindly.
- Accepted reanchor invalidates old actions and requires fresh moving-target lock.
- Battery thresholds, equality/rounding, telemetry failure, provider wait cost, recovery cost and irreversible return. No optional commands after return begins.
- Safety supervision during SDK/model/perception stalls; uncertain command completion cannot release another movement, and admitted steps cover their whole noninterruptible interval.
- Closest-zone selection, occupied/stale zones, uncertain target positions and rejected descent.
- Person/phone proximity without holding evidence, crossing people, target loss and identity ambiguity.
- Interrupted flight and missing report/return cannot score as completed.

Roll out in order: deterministic/replay tests; simulated faults; exact mission dry run; healthy-charge hardware observation and return; shadow reserve calibration and forced early return; stationary tag occlusion/reacquisition; static-object closer inspection; count-and-return; certified-zone phone landing; yaw-only relational tracking; bounded corridor following. Never induce a real battery exhaustion or uncontrolled link loss as a test.

Hardware records include revision, calibration/site hashes, execution mode, model identity, action/decision stream, timestamps, frames, pose and battery samples, detector outputs, terminal state and measured outcomes. Simulated actors/scenes are labelled synthetic. Hardware readiness remains blocked where calibrated pose propagation, clearance, provider connectivity or battery bounds are absent.

## Existing plan disposition and branch maintenance

Explicit inspection escalation remains completed and retains regression coverage. The bounded inspection decision-session plan remains a separate inspection-domain deliverable; its typed feedback and candidate principles inform this room session, but this spec does not mark it done. Amend the demo-QA plan to use this session's deterministic room/drone reads. Defer broader trajectory/model-comparison evaluation while retaining correctness/replay checks and the integrated-survey evaluation backlog. Track every deferred item explicitly; remind the user of the next outstanding plan after each delivered stage.

Use task-sized commits. At each rebase checkpoint, first record clean branch/base state and inspect incoming API changes, rebase onto the locally verified integration head, resolve conflicts against both contracts, then run focused tests and the full serial suite at integration milestones. Recheck detection, runner, schema and geometry changes from adjacent sessions before implementation. No automatic push or integration merge.
