# KB-BOOT-09 — Contract catalog evidence

Card: author SENTINEL contract catalog (`.factory/contracts.yaml`) and register
contract bundle refs. Capability pin: merged Coherence main `cb76075`
(landed by KB-BOOT-08 as branch `land/contract-capability-main`). All commands
run via `uv run --project C:/coding/pi-agent-factory-wt/contract-capability-main
python -m coherence ...` with project root `C:/coding/sentinel-wt/native-coherence-bootstrap-plan`
(HEAD `ffa5382` + this card's changes).

## Design decision (recorded on card before authoring)

Catalog = inventory only per `coherence.contract-catalog.v1` grammar
(docs/contract-artifacts.md, src/coherence/contracts/model.py,
contract_catalog.schema.json @ cb76075). 8 declarations — 7 JSON Schemas + 1
fixture, all kind/status explicit, all `authority:
spec:2026-09-16-sentinel-wp0-shared-coherence-contracts` (the WP0 spec names
every artifact). Only genuinely existing relationships:

- `SENTINEL-V0-MOUNTAIN-FIXTURE` validates_against `SENTINEL-V0-MOUNTAIN-SCHEMA`
  (tests/contracts/test_semantics.py:43-49, tests/test_indoor_alley_contract_probe.py).
- `consumed_by` only for artifacts actually loaded by project code/tests
  (src/sentinel/contracts/schemas.py, tests/contracts/test_schema_validation.py,
  tests/contracts/test_semantics.py, tests/test_indoor_alley_contract_probe.py).
- No consumers for survey-catalogue / provider-profile / evaluation-manifest /
  llm-adapter schemas (no project code consumes them yet — WP3+ scope).
- `provider-profile-unapproved.template.json` deliberately NOT declared
  (UNAPPROVED, not a selectable contract; matches bundle scope boundary).
- Root schema's `$ref` to the wire schema is the only inter-contract reference;
  the compiled edge `contract:SENTINEL-V0-CONTRACT-ROOT references
  contract:SENTINEL-V0-WIRE` was emitted from the file content itself (no
  `references` field exists in the catalog grammar).

## 1. Compiler / trace check

`trace check --project-root .` — zero CONTRACT_* diagnostics. Output is the
pre-existing bootstrap gate state (exit 1, unchanged semantics: 78 pending /
33 deferred sr_proposed, known artifact_uncovered/sr_unsatisfied gaps) plus the
8 new `contract:<id>` nodes. `trace graph --json` shows 8 contract nodes and
typed edges: `consumed_by` (all declared consumers), `validates_against`
(mountain fixture → mountain schema), `validated_by` (inverse edge),
`references` (root → wire, from the file's `$ref`).

## 2. Bundle

`bundles/FEAT-SENTINEL-V0.json` edited additively: 37 → 45 members (8
`contract:<id>` refs appended; all existing members untouched). Description's
stale "no contract refs" clause updated for accuracy.

- `navigate bundle check --repo-root . --draft bundles/FEAT-SENTINEL-V0.json`
  → exit 0, "resolves 45/45 members".
- `navigate membership --repo-root . --gate --json` → exit 0;
  contract kind: total 8, bundled 8, unbundled []; total 53, bundled 45
  (37 original + 8 contract refs; sr 33/33; spec and plan unbundled sets are
  the pre-existing out-of-V0-scope docs, unchanged).

## 3. Freshness

`navigate freshness --repo-root . --json` → exit 0, 10 findings, all
`CONTRACT_VALIDATION_UNAVAILABLE` (warning) — each is an honest callout that a
declared consumer has no recorded validation evidence yet (no
`evidence/runs/` manifests exist in this revision; evidence manifests are not
in this card's allowed paths). Zero CONTRACT_STALE / CONTRACT_CONSUMER_STALE /
CONTRACT_FIXTURE_STALE / CONTRACT_REFERENCE_BROKEN /
CONTRACT_MISSING_PROVENANCE findings — no false findings: all artifacts are
unchanged and all provenance is declared.

## 4. Tests

`uv run pytest -q` → **130 passed** (baseline maintained).

## Changed files

- `.factory/contracts.yaml` (new, 8 declarations)
- `bundles/FEAT-SENTINEL-V0.json` (additive: 8 contract refs, description fix)
- `docs/features/FEAT-SENTINEL-V0.md` (Contract catalog section updated for accuracy)
- `docs/features/kb-boot-09-contract-catalog-evidence.md` (this file)
