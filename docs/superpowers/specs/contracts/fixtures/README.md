# SENTINEL V0 contract fixtures

Fixture files under `docs/superpowers/specs/contracts/fixtures/` validate against
[`sentinel-v0-wire.schema.json`](../sentinel-v0-wire.schema.json) at its root unless
noted otherwise. They do **not** constitute benchmark evidence or authorization to
use a provider in the judged path (see the final section). Classifications below are
current as of base commit `c4ca058`.

## Positive root fixtures (schema-valid wire documents)

These validate against the public root (`sentinel-v0-contract.schema.json` → wire
registry) and must be accepted:

- `event-plan-approved.valid.json` — validated by `tests/contracts/test_schema_validation.py`.
- `event-preflight-reported.valid.json` — consumed by `tests/contracts/test_semantics.py` for event-stream sequencing and restart-cursor replay.
- `judged-perception-live.valid.json` — a schema-valid `#PerceptionResult` for the LIVE shape; placeholder provider/model text is fixture data only and is **not** an approved provider profile. Validated by `tests/contracts/test_schema_validation.py` and `tests/test_indoor_alley_contract_probe.py`.

## Expected-negative root fixtures (root validation must reject)

`command-run-create-approval.invalid.json`, `command-operator-decide-request-text.invalid.json`,
`event-run-created-operation.invalid.json`, `agent-intent-missing-intent.invalid.json`, and
`agent-no-action-inspect.invalid.json` are the mandatory negative discriminator fixtures.
`judged-perception-simulated.invalid.json` and `judged-perception-replayed.invalid.json` are
intentionally schema-invalid: `judged_path: true` with `SIMULATED`/`REPLAYED` artifact/result
provenance must be rejected. All seven must fail root JSON Schema validation and are covered
by the parametrized rejection test in `tests/contracts/test_schema_validation.py`.

## Nested-only records (valid sub-documents, not root wire documents)

These are schema-valid records that are embedded in envelopes/payloads or belong to another
contract; they are **not** standalone root wire documents (the root requires `wire_type`), so
root validation correctly rejects or ignores them:

- `survey-plan-reference.valid.json` — a nested `#SurveyPlanReference`; root validation must reject it (`test_non_wire_survey_plan_reference_is_rejected_by_public_root`).
- `plan-approval.valid.json`, `preflight-report.valid.json`, `station-capture-provenance.valid.json` — nested `#PlanApproval`, `#PreflightReport`, `#StationCaptureProvenance`; they validate as sub-documents where embedded. No test file names them directly; they are exercised transitively through the wire schema inside envelope fixtures.
- `perception-request-artifact-hash-mismatch.invalid.json` — structurally schema-valid by design but semantically invalid. WP0's semantic validator must reject it with `ARTIFACT_HASH_MISMATCH` because the request hash differs from its nested `ArtifactRef.sha256` (`tests/contracts/test_schema_validation.py` asserts root-validity; `tests/contracts/test_semantics.py` asserts the semantic rejection).
- `survey-catalogue-v1.fixture.json` — a `sentinel.survey-catalogue.v1` catalogue instance; it validates against [`survey-catalogue-v1.schema.json`](../survey-catalogue-v1.schema.json), not the wire root.

## Simulated / non-authoritative inputs

`fixtures/sentinel/v0/` (`events-success.jsonl`, `events-reject.jsonl`, `events-in-doubt.jsonl`,
`perception-results.json`, `provider-profile.json`) is the deterministic simulated set consumed
through `sentinel.contracts.fixtures` and owned by `tests/contracts/test_fixture_integrity.py`.
Its provenance is `SIMULATED` and it is never judged evidence. In particular
`provider-profile.json` carries `approval_status: APPROVED` for fixture-shape validity only: it
is simulated, `replay_allowed_in_judged_path` is `false`, and it is **not** the named joint
Colin+Dzivha approval record required to authorize a provider in the judged path.

## Fixture validity vs. authorization / benchmark evidence

Schema validity says only that an instance conforms to its schema. It is not provider
authorization (that requires the named joint Colin+Dzivha approval over a versioned
test-image-set hash per the [V0 coherence spec](../../2026-09-16-sentinel-v0-coherence-spec.md)
§2 D-007 / §8 G3), and it is not benchmark evidence (no evaluation data or benchmark result is
checked in; G3 is BLOCKED). The `UNAPPROVED` provider-profile template
[`provider-profile-unapproved.template.json`](../provider-profile-unapproved.template.json)
is the only checked-in profile instance and is not selectable.