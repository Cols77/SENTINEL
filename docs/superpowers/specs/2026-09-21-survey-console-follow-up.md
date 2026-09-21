# Survey console follow-up specification

Status: approved for native fixture-only execution on 2026-09-21; implemented in the separate follow-up branch. Live integration remains out of scope.

## Decision and sources

Keep PR #7 as the reusable asset-kit foundation. Build this follow-up on a separate branch/PR. The user accepted this scope on 2026-09-21.

Primary requirements: [PR #7 integration review](https://github.com/Cols77/SENTINEL/pull/7#issuecomment-5749878495). Baseline inspected: PR head `367d29ce8d06e9fb3e03fcb3c72a0a741732ad16`. Also read `assets/sentinel-ui/README.md`, `assets/sentinel-ui/component-specs.md`, and `scripts/ui_assets/verify_browser.cjs`. Existing `src/sentinel/client.py` is a DroneClient protocol, not a UI client; do not extend it for this console.

The review's claims about local-only branches and unfinished backend work are historical context, not a current verification of those branches. Do not read or import sibling worktrees. Recheck integration readiness when planning the later backend adapter.

## Deliverable and boundaries

Deliver an independently runnable, fixture-driven browser console under `examples/survey-console/`. Reuse the approved tokens, icons, symbol, and placeholders via relative asset paths. Preserve the five regions and link to the existing legacy alley study. Do not modify the asset kit, its generators, manifest, or validation report. Use plain browser ES modules, Node's built-in unit test runner, and the repository's existing Playwright approach. This is a reviewable survey composition and proposed UI contract, not the final NiceGUI runtime or a flight-capable application.

A second, separately scoped backend integration project must own the real survey projection and adapter. This follow-up documents the requested seam but does not implement SQLite access, flight dispatch, detectors, counting/merge algorithms, LLM/Jev execution, or a conversational interface. The fixture boundary intentionally supports replacing its client while retaining presentation and controller tests.

## Global constraints

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

## Required composition

Header: identity, fixture banner, source/freshness timestamp, visible flight budget, and disabled emergency control explicitly labelled as disconnected. Retain the legacy-study link. Do not imply that this demo can stop a drone.

Left: immutable original request, proposer mode/source F (rules), E (LLM), J (Jev), public activity, and reviewer-only scenario/advance controls. Reviewer controls must be visually separated from operator approval.

Center: REQUEST → PROPOSAL → APPROVAL → FLIGHT → ANALYSIS → REVIEW → REPORT, selected sweep option and every stop on a horizontal rail, planned range/altitude/extent/duration/resolvable classes. Show explicit stop states, including unknown, failed, and aborted. Frame strip shows capture timestamp, commanded and measured pose independently, analysis status, evidence state and provider/consent failure. Active-stop animation is decorative only and respects reduced motion.

Right: class counts, uncertainty/failure summary, selected object provenance, and a fully inspectable approval candidate. Keep overflow inside accessible scroll regions at 1440×900; stack regions on narrow screens.

Bottom: sequenced public events, progress (analysed/total), dropped and unknown labels, report status/rejection, provider/model/score semantics and failure messages. Do not display rejected report text as a valid result.

## Count semantics

Computer: confirmed + uncertain range (e.g. “at least 5, possibly 6”), supporting sightings, distinct stops, and geometry degradation. UI renders backend-supplied aggregates; it does not merge sightings or derive count estimates.

Person: SINGLE_VIEW_LOWER_BOUND, “at least N, as of <capture time>”; no exact aggregate, identity track, or merged person object.

Phone: show explicit observability, pixel-size limit, uncertainty, and the reason a closer follow-up could help. Do not turn a non-observable class into zero.

Pen: NOT_OBSERVABLE with projected-pixel arithmetic and threshold; fixture optics and dimensions are explicitly illustrative, not calibrated claims. Display the supplied arithmetic explanation; do not infer real-world detectability in the UI.

Clicking a class highlights its supporting frames and sightings; clicking an object highlights only that object's frames and boxes. Raw scores retain exact supplied score semantics; never rename arbitrary scores “confidence”.

## Client and lifecycle

The proposed seam has read_survey(run_id), read_survey_events(run_id, after_sequence, limit), read_evidence(run_id, frame_id), and submit_decision(attempt). It is not an adopted coordinator wire API. Payloads are JSON-compatible, allowlisted projections without internal paths. Snapshot versions, event sequences, proposal identity and attempt identity are explicit.

Events prompt a projection refresh; they never independently increment counts or flights. Ignore older/duplicate snapshots and events. Sequence gaps keep the last good snapshot with a refresh warning and disable decisions. A complete, newer snapshot can reconcile a gap. Reject malformed projections without replacing good state. Ignore delayed responses for another run. Explicit source/freshness belongs to the projection; freshness timeout, if later adopted, can only mark stale, never advance operational progress.

Approval presents original request, proposal ID/hash, stops, option, range, altitude, extent, duration, resolvable classes, proposer mode/source, code-derived justification, guardrail disagreements, uncertain-object targets, and consent/envelope/preflight eligibility. Missing, false or stale eligibility disables actions. Submit the same identity on a retry; after conflict, refresh and require a deliberate new attempt. Duplicate clicks cannot create a second submission. Unknown outcomes remain visible and do not enable a fresh attempt for the same unresolved decision. A newer proposal never silently inherits an older pending attempt or result.

## Eight required scenarios

1. Five confirmed computers plus one uncertain; a proposed closer follow-up targets the uncertain object. Budget remains 1/2 until a second completed flight snapshot arrives.
2. People are a timestamped single-view lower bound, with one supporting capture and no merged person track.
3. Phone NOT_OBSERVABLE after sweep 1, then observable but uncertain after a closer completed sweep; budget 2/2 and progress from explicit snapshots.
4. Consent required, no frame retained, evidence forbidden, decision disabled.
5. ABORTED partial sweep, frames labelled excluded from merge, flights used unchanged, no merged partial result.
6. Provider timeout with analysed/total mismatch, per-frame error and explicit unanalysed remainder.
7. Report rejected for invented count; structured rejection visible, authoritative counts unchanged.
8. Distinct LIVE and REPLAY fixture sources, plus stale state with timestamps and decisions disabled.

Additional acceptance: unsupported projection version, event gaps/duplicates, run-switch response races, evidence forbidden/revoked, normalized-box letterboxing, approval retry/conflict/proposal replacement, malicious public text, keyboard interaction, reduced motion, and unknown-vs-zero presentation.

## Backend handoff and exit criteria

Backend owner must adopt/version the projection, define source-kind mapping (existing kit uses REPLAYED), event pagination and snapshot watermark semantics, authoritative flight/merge/count/observability facts, run-scoped evidence capabilities, and typed decision attempt mapping to coordinator commands. Confirm the existing command legality and emergency mapping before any live integration. UI must never invent an executable coordinator command from the proposed fixture seam.

Live integration additionally requires pushed/integrated survey, WP5 flight bridge and proposer dependencies; provider/profile approval; consented frames; detector readiness; actual emergency support; and runtime contract conformance tests. These are exit gates for a separate live-integration plan, not prerequisites for this fixture demo.

Done when all eight scenarios are selectable, client and browser tests pass, evidence/approval race tests pass, no external network calls occur, the 1440×900 and 390×844 layouts work, and the handoff documents explicitly identify the demo and proposed contract as non-production.
