# SENTINEL Native Coherence Bootstrap with Contract Coverage Plan

> **For Hermes:** Implement only after the linked Coherence contract-artifact capability is landed, using `subagent-driven-development` task-by-task.

**Goal:** Add a small native Coherence bootstrap to SENTINEL that identifies the current V0 authority, registers the user-approved subset of the atomic candidate SRs below (proposal-only until consent), bundles the V0 feature, and declares its critical schemas, fixtures, DDL manifest, code consumers, and tests for native trace and freshness reporting.

**Architecture:** SENTINEL stays a consumer of native Coherence. It authors an explicit `.factory/contracts.yaml` catalog, a short authority document, the consent-approved subset of the atomic candidate SRs, one FEAT dossier, and one bundle. Existing JSON Schema/fixture/DDL validation remains in the SENTINEL test suite; Coherence records relationships and invalidates evidence when declared contract inputs change.

**Tech Stack:** Python 3.11, existing SENTINEL `jsonschema` contract validation, pytest, native Coherence CLI released from the linked tooling plan.

**Plan baseline:** `c4ca05890c0758d8faa0f01589ef1b6961b61b65` (`fix: enforce public wire root validation`) on branch `docs/sentinel-native-coherence-bootstrap-plan`.

**Dependency:** The compiler/catalog, `contract:<id>` trace/bundle support, and contract-aware freshness work in `C:/coding/pi-agent-factory-wt/optional-contract-artifacts-plan/.hermes/plans/2026-09-17_135641-optional-contract-artifacts.md` must land and be available in the consumer environment before catalog activation and native verification.

---

## Decision record

### Why this is a native bootstrap rather than custom tooling

SENTINEL will not create a separate contract manifest format, custom staleness graph, or bespoke evidence engine. `.factory/contracts.yaml` is the optional native Coherence catalog; the existing contract tests remain the semantic validators. This limits authoring to a small number of trace anchors while making real contract changes visible to native health/freshness tooling.

### Contract compilation usage

The Coherence compiler is worth using because SENTINEL has local JSON Schema references, fixed fixtures, a checked-in DDL manifest, and Python consumer/test paths. It will compile their declared relationship closure and hashes; it will **not** replace `src/sentinel/contracts/schemas.py`, `src/sentinel/contracts/ddl_manifest.py`, or pytest validation.

### Baseline and reconciliation rule

This plan starts from the committed G0 contract baseline, not either dirty shared checkout. Before implementation, rebase or merge the plan branch onto the then-current accepted V0 documentation/contract commit, inspect the exact diff, and update catalog paths/IDs only for artifacts that still exist. Never silently absorb uncommitted work from another worktree.

### Human-consent boundary

Do not author or accept any SR automatically. Present each proposed SR from the atomic matrix below with its exact authority anchors, allocation, and acceptance criteria for approval. The user may approve any subset; unapproved semantics remain unregistered rather than being inferred by an agent.

## Proposed authority hierarchy

1. `docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md` — V0 cross-cutting authority.
2. `docs/superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md` — WP0 shared-contract authority.
3. Files under `docs/superpowers/specs/contracts/` — normative machine-readable contract artifacts under WP0 ownership.
4. `src/sentinel/contracts/` — current implementation consumers and validators.
5. `tests/contracts/` and `tests/test_indoor_alley_contract_probe.py` — executed validation evidence, not normative sources.
6. Historical plans/specs must be explicitly marked active, superseded, or deferred. In particular, classify `docs/superpowers/plans/2026-09-15-sentinel-djitellopy-backend.md` before accepting its trace status.

## Proposed requirement set (proposal-only until consent)

These are candidate semantic anchors, not approved requirements until the consent checkpoint (Task 4). Canonical registration additionally remains gated on the upstream Coherence contract-artifact capability landing (Dependency section, Task 2); no SR below is canonical while that capability is unavailable.

### Four coarse framing candidates (summary only — not a sufficient set)

The four candidates below preserve the original bootstrap intent (authority/status, closed contract roots, safety/provenance/operator-approval, six-zone staleness) but are **framing summaries, not a sufficient requirement decomposition**. The atomic matrix in the next subsection is the actual candidate list Task 4 must present for consent.

| Candidate | Authority | Intent |
|---|---|---|
| SR-SENTINEL-V0-001 | V0 coherence specification | Declare the canonical active V0 authority and the status of historical material. |
| SR-SENTINEL-V0-002 | WP0 shared-contract specification | Require closed, locally resolvable machine-readable contract roots and declared consumer/validator relationships. |
| SR-SENTINEL-V0-003 | V0 and WP0 safety sections | Preserve provenance, operator-approval, safety, and status invariants through contract validation. |
| SR-SENTINEL-V0-004 | V0 indoor-alley behavior | Preserve six-zone/indoor-alley semantics through contract fixtures and current probe tests. |

### Proposed atomic functional SR matrix (proposal-only)

Each candidate is an atomic functional capability with a stable placeholder ID, a normative statement, source-backed anchors, acceptance criteria, a proof shape, an evidence class, and listed uncovered/deferred topics. IDs are proposal anchors only; nothing becomes canonical until the user approves it at the consent checkpoint and the upstream capability is available. Evidence classes:

- **contract/bootstrap-verifiable** — provable now by existing contract tests/probe or by the native compiler once the upstream capability lands;
- **implementation-pending** — proof requires spec-target tests/code that are not yet in the tree (created only by later WP implementation work, never by this bootstrap);
- **human/live** — requires a supervised real-Tello rehearsal artifact or a named human joint-approval record.

#### A — Agent/model boundary and catalogue-bound proposals

**SR-SENTINEL-V0-A01 — LLM adapter and model boundary.** *Normative statement:* the LLM runtime consumes only the typed `#AgentRequest`/`#AgentModelResult` ports via `AgentModelPort.propose`; it has no SQLite, drone socket, perception provider, policy-arithmetic, approval-transition or UI-storage access, and no model result contains a raw SDK call or dispatch instruction. The adapter-local codes `LLM_CONFIG_MISSING`, `LLM_CONFIG_INVALID`, `LLM_DEPENDENCY_MISSING`, `LLM_CREDENTIAL_MISSING` are not part of the shared coordinator registry. *Anchors:* WP1 §2, §3, §3.1; WP0 §3.5; `docs/superpowers/specs/contracts/llm-adapter-config-v1.schema.json`. *Acceptance:* config validates against the schema; each adapter-local failure is proven per-code; the deterministic fake requires no optional dependency or credential. *Proof shape:* `uv run python -m pytest tests/agent -q` (WP1 §9; `tests/agent` not yet in tree). *Evidence class:* implementation-pending. *Uncovered/deferred:* real provider configuration (spec intentionally declares none).

**SR-SENTINEL-V0-A02 — Catalogue-bound proposals, never dispatch.** *Normative statement:* a successful `INTENT` proposes exactly one `catalogue_plan_id`; the coordinator resolves its catalogue revision/hash before persisting the immutable plan; `NO_ACTION`, `MALFORMED` and `UNAVAILABLE` carry no proposal; no raw SDK command, coordinate, height or unbounded capture count is ever emitted; the agent never dispatches a plan. *Anchors:* WP1 §1, §3.1, §4 row 2; indoor-alley design §2; `docs/superpowers/specs/contracts/survey-catalogue-v1.schema.json`; `tests/test_indoor_alley_contract_probe.py::test_agent_proposal_is_catalogue_bound_and_force_run_is_typed`. *Acceptance:* proposal → immutable plan only; no dispatch path from the model result. *Proof shape:* existing probe plus WP1 §7 simulated-flow tests. *Evidence class:* contract/bootstrap-verifiable (probe) + implementation-pending (flow). *Uncovered/deferred:* revisit policy beyond “new bounded revisit requires fresh approval”.

#### C — Coordinator

**SR-SENTINEL-V0-C01 — Plan identity, canonical serialization, immutability.** *Normative statement:* `plan_sha256` is the SHA-256 of the canonical serialization of the immutable `SurveyPlanReference` (ordered `station_ids`, schema field order, deterministic formatting); plan equality is that hash; the coordinator persists the immutable plan (`PLAN_CREATED`) before any dispatch. *Anchors:* WP1 §4.3; WP0 §3.2 (`#SurveyPlanReference`, `#PlanApproval`); `tests/test_indoor_alley_contract_probe.py::test_wire_contract_exposes_immutable_plan_preflight_and_capture_records` and `::test_review_rework_adds_plan_approval_catalogue_lifecycle_and_preflight_read`. *Acceptance:* equal plans hash equally; dispatch binds the hash; no dispatch before `PLAN_CREATED`. *Proof shape:* `tests/coordinator` (implementation-pending) + existing probe. *Evidence class:* contract/bootstrap-verifiable (wire shape) + implementation-pending (serializer). *Uncovered/deferred:* catalogue revision migration semantics beyond resolution.

**SR-SENTINEL-V0-C02 — Immutable approval binds dispatch.** *Normative statement:* plan approval necessarily precedes dispatch; approval binds plan hash, worker, choreography, run/state version and expiry; `OPERATOR_DECIDE` is a typed target (`PLAN`/`TASK`) that must match the pending approval; task approval precedes station assignment; rejection is event-only and keeps the run refinement-eligible. *Anchors:* WP1 §1, §4; WP0 §3.3.2; `docs/superpowers/specs/contracts/fixtures/plan-approval.valid.json`, `fixtures/event-plan-approved.valid.json`, `fixtures/command-run-create-approval.invalid.json`; probe `::test_force_run_create_is_discriminated_to_typed_payload`. *Acceptance:* discriminator negatives rejected; binding enforced at dispatch; target-mismatch `OPERATOR_DECIDE` rejected. *Proof shape:* `tests/coordinator` (implementation-pending) + `tests/contracts/test_schema_validation.py::test_malformed_or_forbidden_wire_fixtures_are_rejected`. *Evidence class:* contract/bootstrap-verifiable (negatives) + implementation-pending (binding). *Uncovered/deferred:* expiry policy details beyond binding.

**SR-SENTINEL-V0-C03 — Run state machine and outcome vocabulary.** *Normative statement:* the run advances only per the WP1 §4 transition matrix; terminal outcomes are `COMPLETED`, `FAILED`, `EMERGENCY`; `APPROVED`/`REJECTED` are not run states; `NEEDS_REVIEW` is a review-hold escaped only by an explicitly approved new/revisit plan; `PROVIDER_ERROR` and scoreless are total-and-disjoint keyed on status; an active emergency latch cannot be overridden. *Anchors:* WP1 §4, §4.1; WP0 §3.3.2; V0 spec §4; `docs/superpowers/specs/contracts/fixtures/event-run-created-operation.invalid.json`, `fixtures/command-operator-decide-request-text.invalid.json`; probe `::test_wire_contract_uses_indoor_outcome_vocabulary`. *Acceptance:* every matrix row has an explicit transition test incl. rejection, review-hold escape, emergency. *Proof shape:* `uv run python -m pytest tests/coordinator -q` (implementation-pending). *Evidence class:* implementation-pending (vocabulary probe is contract/bootstrap-verifiable).

**SR-SENTINEL-V0-C04 — Idempotency.** *Normative statement:* the idempotency scope is `(run_id, actor_type, actor_id, idempotency_key)` for the run lifetime; same scope + command type + expected state version + canonical payload hash returns the original result; reuse with a different type/version/hash returns `IDEMPOTENCY_KEY_CONFLICT` and performs no action; UI duplicate clicks resend the same envelope/key. *Anchors:* WP0 §3.2; WP2 §5; `tests/contracts/test_semantics.py::test_idempotency_registry_replays_same_scope_and_rejects_conflict`. *Acceptance:* replay-returns-original and conflict-no-action proven. *Proof shape:* existing semantics test + implementation-pending coordinator replay tests. *Evidence class:* contract/bootstrap-verifiable (registry semantics) + implementation-pending (enforcement).

**SR-SENTINEL-V0-C05 — Event immutability, replay cursor, reconciliation.** *Normative statement:* events are immutable with unique monotonic run-scoped `sequence_no`; a side effect has an `OperationRecord` before dispatch; legal transitions `PENDING -> DISPATCHED -> SUCCEEDED|FAILED`, `PENDING|DISPATCHED -> IN_DOUBT`, `IN_DOUBT -> SUCCEEDED|FAILED`; only `WorkerPort.reconcile(operation_id, idempotency_key)` resolves `IN_DOUBT`; `UNKNOWN` reconciliation fails the run and blocks motion (never blind redispatch); at-most-one dispatch attempt before reconciliation. *Anchors:* WP0 §4; WP1 §5; `tests/contracts/test_semantics.py::test_event_stream_requires_monotonic_run_scoped_sequence`, `::test_restart_cursor_replays_events_after_cursor_without_duplicates`, `::test_in_doubt_operation_only_reconciles_to_terminal_state`. *Acceptance:* restart-by-sequence replay without duplicates; `IN_DOUBT` outcome correlation; `UNKNOWN` blocks motion. *Proof shape:* existing semantics tests + implementation-pending coordinator tests. *Evidence class:* contract/bootstrap-verifiable (cursor/transition semantics) + implementation-pending (reconciliation behavior).

**SR-SENTINEL-V0-C06 — Approval eligibility policy.** *Normative statement:* eligibility is `raw_score >= provider_profile.decision_threshold` with matching provider/model/prompt/transform revisions and non-null score; no score, stale/mismatched artifact, non-`LIVE` evidence in judged mode, replay in judged mode, or unapproved/missing owner-approved profile blocks approval; both ranking arrays are ordered non-empty (`minItems = 1`, `maxItems = 64`). *Anchors:* WP1 §6; WP3 §4; WP0 §3.4. *Acceptance:* each blocking condition has a test; empty ranking is invalid. *Proof shape:* `tests/coordinator` (implementation-pending). *Evidence class:* implementation-pending. *Uncovered/deferred:* threshold tuning beyond the frozen profile.

#### M — Six-zone mountain and freshness

**SR-SENTINEL-V0-M01 — Six-zone catalogue and layout modes.** *Normative statement:* the indoor-alley catalogue is exactly the six fixed zones; “section” and “zone” name the same entities across `SectionState.section_id`, `layout_modes[].active_zone_ids`, `SurveyPlanReference.station_ids`, `StationCaptureProvenance.zone_id`; `ROUTE_ONLY_2_ZONE` is exactly the two middle zones with no marker claim; a marker-mode run fails when an expected marker is missing. *Anchors:* indoor-alley design §1; WP0 §3.3.1; V0 spec §5; `docs/superpowers/specs/contracts/mountain-v0.fixture.json`; probe `::test_mountain_fixture_is_the_fixed_six_zone_catalogue`; `tests/contracts/test_semantics.py::test_mountain_fixture_has_six_closed_non_intersecting_zones`. *Acceptance:* six valid closed non-self-intersecting rings, deterministic serialization; explicit mode selection, never silent fallback. *Proof shape:* `uv run python -m pytest tests/contracts -q` + probe. *Evidence class:* contract/bootstrap-verifiable.

**SR-SENTINEL-V0-M02 — Snapshot/state freshness derivation.** *Normative statement:* on every committed event the coordinator atomically increments `state_version` and assigns the next `sequence_no`, writing a mountain snapshot with `snapshot_version == sequence_no` (projection-changing) or retaining the prior snapshot version; `STALE` is never persisted — it is derived as `now - as_of > mountain.fixture.snapshot_stale_after_ms OR snapshot_version < StateSnapshot.latest_event_sequence`; `latest_event_sequence` always equals the highest committed sequence. *Anchors:* WP0 §3.4; V0 spec §5; WP2 §4; `tests/contracts/test_semantics.py::test_stale_version_is_rejected_with_stable_code`. *Acceptance:* derived freshness matches each committed timeline; stale version rejected with `STALE_STATE_VERSION`. *Proof shape:* existing semantics test + implementation-pending coordinator snapshot tests. *Evidence class:* contract/bootstrap-verifiable (version rule) + implementation-pending (atomic commit).

#### P — Perception

**SR-SENTINEL-V0-P01 — Request identity, dimensions, provenance pass-through.** *Normative statement:* each station capture (one to three per approved station) is a separate `PerceptionRequest` with a non-reused `request_id` carrying `StationCaptureProvenance` (`artifact_id`, `sha256`, `capture_index`) passed through unchanged; `image_width`/`image_height` are required and never guessed (reject omission/falsification, or derive from the artifact header). *Anchors:* WP3 §1, §2, §5; WP0 §3.3; `docs/superpowers/specs/contracts/fixtures/perception-request-artifact-hash-mismatch.invalid.json`; probe `::test_checked_in_live_perception_fixtures_validate`. *Acceptance:* 1/2/3-capture station correlation; provenance byte-for-byte pass-through; omitted/falsified dimensions rejected. *Proof shape:* `uv run python -m pytest tests/perception -q` (implementation-pending; `fixtures/perception/live/` manifests exist at baseline). *Evidence class:* implementation-pending.

**SR-SENTINEL-V0-P02 — Artifact store, hash and provenance integrity.** *Normative statement:* `ArtifactStorePort.put_selected_frame()` accepts only JPEG/PNG below 10 MiB, computes SHA-256, writes run-scoped `storage_key` paths and returns `ArtifactRef`; `get(artifact_id)` is coordinator-mediated; hash equality is semantic — request `artifact_sha256 != artifact.sha256` is rejected with `ARTIFACT_HASH_MISMATCH`; V0 retention is run-lifetime plus explicit manual cleanup; absence/corruption fails evidence use. *Anchors:* WP0 §5, §6; WP3 §5; `tests/contracts/test_semantics.py::test_artifact_hash_mismatch_has_stable_error_code`; `docs/superpowers/specs/contracts/fixtures/perception-request-artifact-hash-mismatch.invalid.json`. *Acceptance:* hash-mismatch fixture rejected with the stable code; store path-scope and uniqueness proven. *Proof shape:* existing semantics test + implementation-pending store tests. *Evidence class:* contract/bootstrap-verifiable (rejection) + implementation-pending (store).

**SR-SENTINEL-V0-P03 — Result status, class normalization, boxes.** *Normative statement:* `PerceptionResult` carries every schema field including source/artifact-source kind, revisions and `judged_path`; `DETECTED` requires `rock|pebble_cluster` and a numeric score; `NOT_DETECTED` requires `none` and a numeric score; `UNSCORED`/`PROVIDER_ERROR` require `unknown` and a null score; canonical class is always `rock` under mapping revision `v0-rock-only`; a present `bbox_xyxy_normalized` must satisfy `0 <= x1 < x2 <= 1` and `0 <= y1 < y2 <= 1`. *Anchors:* WP0 §3.4, §6; WP3 §2; `tests/contracts/test_semantics.py::test_inverted_or_zero_area_bbox_is_rejected`. *Acceptance:* inverted/zero-area boxes rejected despite schema-valid arrays; status/score pairing holds on the fixture bundle. *Proof shape:* existing semantics + fixture-integrity tests. *Evidence class:* contract/bootstrap-verifiable.

**SR-SENTINEL-V0-P04 — LIVE vs SIMULATED/REPLAYED judged policy.** *Normative statement:* with `judged_path: true`, `ArtifactRef.source_kind` and `PerceptionResult` source_kind/artifact_source_kind must all be `LIVE`; `SIMULATED` and `REPLAYED` are schema-invalid — `REPLAY_NOT_ALLOWED` for replay, `MISSING_EVIDENCE` for non-live evidence; deterministic fakes always emit `SIMULATED` (no “fake real-mode”); replay is rehearsal-only and rejected in judged runs; the UI cannot select replay in a judged run. *Anchors:* WP0 §5; WP3 §5; WP1 §3.1; V0 spec §4; `docs/superpowers/specs/contracts/fixtures/judged-perception-simulated.invalid.json`, `fixtures/judged-perception-replayed.invalid.json`, `fixtures/judged-perception-live.valid.json`; `tests/contracts/test_schema_validation.py::test_valid_live_perception_fixture_is_accepted_by_public_root`. *Acceptance:* both invalid negatives rejected; live valid accepted; no fake-real-mode path. *Proof shape:* existing schema-validation tests + probe fixtures. *Evidence class:* contract/bootstrap-verifiable.

**SR-SENTINEL-V0-P05 — Provider profile, evaluation manifest, G3 block.** *Normative statement:* a selectable profile validates against `provider-profile-v1.schema.json`; the manifest validates against `evaluation-manifest-v1.schema.json`; `evaluation_pack_sha256` is the single authoritative test-image-set identifier; judged use requires a named joint Colin+Dzivha approval over the versioned test-image-set hash; the checked-in `UNAPPROVED` template is not selectable; **G3 remains BLOCKED** for the judged path until that joint approval exists. *Anchors:* WP0 §5; WP3 §3; V0 spec §8 (G3, “G3 is BLOCKED”); `docs/superpowers/specs/contracts/provider-profile-unapproved.template.json`, `contracts/evaluation-manifest-v1.schema.json`, `contracts/provider-profile-v1.schema.json`; `fixtures/sentinel/v0/provider-profile.json` (simulated `APPROVED` profile — simulated path only); `tests/contracts/test_fixture_integrity.py::test_provider_profile_fixture_is_explicitly_simulated_and_valid`. *Acceptance:* the unapproved template cannot be selected for judged use; the simulated profile is valid for the simulated path; no release-benchmark obligation asserted. *Proof shape:* existing fixture-integrity test + implementation-pending joint-approval gate test. *Evidence class:* contract/bootstrap-verifiable (profiles) + human/live (joint approval record for the judged path). *Uncovered/deferred:* concrete provider name/threshold (spec intentionally declares none).

**SR-SENTINEL-V0-P06 — Retry, error, no invented score, isolation.** *Normative statement:* provider retries are adapter-local for the same request ID only; exhaustion returns a typed provider error, never an invented score; no provider switching or physical retry; a `PROVIDER_ERROR` result marks the run `FAILED` before any score interpretation; WP3 never writes SQLite, coordinator events or drone commands. *Anchors:* WP3 §5, §6; WP1 §4 row (`PROVIDER_ERROR` → `FAILED`); WP0 §3.5 codes `UNSCORED_PERCEPTION`, `PERCEPTION_PROVIDER_UNAVAILABLE`; `tests/contracts/test_fixture_integrity.py::test_perception_fixture_bundle_contains_success_scoreless_and_provider_error`. *Acceptance:* no score on error; no SQLite/event/drone import path. *Proof shape:* `tests/perception` (implementation-pending) + existing fixture-bundle test. *Evidence class:* implementation-pending (retry/isolation) + contract/bootstrap-verifiable (fixture bundle).

#### U — UI projection and operator surface

**SR-SENTINEL-V0-U01 — Projection layout and read-only rendering.** *Normative statement:* the UI renders coordinator facts only, through the WP2 read/command boundary; it owns no factual state, event sequence, policy or SQLite access; five named regions render at 1440×900 with no horizontal scrolling; each region/widget exposes a stable `data-testid`. *Anchors:* WP2 §2, §3, §7. *Acceptance:* smoke harness asserts each region’s visible bounding box in the 1440×900 viewport and no document horizontal overflow; passing screenshot `artifacts/ui-smoke/1440x900.png` is always retained (G2 evidence artifact). *Proof shape:* `uv run python -m sentinel.ui --smoke --fixture fixtures/sentinel/v0/run-approval.json` via Playwright harness `tests/ui/test_smoke.py` (both implementation-pending; the run-approval fixture is not in the baseline tree). *Evidence class:* implementation-pending.

**SR-SENTINEL-V0-U02 — Approval/decision interactions.** *Normative statement:* each Confirm/Reject attempt creates one full `CommandEnvelope` with a fresh `command_id`, correlation ID and idempotency key; until a terminal result or a state-version refresh, retries and duplicate clicks resend that same envelope/key and return the original result; actions render only from coordinator-declared `legal_operator_actions`; `FORCE_RUN_CREATE` is operator-only, from a failed preflight, unavailable under an active emergency latch. *Anchors:* WP2 §5; WP1 §4; `docs/superpowers/specs/contracts/fixtures/command-run-create-approval.invalid.json`. *Acceptance:* duplicate-idempotency and stale-version rejections have direct tests. *Proof shape:* `tests/ui/test_operator_actions.py` (implementation-pending). *Evidence class:* implementation-pending.

**SR-SENTINEL-V0-U03 — State visibility, freshness labelling, emergency surface.** *Normative statement:* the header shows run ID, state version, freshness, a `LIVE`-`SIMULATED`-`REPLAYED` badge and the configured mode; `STALE` is a label/badge, never a persisted status; `IN_DOUBT`, stale, simulated, replayed and failed evidence remain visibly labelled; emergency has a permanently visible one-click control plus keyboard fallback, immediately disables ordinary controls and displays latch acknowledgement; replay cannot be selected in a judged run. *Anchors:* WP2 §3, §4, §5. *Acceptance:* direct tests for emergency, `IN_DOUBT`, stale, evidence-missing states. *Proof shape:* `tests/ui` (implementation-pending). *Evidence class:* implementation-pending.

**SR-SENTINEL-V0-U04 — Video status/handle boundary.** *Normative statement:* the UI opens only `SafeVideoStreamHandle.stream_url` and renders the typed `VideoStatus`; it never receives raw frames, bytes or storage paths; it visibly degrades on `DEGRADED` or `UNAVAILABLE`; a judged run displays `LIVE` status and the selected-frame artifact/hash correlation before perception/approval. *Anchors:* WP2 §2, §5; WP0 §6; WP4 §1. *Acceptance:* fake `VideoStatus`/`SafeVideoStreamHandle` smoke coverage shows LIVE rendering, selected-frame correlation, and visible degraded fallback. *Proof shape:* `tests/ui/test_smoke.py` (implementation-pending). *Evidence class:* implementation-pending.

#### W — Tello worker

**SR-SENTINEL-V0-W01 — Worker command surface and wire contract.** *Normative statement:* the worker implements `WorkerPort.dispatch`, `WorkerPort.reconcile`, `WorkerPort.read_video_status`, `WorkerPort.open_live_video` under `#WorkerDispatch`, `#ReconciliationResult`, `#VideoStatus`, `#SafeVideoStreamHandle`; dispatch consumes the immutable `SurveyPlanReference`, its `approval_reference` and the exact run-bound `PreflightReport`, executing only when all match the approved run; it creates no alternate wire format and honors at-most-one dispatch before reconciliation. *Anchors:* WP4 §1; WP0 §6; WP1 §3; `docs/superpowers/specs/contracts/fixtures/survey-plan-reference.valid.json`, `fixtures/preflight-report.valid.json`. *Acceptance:* dispatch matching/mismatch and at-most-one behavior proven. *Proof shape:* `tests/worker` (implementation-pending). *Evidence class:* implementation-pending. *Uncovered/deferred:* the direct-UDP command-test design (`docs/superpowers/specs/2026-09-15-sentinel-drone-command-test-design.md`) is superseded per indoor-alley design §8; existing `tests/test_runner.py`, `test_envelope.py`, `test_network.py`, `test_video.py`, `test_emergency.py`, `test_cli.py`, `test_dry_run.py`, `test_djitellopy_backend.py` are prior-surface evidence and are not claimed as V0 WP4 validation here.

**SR-SENTINEL-V0-W02 — Preflight.** *Normative statement:* the worker performs preflight and emits the immutable run/plan-bound `PreflightReport` as `PREFLIGHT_REPORTED` with exactly eight named checks (`WIFI_INTERFACE`, `DRONE_LINK`, `BATTERY_TELEMETRY`, `LIVE_VIDEO`, `LAYOUT_MARKERS`, `BOUNDED_AREA`, `HEIGHT_ENVELOPE`, `EMERGENCY_LATCH`); `passed` is the conjunction of the eight; operator authorization is not a ninth check; a failed preflight may be overridden only by an operator-only `FORCE_RUN_CREATE` naming the failed report, operator reason and new run/plan IDs — never a resume, never under an active latch. *Anchors:* WP0 §3.3.1; WP1 §4.2; WP4 §2; `docs/superpowers/specs/contracts/fixtures/event-preflight-reported.valid.json`, `fixtures/preflight-report.valid.json`; probe `::test_preflight_has_named_structured_checks`. *Acceptance:* eight-check identity and conjunction proven; force-new-run binding proven. *Proof shape:* existing probe + `tests/worker` (implementation-pending). *Evidence class:* contract/bootstrap-verifiable (report shape) + implementation-pending (worker behavior).

**SR-SENTINEL-V0-W03 — Flight envelope and network boundary.** *Normative statement:* real flight is DJITelloPy-only; each real run starts in a fresh process with a fresh preflight and `--real --confirm`; every requested target and vertical leg must be inside the configured room safety envelope; network selection/binding never changes the primary internet route. *Anchors:* WP4 §2; indoor-alley design §2, §3; `docs/superpowers/specs/2026-09-15-sentinel-drone-command-test-design.md` (superseded historical source of the envelope model). *Acceptance:* out-of-envelope movement rejected before transmission; fresh-process/confirm gating proven. *Proof shape:* `tests/worker` (implementation-pending). *Evidence class:* implementation-pending. *Uncovered/deferred:* concrete centimetre envelope values and dual-adapter selection must be re-derived in the detailed WP4 slice; this SR claims only the normative V0 statements above.

**SR-SENTINEL-V0-W04 — Live video and station capture.** *Normative statement:* `VideoStatus` reports lifecycle, freshness, dimensions, degradation and the selected-frame artifact ID/SHA-256; `SafeVideoStreamHandle` is an expiring read-only served endpoint with no storage path, bytes or raw-frame field; the coordinator must not persist video bytes or proxy raw frames; WP4 captures one to three live JPEG/PNG frames per approved station with full `StationCaptureProvenance`; marker-mode captures are accepted only with the expected marker; route-only captures are labelled `route-only, not visually verified`. *Anchors:* WP4 §1, §3; WP0 §6; WP2 §5; WP3 §1; `docs/superpowers/specs/contracts/fixtures/station-capture-provenance.valid.json`. *Acceptance:* status/handle typing; capture correlation by artifact ID + SHA-256; marker rule. *Proof shape:* `tests/worker` + `tests/ui/test_smoke.py` (implementation-pending). *Evidence class:* implementation-pending (judged-live evidence is human/live).

**SR-SENTINEL-V0-W05 — Emergency latch.** *Normative statement:* the `EMERGENCY` latch is terminal and non-overridable; after the emergency command no landing, cleanup or retry command is sent; a future run requires a fresh manual preflight; landing is permitted only while no latch is armed. *Anchors:* WP4 §3; WP0 §3.3 (`EmergencySignal`), §4; indoor-alley design §3; WP1 §4 row (`EMERGENCY_LATCHED` → `EMERGENCY`). *Acceptance:* no post-latch ordinary command; latch ingestion when the coordinator is reachable. *Proof shape:* `tests/worker` (implementation-pending) over legacy `tests/test_emergency.py` evidence. *Evidence class:* implementation-pending (real latch behavior is human/live).

**SR-SENTINEL-V0-W06 — G4 physical rehearsal gate.** *Normative statement:* G4 remains required for V0 release and cannot be substituted by dry-run success; the only permitted rehearsal is a named, pre-authorised choreography on one supervised Tello with an operator present and emergency stop available. *Anchors:* V0 spec §7.1, §8; WP4 §3–§4. *Acceptance:* rehearsal evidence shows `VideoStatus.status: LIVE`, a successful safe-handle open, a selected live JPEG/PNG `ArtifactRef` whose SHA-256 equals `PerceptionRequest.artifact_sha256`, the correlated `PerceptionResult`, operation/reconciliation outcome, and safe landing. *Proof shape:* `uv run python -m pytest tests/worker tests/integration -q -k "tello_rehearsal"` (rehearsal command; `tests/worker` and `tests/integration` not in tree — implementation-pending, and the physical evidence cannot be fabricated by this plan). *Evidence class:* human/live.

### Consent and upstream gate

None of the IDs above are canonical while G3 is BLOCKED (V0 spec §8) or while the upstream contract-artifact capability (Dependency section) is unlanded. Task 4 presents these exact rows for consent; the user may approve any subset, and unapproved atomic candidates remain unregistered.

No task ledger, provider approval workflow, physical rehearsal evidence, CI enforcement, or future WP1–WP4 implementation claims are part of this bootstrap.

## Expected contract catalog coverage

Enumerate exact paths; do not use a broad glob. Group entries by relation but retain a stable catalog ID for every declared contract artifact.

- Roots/schemas: `sentinel-v0-contract.schema.json`, `sentinel-v0-wire.schema.json`, `mountain-v0.schema.json`, `survey-catalogue-v1.schema.json`, `provider-profile-v1.schema.json`, `llm-adapter-config-v1.schema.json`, and `evaluation-manifest-v1.schema.json`.
- Data/template artifacts: `mountain-v0.fixture.json` and `provider-profile-unapproved.template.json`.
- Positive and negative contract fixtures under `docs/superpowers/specs/contracts/fixtures/`.
- DDL artifact: `src/sentinel/contracts/ddl_manifest.json`.
- Current code consumers: `src/sentinel/contracts/schemas.py`, `src/sentinel/contracts/fixtures.py`, `src/sentinel/contracts/ddl_manifest.py`, and `src/sentinel/contracts/validators.py` where the accepted final code confirms those relationships.
- Current validation: `tests/contracts/test_schema_validation.py`, `tests/contracts/test_fixture_integrity.py`, `tests/contracts/test_manifest.py`, `tests/contracts/test_semantics.py`, and `tests/test_indoor_alley_contract_probe.py` where each test’s actual scope justifies the declaration.

Do not declare planned future code, external providers, or hardware execution as a consumer/evidence source.

## Task 1: Reconcile the accepted source baseline

**Objective:** Start from a clean, committed V0 contract/doc state without interfering with other worktrees.

**Files:**
- Read-only comparison: the latest accepted V0 branch/commit, `c4ca058`, and this plan branch
- Potential conflict inputs: V0/WP0 specs and `docs/superpowers/specs/contracts/`

**Step 1:** Identify the current accepted commit containing the G0 contract work and the current V0 documentation revision.

**Step 2:** Compare `c4ca058..accepted-head` by path. Classify each changed target as an intended input, a conflict requiring owner resolution, or unrelated work.

**Step 3:** Rebase/merge only through the agreed repository workflow; verify the resulting commit and clean worktree before making bootstrap edits.

**Expected:** The implementation uses exactly one reconciled committed baseline and does not consume another session’s dirty changes.

## Task 2: Confirm the released Coherence consumer surface

**Objective:** Verify that the native features planned upstream are installed and behave in a disposable consumer fixture before SENTINEL artifacts are authored.

**Files:**
- Modify only if required by install/lock workflow: `pyproject.toml`, `uv.lock`
- Test fixture location: a temporary/disposable test project outside the authoritative SENTINEL artifacts

**Step 1:** Record the precise Coherence version/commit that contains contract catalog loading, local `$ref` closure compilation, `contract:<id>` bundle members, and freshness diagnostics.

**Step 2:** Run a minimal fixture catalog through the released CLI. Confirm catalog absent is compatible, a valid catalog appears in trace/health, and a changed referenced schema yields a stale/invalidation finding.

**Step 3:** Do not proceed if the CLI surface is missing, undocumented, or returns non-deterministic output. Update the upstream plan/implementation first.

**Expected:** SENTINEL adopts working native capability rather than pre-authoring artifacts against an imagined API.

## Task 3: Add authority and artifact-status documentation

**Objective:** Make the authoritative V0/WP0/spec-contract relationship explicit before any generated projection is trusted.

**Files:**
- Create: `docs/COHERENCE.md`
- Modify: `docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md`
- Modify: `docs/superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md`
- Modify: `docs/superpowers/specs/contracts/fixtures/README.md`
- Modify only after classification: `docs/superpowers/plans/2026-09-15-sentinel-djitellopy-backend.md` or an explicit supersession/deferral index

**Step 1:** Write `docs/COHERENCE.md` with the authority hierarchy, artifact lifecycle labels, change order, catalog ownership, and rule that evidence/test files are not normative contract sources.

**Step 2:** Add stable frontmatter (`id`, `title`, `status`) to each active V0/WP authority spec, using unique IDs that match feature/requirement citations. Do not change normative prose unless a discrepancy is demonstrated.

**Step 3:** Document fixture categories: positive/negative, root schema, purpose, and executing test owner. Update only confirmed relationships.

**Step 4:** Classify the legacy djitellopy plan as active, superseded, or deferred with a reason. Do not silence the current `plan_no_spec` trace finding by deleting the plan.

**Validation:** `uv run coherence trace check --project-root .` after native contract support is available, plus Markdown/frontmatter parsing tests if the project provides them.

## Task 4: Obtain consent and author the four minimal SRs

**Objective:** Create stable requirement anchors without duplicating the V0/WP0 documents.

**Files:**
- Create after explicit approval: `requirements/SR-SENTINEL-V0-<id>.md` for each candidate approved from the atomic SR matrix (e.g. `requirements/SR-SENTINEL-V0-C04.md`)
- Generate: `requirements/index.json`

**Step 1:** Present the atomic candidate SR matrix (section "Proposed atomic functional SR matrix (proposal-only)") with exact source section anchors, owner/allocation, proposed acceptance criterion, evidence class, and known limitations. The four coarse framing candidates are a summary only and are not presented as a sufficient set.

**Step 2:** Apply only approved SRs. Each SR links to prose authority; it does not restate full schemas, fixtures, or implementation details.

**Step 3:** Use the native index producer:

```bash
uv run coherence register index --project-root .
uv run coherence register check --project-root .
```

**Expected:** Requirement checks are non-vacuous for accepted V0 invariants only; no fabricated acceptance evidence.

## Task 5: Author one V0 feature dossier and bundle

**Objective:** Give native mirrors and membership one bounded feature scope.

**Files:**
- Create: `docs/features/FEAT-SENTINEL-V0.md`
- Create: `bundles/FEAT-SENTINEL-V0.json`
- Generate: native mirror artifacts only through `coherence mirrors generate`

**Step 1:** Create the feature dossier with its purpose, accepted SRs, V0/WP0 authority specs, contract catalog IDs, known implementation coverage, validation commands, and explicit exclusions.

**Step 2:** Create a bundle containing only the accepted feature scope: authority specs, active plan(s) when classified, accepted SRs, and `contract:<id>` entries from the catalog. Do not bundle unapproved future work.

**Step 3:** Generate, never hand-edit, native projections:

```bash
uv run coherence mirrors generate --project-root .
uv run coherence mirrors check --project-root .
uv run coherence navigate membership --gate --repo-root .
```

**Expected:** Feature and bundle checks become useful without making every historical document a V0 obligation.

## Task 6: Author the explicit native contract catalog

**Objective:** Declare contract artifacts and relationships once for all native consumers.

**Files:**
- Create: `.factory/contracts.yaml`
- Reference only: `docs/superpowers/specs/contracts/**/*.json`, `src/sentinel/contracts/**/*.py`, `tests/contracts/**/*.py`, `tests/test_indoor_alley_contract_probe.py`

**Step 1:** Add catalog metadata and one exact entry per selected V0 contract artifact. Use stable IDs, kind, path, authority spec ref, status, and only confirmed relationships.

**Step 2:** For `json_schema` entries, rely on the native compiler for local `$ref` closure. Do not repeat `$ref` edges manually unless the catalog grammar requires a non-schema relation.

**Step 3:** Declare fixtures with `validates_against` a schema/root contract and declare their validating tests. Ensure intentionally invalid fixtures are labelled negative so their expected failure is not interpreted as stale evidence.

**Step 4:** Declare `ddl_manifest.json` as `ddl_manifest`, its implementation owner, and `tests/contracts/test_manifest.py` as the validator. The manifest is covered because its semantics affect SQLite correctness even though it is not a JSON Schema.

**Step 5:** Run the native catalog compiler/check surface and correct every duplicate, unsafe, missing, or broken local relationship before proceeding.

**Expected:** Any future contract change has a single declared route to its code and test consumers.

## Task 7: Bind only real current validation evidence

**Objective:** Record evidence relationships honestly and avoid converting existing test presence into unsupported assurance claims.

**Files:**
- Modify only if the native evidence/freshness format requires it: the supported Coherence evidence/dependency declarations
- Existing test owners: `tests/contracts/test_schema_validation.py`, `tests/contracts/test_fixture_integrity.py`, `tests/contracts/test_manifest.py`, `tests/contracts/test_semantics.py`, `tests/test_indoor_alley_contract_probe.py`

**Step 1:** Map every catalog `consumers`/`validated_by` path to its actual code or test function scope. A test that checks only a filename or text token must not be credited as schema semantic validation.

**Step 2:** Run the existing contract suite and record evidence using the native producer/API rather than editing a freshness snapshot or manifest by hand.

**Step 3:** Change one disposable/copy fixture or schema reference and re-run `navigate freshness` to prove the expected consumer/evidence invalidation. Restore the fixture before committing.

**Expected:** Staleness findings distinguish missing provenance from stale executed validation, and no live/provider/hardware claim is introduced.

## Task 8: Add the single lightweight project gate

**Objective:** Reuse the existing test suite instead of inventing a project-specific gate framework.

**Files:**
- Create: `.factory/factory.yaml`

**Step 1:** Add only the currently available safe gate, for example:

```yaml
gates:
  unit:
    - cmd: "{python} -m pytest -q"
```

**Step 2:** Confirm the configuration syntax against the released Coherence version. Do not declare `integration`, `hardware`, `provider`, `full`, or CI gates until those commands and policies exist.

**Step 3:** Run the gate through the native command path and directly through `uv run python -m pytest -q`; preserve both outputs if they differ.

**Expected:** V0 gets one real automated gate, not speculative operational assurance.

## Task 9: Generate projections and perform a fail-closed verification campaign

**Objective:** Verify native consistency and staleness behavior from a fresh process at the final commit.

**Files:**
- Generated only through native commands: `requirements/index.json` and native mirror/evidence outputs
- No manual edits to generated artifacts

**Step 1:** Run the producers:

```bash
uv run coherence register index --project-root .
uv run coherence mirrors generate --project-root .
```

**Step 2:** Run the native checks:

```bash
uv run coherence trace check --project-root .
uv run coherence register check --project-root .
uv run coherence mirrors check --project-root .
uv run coherence navigate membership --gate --repo-root .
uv run coherence navigate health --repo-root . --json
uv run coherence navigate freshness --repo-root . --json
```

**Step 3:** Run tests from a fresh process:

```bash
uv run python -m pytest tests/contracts tests/test_indoor_alley_contract_probe.py -q
uv run python -m pytest -q
```

**Step 4:** Inspect the final exact diff and generated artifacts. Classify any remaining findings as active, deferred, or outside current V0 scope with evidence; do not call the project healthy merely because the commands exit.

**Acceptance evidence:**
- catalog entries resolve exactly and have no unsafe/missing paths;
- root schema `$ref` closure is compiled;
- a declared contract change invalidates its actual consumer/evidence chain;
- invalid fixtures stay expected-negative rather than creating a false failure;
- register/mirrors/trace findings are explainable and scoped;
- all reported test outcomes name their exact command and final revision.

## Explicit non-goals

- No custom SENTINEL contract manifest or staleness checker.
- No broad conversion of all prose docs to SRs/FEATs.
- No generated code or schema compilation artifacts checked in during this bootstrap.
- No CI enforcement, provider credentials, real drone commands, hardware gate, or physical-rehearsal evidence.
- No claim that future WP1–WP4 behavior is implemented.
- No deletion of historical documents just to improve a trace metric.

## Risks and controls

| Risk | Control |
|---|---|
| Contract inventory misses an important artifact | Start from exact paths and current test/code consumers; add entries only with an authority/consumer rationale. |
| Catalog duplicates schema semantics | Catalog records identity and relationships only; schemas/manifest remain semantic authority. |
| Existing worktree drift invalidates the plan | Reconcile to one accepted commit and re-run the path inventory before authoring. |
| Freshness gives a false green | Require executed evidence/provenance and prove invalidation with a controlled change. |
| Requirements become bureaucratic | Cap the first iteration at an explicitly consented subset of the atomic SR matrix and one V0 FEAT/bundle. |

## Exit criteria

This bootstrap is complete only when the native upstream capability is available, the user-approved subset of atomic candidate SRs (from the matrix, each approved individually at the consent checkpoint) and one feature/bundle are generated and validated, all active V0 contracts are explicitly cataloged, declared contract changes produce native stale findings, and existing contract tests pass with recorded final-commit evidence.
