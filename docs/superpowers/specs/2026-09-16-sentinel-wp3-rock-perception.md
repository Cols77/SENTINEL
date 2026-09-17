# SENTINEL WP3 — Rock Perception Adapter

> **Status:** Revised after independent review; re-review pending
> **Owner:** Dzivha
> **Prerequisite:** WP0 contract revision `sentinel.v0`

## 1. Objective

Turn one selected, hash-bound image into a provider-neutral `PerceptionResult`. Each station capture (one to three per approved station) is a separate request and produces a separate result; request IDs are not reused across captures. WP3 produces evidence only; it does not create tasks, alter mountain/run state, write SQLite or command a drone.

## 2. Contract

Input is `https://sentinel.local/contracts/sentinel-v0-wire.schema.json#PerceptionRequest`, including an `ArtifactRef`/hash, image dimensions, `judged_path` and `provider_profile_id`. The worker-trusted station/zone provenance, and optional marker provenance in marker mode, is passed through unchanged once WP0 exposes those fields; WP3 does not infer or replace it.

Output validates against `https://sentinel.local/contracts/sentinel-v0-wire.schema.json#PerceptionResult` and includes: request/observation/artifact IDs and hash, canonical class `rock`, source class (`rock`, `pebble_cluster`, `none`, `unknown`), raw score/semantics, optional valid normalized box, provider profile/model/revisions, prompt/transform/class-mapping revision, artifact source kind, source kind, latency, diagnostic artifact ID and durable status. `DETECTED` is numeric and `rock|pebble_cluster`; `NOT_DETECTED` is numeric and `none`; `UNSCORED`/`PROVIDER_ERROR` are null-score and `unknown`.

`pebble_cluster` remains source evidence but maps to canonical `rock` under `v0-rock-only`. A VLM confidence band is supplemental evidence and cannot substitute for a numeric detector score.

WP3 never localizes a rock, plans a route, controls a worker or drone, changes the approved in-flight route, or changes the coordinator’s in-flight outcome. It returns only the result for the supplied request; the coordinator owns state and outcome decisions.

## 3. Provider profile and test-image set

WP3 uses the versioned test-image set identified by its immutable hash. The demo gate is an owner-approved provider/model/profile plus that test-image-set hash; the owner approval is authoritative. It does not require a release benchmark, latency target, failure-rate target, redaction policy, cost observation or joint Colin-and-Dzivha approval process.

WP3 validates the test-image-set manifest against [`contracts/evaluation-manifest-v1.schema.json`](contracts/evaluation-manifest-v1.schema.json). An owner approval record and the test-image-set hash are sufficient for this demo profile; the approval may be recorded by the owner without a joint-approval or release-benchmark ceremony. [`contracts/provider-profile-v1.schema.json`](contracts/provider-profile-v1.schema.json) remains the authoritative profile shape, and [`contracts/provider-profile-unapproved.template.json`](contracts/provider-profile-unapproved.template.json) remains `UNAPPROVED` and is not a selection. No unapproved profile is used in the judged path.

## 4. Policy boundary

The coordinator—not WP3—loads the concrete owner-approved profile and checks whether a non-null raw score meets its frozen threshold and whether provenance exactly matches its provider ID, model, prompt, transform, score semantics and class-mapping revisions. An `UNAPPROVED`, unknown or non-concrete profile is never eligible for judged use. Below-threshold, scoreless, profile-mismatched, replayed-in-judged-mode and provider-error outcomes are ineligible for approval. WP3 does not make the approval decision.

## 5. Integrity and diagnostics

- Verify artifact hash before request and bind response to the same hash. WP0 semantic validation must reject `PerceptionRequest.artifact_sha256 != PerceptionRequest.artifact.sha256` with `ARTIFACT_HASH_MISMATCH`; JSON Schema does not pretend to enforce this arbitrary cross-field equality.
- Reject unsupported JPEG/PNG, invalid image dimensions, invalid boxes or malformed provider responses.
- Diagnostic artifacts, if emitted, are bounded implementation diagnostics; no release redaction policy or benchmark-retention obligation is imposed by this demo scope, and credentials/headers are never retained.
- `judged_path: true` requires both `ArtifactRef.source_kind` and `PerceptionResult.source_kind`/`artifact_source_kind` to be `LIVE`; `SIMULATED` and `REPLAYED` are schema-invalid and rejected with `REPLAY_NOT_ALLOWED` for replay or `MISSING_EVIDENCE` for non-live evidence.
- Provider retries are adapter-local for the same request ID only. Exhaustion returns a typed provider error, never an invented score; WP3 never switches provider and never retries a physical operation.

## 6. Acceptance criteria

- The versioned test-image-set manifest, labels and artifact hashes are reproducible and identified by the approved profile’s test-image-set hash; no benchmark report is required.
- The owner-approved provider profile validates; until approval the checked-in template remains `UNAPPROVED` and integration/judged use is blocked. No release benchmark, latency/failure target, redaction-policy or joint-approval obligation is asserted.
- Fixtures cover detected, not-detected, scoreless, malformed, timeout, hash mismatch, replay rejection and valid/invalid boxes.
- Coordinator-integration fixtures reject an `UNAPPROVED` or unknown profile and each mismatch between a result and the loaded approved profile (provider ID, model, prompt, transform, score semantics and class-mapping revisions). Fixtures also prove separate request/result correlation for one-, two- and three-capture station sets and pass through worker-trusted station/zone/optional-marker provenance.
- The selected profile threshold—not `0.90`—is used in coordinator integration fixtures.
- `uv run python -m pytest tests/perception -q` passes without live provider access after fixtures are recorded.
- WP3 tests prove no import/write path to SQLite, coordinator events or drone SDK.

## 7. Suggested paths and non-goals

```text
src/sentinel/perception/{adapter.py,models.py,normalization.py,providers/}
tests/perception/{test_normalization.py,test_provider_adapter.py}
fixtures/perception/{manifest.json,positive/,negative/,responses/}
```

No VLM-only approval, continuous video inference, automatic provider switching, physical command retry, local rock localization, route planning/control, task assignment, mountain mutation or drone control belongs in WP3.
