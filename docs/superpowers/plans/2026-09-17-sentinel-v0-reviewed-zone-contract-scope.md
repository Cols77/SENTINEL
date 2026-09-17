# SENTINEL V0 contract-scope update

> **Status:** Active scope amendment; implementation follows the existing V0 specification branch
> **Decision:** Defer, do not remove
> **Authority:** `docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md`

## Purpose

Stage the existing SENTINEL contract surface for the supervised six-zone indoor-alley demo without deleting compatibility material. V0 ends at a coordinator-persisted zone-level perception result; the UI renders that result read-only. Operators approve proposed plans and future agentic actions, not database state. The central wire registry remains broad; the active profile determines what the V0 implementation may instantiate, require, or emit.

## Active V0 path

1. Operator submits a run request.
2. The real model receives exactly three coordinator-mediated, read-only, run-scoped tools: `read_state`, `read_mountain`, and bounded `read_events`.
3. The coordinator accepts a typed catalogue-bound proposal and creates one immutable `SurveyPlanReference`.
4. The operator confirms or rejects the exact plan through `PlanApproval`.
5. Every real run performs fresh eight-check preflight before supervised dispatch.
6. The coordinator makes at most one physical dispatch attempt; `IN_DOUBT` is reconciled by original operation ID/idempotency key and `UNKNOWN` fails closed without redispatch.
7. The worker captures live JPEG/PNG evidence and publishes `VideoStatus`; a served browser stream is not required.
8. Perception consumes the hash/profile-bound capture and returns a `PerceptionResult` for the fixed zone.
9. The coordinator persists a typed zone-level `PerceptionResult` and advances through the fixed plan; no operator database-state review command is emitted.

## Retained but deferred to V0.1

These remain in the repository and in the wire registry. They are not removed, but they are not required, instantiated, or emitted by the active V0 path:

- `CommandTaskCreatePayload`, `CommandTaskRankPayload`, `TASK_CREATE`, `TASK_RANK`, task read models, ranking records, and patroller/assignment lifecycles;
- target aggregation, assignment approval loops, automatic revisit, external notifications, and generalized mission/task workflows;
- precise hazard coordinates, `flight_path`, detailed `drone_pose`, GIS/SLAM/location abstractions, and route-geometry semantics;
- `SafeVideoStreamHandle`, browser-stream serving, and richer served-video transport/lifecycle;
- `ZONE_RESULT` operator targets and `ZONE_RESULT_REVIEWED` events for a future database-state review flow;
- generalized evidence, diagnostic, storage, provenance, and evaluation projections not needed for the active zone-level perception result.

Existing compatibility schemas, command/event branches, and fixtures remain valid where applicable. The presence of a retained branch does not promote it into V0 runtime scope.

## Retained release and reproducibility artifacts

Keep the provider profile schema, unapproved provider template, evaluation manifest, local LLM adapter configuration, and semantic validators. These artifacts support G3 reproducibility and fail-closed provider selection; they are not additional agent tools or runtime wire commands. Artifact hash equality remains a semantic validation rule outside JSON Schema.

## Planned edits

- Add the machine-readable `x-sentinel-v0-scope` declaration to the wire registry.
- Retain the `ZONE_RESULT` operator target and `ZONE_RESULT_REVIEWED` event as deferred compatibility contracts without making them active V0 behavior.
- Align the coherence profile, design record, and WP0–WP4 with the active/deferred boundary.
- Keep the compatibility wrapper and existing fixtures; validate the deferred zone-review target/event without making it active.

## Verification gates

- `uv run python -m pytest -q`
- `uv run python tests/test_indoor_alley_contract_probe.py -q`
- `git diff --check`
- Parse the wire schema and assert retained deferred symbols remain present.
- Validate the deferred zone-review fixture against the wire schema.
- Confirm no active document describes TASK/patroller assignment, served browser video, or operator review of persisted database state as a V0 acceptance requirement.

## Remaining release gates

- A concrete owner-approved provider profile and reproducible evaluation manifest are still required for G3.
- One supervised real-Tello rehearsal remains mandatory for G4.
- No live-drone or network action is part of this documentation update.
