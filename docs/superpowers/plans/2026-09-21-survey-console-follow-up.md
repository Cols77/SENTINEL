# Survey Console Follow-Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a separate, fixture-driven survey console demonstrating rail sweeps, evidence, honest counts, and approval semantics without changing PR #7 or connecting to a drone.

**Architecture:** A proposed, versioned UI projection feeds a small client interface, an event/snapshot controller, and plain DOM presentation modules. A deterministic fixture client supplies complete snapshots and capability-scoped evidence; presentation never reads stores or derives authoritative flight/count state. Real backend projection and NiceGUI/live integration are a separate project with explicit handoff gates.

**Tech Stack:** Browser ES modules and CSS, Node built-in `node:test`/`node:assert/strict`, existing Playwright browser tooling, Python standard-library HTTP server, existing SENTINEL asset-kit tokens/icons/placeholders. No new runtime package or frontend framework.

**Spec:** `docs/superpowers/specs/2026-09-21-survey-console-follow-up.md` (read alongside this plan); source review: https://github.com/Cols77/SENTINEL/pull/7#issuecomment-5749878495.

## Global Constraints

- PR #7 remains the reusable asset-kit foundation; ship the survey console in a separate follow-up PR.
- No UI-side SQLite reads, sibling-worktree imports, drone SDK calls, or live provider calls.
- All console scenarios show FIXTURE DEMO; LIVE and REPLAY describe fixture source semantics, never an actual connection.
- Preserve the five regions Header, Left, Center, Right, Bottom and the legacy alley view.
- Use 16px minimum body text, 14px secondary metadata, and the 40px symbol-only header from the asset kit.
- Progress changes only from accepted read-model snapshots/events; never from elapsed time or interpolated telemetry.
- An ABORTED partial sweep consumes no flight and is never merged.
- Proposals are not actions; approval acknowledgement is not flight dispatch.
- Unknown, zero, unavailable, stale, uncertain, and not observable remain distinct.
- Evidence is run-scoped and capability-resolved; no storage keys, filesystem paths, or arbitrary provider URLs reach image elements.
- No functioning chat input; show immutable initial request and structured public proposer activity.

## Review Focus

1. A late snapshot/evidence response from another run must not replace or expose the selected run's data (Tasks 3, 6).
2. Duplicate/out-of-order events or a sequence gap must not double-count flights, advance steps, or enable decisions against incomplete state (Tasks 2, 3).
3. An unknown decision outcome followed by retry or proposal replacement must retain attempt identity and never approve the replacement silently (Task 7).
4. Missing/forbidden evidence, unknown numeric facts, and unexpected detector labels must not become a valid image, a zero, or a supported class (Tasks 1, 5, 6).
5. Untrusted public strings and extreme image aspect ratios must remain inert text and place boxes only on the actual image content (Tasks 4, 6).

---

**Execution:** Completed using the approved native workflow; see [execution record](2026-09-21-survey-console-execution.md).

## Execution setup and repository facts

The user requested a plan, not implementation. Review this plan before execution. At execution time use `superpowers:using-git-worktrees` to create an isolated `codex/survey-console-fixtures` branch from the asset-kit commit or its merged successor. If PR #7 is unmerged, make the follow-up explicitly dependent on PR #7 and rebase after it merges. Do not push runtime changes onto `codex/sentinel-ui-asset-kit`.

Inspected baseline: `367d29ce8d06e9fb3e03fcb3c72a0a741732ad16`. Existing console is `assets/sentinel-ui/console.html`, a static alley composition; `console.js` only previews emergency and removes schematic interactivity. `src/sentinel/client.py` defines DroneClient and must remain untouched. No survey UI projection was found in this branch. Some local reads timed out during planning; committed component specs/browser verifier were retrieved through GitHub's contents API. Do not interpret old branch-status statements from the review as current facts.

Read the spec above, asset-kit README/component specs, and WP2 human-surface spec before coding. When runtime integration is eventually planned, resolve WP2 versus adopted coordinator contracts rather than treating this proposed demo API as authoritative.

## File map

All files below are new. Existing asset-kit files are read-only dependencies.

| File | Responsibility |
|---|---|
| `examples/survey-console/contract.mjs` | Versioned projection validation and exported enums |
| `examples/survey-console/fixtures.mjs` | Eight named deterministic scenario timelines |
| `examples/survey-console/fixture-client.mjs` | Proposed client methods, evidence scope, deterministic decision ledger |
| `examples/survey-console/controller.mjs` | Snapshot/event acceptance, run epochs and subscription |
| `examples/survey-console/dom.mjs` | Safe DOM creation; no HTML interpolation |
| `examples/survey-console/shell.mjs` | Five-region layout, header/request/events/rail/frame strip |
| `examples/survey-console/counts.mjs` | Class-specific count copy, uncertainty and report presentation |
| `examples/survey-console/evidence.mjs` | Selection, capability lifecycle and image-box geometry |
| `examples/survey-console/approval.mjs` | Full candidate and identity-preserving decision flow |
| `examples/survey-console/app.mjs` | Assemble fixture client, controller and views; reviewer controls |
| `examples/survey-console/index.html` | Accessible document and local module entry |
| `examples/survey-console/console.css` | Responsive layout and reduced-motion styles |
| `examples/survey-console/README.md` | Demo launch, boundaries and scenario descriptions |
| `examples/survey-console/contract.md` | Proposed client/field semantics and backend adoption checklist |
| `tests/survey_console/contract.test.mjs` | Projection invariants and scenario completeness |
| `tests/survey_console/client.test.mjs` | Client reads, evidence scope and decision deduplication |
| `tests/survey_console/controller.test.mjs` | Snapshot/event/run races |
| `tests/survey_console/counts.test.mjs` | Count wording and unavailable/unknown distinction |
| `tests/survey_console/evidence.test.mjs` | Capability races and box geometry |
| `tests/survey_console/approval.test.mjs` | Decision races and attempt identity |
| `scripts/ui_assets/verify_survey_console.cjs` | Browser acceptance, fixture-only network assertion and screenshots |

Use `.mjs` to avoid adding a root `package.json` solely for module mode. Node tests run with `node --test tests/survey_console/*.test.mjs`. Browser verifier uses `require('playwright')`, matching the existing verifier. If unavailable, use the configured workspace Node/library runtime; record the exact command in the new README, not a developer-specific absolute path. Serve repository root, not the example directory, so relative asset URLs resolve.

## Shared interface definitions

These names are the proposed **demo contract v1**, not existing backend types. Put the following definitions as JSDoc in `contract.mjs` and document each field in `contract.md`. Reject extra internal fields recursively in the validator; use explicit field allowlists for each record. All IDs are nonempty strings, versions/sequences/counts are nonnegative safe integers, nullable values use `null` explicitly, times are ISO timestamps with a timezone, and numeric metres/pixels/seconds are finite and nonnegative. Coordinates may be signed. Hashes are nonempty opaque fixture hashes, clearly identified as illustrative in the demo.

```js
/**
 * @typedef {'computer'|'person'|'phone'|'pen'} ClassName
 * @typedef {'REQUEST'|'PROPOSAL'|'APPROVAL'|'FLIGHT'|'ANALYSIS'|'REVIEW'|'REPORT'} Step
 * @typedef {{x_m:number,y_m:number,z_m:number,yaw_deg:number}} Pose
 * @typedef {{id:string,status:'PLANNED'|'ACTIVE'|'COMPLETED'|'FAILED'|'ABORTED'|'UNKNOWN',
 *   range_m:number,altitude_m:number,extent_m:number,duration_s:number,
 *   resolvable_classes:ClassName[]}} Stop
 * @typedef {{id:string,stop_id:string,captured_at:string|null,commanded_pose:Pose|null,
 *   measured_pose:Pose|null,analysis:'PENDING'|'ANALYSED'|'FAILED'|'NOT_RETAINED',
 *   evidence:'AVAILABLE'|'MISSING'|'FORBIDDEN'|'REVOKED',failure:string|null,
 *   merge_eligible:boolean}} Frame
 * @typedef {{id:string,frame_id:string,class_name:ClassName,
 *   bbox:[number,number,number,number]|null,raw_score:number|null,
 *   score_semantics:string,provider:string,model:string}} Sighting
 * @typedef {{id:string,class_name:ClassName,state:'UNCERTAIN'|'CONFIRMED',
 *   sighting_ids:string[],reason:string,geometry_degraded:boolean}} ObjectFact
 * @typedef {{class_name:ClassName,semantics:'RANGE'|'SINGLE_VIEW_LOWER_BOUND'|'UNKNOWN',
 *   confirmed:number|null,uncertain:number|null,lower_bound:number|null,
 *   captured_at:string|null,observability:'OBSERVABLE'|'LIMITED'|'NOT_OBSERVABLE'|'UNKNOWN',
 *   projected_px:number|null,min_px:number|null,reason:string,arithmetic:string|null,
 *   sighting_ids:string[],distinct_stops:number|null,geometry_degraded:boolean}} Count
 * @typedef {{id:string,sha256:string,option_id:string,stops:Stop[],range_m:number,
 *   altitude_m:number,extent_m:number,duration_s:number,resolvable_classes:ClassName[],
 *   proposer:{mode:'F'|'E'|'J',source:string},justification:string,
 *   guardrail_disagreements:string[],target_object_ids:string[],
 *   eligibility:{consent:boolean|null,envelope:boolean|null,preflight:boolean|null,
 *     current:boolean},disabled_reasons:string[]}} Proposal
 * @typedef {{schema_version:1,run_id:string,version:number,event_sequence:number,
 *   updated_at:string,source:'LIVE'|'REPLAY',freshness:'FRESH'|'STALE',
 *   status:'ACTIVE'|'COMPLETE'|'ABORTED',step:Step,request_text:string,
 *   proposer:{mode:'F'|'E'|'J',source:string},
 *   flights:{used:number,max:2},sweep:{id:string,option_id:string,status:'PLANNED'|'ACTIVE'|'COMPLETE'|'ABORTED',
 *     merged:boolean,flights_used_before:number,stops:Stop[]},
 *   frames:Frame[],sightings:Sighting[],objects:ObjectFact[],counts:Count[],
 *   analysis:{analysed:number,total:number,dropped_labels:string[],unknown_labels:string[]},
 *   provider:{name:string,model:string,source_kind:string},failures:string[],
 *   proposal:Proposal|null,report:{status:'ABSENT'|'ACCEPTED'|'REJECTED',text:string|null,reason:string|null}} Survey
 * @typedef {{id:string,run_id:string,sequence:number,occurred_at:string,summary:string}} SurveyEvent
 * @typedef {{run_id:string,proposal_id:string,proposal_sha256:string,expected_version:number,
 *   decision:'APPROVE'|'REJECT',attempt_id:string,idempotency_key:string}} Attempt
 * @typedef {{attempt_id:string,status:'ACKNOWLEDGED'|'REJECTED'|'CONFLICT'|'IN_DOUBT',message:string}} DecisionResult
 * @typedef {{status:'AVAILABLE',run_id:string,frame_id:string,media_type:'image/png'|'image/jpeg',
 *   bytes:Uint8Array,width:number,height:number}|{status:'NOT_FOUND'|'FORBIDDEN'|'REVOKED',
 *   run_id:string,frame_id:string,reason:string}} EvidenceResult
 * @typedef {{id:string,title:string,snapshots:Survey[],events:SurveyEvent[],
 *   decision_results:DecisionResult['status'][]}} Scenario
 */
```

`SurveyClient` methods are asynchronous: `read_survey(run_id): Promise<Survey>`, `read_survey_events(run_id, after_sequence, limit): Promise<SurveyEvent[]>`, `read_evidence(run_id, frame_id): Promise<EvidenceResult>`, `submit_decision(attempt): Promise<DecisionResult>`. Invalid run/limit/identity throws an `Error` with a stable `.code` (`NOT_FOUND`, `INVALID_ARGUMENT`, `IDENTITY_REUSED`). Evidence denials are tagged results, not exceptions. Unexpected transport failure remains an exception and must not imply command rejection.

Fixture-only methods: `advance(): void` exposes the next whole snapshot; `resolveDecision(status): void` finishes an intentionally pending fake submission; `calls: Attempt[]` records submissions for tests. No time-driven advancement. Browser production code must depend only on the four client methods; the reviewer-control assembly may call fixture-only methods.

## Task 1: Validated projection and eight fixture timelines

**Files:** Create `contract.mjs`, `fixtures.mjs`, `contract.md`, `tests/survey_console/contract.test.mjs` in the paths above.

**Interfaces:** Export `STEPS`, `CLASSES`, `validateSurvey(value): Survey` (throws `Error` with code `INVALID_PROJECTION`), `makeScenario(id): Scenario` (fresh deep clone), `scenarioIds: string[]`. Later tasks consume the exact JSDoc types above.

- [x] **Step 1: Write fixture and invariant tests.** Use the eight IDs below. Assert the following plus referential integrity, no person ObjectFact, no unsupported labels counted as supported classes, safe-integer budgets and complete count records for all four classes.

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import {validateSurvey} from '../../examples/survey-console/contract.mjs';
import {makeScenario, scenarioIds} from '../../examples/survey-console/fixtures.mjs';

test('every scenario snapshot satisfies the proposed projection', () => {
  assert.equal(scenarioIds.length, 8);
  for (const id of scenarioIds) {
    for (const s of makeScenario(id).snapshots) assert.equal(validateSurvey(s), s);
  }
});
test('aborted frames never merge or consume a flight', () => {
  const s = makeScenario('aborted').snapshots.at(-1);
  assert.equal(s.sweep.merged, false);
  assert.equal(s.flights.used, s.sweep.flights_used_before);
  assert.ok(s.frames.every(f => !f.merge_eligible));
  s.sweep.merged = true;
  assert.throws(() => validateSurvey(s), {code: 'INVALID_PROJECTION'});
});
test('unsupported version and internal evidence paths are rejected', () => {
  const s = makeScenario('computers-followup').snapshots[0];
  assert.throws(() => validateSurvey({...s, schema_version: 2}));
  s.frames[0].storage_key = '/private/frame.png';
  assert.throws(() => validateSurvey(s));
});
```

- [x] **Step 2:** Run `node --test tests/survey_console/contract.test.mjs`; expect missing module failures before implementation.
- [x] **Step 3: Implement the enumerations and validator first.** Use plain object/array validators with exact allowlists from the type definitions; do not coerce strings into numbers. Validate `bbox` order and each value in [0,1], references from sighting→frame→stop and object/count→sighting, duplicate IDs, all four unique class rows, and non-null score semantics when a score exists. Enforce person lower-bound timestamp and one supporting frame; prohibit person objects. Non-observable counts use null numeric estimates. Enforce `analysis.total === frames.length` and `analysis.analysed === frames.filter(f => f.analysis === 'ANALYSED').length`. For aborted sweeps enforce no eligible frames, no merged sweep and unchanged used budget. Reject invalid dates and missing fields with a path in the message.

```js
export const STEPS = Object.freeze(['REQUEST','PROPOSAL','APPROVAL','FLIGHT','ANALYSIS','REVIEW','REPORT']);
export const CLASSES = Object.freeze(['computer','person','phone','pen']);
function requireFact(ok, path) {
  if (!ok) throw Object.assign(new Error(`Invalid projection: ${path}`), {code:'INVALID_PROJECTION'});
}
// Within validateSurvey, after structural/field checks:
requireFact(s.flights.used <= s.flights.max, 'flights.used');
if (s.sweep.status === 'ABORTED') {
  requireFact(!s.sweep.merged, 'sweep.merged');
  requireFact(s.flights.used === s.sweep.flights_used_before, 'flights.used');
  requireFact(s.frames.every(f => !f.merge_eligible), 'frames.merge_eligible');
}
```

- [x] **Step 4: Author timelines using one base factory and explicit patches.** Every `makeScenario` return is `structuredClone`d. Use run IDs `fixture-<scenario-id>`, base time `2026-09-21T00:00:00Z`, increasing versions, and matching sequence watermarks. Snapshot records are complete; patches are authoring conveniences only. Follow this exact scenario table; use synthetic placeholder image bytes only.

| ID | Required snapshots and facts |
|---|---|
| `computers-followup` | Explicit REQUEST, PROPOSAL, APPROVAL, FLIGHT, ANALYSIS, REVIEW snapshots. Stops `s1,s2,s3`, range 2m, altitude 1m, extent 3m, total planned duration 18s. Final three analysed frames `f1,f2,f3`; five confirmed computer objects plus `computer-6` uncertain; six supporting sightings; used 1/2; follow-up proposal `p2` at 1m targets `computer-6`. |
| `people-lower-bound` | REVIEW, one analysed `f1` with three person sightings, lower_bound 3, timestamp `2026-09-21T00:00:04Z`, no person objects. |
| `phone-closer` | REVIEW after sweep1: used1, phone NOT_OBSERVABLE, projected6px/min12px; APPROVAL of closer proposal; FLIGHT; REVIEW after completed sweep2: used2, projected18px/min12px, phone observable with 0 confirmed/1 uncertain and one supporting sighting. Use unique frame IDs in sweep2. For this scenario the earlier sweep has no retained supporting sightings; expose sweep2 frames/stops together and its analysed/total progress, explicitly labelled “current sweep”. Do not carry earlier frame references into a projection that omits those frames. All changed status/budget facts come from supplied snapshots. |
| `consent-required` | APPROVAL, eligibility.consent false, f1 NOT_RETAINED/FORBIDDEN, no capture time/poses/sightings; evidence read forbidden; used0. |
| `aborted` | FLIGHT used0; ABORTED used0, completed s1 and aborted s2/s3, captured f1 excluded from merge, no sightings contributing to count rows; unknown counts. |
| `provider-timeout` | ANALYSIS with 3 total frames: f1 ANALYSED, f2 FAILED with timeout, f3 PENDING; analysed1; unknown label `macbook`, dropped label `background`, explicit provider failure. |
| `report-rejected` | REVIEW with valid computer range; REPORT with REJECTED, text null, reason `Report invents a count of 9; authoritative range remains 5–6`. No change to count rows. |
| `source-stale` | LIVE/FRESH, REPLAY/FRESH, REPLAY/STALE snapshots with unchanged operational facts, explicit updated_at and disabled decisions in the stale state. |

For every snapshot, event records through its watermark must exist in the scenario timeline; each event has a unique ID, contiguous sequence starting at 1, and public summary. Intermediate source/step changes get their own snapshot versions and event records. Populate provider/proposer metadata and all nullable fields explicitly, even in REQUEST. Complete snapshot status remains ACTIVE while review/approval is available; use COMPLETE only when the run is closed.

All applicable snapshots include pen NOT_OBSERVABLE, projected3px/min12px, reason `Below illustrative detection threshold`, arithmetic `0.006 m × 1000 px / 2 m = 3 px < 12 px (illustrative optics)`. Where there is no capture/geometry, pen remains NOT_OBSERVABLE only if the supplied planned observability establishes it; label it planned. Unknown computer/phone/person estimates are null, not zero. Include one degraded-geometry computer and raw provider score `0.73` with `provider detection score; not calibrated probability`.

- [x] **Step 5:** Run `node --test tests/survey_console/contract.test.mjs`; expect PASS. Document field and enum mappings, including demo REPLAY versus existing REPLAYED, without changing adopted contracts.
- [x] **Step 6:** Commit the four task files: `git commit -m "feat: define survey console projection and scenarios"` after staging only these files.

## Task 2: Deterministic fixture client with scoped evidence and idempotent decisions

**Files:** Create `fixture-client.mjs`, `tests/survey_console/client.test.mjs`.

**Interfaces:** `createFixtureClient(scenario: Scenario): SurveyClient` plus fixture-only `advance`, `resolveDecision`, and `calls` defined above. `Scenario.decision_results` supplies the permitted choices for the reviewer result selector; it never auto-settles a promise. Read methods clone their output. The default decision remains pending until the reviewer/test calls `resolveDecision`; it never advances a snapshot.

- [x] **Step 1: Write client tests.** Include unknown run, limit0/negative/over200, duplicate IDs with different bodies, missing/forbidden evidence and paginated events. Two equal attempts share one in-flight promise and one ledger entry; identical settled retries return the recorded result. Execution correction: IN_DOUBT is an intermediate outcome; retry preserves identity but opens another pending reconciliation response, and a different identity remains blocked until the outcome settles.

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import {makeScenario} from '../../examples/survey-console/fixtures.mjs';
import {createFixtureClient} from '../../examples/survey-console/fixture-client.mjs';

test('evidence is scoped to the selected run', async () => {
  const c = createFixtureClient(makeScenario('consent-required'));
  assert.equal((await c.read_evidence('fixture-consent-required','f1')).status, 'FORBIDDEN');
  assert.equal((await c.read_evidence('another-run','f1')).status, 'NOT_FOUND');
});
test('events are paged after the cursor and only through the exposed snapshot', async () => {
  const c = createFixtureClient(makeScenario('computers-followup'));
  const s = await c.read_survey('fixture-computers-followup');
  const rows = await c.read_survey_events(s.run_id, 0, 1);
  assert.ok(rows.length <= 1);
  assert.ok(rows.every(e => e.sequence > 0 && e.sequence <= s.event_sequence));
});
```

- [x] **Step 2:** Run `node --test tests/survey_console/client.test.mjs`; expect missing module failure.
- [x] **Step 3: Implement client closure.** Keep index, ledger keyed by idempotency key, and at most one unresolved record per attempt. Resolve run and frame before reading bytes. Generate or embed a tiny synthetic PNG byte array in this module; `width`/`height` must match decoded dimensions. Set evidence AVAILABLE only when the frame permits it. Validate attempt run, proposal ID/hash and expected version against the currently exposed snapshot; return CONFLICT without dispatch on mismatch. Key reuse with unequal payload throws IDENTITY_REUSED. Advance saturates at the last snapshot.

```js
const identity = JSON.stringify(attempt);
const prior = ledger.get(attempt.idempotency_key);
if (prior) {
  if (prior.identity !== identity) {
    throw Object.assign(new Error('Attempt identity reused with changed payload'), {code:'IDENTITY_REUSED'});
  }
  // IN_DOUBT is intermediate: renew the response promise, not the attempt identity.
  if (prior.latestStatus === 'IN_DOUBT') {
    prior.latestStatus = null;
    prior.promise = new Promise(resolve => { prior.resolve = resolve; });
    pending = prior;
  }
  return prior.promise;
}
// Store identity and the new promise before any await or callback.
// resolveDecision settles that promise with the same attempt_id and a fixture-only message.
```

- [x] **Step 4:** Add the duplicate/identity tests using the final computers snapshot and the complete Attempt record; assert `c.calls.length === 1`, same attempt ID in both returned results, and unchanged `flights.used` after ACKNOWLEDGED. Run client+contract test files; expect PASS.
- [x] **Step 5:** Stage task files and commit `feat: add deterministic survey fixture client`.

## Task 3: Snapshot/event controller resistant to gaps and run races

**Files:** Create `controller.mjs`, `tests/survey_console/controller.test.mjs`.

**Interfaces:** `createController(client)` returns `open(run_id): Promise<void>`, `refresh(): Promise<void>`, `ingest(events: SurveyEvent[]): Promise<void>`, `subscribe(fn): () => void`, and `getState(): {survey:Survey|null,events:SurveyEvent[],needs_refresh:boolean,log_gaps:number[],error:string|null}`. Subscribers receive accepted immutable state. `log_gaps` holds missing sequence numbers independently of `needs_refresh`, which blocks decisions only until operational state is reconciled. `open` starts a new epoch and clears the previous run's state before awaiting reads. `refresh` reads a full snapshot then paginates events after the accepted cursor, limit100.

- [x] **Step 1: Write race and invariant tests.** Use manually resolvable promises, not time delays. Verify older snapshots do not regress state; duplicates neither notify nor duplicate log entries; conflicting duplicate event identity or noncontiguous sequence marks needs_refresh; wrong-run events are ignored; malformed reads preserve last good state.

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import {makeScenario} from '../../examples/survey-console/fixtures.mjs';
import {createController} from '../../examples/survey-console/controller.mjs';

test('late run A read cannot replace run B', async () => {
  let finishA;
  const a = makeScenario('aborted').snapshots[0];
  const b = makeScenario('people-lower-bound').snapshots[0];
  const client = {
    read_survey: id => id === a.run_id ? new Promise(r => {finishA=r;}) : Promise.resolve(b),
    read_survey_events: async () => [],
  };
  const controller = createController(client);
  const openingA = controller.open(a.run_id);
  await controller.open(b.run_id);
  finishA(a); await openingA;
  assert.equal(controller.getState().survey.run_id, b.run_id);
});
```

- [x] **Step 2:** Run `node --test tests/survey_console/controller.test.mjs`; expect missing module failure.
- [x] **Step 3: Implement epoch/version checks and gap recovery.** Capture epoch before every async read and recheck before mutation. Events are notifications/log facts, never reducers for flight/count values. Track event cursor independently of snapshot version. A full validated snapshot with watermark at or beyond the missing sequence reconciles current operational state, but retain a visible log-gap notice until missing log entries are fetched; decision eligibility depends on reconciled operational state. Do not fabricate missing events. Paginate until fewer than100 results, rejecting non-progressing pages; preserve last state and expose an error on failure.

```js
const readEpoch = epoch;
const candidate = await client.read_survey(runId);
if (readEpoch !== epoch || candidate.run_id !== runId) return;
validateSurvey(candidate);
if (!state.survey || candidate.version > state.survey.version) {
  state = {...state, survey: structuredClone(candidate)};
}
// Still fetch missing event pages when the snapshot version is unchanged.
// Equal-version payload disagreement is a protocol error; never replace accepted state.
```

- [x] **Step 4:** Add a test with event sequences 1, 3 and then a version2 snapshot watermark3: before reconciliation decisions are blocked; afterwards operational state matches snapshot2, while log explicitly reports missing2. Include a 101-event pagination test and a repeated page failure. Run all three test files; expect PASS.
- [x] **Step 5:** Stage task files and commit `feat: reconcile survey snapshots and sequenced events`.

## Task 4: Accessible five-region shell and rail/frame presentation

**Files:** Create `dom.mjs`, `shell.mjs`, `index.html`, `console.css`, initial `app.mjs`, initial `scripts/ui_assets/verify_survey_console.cjs`.

**Interfaces:** `el(tag, attrs={}, children=[]): HTMLElement` accepts DOM nodes or strings, sets strings with `textContent`, treats `on*` attributes as forbidden, and allows only caller-owned attribute names. `renderShell(root, state, {onFrame,onClass,onObject}): void` renders Header/Left/Center/Right/Bottom and supplies child hosts `#counts`, `#evidence`, `#approval`. Only accepted controller state reaches rendering. `app.mjs` initially wires scenario selection, Next fixture snapshot and controller subscription; later tasks attach focused views.

- [x] **Step 1: Add the browser shell assertions.** Create the verifier with the same Chromium launch/finally-close pattern as `verify_browser.cjs`; base defaults to `http://127.0.0.1:8770`. Use `page.goto(base+'/examples/survey-console/')`. Assert all five `[data-testid]` regions exist, header contains FIXTURE DEMO, request is text without an editable input, rail has three stops, frame buttons use IDs, and legacy link points to `../../assets/sentinel-ui/console.html`.

```js
await page.selectOption('#scenario', 'computers-followup');
const before = await page.locator('#run-stepper').innerText();
await page.waitForTimeout(100);
assert.equal(await page.locator('#run-stepper').innerText(), before);
await page.locator('#next-snapshot').click();
assert.equal(await page.locator('[aria-current="step"]').innerText(), 'PROPOSAL');
assert.equal(await page.locator('#emergency-disconnected').isDisabled(), true);
```

- [x] **Step 2:** Start `python3 -m http.server 8770 --bind 127.0.0.1` from repository root in a separate terminal; run `node scripts/ui_assets/verify_survey_console.cjs`; expect missing page/controls failure.
- [x] **Step 3: Implement the document and layout.** Link `../../assets/sentinel-ui/tokens/tokens.css`; avoid `review.css`'s existing global composition rules. Put module entry `./app.mjs` at the end of body. Build top-level regions using CSS grid; internal panes scroll, no page horizontal overflow. At 390px stack all regions. Use a semantic ordered list for stepper and rail, native buttons for stops/frames, text for status and units, `<time datetime>` for timestamps, `aria-current="step"` on the supplied current step. Show used/max and next ordinal separately: `Flights used: 1 of 2` and `Proposed flight: 2 of 2`; aborted shows `Flights used: 0 of 2 · partial sweep aborted; excluded from merge`. Never derive a completed flight from completed stops.

```js
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (/^on/i.test(key)) throw new Error('Inline event handlers forbidden');
    if (value !== null && value !== false) node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
```

Label analysed/total progress “current sweep”; class count provenance may include only references present in the complete projection. Frame cards display commanded and measured pose separately, using `— · unavailable` for null; label analysis/evidence failures. Provider name/model/source_kind and analysed/total/dropped/unknown labels stay visible even with no counts. Use immutable original request + public proposer activity; no chat bubble suggesting a running assistant. Header emergency button says `Emergency stop unavailable — fixture demo` and remains disabled.

- [x] **Step 4:** Add a malicious request fixture in browser memory by calling `renderShell` with a cloned scenario whose request is `<img src=x onerror=alert(1)>`; assert literal text and no new image node. Verify arrow-free Tab/Enter/Space navigation, reduced-motion media styles, and 1440×900/390×844 no horizontal overflow. Run browser verifier; expect shell checks PASS.
- [x] **Step 5:** Stage task files and commit `feat: render fixture survey rail and frame progress`.

## Task 5: Honest class counts, uncertainty and report status

**Files:** Create `counts.mjs`, `tests/survey_console/counts.test.mjs`; modify `app.mjs` and browser verifier to mount count rendering.

**Interfaces:** `countText(count: Count): string`; `renderCounts(host, survey, {onClass,onObject}): void`. Class buttons pass a ClassName; object buttons pass object ID. Display supplied sightings/distinct stops/degradation and provider score semantics; no inference of counts from object-array length.

- [x] **Step 1: Write exact-copy unit tests.** Include confirmed0/uncertain0 versus null, pen/phone non-observable, person with timestamp, and rejected report authoritative counts.

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import {makeScenario} from '../../examples/survey-console/fixtures.mjs';
import {countText} from '../../examples/survey-console/counts.mjs';
test('computer range and person lower bound preserve semantics', () => {
  const computer = makeScenario('computers-followup').snapshots.at(-1).counts.find(c=>c.class_name==='computer');
  assert.equal(countText(computer), 'at least 5, possibly 6');
  const person = makeScenario('people-lower-bound').snapshots[0].counts.find(c=>c.class_name==='person');
  assert.equal(countText(person), 'at least 3, as of 2026-09-21T00:00:04Z');
  assert.equal(countText({...computer,confirmed:null,uncertain:null}), 'Unknown');
  assert.equal(countText({...computer,confirmed:0,uncertain:0}), 'at least 0, possibly 0');
});
```

- [x] **Step 2:** Run `node --test tests/survey_console/counts.test.mjs`; expect missing module failure.
- [x] **Step 3: Implement pure wording and DOM cards.** Show `NOT_OBSERVABLE` before any numeric wording; arithmetic/reason/pixel threshold are adjacent text. Person never follows range wording. Label phone uncertain and display follow-up rationale. Report REJECTED shows reason only, ABSENT says no accepted report, ACCEPTED shows supplied text as plain text. Keep raw score under its exact semantics, not a confidence percentage.

```js
export function countText(c) {
  if (c.observability === 'NOT_OBSERVABLE') return 'NOT_OBSERVABLE';
  if (c.semantics === 'SINGLE_VIEW_LOWER_BOUND') {
    return c.lower_bound === null || c.captured_at === null ? 'Unknown' : `at least ${c.lower_bound}, as of ${c.captured_at}`;
  }
  if (c.semantics !== 'RANGE' || c.confirmed === null || c.uncertain === null) return 'Unknown';
  return `at least ${c.confirmed}, possibly ${c.confirmed + c.uncertain}`;
}
```

- [x] **Step 4:** Run unit tests and extend browser checks for all four classes, `macbook` shown under unknown labels, pen arithmetic, phone before/after, rejected report with no accepted report text, and no person object card. Expect PASS.
- [x] **Step 5:** Stage task files and commit `feat: show bounded counts and explicit observability`.

## Task 6: Frame/object provenance and scoped evidence lifecycle

**Files:** Create `evidence.mjs`, `tests/survey_console/evidence.test.mjs`; modify `app.mjs`, `console.css`, browser verifier.

**Interfaces:** `supportingSightings(survey, selection): Sighting[]`, where selection is `{kind:'frame'|'class'|'object',id:string}`; `fitBox(containerWidth,containerHeight,imageWidth,imageHeight,bbox): {left:number,top:number,width:number,height:number}`; `createEvidenceView(host, client)` returns `select(survey,selection): Promise<void>`, `clear(): void`, and `dispose(): void`. The view exposes supporting frames, per-sighting scores and a selected-frame image. App highlights matching `[data-frame-id]` and `[data-sighting-id]` elements; selection never changes authoritative state.

- [x] **Step 1: Write geometry/selection tests.** Exclude unrelated sightings. Person class selection uses its supplied count sighting IDs; no identity merging. Test both portrait and landscape letterboxing and zero image dimensions (reject).

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import {fitBox, supportingSightings} from '../../examples/survey-console/evidence.mjs';
import {makeScenario} from '../../examples/survey-console/fixtures.mjs';
test('box aligns with contained landscape image', () => {
  assert.deepEqual(fitBox(400,400,800,400,[.25,.25,.75,.75]),
    {left:100,top:150,width:200,height:100});
  assert.throws(() => fitBox(400,400,0,400,[0,0,1,1]));
});
test('object selection excludes other objects', () => {
  const s=makeScenario('computers-followup').snapshots.at(-1);
  const rows=supportingSightings(s,{kind:'object',id:'computer-6'});
  assert.deepEqual(rows.map(r=>r.id).sort(), s.objects.find(o=>o.id==='computer-6').sighting_ids.slice().sort());
});
```

- [x] **Step 2:** Run `node --test tests/survey_console/evidence.test.mjs`; expect missing module failure.
- [x] **Step 3: Implement selection and box math.** Resolve IDs only against the current validated Survey; missing object/frame yields empty selection and an explicit reason. For object selection traverse only listed sighting IDs; for class selection use Count.sighting_ids. Group sightings by frame and offer native frame buttons; do not request evidence for nonselected frames. Display supporting sighting count/distinct stops supplied by the read model and each raw score with its provider/model/semantics. Use ResizeObserver to recalculate overlay on decoded image bounds.

```js
export function fitBox(cw,ch,iw,ih,[x1,y1,x2,y2]) {
  if (![cw,ch,iw,ih].every(n=>Number.isFinite(n)&&n>0) ||
      ![x1,y1,x2,y2].every(n=>Number.isFinite(n)&&n>=0&&n<=1) || x2<x1 || y2<y1) {
    throw new Error('Invalid image bounds');
  }
  const scale=Math.min(cw/iw,ch/ih), w=iw*scale, h=ih*scale;
  return {left:(cw-w)/2+x1*w,top:(ch-h)/2+y1*h,width:(x2-x1)*w,height:(y2-y1)*h};
}
```

- [x] **Step 4: Implement capability loading with an incrementing selection token.** Clear old image and revoke previous object URL before awaiting a new selection. Check token, run_id and frame_id before attaching bytes. Accept PNG/JPEG only, maximum 10 MiB, and time out after5s with `Evidence unavailable · timed out`; timeout cannot change analysis/count state. Decode image and use actual intrinsic dimensions; reject disagreement with declared dimensions. Never use an evidence response field as an arbitrary URL. Denial/revocation clears the image immediately. On snapshot change, re-resolve retained selection; if frame becomes forbidden/revoked or selection disappears, clear it. Revoke object URLs and disconnect ResizeObserver on dispose.

```js
const token=++selectionToken;
clearImage();
const result=await client.read_evidence(survey.run_id, frameId);
if (token!==selectionToken || result.run_id!==survey.run_id || result.frame_id!==frameId) return;
if (result.status!=='AVAILABLE') { showReason(result.status, result.reason); return; }
// showReason and clearImage are private view helpers using dom.mjs.
// Validate media/size, then URL.createObjectURL(new Blob([result.bytes], {type:result.media_type})).
```

- [x] **Step 5: Add browser capability-race tests.** Import `createEvidenceView` into `page.evaluate`, attach it to a temporary host, inject a fake client with two deferred reads, select runA then runB, resolve runB then runA, and assert host shows only runB. Change selected frame to REVOKED and assert zero images remain. Test NOT_FOUND/FORBIDDEN, invalid media, oversized bytes, and that an earlier decoded image cannot return after a switch. Use synthetic PNG fixtures from Task2. Confirm class and object clicks highlight only supporting frames/boxes and keyboard selection works. Run evidence unit tests + browser verifier; expect PASS.
- [x] **Step 6:** Stage task files and commit `feat: connect counts to scoped frame evidence`.

## Task 7: Full approval candidate and identity-preserving decision controller

**Files:** Create `approval.mjs`, `tests/survey_console/approval.test.mjs`; modify `app.mjs`, browser verifier.

**Interfaces:** `canDecide(survey,needsRefresh): boolean`; `createDecisionController(client,newId=()=>crypto.randomUUID())` returns `update(survey,needsRefresh): void`, `submit(decision): Promise<void>`, `retry(): Promise<void>`, `subscribe(fn): () => void`, `getState(): {attempt:Attempt|null,result:DecisionResult|null,pending:boolean,error:string|null,detached:boolean}`. `renderApproval(host,survey,decisionState,{onApprove,onReject,onRetry},needsRefresh): void` renders complete candidate and decision controls. `update` never transfers attempts across proposal/run identities. Detached prior results stay visibly associated with their old proposal until acknowledged by the reviewer; they never enable or mark a new proposal approved.

- [x] **Step 1: Write decision tests using deferred promises and deterministic IDs.** Validate pending duplicate clicks, stable retry after uncertain transport result, version conflict, stale/unknown eligibility, proposal replacement while pending, and ACKNOWLEDGED not flight dispatch.

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import {makeScenario} from '../../examples/survey-console/fixtures.mjs';
import {createDecisionController,canDecide} from '../../examples/survey-console/approval.mjs';
test('double click submits one attempt', async () => {
  const calls=[]; let finish;
  const client={submit_decision:a=>{calls.push(a);return new Promise(r=>{finish=r;});}};
  const s=makeScenario('computers-followup').snapshots.at(-1);
  const d=createDecisionController(client,()=> 'attempt-1'); d.update(s,false);
  const first=d.submit('APPROVE'); const second=d.submit('APPROVE');
  assert.equal(calls.length,1);
  assert.equal(d.getState().pending,true);
  finish({attempt_id:'attempt-1',status:'ACKNOWLEDGED',message:'Fixture acknowledgement only'});
  await Promise.all([first,second]);
  assert.equal(s.flights.used,1);
  assert.equal(d.getState().attempt.proposal_id,'p2');
});
test('stale or unknown consent cannot be approved', () => {
  const s=makeScenario('computers-followup').snapshots.at(-1);
  assert.equal(canDecide({...s,freshness:'STALE'},false),false);
  s.proposal.eligibility.consent=null;
  assert.equal(canDecide(s,false),false);
});
```

- [x] **Step 2:** Run `node --test tests/survey_console/approval.test.mjs`; expect missing module failure.
- [x] **Step 3: Implement full candidate renderer and eligibility.** Render every Proposal field, original request, target uncertain-object reasons and complete stop table, not just option ID. Include range/altitude/extent/duration/classes, mode/source, justification and guardrail disagreements. Empty disagreements must say `None reported`; this is not an assurance of safety. Unknown eligibility is explicit. Disable approval and rejection when projection is stale, unreconciled, no proposal, eligibility missing/false, or unresolved attempt exists. Keep details inspectable while buttons are disabled.

```js
export function canDecide(s,needsRefresh) {
  const p=s?.proposal, e=p?.eligibility;
  return Boolean(p && s.status==='ACTIVE' && s.freshness==='FRESH' && !needsRefresh &&
    e.current===true && e.consent===true && e.envelope===true && e.preflight===true &&
    p.disabled_reasons.length===0);
}
// Build once per deliberate fresh submission; retry reuses this exact frozen object.
const id=newId();
const attempt=Object.freeze({run_id:survey.run_id,proposal_id:survey.proposal.id,
  proposal_sha256:survey.proposal.sha256,expected_version:survey.version,
  decision,attempt_id:id,idempotency_key:id});
```

- [x] **Step 4: Implement decision transitions.** Set pending before calling the client. A thrown transport error becomes visible IN_DOUBT with preserved attempt; retry calls the same payload and key. Retry is blocked after proposal/run changes until reconciliation establishes the prior outcome; keep the old attempt visibly detached. CONFLICT requires `controller.refresh()` and a newer accepted snapshot before allowing a deliberate new attempt with a new ID. REJECTED/ACKNOWLEDGED are submission outcomes only; do not locally mutate proposal/flight state. A repeated click after ACKNOWLEDGED on the same run/proposal/hash remains disabled, including after harmless snapshot refresh. A replacement proposal may enable a new deliberate action only when the old attempt is settled and current eligibility permits it. Retain the settled prior attempt in an audit message labelled with its proposal ID.

- [x] **Step 5: Add exact retry/replacement assertions.** Fake first transport call rejects, second resolves; `assert.deepEqual(calls[0],calls[1])`. Replace proposal p2 with p3 while p2 is pending; resolve p2 ACKNOWLEDGED and assert result still names p2 and UI never labels p3 approved. On conflict assert new submission is blocked at same version and enabled only after update with newer eligible snapshot. Assert wrong-attempt response is treated as IN_DOUBT. Browser tests inspect every candidate field and use the reviewer-only result selector to resolve ACKNOWLEDGED/CONFLICT/IN_DOUBT; verify buttons disable immediately and rail never advances. Run all Node tests and browser verifier; expect PASS.
- [x] **Step 6:** Stage task files and commit `feat: preserve proposal identity through approval retries`.

## Task 8: Complete scenario assembly, accessibility and backend handoff

**Files:** Finalize `app.mjs`, `console.css`, `scripts/ui_assets/verify_survey_console.cjs`; create `examples/survey-console/README.md`; finalize `examples/survey-console/contract.md`.

**Interfaces:** `app.mjs` owns one fixture client/controller/decision controller/evidence view per selected scenario. It tears down subscriptions and object URLs on scenario change. No test-only global mutators; browser tests use public reviewer controls or module imports with dependency-injected clients. Views expose stable native controls and IDs documented below.

- [x] **Step 1: Add complete browser acceptance matrix.** Use `#scenario`, `#next-snapshot`, `#fixture-decision-result`, `#resolve-fixture-decision`, `#counts`, `#evidence`, `#approval`, `#run-stepper`, `#flight-budget`, `#analysis-progress`, `#report-status`, `#event-log`. A helper `advanceToEnd(page)` clicks Next while enabled, maximum20 clicks, then asserts disabled. Define this helper in the verifier (not the application).

```js
async function advanceToEnd(page) {
  for (let i=0;i<20 && await page.locator('#next-snapshot').isEnabled();i++) {
    await page.locator('#next-snapshot').click();
  }
  assert.equal(await page.locator('#next-snapshot').isDisabled(),true);
}
for (const id of ['computers-followup','people-lower-bound','phone-closer',
                  'consent-required','aborted','provider-timeout','report-rejected','source-stale']) {
  await page.selectOption('#scenario',id);
  await advanceToEnd(page);
  assert.match(await page.locator('[data-testid="Header"]').innerText(),/FIXTURE DEMO/);
  await page.screenshot({path:`artifacts/survey-console/${id}.png`});
}
```

Add scenario-specific assertions, not just screenshots:

| Scenario | Required browser assertion |
|---|---|
| computers-followup | “at least 5, possibly 6”; computer-6 targets p2; select object highlights supporting frames only; used1/2 |
| people-lower-bound | SINGLE_VIEW_LOWER_BOUND and exact supplied timestamp; no person identity card |
| phone-closer | Before advance NOT_OBSERVABLE/6px; afterwards OBSERVABLE/18px, uncertain1 and used2/2 |
| consent-required | No evidence image, explicit FORBIDDEN, consent failure, disabled approval |
| aborted | used0/2, ABORTED, not merged, excluded frame; no inferred count |
| provider-timeout | 1/3 analysed, timeout on f2, pending f3, unknown macbook and dropped background |
| report-rejected | REJECTED/reason visible, computer range remains5–6, no accepted invented report |
| source-stale | Inspect all3 snapshots: LIVE, REPLAY, STALE timestamp; no operational progress caused by source change |

- [x] **Step 2:** Run browser verifier before finishing assembly; expect missing scenario-specific wiring to fail.
- [x] **Step 3: Wire views without destroying pending/focused state.** Maintain stable region/host nodes; update their contents by key, or capture and restore focused element ID when an accepted projection requires replacement. Decision controller and evidence view persist for a selected run. A decision state notification only rerenders the approval host; it cannot rebuild the entire controller. On snapshot refresh update shell/counts/approval and invalidate evidence as needed. Event announcements use a dedicated `aria-live="polite"` region and announce only changed high-level facts; operational faults use `role="alert"`. Do not move focus or autoscroll when a new event arrives. Conflict focuses the decision heading after refresh. Keep prior unresolved attempt details visible through same-run proposal changes.

```js
const unsubscribe=controller.subscribe(state=>{
  decision.update(state.survey,state.needs_refresh);
  renderShell(root,state,selectionHandlers);
  if (!state.survey) return;
  renderCounts(document.getElementById('counts'),state.survey,selectionHandlers);
  renderApproval(document.getElementById('approval'),state.survey,
    decision.getState(),decisionHandlers,state.needs_refresh);
});
// selectionHandlers owns onFrame/onClass/onObject and calls evidence.select with exact kind/id.
// decisionHandlers owns onApprove/onReject/onRetry and delegates to decision methods.
// Scenario disposal calls unsubscribe(), evidence.dispose(), and the decision unsubscribe.
```

Define these handlers in `app.mjs`; prevent reentrant duplicate renders when `decision.update` emits. `renderShell` must reuse child host nodes so an evidence view never points at a detached node. Scope app callbacks to the active scenario epoch to ignore settled promises from a disposed controller.

- [x] **Step 4: Add accessibility/layout/network checks.** Capture console/page errors and any external-origin request. Fail if a browser request leaves the local server origin (blob evidence URLs are allowed). At1440×900 all5 regions are inside viewport; internal scrolling exposes the entire proposal. At390×844 all content remains reachable without horizontal document overflow. Verify native keyboard scenario/stop/frame/count/approval actions, visible focus, reduced-motion computed animation `none`, text status independent of color, 16px body/14px metadata, and a40px header symbol. Confirm no clocks/intervals advance flight or analysis state. Add computed contrast checks for any new text/status color pairs or use unchanged validated token pairings.

- [x] **Step 5: Write run and backend handoff docs.** Include these exact launch commands and distinguish Node unit tests, browser acceptance, and non-coverage of live integration:

```sh
# Terminal 1, repository root
python3 -m http.server 8770 --bind 127.0.0.1
# Terminal 2, repository root
node --test tests/survey_console/*.test.mjs
node scripts/ui_assets/verify_survey_console.cjs http://127.0.0.1:8770
```

README links `/examples/survey-console/` and `/assets/sentinel-ui/console.html`, describes each reviewer control, says no direct-file support is promised for ES modules, and marks screenshots as fixture evidence. Store verifier report under ignored `artifacts/survey-console/validation.json`, not asset-kit `validation.json`.

In `contract.md`, list each method/type from the shared definitions and the guarantees tested in Tasks1–7. Add a backend-owner checklist: adopted/versioned projection; authoritative counts/flight merge and observability; source mapping; cursor/snapshot watermark behavior; run-scoped capability and revocation; typed approve/reject identity mapping; existing emergency legality; actual consent/provider/flight/proposer readiness. These require a separate backend-integration design before replacing the fixture client. Do not send GitHub requests or tag collaborators merely because this document lists an owner responsibility.

- [x] **Step 6: Run final acceptance once.** `node --test tests/survey_console/*.test.mjs`, then browser verifier; expect PASS. Run `git diff --check`. Confirm `git diff --name-only` contains only new example/test/verifier/docs files and no asset-kit or backend files. Since this follow-up does not alter Python/runtime code or asset-kit outputs, do not claim full backend tests or rerun generators that rewrite kit hashes. If implementation changes those boundaries, stop and revise scope and validation.
- [x] **Step 7:** Stage exact task files and commit `test: complete survey console acceptance and handoff`. Report actual test counts, screenshot locations and any untested limitations. Submit a follow-up PR only when separately authorized; no merge or live deployment is part of this plan.

## Coverage and self-review

| Spec requirement | Owning tasks |
|---|---|
| Separate PR, unchanged brand/tokens/legacy, five regions | 4,8 |
| Proposed UI seam; no SQL/SDK/live dependencies | 1,2,8 |
| Rail/stepper/stop details, explicit event advancement, frame strip | 1,3,4 |
| Budget and aborted/nonmerged semantics | 1,3,4,8 |
| Computer/person/phone/pen semantics, unknown/dropped labels | 1,5,8 |
| Linked frame/object/box evidence and exact scores | 2,5,6 |
| Complete candidate, eligibility, attempt identity and conflicts | 1,2,7 |
| Immutable request and no conversational UI | 4,8 |
| All eight fixtures and live/replay/stale distinction | 1,8 |
| Unsupported schema, gaps, race conditions, inert strings | 1,3,4,6,7 |
| Report rejection and no fabricated results | 1,5,8 |
| Backend ownership and live-readiness gates | 8 |

Self-review performed when drafting: requirements mapped above; proposed types and method names centralized; review-focus cases assigned to tests; code examples provided for critical behavior. Runtime code and tests have not been implemented or executed as part of planning.

Recommended execution: **Native**, followed by one independent whole-branch review. The eight tasks share a small projection/controller boundary and all behavior is fixture-only; keeping one implementer across those interfaces is economical. Use subagent-driven execution if independent review after each task is preferred. Both paths require reviewing this plan before implementation under the invoked writing-plans skill.
