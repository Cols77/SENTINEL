# SENTINEL Native Coherence Bootstrap — Kanban Handoff

**Created:** 2026-09-17
**Purpose:** Continue this work in a new clean parent session and port the remaining execution onto one Hermes Kanban board.
**Repository:** `C:/coding/sentinel-wt/native-coherence-bootstrap-plan`
**Branch:** `docs/sentinel-native-coherence-bootstrap-plan`

## Current verified state

### SENTINEL plan worktree

- HEAD: `3ddf66621cc29b37a2e7dc671e855f541f77d78b`
- Commit: `docs: expand bootstrap plan into atomic SR matrix`
- Worktree was clean immediately before creating this handoff.
- The requested plan is now committed at:
  `.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md`
- The plan contains all original Tasks 1–9 plus a proposal-only matrix of **26 atomic candidates**:
  - Agent: `A01–A02`
  - Coordinator: `C01–C06`
  - Mountain/freshness: `M01–M02`
  - Perception/provider/evidence: `P01–P06`
  - UI: `U01–U04`
  - Worker/Tello/G4: `W01–W06`
- The plan records, for every candidate, the normative statement, source anchors, acceptance criteria, proof shape, evidence class, and uncovered/deferred topics.
- The candidate IDs are not canonical requirements. Registration remains gated on explicit user consent and the upstream Coherence capability.

### Documentation preparation candidate

Separate worktree:

- Path: `C:/coding/sentinel-wt/sentinel-coherence-docs-prep`
- Branch: `feat/sentinel-coherence-docs-prep`
- HEAD: `4faf757c9f8c701a3fc01717d788b8ce105578d4`
- Commit: `docs: establish SENTINEL coherence authority map`
- Changed paths are exactly:
  - `docs/COHERENCE.md`
  - `docs/superpowers/plans/2026-09-15-sentinel-djitellopy-backend.md`
  - `docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md`
  - `docs/superpowers/specs/2026-09-16-sentinel-wp0-shared-coherence-contracts.md`
  - `docs/superpowers/specs/contracts/fixtures/README.md`
- It was independently reviewed from:
  `C:/coding/sentinel-wt/sentinel-coherence-docs-review`
- Fresh review verdict: **PASS**; no Critical or Important findings.
- Review minors to resolve or explicitly defer before adoption:
  1. `docs/COHERENCE.md` and `docs/superpowers/specs/contracts/fixtures/README.md` lack a final newline in the candidate commit.
  2. `docs/COHERENCE.md` §2 says `contract_version` on the DDL manifest; the actual DDL field is `manifest_version: "sentinel.v0"`.
  3. `review-pending` frontmatter versus the existing body wording “re-review pending” is semantically consistent but cosmetically different.
- The documentation candidate has **not** been cherry-picked into the plan branch.

### Direct verification already completed

- Targeted contract suite from clean candidate worktrees: `36 passed, 2 warnings`.
- Full SENTINEL suite from the plan worktree: `130 passed, 2 warnings`.
- Plan commit `git diff 3ddf666^ 3ddf666 --check`: passed.
- Plan structure check: 368 lines, final newline, zero trailing-whitespace lines, all 9 Tasks present, all 26 atomic IDs present.
- Documentation candidate direct targeted suite: `36 passed, 2 warnings`.
- Documentation candidate allowlist and review worktree were clean.

## Hard blockers and boundaries

1. **Upstream Coherence capability is not released/verified.**
   - Upstream `main` checked at `04c8833e190f852c18a6e77d54654633b21bf9e2`.
   - On that exact revision these required paths are absent:
     - `src/coherence/contracts/model.py`
     - `src/coherence/contracts/catalog.py`
     - `src/coherence/contracts/compiler.py`
     - `src/substrate/schemas/contract_catalog.schema.json`
   - `uv run coherence --help` in SENTINEL failed with exit code 2: program not found.
   - Existing upstream trace/freshness modules do not prove the required contract-catalog/compiler/bundle/freshness capability.
   - A partial or dirty upstream task commit is not a consumer release gate. Require exact implementation paths, tests, fresh CLI/import smoke, pinned provenance, and direct consumer-facing probes.
2. **Do not author native SENTINEL catalog, requirements, feature, bundle, factory, mirror, or evidence artifacts while the upstream gate is unmet.**
3. **Do not author canonical SR files until the user approves the exact proposal-only atomic candidates.**
4. **G3 remains BLOCKED.** No named joint Colin+Dzivha approval over a versioned test-image-set hash and evaluation-manifest evidence exists.
5. **G4 remains deferred.** No real-Tello rehearsal, provider evidence, credentials, or live hardware claim may be fabricated.
6. The shared upstream checkout `C:/coding/pi-agent-factory` is dirty and must not be modified by this continuation. Use isolated worktrees only.

## Required Kanban setup in the new clean session

The new session must be the authorized parent/orchestrator context before mutating Kanban. Do not mutate Kanban from a delegated child and do not reuse an unrelated board.

Create one board for this workstream, with absolute workdir:
`C:/coding/sentinel-wt/native-coherence-bootstrap-plan`

Use separate worktrees for any task that changes a different repository or that must be independently reviewed. Serialize cards that share files. Every implementation card must name its exact allowed paths, prohibited paths, commit message, direct checks, and review handoff.

Recommended card graph:

1. `KB-BOOT-01` — **Adopt documentation preparation**
   - Worktree: fresh SENTINEL worktree based on `4faf757`.
   - Allowed paths: the five documentation paths listed above.
   - Fix the two concrete minors (final newlines and DDL field wording); the status wording may remain if the reviewer accepts it.
   - Run targeted tests and `git diff --check`.
   - Fresh broad review, then cherry-pick the accepted candidate into the plan branch.
2. `KB-BOOT-02` — **Upstream capability release gate**
   - Worktree: isolated `pi-agent-factory` worktree only.
   - Read-only until the upstream implementation is actually landed/authorized.
   - Verify exact paths, tests, import, CLI, release/pinned commit, and disposable catalog/freshness probes.
   - Remain blocked if any required proof is missing.
3. `KB-BOOT-03` — **Human consent for atomic SR scope**
   - Human-input card, not auto-dispatched.
   - Present the exact proposal rows in the committed plan, including anchors, acceptance criteria, evidence class, and consequences.
   - Record a per-candidate decision; do not infer consent from silence or from the earlier four-SR framing.
4. `KB-BOOT-04` — **Author approved SRs and native register projections**
   - Depends on `KB-BOOT-01`, `KB-BOOT-02`, and `KB-BOOT-03`.
   - Only approved IDs may be created under `requirements/`.
   - Use native producers; never hand-edit `requirements/index.json`.
5. `KB-BOOT-05` — **Catalog and authority-bound feature dossier**
   - Depends on `KB-BOOT-04` and upstream capability.
   - Create only `.factory/contracts.yaml`, the bounded feature dossier, and the approved bundle scope.
   - Declare exact paths and real current consumers/tests only.
6. `KB-BOOT-06` — **Native mirrors, membership, and controlled freshness proof**
   - Depends on `KB-BOOT-05`.
   - Generate projections only through native producers.
   - Mutate a disposable/copy contract input, prove the actual consumer/evidence chain becomes stale, restore it, and verify the final clean state.
7. `KB-BOOT-07` — **Project gate and final bootstrap campaign**
   - Depends on `KB-BOOT-06`.
   - Run native trace/register/mirror/membership/health/freshness checks plus exact targeted and full pytest commands from a fresh process.
   - Reconcile every finding as active, deferred, or outside V0 scope.

Do not dispatch `KB-BOOT-04` or later until all predecessor cards have independently verified accepted revisions and current evidence.

## Bootstrap acceptance contract

The new session must not call the bootstrap complete unless all applicable items below are proven at the final exact revision:

- Authority map identifies canonical V0/WP0/spec-contract sources, owners, lifecycle labels, change order, and historical/non-authoritative material.
- Only explicitly approved atomic SRs are registered; unapproved candidates remain absent.
- Upstream contract-artifact capability is proven from an exact clean/pinned revision and works in a fresh consumer environment.
- `.factory/contracts.yaml` contains exact, unique, resolvable catalog entries for selected schemas, fixtures, DDL, code consumers, and test consumers.
- Local `$ref` closure is compiled by native Coherence; no custom SENTINEL catalog or staleness engine is introduced.
- The public contract root accepts known-good documents and rejects malformed/impossible discriminator and judged-simulated/replayed documents.
- Semantic checks prove artifact-hash equality, positive-area boxes, provenance/status coupling, six-zone catalogue invariants, and stale-version behavior.
- Feature/bundle membership contains only the approved V0 scope and no future WP1–WP4 or hardware claims.
- Generated register/mirror/evidence artifacts are producer-owned and current; no hand-edited generated output is accepted.
- A controlled contract change produces a native stale/invalidation finding for the actual declared consumer/evidence chain, then the restored final revision is clean.
- The existing contract suite passes with the exact command and final revision recorded; the full suite passes with the exact command and final revision recorded.
- G3 stays explicitly BLOCKED until named joint approval and evaluation-pack evidence exist.
- G4 stays explicitly deferred until supervised live-Tello evidence exists; dry-run/tests do not substitute for it.

## Do not do

- Do not push, merge, rewrite history, or modify the shared upstream checkout without explicit authorization.
- Do not install or target an imagined Coherence CLI/API.
- Do not create guessed SR/catalog/bundle/mirror/evidence files to make a gate green.
- Do not fabricate provider credentials, benchmark scores, hardware evidence, CI enforcement, or live-drone results.
- Do not delete historical documents to improve trace results.
- Do not treat a Kanban `done` state, worker summary, or chat approval as implementation or evidence acceptance without parent-side verification.

## First actions for the new session

1. Read this handoff and `.hermes/plans/2026-09-17_135641-sentinel-native-coherence-bootstrap.md`.
2. Re-run `git status`, branch, and exact HEAD checks in every referenced worktree.
3. Create the single Kanban board from the parent context and add/link the cards above one mutation at a time.
4. Preserve the current plan commit `3ddf666` and documentation candidate commit `4faf757`; do not recreate them.
5. Resolve the documentation minor fixes through a fresh worktree/review cycle, then proceed only through the upstream and human-consent gates.
