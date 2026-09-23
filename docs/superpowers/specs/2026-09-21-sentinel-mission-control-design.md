---
id: sentinel-mission-control-design
title: "SENTINEL mission control center"
status: approved
date: 2026-09-21
---

# SENTINEL mission control center — design

> Revised on 2026-09-21 after Alan approved the shared-runtime, dry-run-first approach.
> Alan approved this written revision on 2026-09-21; the implementation plan is being revised.
> It supersedes the earlier delivery sequence; implementation ownership remains with LightCode.
> Delivery amendment on 2026-09-23 adds the approved bounded browser-voice surface and schema v3 runtime observations.

## 1. Purpose and release boundary

Mission Control is the operator-facing center for the SENTINEL rail-sweep demo. One operator
uses it to submit a natural-language request, inspect and approve a legal sweep, supervise
flight and localization, inspect captured evidence, authorize an optional follow-up, and present
an evidence-backed answer.

The immediate goal is to iterate on the integrated system without a connected drone. Dry-run
and Live use the same application interfaces and production orchestration; deterministic adapters
replace external hardware and provider boundaries in dry-run. The existing frontend is preserved.
The first milestone is one existing scenario executed end to end through the UI, including an
explicit approval, evidence, authoritative counts, and a validated report. Live adapter wiring
follows that baseline; the complete release retains the capabilities described below.

The first release optimizes for a laptop or projector at 1440x900 or 1920x1080. It retains a
usable narrow-screen fallback but does not optimize the live demo for a phone.

The first release contains one golden path:

1. Submit a text request.
2. Show the generated legal sweep proposal.
3. Approve or reject it inline.
4. Show route, stop, flight, and localization progress.
5. Show the latest authorized frame with localization or survey overlays.
6. Show confirmed and uncertain counts plus analysis gaps.
7. Approve an optional follow-up sweep.
8. Show the validated evidence-backed report.
9. Keep controlled Land and independent Emergency motor stop available throughout.

The delivered browser may expose restrained hold-to-talk input when `SpeechRecognition` is
available. It submits one final English transcript through the existing idempotent chat path.
Only the exact normalized phrase `Sentinel, land now` starts a visible, cancellable three-second
controlled-Land countdown, and execution requires `LAND` to remain legal. Voice cannot approve
or reject proposals or trigger Emergency. V1 still excludes draggable panels, free-form waypoint
editing, multi-run management, provider or credential configuration, a general telemetry
dashboard, and a full event-replay editor.

## 2. Product decisions

- The central workspace, not chat, dominates the screen.
- Chat remains one persistent thread for the entire run.
- Chat accepts unrestricted natural language. System mutations remain constrained to currently
  legal typed actions and explicit operator approvals.
- Chat responses are complete, persisted messages rather than token streams. Operational state
  and frames still update live.
- Proposal cards in the chat thread contain working Approve and Reject buttons.
- Live hardware configuration is fixed when the process starts. The browser cannot change the
  site, provider, pose mode, consent, credentials, or flight authorization.
- Rehearsal and Live share one projection and interface. Their adapters differ, and Live never
  silently falls back to Rehearsal.
- Land now and Emergency motor stop are the only permanent safety controls in V1. Pause or hold
  is not shown without an adopted runtime capability.
- The layout remains geometrically stable. Mission state changes visual emphasis and content,
  not panel position.

## 3. Existing work and reuse

The implementation reuses LightCode's fixture console rather than replacing it. The following
parts are retained and promoted behind an adopted backend contract:

- responsive shell, accessibility behavior, tokens, icons, and SENTINEL assets;
- complete-snapshot and sequenced-event reconciliation;
- evidence image lifecycle and normalized box geometry;
- immutable proposal identity, idempotent decision attempts, and `IN_DOUBT` reconciliation;
- confirmed/uncertain count presentation and explicit observability states;
- fixture scenarios as isolated browser-acceptance inputs; integrated Rehearsal uses runtime
  scenario inputs rather than precomputed browser snapshots.

The local Python host uses [NiceGUI](https://github.com/zauberzeug/nicegui). Its supported
FastAPI coexistence, static/media serving, test facilities, and image-update patterns remove the
need for a second web stack. The implementation may adapt the official NiceGUI OpenCV webcam
example's non-flickering interactive-image pattern. Copied upstream code retains required
license and attribution notices.

V1 does not add React, a JavaScript build system, a mapping library, a chart library, a separate
SSE package, or a client state-management framework. The route remains lightweight inline SVG.

Planning documentation lands on LightCode's existing PR branch. Before implementation begins,
that branch must incorporate the latest `integration/survey-demo` ancestry containing the
survey, proposer, flight, and pose work. The implementer must not wire against sibling worktree
files or bypass missing runtime contracts by reading their databases directly from browser code.

The original design was prepared against local integration commit `9b6b5f1`. Delivery fetched
and normally merged published `integration/survey-demo` commit
`4aa9e19361b3ed31a4c8025405405e3c4a6b1b44` as merge commit
`15be1e6573eb1dc2c309f6b2e4faf7cfdc1c4faa`; no history was rewritten. Runtime composition is
from that ancestry, with no sibling-worktree dependency.

## 4. Architecture

Mission Control is a local in-process application launched from the terminal with an immutable
session configuration. It has five bounded units.

### 4.1 NiceGUI host

NiceGUI serves the existing static frontend and a small set of typed local API routes. V1 uses
cursor polling rather than adding another streaming transport. The host remains responsive while
the synchronous survey and flight code runs on a dedicated mission worker.

The canonical frontend URL is `/console/`: `/` redirects there and static serving supplies its
index. Existing relative module and stylesheet URLs must resolve under that mount; shared assets
remain under `/assets/sentinel-ui`. Host acceptance loads the page and its imports, styles and
API through the actual application server, rather than checking only the root HTML status.

### 4.2 Projection service

The projection service creates one browser-safe `MissionControlSnapshot` from adopted runtime
read interfaces. It may use backend repositories over authoritative stores; frontend code never
receives a database connection, SQL capability, raw path, storage key, provider secret, raw
private prompt, or chain of thought.

The projection supplies complete facts. The UI never calculates authoritative counts, flight
completion, approval legality, pose freshness, containment, or report validity.

### 4.3 Mission worker and approval broker

One background worker serializes mission mutations and runs the existing synchronous survey and
flight loop. At an approval point, an approval broker blocks only that worker. The UI host,
read-only chat questions, snapshot reads, Land, and Emergency remain responsive.

Mission admission is atomic: a second distinct mutating mission is refused while one is active,
including during approval waits. A single-thread executor alone is insufficient because it queues
work. Retries with the same command identity reconcile the existing mission instead of enqueueing
another. Admission is released only after terminal cleanup; safety latches still prohibit restart.

An approval wait survives browser refresh because its proposal and attempt are persisted. V1
does not claim process-crash continuation. If the backend process disappears during a Live run,
the run reopens as `RECOVERY_REQUIRED` and cannot automatically resume or take off.

### 4.4 Command service

The command service accepts chat messages, decisions, and controlled Land. Every mutation is
bound to session/run identity, expected state version, a command ID, and an idempotency key.
Duplicate delivery returns or reconciles the original result.

Chat text cannot directly authorize flight. The bounded decision session may answer, clarify,
suggest, select a legal proposal, or escalate. A proposal mutates nothing until the exact current
proposal is approved through its action control.

### 4.5 Safety adapter

`SafetyControlPort` exposes two distinct operations:

```text
request_land(run_id, command_identity) -> acknowledgement
trigger_emergency(session_id, command_identity, active_run_id?) -> acknowledgement
```

Land requests controlled landing through the active runner. Emergency bypasses chat, the agent,
the approval broker, and the normal mission queue, and delegates to the existing independent
emergency transport. UI acknowledgement confirms command acceptance only; it does not claim
physical safety or completed landing.

Emergency first latches the active runner and prevents subsequent normal command dispatch, then
attempts the independent emergency transport. The latch remains set even if sending fails. The
adapter must not call only `EmergencySender.send()`, which does not latch the runner, or rely on a
normal command transport that can be blocked by mission activity. Adopt or add a public runner
seam that coordinates the latch with the independent sender. With no active runner, a valid
session-level emergency still reaches the independent transport. Safety work never waits for
mission admission or an approval waiter. Repeated delivery reconciles the same command; stale
proposal or snapshot versions must not suppress an otherwise valid session emergency.

## 5. Runtime modes

### 5.1 Rehearsal

Rehearsal is the dry-run mode. It runs the real mission worker, survey loop, bounded decision
flow, candidate selection, command service, approval broker, merge, report validation, persistence,
projection and UI over deterministic recorded or scripted boundary adapters. It requires no drone,
external network or provider credentials. Scripted policy/provider responses pass through the
same legality and validation rules as Live; they cannot supply authoritative counts or reports
that bypass the runtime. Flight, camera, detector, pose and safety boundary adapters are selected
at startup and cannot open a hardware connection in Rehearsal.

Use the existing clean-run scenario in `tests/survey/test_loop.py`
(`test_a_clean_run_flies_once_counts_and_reports`) as the first integration baseline. Promote its
scenario inputs into reusable runtime fixtures without importing test-private helpers in product
code. Supply actual synthetic image artifacts with matching hashes and dimensions for browser
evidence: test-only placeholder paths and zero hashes are not valid image capabilities. The
scenario must pass through the UI approval broker, not an unconditional approval callback.

Existing frontend snapshots remain useful for isolated presentation regressions. Advancing those
snapshots is not an integrated Rehearsal run and cannot satisfy the first milestone. Runtime events
and persisted facts alone advance the integrated evidence spine. Additional existing scenarios
cover uncertainty, missing analysis and follow-up after the initial clean run works.

Rehearsal is explicitly selected at startup as a demonstration mode. Every snapshot, frame and
message is labelled `REHEARSAL` with its underlying `SIMULATED` or `REPLAYED` source kind.
There is no automatic switch from a failed Live session.

### 5.2 Live

Live uses the pre-armed flight, pose, detector/provider, and emergency adapters selected at
process start. Live startup fails closed unless required configuration, consent, provider/profile
approval, preflight, and emergency transport are ready. A blocked Live configuration is not
substituted with scripted behavior.

## 6. Interface and visual system

### 6.1 Art direction

The visual thesis is **field-command ledger**: an asymmetric dark mineral workspace whose visual
hierarchy is driven by evidence and operator responsibility, not decorative telemetry.

- graphite is the dominant field;
- warm off-white carries primary text;
- cyan identifies current measured or Live facts;
- amber identifies aging state or operator attention;
- red is reserved for emergency or containment failure;
- unavailable and historical facts use neutral gray plus explicit text.

IBM Plex Sans and IBM Plex Mono are vendored locally from the official open-source repository
with their license and attribution. The application makes no runtime font or asset requests.

The memorable interaction is an **evidence spine** connecting request, proposal, approval,
flight, captures, analysis, follow-up, and report. Every completed point comes from an actual
event or persisted fact. Time alone never advances the spine.

### 6.2 Stable 16:9 composition

1. **Top command bar** — identity, run, mode, source, freshness, current phase, Land now, and
   Emergency motor stop.
2. **Left chat rail** — persistent transcript, compact suggested prompts, composer, agent status,
   and actionable proposal cards.
3. **Central mission workspace** — a top-down SVG mission sheet beside the current camera or
   evidence frame, followed by a small capture strip and the evidence spine.
4. **Right facts rail** — flight budget, count intervals, observability, frame-analysis gaps,
   pose health, and the current evidence-backed answer.
5. **Audit drawer** — complete ordered public events, hidden from the primary presentation until
   requested.

The central split remains present through all phases. Before flight the route and proposal carry
greater contrast; during flight the camera and localization layer do; during analysis the capture
and object layers do; after completion the report and its supporting evidence do.

On narrow screens, safety remains sticky and content order is mission, chat, then facts.

### 6.3 Chat behavior

- One thread belongs to one run.
- The initial request, clarifications, answers, proposals, decisions, failures, and report remain
  in sequence.
- Suggested prompts expose common capabilities without limiting free-text input.
- Sending is disabled only while the same command is pending; recoverable failure preserves text.
- Agent replies identify whether they are answering current state, historical observations, or
  an unavailable capability.
- Private chain of thought and raw provider payloads are never rendered.
- Proposal cards show the full candidate, geometry, duration, classes, proposer source,
  justification, constraints, and current eligibility before enabling Approve or Reject.

## 7. Browser projection

`MissionControlSnapshot` is a complete, versioned projection. The implementation defines and
validates the exact schema, with at least these top-level units:

```text
session       immutable mode and startup configuration summary
run           identity, request, status, phase, version, timestamps
source        LIVE/REHEARSAL plus LIVE/SIMULATED/REPLAYED and freshness
safety        land and emergency availability/acknowledgement state
chat          ordered public messages and pending command identity
proposal      current complete proposal, decision identity, eligibility
mission       envelope, selected route, stops, progress, flight budget
localization  latest pose, health, stream summary, pose evidence capability
evidence      frames, selected frame, sightings, analysis status
results       authoritative counts, uncertainty, observability and gaps
report        validated report or rejection state
events        latest exposed sequence and snapshot watermark
legal_actions exact current operator actions and disabled reasons
```

The delivered contract uses `schema_version: 3`, separate from `run.version`. It adds per-stop
coverage/prediction/trim/return/arrival facts, capture detection state and latency, proposer
provenance and escalation reason, and vertical-height consistency. A separate
`run.decision_version` tracks authoritative decision changes: approval `expected_version` binds
to it, so display-only pose-age refreshes cannot invalidate an otherwise current proposal. The implementation
plan must define the exact versioned payload, identity fields and response envelopes together
with a consumer migration; it cannot change validation while leaving consumers on the old shape.

Unknown fields and unresolved references are rejected. Every frame, sighting, object, stop,
proposal, message, and report belongs to the same run. Source kind, freshness, and operational
status remain separate axes.

The adopted local API is deliberately small:

```text
GET  /api/v1/session
GET  /api/v1/runs/{run_id}/snapshot
GET  /api/v1/runs/{run_id}/events?after_sequence={n}&limit={1..200}
POST /api/v1/runs/{run_id}/chat
POST /api/v1/runs/{run_id}/decisions
GET  /api/v1/runs/{run_id}/evidence/{artifact_id}
POST /api/v1/runs/{run_id}/land
POST /api/v1/safety/emergency
```

The browser decision port retains `submit_decision(attempt)`, deriving the route run ID from the
immutable attempt. The backend verifies that route and body identities agree. Response envelopes
preserve `attempt_id`, typed status and a safe public message. HTTP 409 decision conflicts are
validated and returned as `CONFLICT`, not converted into transport exceptions and `IN_DOUBT`.
An uncertain outcome uses `IN_DOUBT` and retains the same attempt identity on reconciliation.
The client also implements the bounded evidence read contract, including denial and revocation.
Contract tests exercise the HTTP adapter with the existing decision controller.

Event polling defaults to 500 ms while the page is visible and backs off when hidden. Polls do
not overlap. A newer complete snapshot may restore operational state after an event gap, but the
missing audit sequence remains visible until fetched. Live display frames are bounded to at most
5 Hz in V1; intermediate display frames may be dropped and must never block flight, recording,
localization, or analysis.

## 8. Signal bindings

| UI fact | Authoritative runtime source |
|---|---|
| Request and chat | persisted mission thread and bounded decision session |
| Legal proposal and rationale | `survey.candidates` plus the selected proposer result |
| Approval eligibility | proposal identity, version, consent, envelope, preflight and legality |
| Route, stops and flight budget | selected survey option and site envelope |
| Stop and flight progress | event-emitting `CubeMissionRunner` adapter |
| Current measured pose | latest run-bound `DronePoseReport` |
| Commanded position | mission runner's current approved route/log state |
| Localization tags | pose evidence frame bound to the exact pose report |
| Captures and analysis | `SurveyStore.frames` through backend read methods |
| Survey boxes | normalized `sightings` bound to frame IDs and hashes |
| Confirmed/uncertain counts | persisted `objects` and `class_counts` |
| Observability and analysis gaps | supplied survey result facts, never UI inference |
| Final answer/report | deterministic report validation and persisted report |
| Land and Emergency | `SafetyControlPort` acknowledgements and runner state |

The event-emitting flight adapter appends public lifecycle records for proposal, decision,
mission start, stop arrival, localization result, frame capture, analysis completion, merge,
follow-up, report, Land, and Emergency. Payloads are allowlisted summaries with stable references,
not raw internal objects.

## 9. Localization observability

Localization is a first-class demo story, not a small telemetry card.

The existing `DronePoseReport` supplies run/report identity, observation time, source kind,
position, heading, optional altitude, method, used tag IDs/zones, tag distances, and per-tag plus
overall reprojection error. Pose age and staleness are derived on the backend. The currently
adopted default stale threshold is 2000 ms; the snapshot carries the effective threshold rather
than making the browser duplicate it.

### 9.1 Pose health

The projection supplies:

```text
status: FRESH | AGING | STALE | UNAVAILABLE
age_ms
stale_after_ms
latest_report
recent_stream: frames_seen, reports_emitted, no_tag_frames, gated_frames, timeouts
containment: INSIDE | MARGIN | OUTSIDE | UNAVAILABLE
```

`AGING` begins at half the effective stale threshold. `STALE` begins only when the backend's
threshold is exceeded. The backend refreshes `age_ms`; the browser does not decide truth from its
own clock.

Fresh measured pose is a solid cyan marker. Aging pose is amber. Stale pose is hollow and dashed,
freezes at the last valid location, and shows age and original observation time. `OUTSIDE` uses
red. Commanded position is a distinct ghost marker. Styling never depicts age as physical
uncertainty.

### 9.2 Pose evidence frame

The existing pose report intentionally omits image-space corners. Mission Control therefore adds
an adopted browser projection, not a browser-side detector:

```text
PoseEvidenceFrame
  run_id, report_id, artifact_id, sha256, captured_at
  width_px, height_px, source_kind
  tags[]
    tag_id
    corners_xy_normalized[4]
    status: USED | DETECTED_NOT_USED
    reason_code?                # allowlisted, no private exception text
    zone_id?
    reprojection_error_px?
```

The pose pipeline must capture this diagnostic at detection time before corners are discarded,
bind it to the exact pose report and frame hash, and expose the image only through the bounded
evidence capability. Rehearsal data follows the same shape.

The camera has separate selectable layers for localization tags and survey objects. During flight
the localization layer is primary; during survey analysis the object layer is primary. Labels
and geometry prevent the two detection meanings from being confused.

### 9.3 Localization ribbon

A compact recent ribbon shows successful fixes and no-fix intervals. The pose stream emits or
aggregates enough public health information to distinguish no tag, gated observation, timeout,
and valid report without persisting raw frames or flooding the event journal. A new valid report
returns health to cyan and updates the measured marker. Reduced-motion mode performs the same
state change without animation.

All coordinate conversion into the mission sheet's room coordinate system occurs in the backend
through the adopted site transform. The browser never assumes that pose units and mission
centimetres are interchangeable.

## 10. Decisions and safety semantics

An approval attempt binds the proposal ID, proposal hash, run/state version, decision, command
ID, correlation ID, and idempotency key. Pending double clicks cannot create another attempt.
A stale or replaced proposal disables its controls. `IN_DOUBT` reconciliation reuses the same
identity; a deliberate new decision requires a refreshed proposal and a new identity.

**Land now** is one click because delay is the greater safety risk. Submission immediately
disables ordinary mission mutations and presents requested, acknowledged, and completed as
distinct states.

**Emergency motor stop** is visually isolated and press-and-hold. It remains available while
ordinary controls are disabled and does not depend on agent, proposal, or mission queue state.
The UI must show when emergency transport itself is unavailable and retain the documented
physical/terminal fallback.

## 11. Failure handling

- Chat/provider failure preserves operator text and returns a stable public error code.
- Unsupported or contradictory requests clarify, refuse, or escalate instead of selecting the
  nearest legal mission.
- Browser disconnect does not approve pending work. A worker already waiting for approval remains
  paused; an executing mission continues under backend safety policy. Reconnect loads a complete
  snapshot and resumes event reads after the last known sequence without implicitly authorizing work.
- Event gaps never produce synthetic transitions.
- Camera and evidence failures remain explicitly unavailable, degraded, forbidden, revoked, or
  stale. Old imagery is not relabelled current.
- Detector failure preserves successful analyses and reports only defensible ranges or lower
  bounds with explicit gaps.
- Pose loss marks measured pose stale or unavailable. Commanded pose remains separately labelled;
  the approved backend flight mode decides whether operation continues or lands.
- Backend loss during Live yields `RECOVERY_REQUIRED`; no automatic flight resume occurs.
- Rehearsal data, scripted providers, and replay frames can never appear under a Live label.

## 12. Accessibility, privacy and performance

- Semantic landmarks, native controls, visible focus, skip link, and keyboard operation are
  mandatory.
- Minimum body text is 16 px and metadata is at least 14 px for projector readability.
- Color is never the sole state cue; labels, shape, line style, and timestamps accompany it.
- Routine updates use polite announcements. Emergency, containment failure, and command failure
  use alerts without repeatedly announcing unchanged polling results.
- Reduced-motion mode removes pulsing, animated transitions, and smooth scrolling.
- The transcript does not auto-scroll when the operator is inspecting history.
- Evidence descriptions identify capture and status. Full hashes are inspectable and copyable.
- Raw image bytes, provider secrets, private prompts, filesystem paths, and storage keys are not
  exposed in snapshots or logs.
- Microphone permission is requested only on an operator hold-to-talk action. The browser speech
  service may process audio remotely; SENTINEL uploads or retains no audio and never listens
  passively. Text remains available when speech is unsupported, denied, cancelled, or fails.
- Chat, safety controls, and snapshot reads remain responsive while the mission worker is waiting,
  flying, or analysing.

## 13. Testing and acceptance

### 13.1 Automated layers

1. **Projection contract:** unknown fields, cross-run references, invalid versions, unbound
   evidence, and internal fields fail closed.
2. **Localization:** injected clocks cover fresh, aging, stale, unavailable, and refreshed state;
   overlay corners remain bound to the exact frame/report/hash; no-fix intervals cannot become
   valid poses.
3. **Commands:** duplicate chat, decisions, Land, and Emergency reuse identity; stale versions
   and replaced proposals cannot execute.
4. **Mission integration:** real `SurveyStore`, candidates, bounded session, approval broker,
   replay flight, detector, merge, report validator, and fake safety adapter complete the golden
   path.
5. **Failures:** rejection, consent/preflight failure, provider timeout, pose loss, evidence
   revocation, event gaps, browser reload at approval, process restart, and uncertain command
   outcomes are covered.
6. **Browser acceptance:** 1440x900, 1920x1080 and narrow layout; keyboard and reduced motion;
   no horizontal overflow; no external requests; no console errors; stable event reconciliation.
7. **Mode integrity:** Rehearsal and Live adapters emit the same projection shape while preserving
   unmistakable source and capability labels.
8. **Performance:** polls do not overlap, frame display is bounded, intermediate display frames
   may drop safely, and UI commands stay responsive during mission activity.

### 13.2 First milestone acceptance

Run the actual application host with Rehearsal adapters and drive the browser from text request
through proposal, explicit approval, simulated flight/capture, merge and validated report. Assert
one confirmed computer against the existing clean-run baseline, inspect authorized image bytes,
and verify that events and results originate from the real runtime and store. No fixture snapshot
sequence may stand in for execution.

Additional focused checks cover rejection with no flight dispatch, duplicate approval delivery,
stale-proposal conflict, uncertain-result reconciliation, browser reload at approval, concurrent
mission refusal and responsive status reads during a blocked worker. Runner/adapter tests prove
emergency latching precedes independent send, survives send failure and prevents later normal
commands; all transports are fakes. Host/browser checks fail on missing imports, styles or API
routes. The full localization demonstration below is a later acceptance gate, not evidence that
the first milestone already implements Live operation.

### 13.3 Full release acceptance scenario

The browser test and Rehearsal demonstration show, in one run:

1. text request and proposal;
2. inline approval;
3. route and stop progress;
4. tag detection, fresh pose, aging, staleness, and recovery;
5. measured versus commanded position;
6. object evidence and uncertain counts;
7. optional approved follow-up;
8. validated evidence-backed report;
9. Land and Emergency availability throughout.

### 13.4 Hardware gate

Automated tests send no real motor command. A Live label requires separately authorized checks of
camera transport, localization/tag overlay binding, pose freshness transitions, controlled Land,
and independent emergency transport. Emergency acknowledgement is not described as physical
success without separate observed evidence.

## 14. Delivery and ownership

LightCode owns the UI integration. Preserve the tested frontend and Colin's product direction;
revise the existing implementation plan after this written spec is approved. The existing plan's
fixture-advancement milestone, two-argument decision client, root static route, unguarded mission
executor and sender-only emergency adapter are superseded by this specification.

Delivery proceeds in three ordered milestones:

1. **Shared-runtime dry-run:** incorporate published integration ancestry, establish the baseline,
   adopt typed runtime and browser interfaces, and complete the clean scenario through the real
   host, mission worker and approval broker. Include valid evidence, persisted results, admission
   control and simulated safety behavior. This milestone proves iteration without a drone.
2. **Broader dry-run coverage and presentation:** add existing uncertainty/follow-up scenarios,
   mission/evidence layout and localization diagnostics. Preserve source labels and all existing
   accessibility, evidence lifecycle and decision identity regressions.
3. **Live adapters:** compose the same application with live boundary adapters, immutable startup
   gates and independent safety transport. Pass adapter contract tests and startup refusal tests
   before separately authorized hardware verification. No hardware operation is authorized by
   approval of this design or by automated tests.

Both modes share orchestration, persistence semantics, projections and command interfaces.
Presentation fixtures remain available for component tests but are not a parallel application
runtime. Contract or ownership gaps are reported against the published integration source and
resolved through explicit backend interfaces.

After implementation and verification, update PR #8 with integration ancestry, completed scope,
commands/results, remaining Live gates and reviewer-facing explanations of contract corrections;
request review again. Keep PR #7 linked as the existing asset foundation without duplicating its
implementation. No implementation or plan approval is implied by this written-spec revision.
