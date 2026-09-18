# KB-BOOT-07 — Project gate and final bootstrap campaign

Task: t_1684f385 · Worktree: C:/coding/sentinel-wt/native-coherence-bootstrap-plan
Gate commit verified: ec3ebc55eba98fdf6ec7c058e77ed8c17b4884e0 (KB-BOOT-05 dossier commit).
CLI: released Coherence CLI from pi-agent-factory @ cb760759136191f98d741581df0deda2809512a4 (merged contract-capability main, PR #4; supersedes the pre-merge pin 04c8833e recorded at run time), invoked as
`uv run --project C:/coding/pi-agent-factory coherence ...` from the worktree.
No source contracts, historical docs, or any file outside the card's allowed paths
were modified by this task. Working tree after the run is byte-identical to the
pre-run state.

## 1. Native checks (all reproduced this run)

| Command | Exit | Result | Reconciliation |
|---|---|---|---|
| `coherence trace check` | 1 | 78 findings: 33 sr_proposed, 33 sr_unsatisfied, 43 artifact_uncovered (33 SR + 9 spec + 1 feat), 2 plan_no_spec (historical plans) | ACTIVE by design: all 33 SRs are accepted-but-unbound bootstrap state; implementation task cards are the follow-up that resolves sr_unsatisfied. plan_no_spec is on the two historical plans outside the V0 bundle — DEFERRED/out-of-V0-scope (KB-BOOT-05 deliberate unbundling). |
| `coherence register check` | 1 | 33 pending, 0 unmeasurable / failing / declined | ACTIVE by design: no measurement/task/deferral binding exists yet — the bootstrap registers requirements only; binding is implementation-phase work. |
| `coherence mirrors check` | 1 | 1 divergent: FEAT-SENTINEL-V0.md has no `## Related requirements` heading | GENUINE FINDING, DEFERRED to follow-up card: the producer refuses to generate without the hand-appended heading, and hand-editing the dossier is outside this card's allowed paths. Fix is one producer run after the heading lands (KB-BOOT-06 finding 1, accepted by parent review). |
| `coherence navigate membership --gate --repo-root .` | 2 | coverage 37/45 (sr 33/33, spec 3/9, plan 1/3); 8 unbundled | ACTIVE by design: exit 2 whenever anything is unbundled; the 8 unbundled artifacts are the WP1–WP4/historical specs and plans deliberately out of the V0 bundle (KB-BOOT-05). |
| `coherence navigate health --repo-root . --json` | 1 | UncompiledPresetError: profile `product` not compiled (compiled: prototype, high_assurance) | DEFERRED: preset compilation is an upstream CLI capability question, not a SENTINEL V0 artifact defect. Not fakeable or fixable in this card. |
| `coherence navigate freshness --repo-root . --json` | 0 | `{"findings": []}` | CLEAN: no contract catalog at this pin, so no declared edges to invalidate. Contract-catalog freshness proof remains deferred with the catalog card (KB-BOOT-06 finding 2). |

All values match the KB-BOOT-06 baselines and the parent review's independent
reproduction (t_b8737fe1) exactly.

## 2. Controlled stale/restore proof

Not re-executed (it mutates the working copy); verified through the immediately
preceding accepted chain: KB-BOOT-06 performed the full proof (generate writes
the derived mirror block; W08 frontmatter removal makes mirrors check exit 1 with
graph propagation via trace check; byte-backup restoration verified with cmp and
empty git diff), and the parent review (t_b8737fe1, this date) independently
reproduced it and confirmed restoration to HEAD. Current working copy confirmed
byte-clean: `git diff --numstat` shows only the 3 pre-existing PM-authority spec
amendments; docs/features, requirements/, bundles/ show zero diffs.

## 3. Exact pytest from a fresh process (this run)

- `uv run python -m pytest tests/contracts tests/test_indoor_alley_contract_probe.py -q` → **36 passed** (exit 0)
- `uv run python -m pytest -q` → **130 passed** (exit 0)

## 4. Final state verification

- Revision: HEAD = ec3ebc55eba98fdf6ec7c058e77ed8c17b4884e0 on
  docs/sentinel-native-coherence-bootstrap-plan.
- Allowlist: nothing outside the card's allowed paths was written by this task.
  Pre-existing working-tree state (present before the bootstrap campaign, per
  KB-BOOT-06 evidence §3): 3 modified spec files (PM-authority safety amendments)
  and untracked HANDOFF-*.md, kb-boot-06-evidence.md, kb-boot-07-gate-evidence.md,
  uv.lock. Zero diffs in docs/features, requirements/, bundles/, src/, tests/.
- Generated artifacts: requirements/index.json (33 SRs + feature) committed at
  8d9f550; bundle FEAT-SENTINEL-V0.json committed at ec3ebc5. No mirror block
  generated (see finding above).

## 5. Gate status

- G3: **BLOCKED** — no named joint Colin+Dzivha approval over a versioned
  test-image-set hash and evaluation-manifest evidence exists.
- G4: **DEFERRED** — no supervised live-Tello evidence; nothing fabricated.
- Bootstrap is NOT declared complete: per the card, completion requires fresh
  final review and parent-side verification. This run requests that review.

## Re-verification at merged pin cb76075

Pin update: the capability pin referenced above is superseded by merged
contract-capability main `cb760759136191f98d741581df0deda2809512a4` (PR #4,
origin/main verified after fetch). Re-ran all four native checks from the
SENTINEL worktree with the CLI built from that pin:
`uv run --project C:/coding/pi-agent-factory-wt/contract-capability-main coherence ...`
(detached worktree on branch land/contract-capability-main at cb76075).

| Command | Exit | Summary |
| --- | --- | --- |
| `register index --requirements-dir requirements` | 0 | 37 SRs indexed, all `proposed: true`, checksums null (unchanged from 04c8833e baseline) |
| `register check --project-root .` | 1 | traceability health 3%; 78 pending, 33 deferred, 0 exempt (designed bootstrap fail-closed state) |
| `navigate membership --gate --repo-root .` | 2 | bundle coverage 37/45 (sr 33/33, spec 3/9, plan 1/3); 8 unbundled (designed bootstrap state) |
| `trace check --project-root .` | 1 | traceability health 3%; 78 pending, 33 deferred, 0 exempt (designed bootstrap fail-closed state) |

Equivalence proof: outputs of all four commands were captured to files and
diffed (CRLF-normalized) against the same four commands run with the
pre-merge venv binary at 04c8833e — byte-identical, exit codes identical.
The merged pin does not change bootstrap behavior; it only adds the
contract-artifact capability. No files outside the two evidence documents
were modified by this re-verification.
