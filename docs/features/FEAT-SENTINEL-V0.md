---
id: FEAT-SENTINEL-V0-BOOTSTRAP
title: "SENTINEL V0 native coherence bootstrap"
status: review-pending
profile: product
requirements:
  - SR-SENTINEL-V0-001
  - SR-SENTINEL-V0-002
  - SR-SENTINEL-V0-003
  - SR-SENTINEL-V0-004
  - SR-SENTINEL-V0-A01
  - SR-SENTINEL-V0-A02
  - SR-SENTINEL-V0-A03
  - SR-SENTINEL-V0-C01
  - SR-SENTINEL-V0-C02
  - SR-SENTINEL-V0-C03
  - SR-SENTINEL-V0-C04
  - SR-SENTINEL-V0-C05
  - SR-SENTINEL-V0-C06
  - SR-SENTINEL-V0-M01
  - SR-SENTINEL-V0-M02
  - SR-SENTINEL-V0-P01
  - SR-SENTINEL-V0-P02
  - SR-SENTINEL-V0-P03
  - SR-SENTINEL-V0-P04
  - SR-SENTINEL-V0-P05
  - SR-SENTINEL-V0-P06
  - SR-SENTINEL-V0-U01
  - SR-SENTINEL-V0-U02
  - SR-SENTINEL-V0-U03
  - SR-SENTINEL-V0-U04
  - SR-SENTINEL-V0-W01
  - SR-SENTINEL-V0-W02
  - SR-SENTINEL-V0-W03
  - SR-SENTINEL-V0-W04
  - SR-SENTINEL-V0-W05
  - SR-SENTINEL-V0-W06
  - SR-SENTINEL-V0-W07
  - SR-SENTINEL-V0-W08
---

# FEAT-SENTINEL-V0-BOOTSTRAP — SENTINEL V0 native coherence bootstrap

## Purpose

Bind the consent-approved SENTINEL V0 requirement set (33 atomic SRs, accepted by
Colin Aube in the `native-coherence-bootstrap` parent chat, 2026-09-17) to its
normative authority documents, current implementation consumers, and validation
commands, so that native Coherence trace, membership, and (downstream) mirror and
freshness checks operate over exactly the approved V0 scope and nothing else.

## Note on id and filename

The canonical feature id is `FEAT-SENTINEL-V0-BOOTSTRAP`, matching the
`feature:` frontmatter field declared by all 33 registered SRs. This file is
named `FEAT-SENTINEL-V0.md` because the bootstrap card allowlist fixes that
exact path; Coherence resolves feature nodes from frontmatter `id`, not the
filename, so the id is the addressable form (`feat:FEAT-SENTINEL-V0-BOOTSTRAP`).

## Authority (normative sources, highest first)

1. `spec:2026-09-16-sentinel-v0-coherence-spec.md` — V0 coherence specification
   (canonical profile, `sentinel.v0` contract revision).
2. `spec:2026-09-16-sentinel-v0-indoor-alley-survey-design.md` — V0 indoor-alley
   survey design (room-demo decision record).
3. `spec:2026-09-16-sentinel-wp0-shared-coherence-contracts.md` plus its
   machine-readable contract artifacts in
   `docs/superpowers/specs/contracts/` — WP0 executable shared semantics.
4. `plan:docs/superpowers/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md`
   — the bootstrap plan; source of the atomic SR matrix that is the consent
   artifact for every registered SR.

The full authority map (ownership, change order, historical material) is
`docs/COHERENCE.md` (§1–§3); this dossier does not restate it.

## Requirements in scope

Exactly the 33 registered SRs listed in frontmatter: `SR-SENTINEL-V0-001` …
`004`, `A01`–`A03`, `C01`–`C06`, `M01`–`M02`, `P01`–`P06`, `U01`–`U04`,
`W01`–`W08`. Each SR stub records its consent record and points at the plan
matrix as the canonical statement; the stubs are registration anchors, not
restatements.

## Contract catalog

The native Coherence contract-artifact capability (catalog loading,
`contract:<id>` bundle members, contract-aware freshness) landed upstream at
merged pin `cb76075` (landed as the bootstrap capability pin by KB-BOOT-08).

`.factory/contracts.yaml` is now authored with 8 declarations covering the
normative machine-readable artifacts under `docs/superpowers/specs/contracts/`
(7 JSON Schemas + the mountain fixture; the deliberately `UNAPPROVED` provider
template is not declared). All contracts carry
`spec:2026-09-16-sentinel-wp0-shared-coherence-contracts` as authority; the
mountain fixture `validates_against` the mountain schema; consumers are only
the tests/code that actually load each artifact. `contract:<id>` refs for all
8 declarations are members of `bundles/FEAT-SENTINEL-V0.json` (45 members).

Freshness at this revision reports 10 `CONTRACT_VALIDATION_UNAVAILABLE`
warnings (honest callouts: no `evidence/runs/` manifests exist yet) and zero
errors or other contract findings.

## Current implementation consumers and validation commands

The only implementation-anchored consumer today is the contract test surface;
no coordinator/perception/worker implementation exists yet.

- `tests/contracts/test_schema_validation.py` — wire/root schema validation.
- `tests/contracts/test_fixture_integrity.py` — fixture integrity.
- `tests/contracts/test_manifest.py` — DDL manifest (`ddl_manifest.json`).
- `tests/contracts/test_semantics.py` — semantic checks (hash equality, boxes,
  provenance/status coupling, six-zone invariants, stale-version behavior).
- `tests/test_indoor_alley_contract_probe.py` — catalogue-bound proposal probe.

Commands (run from the repository root):

- `uv run pytest tests/contracts` — targeted contract suite.
- `uv run pytest` — full suite (130 passed at the bootstrap baseline).
- `uv run coherence register check --project-root .` — requirement state
  (33 proposed/pending is the expected bootstrap gate state).

## Explicit exclusions

- **G3 (perception approval) is BLOCKED.** No named joint Colin+Dzivha approval
  over a versioned test-image-set hash and evaluation-manifest evidence exists.
  The checked-in provider profile remains the `UNAPPROVED` template; no provider
  name, threshold, or benchmark claim is made.
- **G4 (supervised live-Tello rehearsal) is DEFERRED.** No live hardware,
  credential, or flight evidence exists; dry runs and tests do not substitute.
- No WP1–WP4 future-work requirements, no unapproved candidates (none remain —
  the consent rounds accepted the full matrix), and no hardware or provider
  evidence claims of any kind.
- Native mirror/evidence generation is owned by the downstream card
  (`coherence mirrors generate`); nothing is hand-generated here.

## Related requirements

<!-- derived — generated by `coherence mirrors generate`; do not edit -->
<!-- fingerprint: sha256:ed0ebe8185c9a7386b9b0b04abd0769b452ee8ae62fe594e2ff77a2dc4c3a380 -->
- [[SR-SENTINEL-V0-001]]
- [[SR-SENTINEL-V0-002]]
- [[SR-SENTINEL-V0-003]]
- [[SR-SENTINEL-V0-004]]
- [[SR-SENTINEL-V0-A01]]
- [[SR-SENTINEL-V0-A02]]
- [[SR-SENTINEL-V0-A03]]
- [[SR-SENTINEL-V0-C01]]
- [[SR-SENTINEL-V0-C02]]
- [[SR-SENTINEL-V0-C03]]
- [[SR-SENTINEL-V0-C04]]
- [[SR-SENTINEL-V0-C05]]
- [[SR-SENTINEL-V0-C06]]
- [[SR-SENTINEL-V0-M01]]
- [[SR-SENTINEL-V0-M02]]
- [[SR-SENTINEL-V0-P01]]
- [[SR-SENTINEL-V0-P02]]
- [[SR-SENTINEL-V0-P03]]
- [[SR-SENTINEL-V0-P04]]
- [[SR-SENTINEL-V0-P05]]
- [[SR-SENTINEL-V0-P06]]
- [[SR-SENTINEL-V0-U01]]
- [[SR-SENTINEL-V0-U02]]
- [[SR-SENTINEL-V0-U03]]
- [[SR-SENTINEL-V0-U04]]
- [[SR-SENTINEL-V0-W01]]
- [[SR-SENTINEL-V0-W02]]
- [[SR-SENTINEL-V0-W03]]
- [[SR-SENTINEL-V0-W04]]
- [[SR-SENTINEL-V0-W05]]
- [[SR-SENTINEL-V0-W06]]
- [[SR-SENTINEL-V0-W07]]
- [[SR-SENTINEL-V0-W08]]
<!-- end derived -->
