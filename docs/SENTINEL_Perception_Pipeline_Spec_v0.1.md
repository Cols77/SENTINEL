# SENTINEL Perception and Evidence Pipeline Specification

**Document version:** 0.1
**Status:** Supporting draft; authoritative V0 details are in [`docs/superpowers/specs/2026-09-16-sentinel-wp3-rock-perception.md`](superpowers/specs/2026-09-16-sentinel-wp3-rock-perception.md) and the V0 coherence spec.
**Component owner:** Perception engineer, with PM/architect approval
**Parent specification:** [`SENTINEL_Demo_State_Database_Spec_v0.2.md`](SENTINEL_Demo_State_Database_Spec_v0.2.md)
**Scope:** Selected Tello evidence frame -> hosted/open-vocabulary detector or verifier -> normalized detection result -> coordinator ingestion and target evidence for V0

This document is retained as supporting design history. It does not define the current executable V0 contract. Agents must use the linked WP3 and coherence specifications as authority; the live video stream remains an operator-visibility concern owned by the later worker/UI boundary.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Objective and V0 contract

The pipeline must turn one valid selected observation frame into a reproducible, provenance-labelled result:

```text
selected observation frame
    -> artifact and image validation
    -> versioned prompt/query set
    -> pinned provider request
    -> bounded response
    -> schema validation and normalization
    -> coordinator-owned detection ingestion
    -> DETECTION_RECORDED
    -> deterministic target policy
```

V0 prioritizes a trustworthy section-level hazard result and an auditable evidence frame over inference on every video frame. Bounding boxes are preferred for the UI overlay but are not allowed to block the minimum loop when a provider cannot return reliable boxes.

## 2. Ownership boundary

### 2.1 Perception component owns

- the representative Tello-frame evaluation pack;
- provider/API investigation and the frozen provider contract;
- prompt/query-set versioning;
- image preprocessing that does not change evidence identity without recording it;
- provider request/response adapter;
- schema validation and normalized detection output;
- raw response preservation and latency measurement;
- provider failure classification;
- perception-quality metrics, regression fixtures and replay fixtures.

### 2.2 Coordinator owns

- `observations` and `detections` database writes;
- event creation and event ordering;
- threshold/policy application and target/task state transitions;
- linking detections to runs, missions and targets;
- idempotency across ingestion retries;
- operator-facing provenance and final decisions.

The perception package may expose a pure target-association proposal helper, but it MUST NOT open SQLite or write `detections`, `targets` or `events` directly.

### 2.3 Drone worker owns

- continuous stream reception and operator video;
- frame burst capture after choreography acknowledgement, relative-yaw completion and settling;
- motion/blur/exposure quality measurements used to select the best frame;
- writing the image artifact and its integrity hash;
- capture context and `OBSERVATION_CAPTURED` event.

The perception component consumes the selected frame. It does not control flight and cannot request a closer view by itself.

## 3. Canonical input: `PerceptionRequest`

A request is created by the coordinator after an observation has been accepted:

```json
{
  "schema_version": 1,
  "request_id": "uuid",
  "run_id": "uuid",
  "mission_id": "uuid",
  "observation_id": "uuid",
  "image_uri": "artifact://runs/<run_id>/observations/<observation_id>.jpg",
  "image_sha256": "sha256 hex",
  "image_width": 960,
  "image_height": 720,
  "viewpoint": "PRIMARY|CLOSER_CONFIRMATION",
  "section_id": "NORTH_TRAIL",
  "query_set_id": "rocks-v1",
  "provider_profile_id": "grounding-dino-replicate-v1",
  "timeout_ms": 10000,
  "idempotency_key": "run-uuid:perception:observation-uuid"
}
```

Rules:

1. `image_uri` resolves through an artifact-store interface, not an arbitrary filesystem path supplied by the model or user.
2. The artifact must exist, be readable, have a supported image type, and match `image_sha256` before provider submission.
3. `image_width` and `image_height` are the dimensions used for coordinate normalization; the adapter records any resize/crop operation.
4. `viewpoint` is copied from the observation and is not inferred from the image.
5. The request is immutable. A changed frame creates a new observation/request, not a mutation of an existing request.
6. The coordinator can safely retry this exact request using the same idempotency key.

## 4. Canonical output: `PerceptionResult`

```json
{
  "schema_version": 1,
  "request_id": "uuid",
  "observation_id": "uuid",
  "status": "SUCCEEDED|NO_DETECTION|FAILED|TIMED_OUT|SCHEMA_REJECTED",
  "provider_id": "replicate",
  "provider_request_id": "provider-request-id-or-null",
  "model_id": "grounding-dino",
  "model_version": "pinned-version-or-null",
  "prompt_version": "rocks-v1",
  "response_schema_version": 1,
  "latency_ms": 1378,
  "detections": [],
  "preprocessing": {"resize": null, "crop": null, "color_space": "RGB"},
  "raw_result_json": {},
  "error": null,
  "provenance_kind": "LIVE"
}
```

A successful result may have zero detections (`NO_DETECTION`). A provider failure has no detection list that can be interpreted as evidence. `latency_ms` measures the complete provider request observed by the coordinator, including upload and response parsing, not only provider-reported inference time.

### 4.1 Normalized detection

Each item in `detections` has this shape:

```json
{
  "class": "rock|pebble_cluster",
  "matched_prompt": "rock",
  "raw_score": 0.934,
  "score_kind": "DETECTOR_RAW|VLM_CONFIDENCE_BAND|NONE",
  "bbox": {"x": 312, "y": 184, "width": 96, "height": 72},
  "bbox_coordinate_space": "PIXELS_TOP_LEFT",
  "description": "loose rock on the inspected surface",
  "provider_item_id": "optional-provider-id"
}
```

Rules:

- `class` is one of the controlled V0 labels. Unknown labels are rejected or mapped only by a versioned explicit mapping.
- Pixel coordinates use origin `(0,0)` at the top-left of the submitted image. `x`, `y`, `width` and `height` MUST be finite, non-negative and within image bounds after normalization.
- `bbox` is nullable. A box is not manufactured from prose or a guessed image location.
- `raw_score` is nullable. It may be populated only when the provider returned a numeric score with documented semantics.
- `score_kind=DETECTOR_RAW` identifies a detector score that can be compared to the configured detector policy only after the policy says it is compatible.
- `score_kind=VLM_CONFIDENCE_BAND` is qualitative/banded evidence and MUST NOT be compared to the detector's `0.90` threshold.
- `score_kind=NONE` means no numeric confidence was returned; the policy must use a separate rule, another view or operator review.
- Descriptions are evidence annotations, not factual claims about object identity beyond the controlled class/result.

The coordinator stores the normalized fields in `detections` and preserves the complete provider response in `raw_result_json` subject to configured redaction. Secrets, authorization headers and unrelated provider account data must not be persisted.

## 5. Pipeline state machine

```text
RECEIVED
   -> VALIDATING_IMAGE
   -> PREPARING_REQUEST
   -> INFERENCING
   -> NORMALISING
   -> READY_FOR_INGESTION
   -> INGESTED

VALIDATING_IMAGE  -> FAILED
PREPARING_REQUEST -> FAILED
INFERENCING       -> TIMED_OUT | FAILED
NORMALISING       -> SCHEMA_REJECTED | FAILED
READY_FOR_INGESTION -> INGESTION_FAILED
```

The provider adapter is a pure external-call boundary. The coordinator's ingestion transaction:

1. verifies the observation exists and belongs to the same run/mission;
2. verifies the request has not already been ingested;
3. writes one detection row per normalized result, or one explicit no-detection result in the tool/event record;
4. records provider/model/prompt/latency/raw response metadata;
5. applies the deterministic target policy;
6. emits `DETECTION_RECORDED` and any `TARGET_CREATED`/`TARGET_CONFIRMED` events;
7. commits before waking the agent.

A repeated provider response with the same perception idempotency key cannot create duplicate detections. A result referencing a missing observation is rejected rather than attached to the nearest observation.

## 6. Provider adapter contract

The perception implementation should expose a narrow interface equivalent to:

```text
perceive(request: PerceptionRequest) -> PerceptionResult
```

The adapter MUST:

- use the provider/model/profile explicitly named in the request;
- pin the model version where the provider supports it;
- send the exact versioned query set and thresholds;
- use a bounded connect, upload and total request timeout;
- cap image size and response size;
- validate HTTP status, content type, JSON shape and required fields;
- preserve provider request ID and actual provider identity;
- return a typed error result for timeout, rate limit, authentication, transport, schema and content failures;
- avoid logging image bytes, API keys or authorization headers;
- make provider fallback explicit in the result and event if enabled.

A provider must not be selected merely because a model page or repository exists. It is viable for V0 only after the exact hosted request/response path has been exercised on representative frames.

### 6.1 Initial candidate order

The parent specification recommends this investigation order:

1. hosted Grounding DINO on selected frames, with Replicate as the first endpoint to test;
2. Roboflow as a detector-provider alternative;
3. OpenAI vision and OpenRouter/Qwen as semantic verification candidates, not presumed detector-equivalent sources.

The frozen implementation must record the final provider, model, prompt, response schema and policy versions. Uncontrolled provider switching makes latency, boxes and score behavior non-reproducible.

### 6.2 Fallback semantics

Fallbacks are not silent:

- detector -> verifier: record `fallback_reason`, both provider identities, and which result supplied the final evidence;
- live provider -> replayed response: set `provenance_kind=REPLAYED`, emit a fallback event, and show a persistent `REPLAYED FALLBACK` label;
- provider failure -> no detection: record the failure; do not emit `DETECTION_RECORDED` as if inference succeeded.

The normal judged path must remain live when the provider is available. A replay result can demonstrate downstream database/agent/approval behavior but does not prove live perception.

## 7. Frame and evidence contract

The selected observation must include the parent-spec fields:

- `image_uri` and `image_sha256`;
- `viewpoint`;
- `quality_json` for blur, exposure, motion/stability and selection metrics;
- `capture_context_json` for choreography step, commanded yaw, reported attitude when available, settle duration, source frame identifiers and frame-selection metrics;
- `captured_at`, `provenance_kind`, `run_id`, `mission_id`, `drone_id` and `section_id`.

The perception component must not claim global drone pose from commanded yaw or from a frame. It may report image-space evidence and preprocessing metadata only.

If the implementation creates crops:

1. preserve the original observation as the evidence root;
2. store crop coordinates in the same top-left pixel convention;
3. hash the crop artifact separately;
4. link every crop result to the parent observation/request;
5. never overwrite the full-frame artifact.

## 8. Detection and target policy inputs

The initial policy is configured per scenario and records:

```json
{
  "policy_version": "target-confirmation-v1",
  "confirmation_threshold": 0.90,
  "accepted_classes": ["rock", "pebble_cluster"],
  "score_kind_required": "DETECTOR_RAW",
  "association": "same-section-same-mission",
  "closer_observation_max_per_target": 1
}
```

The number `0.90` is a starting policy value, not a calibrated probability. Calibration or threshold tuning requires a held-out set of real Tello frames. Scores from different providers, models or prompts are not comparable unless the policy explicitly establishes compatibility.

A detection at or above the threshold may support `CANDIDATE -> CONFIRMED`. A below-threshold result remains a candidate or enters operator/closer-review policy. A model without a meaningful numeric score cannot be promoted using a fabricated number.

The target reducer associates first-slice detections only when they share section and mission context and satisfy the configured class rule. It records `primary_detection_id`, optional `confirmation_detection_id`, evidence IDs, policy version, raw score, model version and prompt version.

## 9. Evaluation pack and quality gates

Before freezing the provider, the perception engineer must create a small versioned evaluation pack containing at least:

- clear surface/no hazard;
- one obvious rock;
- several small pebbles or a cluster;
- hard negatives such as texture, shadow, debris or equipment;
- different distances and orientations;
- varied illumination and motion blur;
- at least one frame representative of the intended choreography.

The evaluation is not training data for the MVP. Every result records:

- frame ID/hash;
- query set and prompt version;
- provider/model/version;
- raw response;
- detection success and false-positive outcome;
- box quality when a box exists;
- small-object misses;
- complete round-trip latency;
- timeout/rate-limit/schema-failure behavior;
- score stability across repeated calls where relevant.

The provider is frozen only after the team has selected a contract that is reliable enough for the V0 acceptance path. A higher FPS claim without stable evidence, acceptable latency and recoverable failures is not a passing result.

## 10. Security and data handling

- API credentials come from the runtime environment or a secret store and never from user text, prompts or committed files.
- Provider requests use TLS where supported and send only the selected evidence needed for the task.
- Logs contain hashes, dimensions, IDs and bounded metadata, not credentials or unnecessary image bytes.
- Artifact URIs are validated by the artifact-store layer; path traversal and arbitrary remote fetches are rejected.
- Raw provider responses are size-limited and redacted for credentials before persistence.
- The UI distinguishes the image's provenance from the detection's provenance; a live image with a replayed detection is not presented as fully live.

## 11. Perception failure handling

| Failure | Result | Coordinator behavior |
|---|---|---|
| Missing/mismatched artifact hash | `FAILED` | No provider call; record observation integrity fault |
| Unsupported/corrupt image | `FAILED` | No detection; surface actionable error |
| Provider timeout | `TIMED_OUT` | Retry only with same idempotency key if policy permits; otherwise fault/review |
| Provider rate limit | `FAILED` with bounded retry metadata | Back off within run budget; never switch silently |
| Invalid provider JSON | `SCHEMA_REJECTED` | Preserve bounded raw response; no detection write |
| Unknown class/box outside bounds | `SCHEMA_REJECTED` | Reject item/result according to strictness policy; do not clamp silently |
| Provider returns no score | `SUCCEEDED` or `NO_DETECTION` with `score_kind=NONE` | Use explicit no-score policy; never invent a number |
| Provider unavailable during judging | `FAILED` or explicit replay path | Show persistent live/replay status and record fallback event |
| Duplicate delivery | original terminal result | No duplicate detection/target/event state |

## 12. Component acceptance tests

1. A valid observation request is accepted only when the image exists and its SHA-256 matches.
2. The adapter sends the exact configured provider, model/profile, query-set version and timeout.
3. A detector response with one valid rock box becomes one normalized detection with top-left pixel coordinates and the original raw response preserved.
4. A response with no detections completes as an explicit no-detection result and does not create a target.
5. A response with an out-of-bounds or non-numeric box is rejected or marked invalid according to the contract; it is never silently clamped.
6. A response without a meaningful numeric score stores `raw_score=null` and cannot be evaluated against `0.90`.
7. A VLM confidence band is visibly and structurally distinct from `DETECTOR_RAW`.
8. A provider timeout returns a typed terminal/intermediate result within the configured deadline and does not fabricate a detection.
9. A duplicate perception request returns the original result and creates no second detection or target.
10. A result for a nonexistent observation is rejected by coordinator ingestion.
11. A live observation with a replayed provider response is labelled `REPLAYED` in storage, events and UI data.
12. Repeated inference over the same fixture records provider/model/prompt versions and makes latency available for comparison.
13. A qualifying raw detector score can drive the target policy to `CONFIRMED`; a sub-threshold score remains a candidate.
14. The complete live/simulated vertical path emits `OBSERVATION_CAPTURED`, `DETECTION_RECORDED` and the target event in sequence, and the agent resumes from `DETECTION_RECORDED`.

## 13. Implementation increments

1. Create the immutable request/result types and controlled vocabularies.
2. Build the evaluation-pack manifest and fixture runner.
3. Implement one provider adapter with strict schema validation and bounded timeouts.
4. Implement normalization, coordinate checks, score-kind semantics and raw-result redaction.
5. Implement coordinator ingestion with idempotency and event emission.
6. Add deterministic target-policy tests and a simulated adapter for M1.
7. Exercise the selected provider on real representative frames and freeze the contract.
8. Add explicit replay fallback only if it is permanently labelled and permitted by the judging context.

## 14. Open decisions for team review

1. Which artifact store is used first: local run directory, coordinator-managed object store, or another local service?
2. Does V0 use detector-only output, detector plus semantic verifier, or the first provider that passes the evaluation gate alone?
3. What response-size and image-size limits are acceptable for the selected provider?
4. Should provider retry be one bounded retry or a scenario-configured budget?
5. Which image-quality metrics are mandatory for the initial frame-selection gate, and which are diagnostic only?

No open decision may weaken raw-response provenance, score semantics, idempotent ingestion, or the distinction between live and replayed evidence.
