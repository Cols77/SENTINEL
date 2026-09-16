# SENTINEL WP3 — Rock Perception Adapter

> **Status:** Revised after independent review; re-review pending
> **Owner:** Dzivha
> **Prerequisite:** WP0 contract revision `sentinel.v0`

## 1. Objective

Turn one selected, hash-bound image into a provider-neutral, provenance-complete `PerceptionResult`. WP3 produces evidence only; it does not create tasks, alter mountain/run state, write SQLite or command a drone.

## 2. Contract

Input is `https://sentinel.local/contracts/sentinel-v0-wire.schema.json#PerceptionRequest`, including an `ArtifactRef`/hash, image dimensions, `judged_path` and `provider_profile_id`.

Output validates against `https://sentinel.local/contracts/sentinel-v0-wire.schema.json#PerceptionResult` and includes: request/observation/artifact IDs and hash, canonical class `rock`, source class (`rock`, `pebble_cluster`, `none`, `unknown`), raw score/semantics, optional valid normalized box, provider profile/model/revisions, prompt/transform/class-mapping revision, artifact source kind, source kind, latency, diagnostic artifact ID and durable status. `DETECTED` is numeric and `rock|pebble_cluster`; `NOT_DETECTED` is numeric and `none`; `UNSCORED`/`PROVIDER_ERROR` are null-score and `unknown`.

`pebble_cluster` remains source evidence but maps to canonical `rock` under `v0-rock-only`. A VLM confidence band is supplemental evidence and cannot substitute for a numeric detector score.

## 3. Provider benchmark and freeze

WP3 first creates a versioned evaluation pack with labels and hashes. It benchmarks candidate providers on identical inputs, recording schema validity, raw score semantics, source-class mapping, box availability, repeatability, positive/negative outcomes, latency, error rate and cost observation.

WP3 first validates reproducible inputs against [`contracts/evaluation-manifest-v1.schema.json`](contracts/evaluation-manifest-v1.schema.json). WP3 may report `SPIKE_COMPLETE`, but G3 remains **BLOCKED** until Colin and Dzivha jointly record a named approval decision after reproducible benchmark evidence and approve a concrete JSON profile validating against [`contracts/provider-profile-v1.schema.json`](contracts/provider-profile-v1.schema.json). [`contracts/provider-profile-unapproved.template.json`](contracts/provider-profile-unapproved.template.json) remains `UNAPPROVED` and is intentionally not a provider selection. The approved profile is the objective gate: it names the selected provider/model/revisions, evaluation-pack hash, numeric decision threshold, max p95 latency, max failure rate, redaction policy and no judged replay. No provider profile may be used in the judged path before that approval and evidence exist.

## 4. Policy boundary

The coordinator—not WP3—loads a concrete `APPROVED` profile and checks whether a non-null raw score meets its frozen threshold and whether provenance exactly matches its provider ID, model, prompt, transform, score semantics and class-mapping revisions. An `UNAPPROVED`, unknown or non-concrete profile is never eligible for judged use. Below-threshold, scoreless, profile-mismatched, replayed-in-judged-mode and provider-error outcomes are ineligible for approval.

## 5. Integrity and diagnostics

- Verify artifact hash before request and bind response to the same hash. WP0 semantic validation must reject `PerceptionRequest.artifact_sha256 != PerceptionRequest.artifact.sha256` with `ARTIFACT_HASH_MISMATCH`; JSON Schema does not pretend to enforce this arbitrary cross-field equality.
- Reject unsupported JPEG/PNG, invalid image dimensions, invalid boxes or malformed provider responses.
- Diagnostic raw response is redacted by the profile’s named policy, capped at 64 KiB and stored only as an artifact; credentials/headers are never retained.
- `judged_path: true` requires both `ArtifactRef.source_kind` and `PerceptionResult.source_kind`/`artifact_source_kind` to be `LIVE`; `SIMULATED` and `REPLAYED` are schema-invalid and rejected with `REPLAY_NOT_ALLOWED` for replay or `MISSING_EVIDENCE` for non-live evidence.
- Retries are bounded by the profile; exhaustion returns typed provider error, never an invented score.

## 6. Acceptance criteria

- Evaluation pack manifest, labels, artifact hashes and benchmark report are reproducible.
- Provider-profile JSON validates and carries a joint Colin-and-Dzivha selection decision ID only after reproducible evidence; until then the checked-in template remains `UNAPPROVED` and integration/judged use is blocked.
- Fixtures cover detected, not-detected, scoreless, malformed, timeout, hash mismatch, replay rejection and valid/invalid boxes.
- Coordinator-integration fixtures reject an `UNAPPROVED` or unknown profile and each mismatch between a result and the loaded approved profile (provider ID, model, prompt, transform, score semantics and class-mapping revisions).
- The selected profile threshold—not `0.90`—is used in coordinator integration fixtures.
- `uv run python -m pytest tests/perception -q` passes without live provider access after fixtures are recorded.
- WP3 tests prove no import/write path to SQLite, coordinator events or drone SDK.

## 7. Suggested paths and non-goals

```text
src/sentinel/perception/{adapter.py,models.py,normalization.py,benchmark.py,providers/}
tests/perception/{test_normalization.py,test_provider_adapter.py,test_benchmark.py}
fixtures/perception/{manifest.json,positive/,negative/,responses/}
```

No VLM-only approval, continuous video inference, automatic provider switching, task assignment, mountain mutation or drone control belongs in WP3.
