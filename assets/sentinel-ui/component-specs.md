# SENTINEL component specifications

This kit specifies presentation and contains interactive local demonstrations. It implements no coordinator commands. Example values are illustrative, not wire-valid run fixtures. The WP2 spec and adopted coordinator contracts remain authoritative.

## Shared rules

- Each runtime region/component exposes a stable `data-testid`; Header, Left, Center, Right and Bottom are the five required regions.
- Use actual DOM text, buttons, focus indicators and labels. Icons accompanying text are decorative (`aria-hidden=true`); icon-only controls have accessible names.
- Use `tokens/tokens.css`. Minimum body text is 16px, secondary metadata 14px. Source IDs and timestamps use the mono stack.
- Never use logo color as telemetry. Header logo is `brand/symbol-dark.svg`, 40px with 8px clear space. Compact variants are for sizes below 40px.
- Source badges LIVE/SIMULATED/REPLAYED; freshness STALE; operation outcome IN_DOUBT; video status STARTING/LIVE/DEGRADED/UNAVAILABLE/STOPPED are separate axes. The review sheet's FRESH means no stale presentation flag, not a new persisted enum.
- Unavailable values use an em dash and a reason. Do not carry forward stale numeric values without a visible stale label and timestamp.
- Announce routine status changes through a polite live region; emergency acknowledgement and operational failures use an alert. Do not re-announce unchanged polling results.
- Decorative schematic/overlay examples never establish state. All operational data comes through CoordinatorPort read models.

## Header

Purpose: establish the run, source, freshness and layout mode.

Inputs: `StateSnapshot.run_id`, `state_version`, `latest_event_sequence`, `mountain_snapshot_version`, `updated_at`; `VideoStatus.source_kind`; plan `mode`; coordinator mountain snapshot freshness/as-of facts. Apply WP2's time/sequence stale rules using fixture `snapshot_stale_after_ms`.

States: loading, normal, stale, source LIVE/SIMULATED/REPLAYED, marker mode, route-only. Keep the last snapshot visible with STALE while requesting refresh. Judged runs cannot select replay. Symbol has accessible name SENTINEL.

Actions: header does not own operational state. Emergency remains visible in every header variant, with a labelled keyboard fallback.

## Telemetry

Purpose: present supplied link/battery/mode/measurements with units and freshness.

Contract availability: the inspected `StateSnapshot` has no altitude, speed, orientation, distance, numeric battery or drone-link fields. `PreflightReport.checks` can establish that a check passed, not a battery percentage or current connection. A typed coordinator extension or existing adopted read model must be identified before connecting these display slots. Show unavailable values until then; do not infer flight mode from `run_status`.

States: available, stale, unavailable, coordinator-supplied low-battery warning. Units: battery %, height/distance m, speed m/s, angle ° only when their source semantics are known. Values do not animate through fabricated intermediate numbers.

Accessibility: definition list or labelled cells; text warning accompanies battery color. No operator action in this component.

## Operator panel

Purpose: show operator intent and public structured activity, not private chain-of-thought.

Inputs: public event/read-model content such as `EventEnvelope.event_type`, `occurred_at`, `payload` and `StateSnapshot.agent_phase`. Do not display a raw payload dump; use approved public summaries.

States: idle, submitting, pending, rejected, failed, disabled during emergency. Submission preserves entered text if a recoverable failure occurs. Pending state identifies the request and prevents creating an accidental duplicate attempt.

Actions: intent submit through the existing typed command contract. No SDK calls. Label the input; keep validation text associated through `aria-describedby`; restore focus to the input after successful submission.

## Schematic and mission progress

Purpose: zone-level progress with evidence drill-down. Use exact `section_id` polygons and statuses from `schematics/source-fixture.json` and mountain snapshot fields; mission banner uses `StateSnapshot.run_status`/`agent_phase`. Do not calculate flight progress from elapsed time or zone order.

States: UNSURVEYED, SURVEYING, SURVEYED, HAZARD_PENDING, BLOCKED. Selection, keyboard focus and stale remain independent. Route-only includes only LEFT_ALLEY_MIDDLE and RIGHT_ALLEY_MIDDLE and reads `route-only, not visually verified`.

Actions: click, Enter and Space select a zone and call `read_hazard(target_id)` for linked data. Missing link renders `no linked hazard`. Selection changes no survey status. Provide a matching accessible text list. Production must reconcile any hazard/target mapping using the fixture/read model; the graphic cannot invent it.

Use inline SVG when applying CSS state or keyboard behavior. The standalone SVG groups supply stable IDs and button semantics; the consuming component supplies handlers. Embedding an SVG as an `<img>` makes it a static illustration.

## Camera and marker presentation

Inputs: `VideoStatus.status`, `source_kind`, `last_frame_at`, `degradation_reason`, `selected_frame_artifact_id`, `selected_frame_sha256`, and `SafeVideoStreamHandle.stream_url`. The UI never receives raw frames. Image coordinates are normalized to the displayed frame before drawing a supported box; account for letterboxing.

States: STARTING uses `camera-connecting.svg`; UNAVAILABLE/STOPPED uses `camera-unavailable.svg`; DEGRADED uses the warning banner plus the supplied reason. A retained stale frame must be labelled with its original timestamp. No current frame must not look live.

Marker contract distinction: `StationCaptureProvenance.marker_id` describes a station capture. `PerceptionResult.bbox_xyxy_normalized` and score describe the classified rock, not a live marker detector. Do not relabel a rock box as a marker box or use a station ID as a current detection. Live marker detection/pose/score require a verified supported coordinator payload; the kit currently shows them as unavailable except for explicitly illustrative treatment examples.

The overlay files contain no valid physical markers. A runtime marker label is actual DOM/SVG text. No fabricated confidence percentage; render a provider score only alongside its exact semantics.

## Approval and plan review

Inputs: `PlanReadModel.plan_id`, `plan_sha256`, `mode`, `station_ids`, `approval_id`; `PlanApproval` binding/expiry as provided; lifecycle facts from coordinator events; `ApprovalReadModel.task_id`, `expected_state_version`, `eligible`, `evidence`, `perception`, `decision_threshold`, `ranking`, `provenance`. Display worker/run/hash bindings, immutable preflight and evidence before enabling decisions as WP2 requires.

States: proposed, awaiting required facts, eligible, submitting, approved, dispatched, rejected-event, version conflict, expired, evidence unavailable, IN_DOUBT, emergency-disabled. PLAN_REJECTED is an event, not a terminal plan state. Do not add a lifecycle field to PlanReadModel if absent; use the adopted coordinator projection.

Actions: CONFIRM/REJECT only if coordinator-declared legal and required facts are present/current. Submit one `CommandEnvelope` using the appropriate OPERATOR_DECIDE payload. Duplicate clicks and retries reuse that attempt's `command_id`, `correlation_id` and `idempotency_key`. State-version rejection refreshes before a new action gets a new identity. Announce errors and preserve review context. Do not invent a separate CONFIRM_DISPATCH action from a generated image label.

Accessibility: native buttons, disabled reason text, pending label, keyboard activation, and focus returned to the refreshed decision heading after a version conflict. Details remain accessible without hover.

## Evidence and provenance

Inputs: run-scoped `read_evidence(run_id, artifact_id)` capability; public `ArtifactRef.artifact_id`, `sha256`, `media_type`, `byte_size`, `source_kind`, `created_at`; `PerceptionResult` provider/model/revision fields, `raw_score`, `score_semantics`, optional box, and station capture correlation; approval ranking/threshold/provenance.

States: empty, loading, available, NOT_FOUND, FORBIDDEN, failed/expired/revoked, stale, IN_DOUBT. Use the placeholder that describes the actual condition and display the stable error code where relevant. Do not manufacture replacement evidence.

The current wire ArtifactRef includes `storage_key`; WP2 forbids exposing/using it in the UI. Present only allowlisted public metadata. Never make an image URL from that field. Capability reads allow JPEG/PNG only and obey the coordinator's 10 MiB and timeout limits.

Accessibility: descriptive image text identifying the capture and its evidentiary status; hashes selectable/copyable with a text confirmation. Truncated hashes expand to the complete value on request.

## Preflight and force-new-run

Inputs: `PreflightReport.report_id`, `run_id`, `plan_id`, `plan_sha256`, `passed`, `checked_at`, and `checks[].name/passed/checked_at`. Show the exact eight entries:

1. WIFI_INTERFACE
2. DRONE_LINK
3. BATTERY_TELEMETRY
4. LIVE_VIDEO
5. LAYOUT_MARKERS
6. BOUNDED_AREA
7. HEIGHT_ENVELOPE
8. EMERGENCY_LATCH

States: unknown, checking, passed, failed, report stale, dispatch ineligible, emergency-disabled. A passed EMERGENCY_LATCH check is displayed as `Check passed · latch clear`, never `Emergency active ✓`. Unknown is not failure or success.

Actions: operator-only FORCE_RUN_CREATE from a failed report, with reason; records the failed report and creates a new run. It does not resume the previous operation and is unavailable while the emergency latch is active. Require adopted coordinator eligibility/command support before enabling it.

Accessibility: eight-row list with check name and explicit Passed/Failed/Unknown text; reason textarea has a visible label and validation message. Review example fields are illustrative only.

## Emergency

Purpose: keep emergency access available and accurately communicate acknowledgement.

States: ordinary presentation, request pending, acknowledged latch, acknowledgement unavailable. A stop request immediately disables ordinary controls, while the stop control remains visible and retriable according to the existing emergency contract. Do not claim landing or physical safety from UI acknowledgement.

Action: visible `EMERGENCY STOP`; proposed keyboard fallback Shift+Escape, documented next to the control. Use the adopted typed command mapping; the current wire command is `EMERGENCY_SIGNAL`, whereas WP2 names the presentation action EMERGENCY_STOP. Never send an invented command type.

Current contract discrepancy: inspected `StateSnapshot.legal_operator_actions` permits only CONFIRM and REJECT, while WP2 requires emergency and gated force-new-run actions. Integration must reconcile the adopted coordinator contract before claiming WP2 conformance. This kit neither changes that schema nor bypasses the coordinator.

Accessibility: native button with explicit name; an alert announces pending/acknowledged state; focus remains usable after ordinary controls disable. The local review demo's stop button changes illustrative presentation only and its acknowledgement button is a reviewer control, not a proposed production action.

## Events and warnings

Inputs: `EventEnvelope.event_id`, `sequence_no`, `event_type`, `occurred_at`, `state_version` and allowlisted public payload summaries. Render in coordinator sequence order; deduplicate by event identity. Never fill a sequence gap with a synthetic transition.

States: empty, populated, loading older entries, gap/refresh pending, fault, emergency, coordinator-supplied low battery or marker missing. A warning needs a reason and timestamp where available. `IN_DOUBT` remains visible until a coordinator reconciliation result resolves it.

Accessibility: semantic list, readable timestamps and concise summaries; no auto-scrolling that takes control from someone inspecting older events. Live announcement is limited to new meaningful high-level events.

## Integration acceptance handoff

The later NiceGUI stage uses contract-valid fixtures and fake CoordinatorClient, verifies all five regions at 1440×900 and no horizontal overflow, then exercises duplicate actions, stale version, evidence missing/forbidden, marker missing, preflight failure, IN_DOUBT and emergency states. The kit's local HTML examples are not a substitute for those tests.
