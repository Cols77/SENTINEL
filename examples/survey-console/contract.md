# Mission Control projection v2

`src/sentinel/ui/schema.py` owns the closed JSON schema, generated into `mission-schema.mjs`. Python and browser validate the same payload. `mission-contract.mjs` adds reference checks. Unknown nested fields are rejected; private paths, provider credentials, encoded images and internal prompts are excluded. The retained fixture v1 contract is documented below.

| HTTP route | Purpose |
|---|---|
| `GET /api/v1/session` | Immutable mode and session/run identity |
| `GET /api/v1/runs/{run}/snapshot` | Full validated v2 projection |
| `GET /api/v1/runs/{run}/events?after_sequence=N&limit=200` | Ordered durable events |
| `GET /api/v1/runs/{run}/evidence/{artifact}` | Run-bound verified PNG/JPEG bytes or explicit denial |
| `POST /api/v1/runs/{run}/chat` | Durable scoped text command |
| `POST /api/v1/runs/{run}/decisions` | Immutable approval/rejection attempt |
| `POST /api/v1/runs/{run}/land` | Controlled Land on the registered runner |
| `POST /api/v1/safety/emergency` | Session latch plus independent transport |

The service binds only to localhost. Mode is immutable for a session. Browser code cannot query SQLite or construct flight geometry. Rehearsal and Live implement the same methods and shape; source provenance remains explicit.

`run.version` tracks visible snapshots. `run.decision_version` tracks authoritative decision state: pose aging alone does not invalidate approval. A decision binds session/run/proposal ID, SHA-256, decision version, choice, command/correlation/attempt IDs and idempotency key. The journal commits before the waiting worker wakes. Same-payload retries return the stored result, including after a lost browser response; changed-payload key reuse is a conflict. The HTTP adapter preserves typed 409 results. Local storage retains the immutable uncertain decision attempt for reload reconciliation.

A single guarded worker refuses concurrent missions rather than queuing them. Reads, chat and safety remain outside that worker. Live startup refuses incomplete configuration; restart marks interrupted Live runs RECOVERY_REQUIRED. Approval acknowledgement never means flight or landing completion.

Evidence serving resolves only captured run-bound artifacts, verifies hashes, image format and dimensions, and excludes denied/revoked bytes. Pose diagnostics bind the exact encoded image hash, report ID, corners and run. Freshness is computed by the backend clock: FRESH below half the 2000ms threshold, AGING through 2000ms, STALE above it. A stale measured location remains frozen; commanded position is displayed separately. Pose displays use bounded asynchronous sinks and do not control flight.

## Retained fixture projection v1

This is a fixture-only contract, not an adopted coordinator transport. `contract.mjs` documents the complete field types and rejects extra internal fields recursively.

Methods: `read_survey(run_id)`, `read_survey_events(run_id, after_sequence, limit)`, `read_evidence(run_id, frame_id)`, `submit_decision(attempt)`. All return promises. Limits are 1–200; events are ordered after the cursor and no later than the current snapshot watermark. Missing runs raise NOT_FOUND; evidence failures are tagged results.

LIVE and REPLAY are fixture semantics. The existing kit uses REPLAYED; a backend owner must adopt an explicit mapping. Counts, flight budget, observability and merging are supplied facts, not reconstructed by the UI. Current-sweep progress counts only frames present in the projection. Every referenced frame, stop and sighting must be present.

## Fields and ownership

`contract.mjs` contains JSDoc definitions for `ClassName`, `Step`, `Pose`, `Stop`, `Frame`, `Sighting`, `ObjectFact`, `Count`, `Proposal`, `Survey`, `SurveyEvent`, `Attempt`, `DecisionResult`, `EvidenceResult`, and `Scenario`. The validator enumerates every accepted field; unknown/internal fields are rejected, including nested storage keys. Counts use null for absent facts. Snapshot/event versions are nonnegative safe integers; event sequence starts at 1. All timestamps carry a timezone. Normalized boxes must be ordered within [0,1]. Supporting sighting/frame/stop references must resolve in the same projection.

| Method | Result and guarantees |
|---|---|
| `read_survey(run_id)` | Complete cloned `Survey`; invalid run raises NOT_FOUND |
| `read_survey_events(run_id, after_sequence, limit)` | Ordered `SurveyEvent[]`; exclusive cursor, limit 1–200, never beyond the exposed snapshot watermark |
| `read_evidence(run_id, frame_id)` | Scoped `EvidenceResult`; NOT_FOUND, FORBIDDEN, REVOKED are explicit tagged denials; AVAILABLE contains synthetic PNG/JPEG bytes only |
| `submit_decision(attempt)` | `DecisionResult`; immutable proposal/hash/version binding; same attempt and key reuse the pending promise or settled result; IN_DOUBT retries reopen reconciliation with the original identity; changed payload/key reuse raises IDENTITY_REUSED |

Fixture-only `advance()`, `resolveDecision(status)`, and cloned `calls` are consumed only by reviewer controls/tests. The client has no HTTP, database, drone or provider adapter.

## Snapshot and event reconciliation

A run switch clears previous state and starts a new epoch. Late reads from an older epoch cannot publish. Older snapshots cannot regress facts; same-version changes are protocol errors. Events are log records/refresh notifications, never increments to counts or flights. A newer full projection can reconcile operational state across an event gap; the missing log sequences remain visible until fetched. The UI reports the first 200 missing sequence numbers, with pagination restarting at the earliest gap. Conflicting identities, invalid pages and non-progressing cursors block decisions. No timer changes flight or analysis progress.

## Evidence lifecycle

Evidence accepts PNG/JPEG up to 10 MiB, times out after 5 seconds and checks decoded dimensions. Each selection invalidates previous async responses. Run/frame IDs must match the requested capability. Revocation, denial, selection change and disposal remove the previous image and release object URLs. Letterboxing uses actual decoded dimensions and a ResizeObserver. This demo's bounded bytes are not a proposed replacement for WP2's Python-only capability; a transport needs backend adoption before live use.

## Approval lifecycle

The full candidate is inspectable: original request, ID/hash, complete stops and geometry, duration, classes, proposer mode/source, justification, guardrail disagreement, target uncertainty and consent/envelope/preflight eligibility. Both choices require a current, eligible projection. Pending double clicks cannot create a second attempt. An uncertain transport outcome retries the same attempt and key. In the fixture client, retry after IN_DOUBT creates a pending reconciliation response so reviewer controls can settle it as ACKNOWLEDGED or REJECTED; it never creates a new decision identity. A conflict requires a newer snapshot before a deliberate new attempt. ACKNOWLEDGED means only submission acknowledgement. A settled decision cannot be submitted again on the same proposal/hash merely because the snapshot version changes. Detached results remain associated with the old proposal. The controller's UI-only `history` stores prior attempt/result pairs when a deliberate new attempt starts.

## Backend-owner adoption checklist

- Adopt and version a complete UI projection; supply authoritative class counts, observability, merge eligibility, sweep completion and flight budget.
- Define source-kind mapping to existing LIVE/SIMULATED/REPLAYED contracts; preserve consent and freshness independently.
- Agree event cursor, pagination, immutable identity and complete-snapshot watermark guarantees, including gap recovery.
- Provide run-scoped evidence capabilities with revocation, bounded image reads and adopted transport semantics; never expose raw paths or storage keys.
- Map APPROVE/REJECT and stable attempt identity to adopted coordinator commands, legality, expected version and idempotency. These fixture names are not executable command enums.
- Reconcile emergency legality and provide a functioning emergency path before any live console claim; the fixture control is intentionally disconnected.
- Confirm pushed/integrated survey, WP5 flight bridge and proposer work; provider/profile approvals; consented frames; live detector readiness; backend conformance tests.

Until those gates are met, keep the fixture adapter and labels. Do not read SQLite or sibling worktrees to bypass missing backend projections.
