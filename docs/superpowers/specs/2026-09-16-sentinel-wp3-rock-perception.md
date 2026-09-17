# SENTINEL WP3 — Rock Perception Adapter

> **Status:** Revised after independent review and reviewed-zone V0 scope decision; re-review pending
> **Owner:** Dzivha
> **Prerequisite:** WP0 contract revision `sentinel.v0`

The authoritative `PerceptionPort` signature is in WP0 §6.1 and is declared for implementation at `sentinel.coordinator.ports`. Dzivha implements that port in `sentinel.perception`; WP1 injects it into the coordinator and owns no provider-specific implementation. Dzivha also owns the deterministic fake perception responses and `fixtures/perception/`; Colin consumes that fake through the port for coordinator simulation.

## 1. Objective

Turn one selected, hash-bound image into a provider-neutral `PerceptionResult`. Each station capture (one to three per approved station) is a separate request and produces a separate result; request IDs are not reused across captures. The coordinator maps the result directly to the station's zone-level `ZONE_RESULT` review; WP3 produces evidence only and does not aggregate targets, create tasks, alter mountain/run state, write SQLite or command a drone.

## 2. Contract

Input is `https://sentinel.local/contracts/sentinel-v0-wire.schema.json#PerceptionRequest`, including a typed `ArtifactRef` and `artifact_sha256`, the required `image_width`/`image_height` (rejecting a request that omits or falsifies them, or explicitly deriving them from the artifact header — WP3 never guesses), `judged_path` and `provider_profile_id`. The worker-trusted station/zone provenance is passed through unchanged and unconditionally: WP3 neither infers, drops, nor replaces it. In marker mode the optional marker provenance is likewise passed through; in `ROUTE_ONLY_2_ZONE` no marker claim is made. Each station contributes one to three captures, each in a separate `PerceptionRequest` with its own `request_id` (never reused across captures), each carrying its `StationCaptureProvenance` with `artifact_id`, `sha256` and `capture_index`.

Output validates against `https://sentinel.local/contracts/sentinel-v0-wire.schema.json#PerceptionResult` and includes: request/observation/artifact IDs and hash, canonical class `rock`, source class (`rock`, `pebble_cluster`, `none`, `unknown`), raw score/semantics, optional valid normalized box, provider profile/model/revisions, prompt/transform/class-mapping revision, artifact source kind, source kind, latency, diagnostic artifact ID and durable status. `DETECTED` is numeric and `rock|pebble_cluster`; `NOT_DETECTED` is numeric and `none`; `UNSCORED`/`PROVIDER_ERROR` are null-score and `unknown`.

`pebble_cluster` remains source evidence but maps to canonical `rock` under `v0-rock-only`. A VLM confidence band is supplemental evidence and cannot substitute for a numeric detector score.

WP3 never localizes a rock, plans a route, controls a worker or drone, changes the approved in-flight route, or changes the coordinator’s in-flight outcome. It returns only the result for the supplied request; the coordinator owns state and outcome decisions.

## 3. Provider profile and test-image set

WP3 uses the versioned test-image set identified by its immutable hash. The demo gate is a named joint Colin-and-Dzivha approval of the concrete provider/model/profile plus that test-image-set hash; the gate does not require a release benchmark, latency target, failure-rate target, redaction policy or cost observation. The "test-image-set hash" is the profile's authoritative `evaluation_pack_sha256` field — that one field is the identifier everywhere it appears; no parallel hash name is introduced.

WP3 validates the test-image-set manifest against [`contracts/evaluation-manifest-v1.schema.json`](contracts/evaluation-manifest-v1.schema.json). The joint Colin-and-Dzivha approval record plus the profile's `evaluation_pack_sha256` are sufficient for this demo profile. Execution of the perception adapter is Dzivha's alone; Colin and Dzivha share the approval/review gate. [`contracts/provider-profile-v1.schema.json`](contracts/provider-profile-v1.schema.json) remains the authoritative profile shape, and [`contracts/provider-profile-unapproved.template.json`](contracts/provider-profile-unapproved.template.json) remains `UNAPPROVED` and is not a selection. No unapproved profile is used in the judged path.

## 4. Policy boundary

The coordinator—not WP3—loads the concrete owner-approved profile and checks whether a non-null raw score meets its frozen threshold and whether provenance exactly matches its provider ID, model, prompt, transform, score semantics and class-mapping revisions. An `UNAPPROVED`, unknown or non-concrete profile is never eligible for judged use. Below-threshold, scoreless, profile-mismatched, replayed-in-judged-mode and provider-error outcomes are ineligible for approval. WP3 does not make the approval decision.

## 5. Integrity and diagnostics

- Verify artifact hash before request and bind response to the same hash. WP0 semantic validation must reject `PerceptionRequest.artifact_sha256 != PerceptionRequest.artifact.sha256` with `ARTIFACT_HASH_MISMATCH`; JSON Schema does not pretend to enforce this arbitrary cross-field equality.
- Reject unsupported JPEG/PNG, invalid image dimensions, invalid boxes or malformed provider responses.
- Diagnostic artifacts, if emitted, are bounded implementation diagnostics; no release redaction policy or benchmark-retention obligation is imposed by this demo scope, and credentials/headers are never retained.
- `judged_path: true` requires both `ArtifactRef.source_kind` and `PerceptionResult.source_kind`/`artifact_source_kind` to be `LIVE`; `SIMULATED` and `REPLAYED` are schema-invalid and rejected with `REPLAY_NOT_ALLOWED` for replay or `MISSING_EVIDENCE` for non-live evidence.
- Provider retries are adapter-local for the same request ID only. Exhaustion returns a typed provider error, never an invented score; WP3 never switches provider and never retries a physical operation.

## 6. Acceptance criteria

- The versioned test-image-set manifest, labels and artifact hashes are reproducible and identified by the approved profile's `evaluation_pack_sha256`; no benchmark report is required.
- The joint Colin-and-Dzivha approved provider profile validates; until approval the checked-in template remains `UNAPPROVED` and integration/judged use is blocked. No release benchmark, latency/failure target or redaction-policy obligation is asserted.
- Fixtures cover detected, not-detected, scoreless, malformed, timeout, hash mismatch, replay rejection and valid/invalid boxes.
- Fixtures prove separate request/result correlation for one-, two- and three-capture station sets and pass through the worker-trusted station/zone/optional-marker `StationCaptureProvenance` (`artifact_id`, `sha256`, `capture_index`) unchanged.
- `uv run python -m pytest tests/perception -q` passes without live provider access after fixtures are recorded.
- WP3 tests prove no import/write path to SQLite, coordinator events or drone SDK.

## 7. Suggested paths and non-goals

```text
src/sentinel/perception/{adapter.py,models.py,normalization.py,providers/}
tests/perception/{test_normalization.py,test_provider_adapter.py}
fixtures/perception/{manifest.json,positive/,negative/,responses/}
```

No VLM-only approval, continuous video inference, automatic provider switching, physical command retry, local rock localization, route planning/control, task assignment, mountain mutation or drone control belongs in WP3.
