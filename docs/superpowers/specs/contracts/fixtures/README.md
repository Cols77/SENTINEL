# SENTINEL V0 contract fixtures

- `judged-perception-live.valid.json` is a schema-valid `#PerceptionResult` example for the LIVE shape. Placeholder provider/model text is fixture data only and is **not** an approved provider profile.
- `judged-perception-simulated.invalid.json` is intentionally schema-invalid: `judged_path: true` with `SIMULATED` artifact/result provenance must be rejected. Implementations must similarly reject `REPLAYED` judged provenance.
- `command-operator-decide-zone-result.valid.json` is the active V0 operator decision shape for reviewing one zone-level perception result (`target.kind: ZONE_RESULT`).
- `event-zone-result-reviewed.valid.json` is the active V0 persisted event shape for that decision.

`command-run-create-approval.invalid.json`, `command-operator-decide-request-text.invalid.json`, `event-run-created-operation.invalid.json`, `agent-intent-missing-intent.invalid.json`, and `agent-no-action-inspect.invalid.json` are the mandatory negative discriminator fixtures. Root JSON Schema validation must reject every one.

`perception-request-artifact-hash-mismatch.invalid.json` is structurally schema-valid by design but semantically invalid. WP0's semantic validator must reject it with `ARTIFACT_HASH_MISMATCH` because the request hash differs from its nested `ArtifactRef.sha256`.

The task/ranking/patroller and `SafeVideoStreamHandle` branches remain in the registry and existing fixtures for compatibility. They are retained but deferred from the V0 reviewed-zone path; their presence does not make them V0 acceptance requirements.

These fixtures validate against `sentinel-v0-wire.schema.json` at its root unless noted otherwise. They do not constitute benchmark evidence or authorization to use a provider in the judged path.
