# KB-BOOT-06 — Native mirrors, membership, and controlled freshness proof

Task: t_b8737fe1 · Worktree: C:/coding/sentinel-wt/native-coherence-bootstrap-plan · Base commit: ec3ebc5
CLI: released Coherence CLI from pi-agent-factory @ cb760759136191f98d741581df0deda2809512a4 (merged contract-capability main, PR #4; supersedes the pre-merge pin 04c8833e recorded at run time), invoked as
`uv run --project C:/coding/pi-agent-factory coherence ...` from the worktree
(`coherence` is not installed in the SENTINEL env — `program not found` is expected there).

## 1. Baseline native producer results

| Command | Exit | Result |
|---|---|---|
| `coherence navigate membership --gate --repo-root .` | 2 | coverage 37/45 (sr 33/33, spec 3/9, plan 1/3); unbundled = 6 WP1–WP4/historical specs + 2 historical plans, deliberately out of the V0 bundle (KB-BOOT-05). CLI source (navigate/cli.py): the gate returns 2 whenever any artifact is unbundled → exit 2 is the designed bootstrap state, same class as `register check` exit 1 (33 pending). |
| `coherence navigate freshness --repo-root . --json` | 0 | `{"findings": []}` — no contract catalog exists at this pin, so no declared dependency edges to invalidate. |
| `coherence navigate health --repo-root . --json` | 1 | native `UncompiledPresetError`: profile `product` is a known preset but uncompiled (compiled: prototype, high_assurance). |
| `coherence mirrors check` / `generate` | 1 | per-file MirrorFormatError: `docs/features/FEAT-SENTINEL-V0.md` has no `## Related requirements` section. Generator is fail-closed — file left byte-identical, nothing written. GENUINE FINDING: the mirror projection for the V0 feature cannot currently be produced. |
| `coherence trace check` | 1 | 78 findings, all expected bootstrap states (sr_proposed "binding not yet decided", artifact_uncovered, plan_no_spec on historical plans). |

## 2. Controlled staleness proof (working-copy experiment; fully restored)

Backups taken before any mutation: `docs/features/FEAT-SENTINEL-V0.md` and
`requirements/index.json` copied to %LOCALAPPDATA%/Temp (*.bak). No authoritative
artifact was hand-edited; the only in-place edits were to the disposable working
copy and both were reverted.

a. Appended an empty `## Related requirements` section to the working dossier →
   `mirrors generate` exit 0: the native producer wrote the full sentinel-closed
   block (33 wikilink entries, sha256 fingerprint comment, `<!-- end derived -->`
   sentinel). `mirrors check` then exit 0 (0 divergent). Producer ownership proven
   end-to-end, byte-for-byte derived from frontmatter.

b. Controlled change: removed one requirement (`SR-SENTINEL-V0-W08`) from the
   frontmatter `requirements:` list →
   - `mirrors check` exit 1: "block does not match its frontmatter/trace-graph
     derivation — regenerate with `coherence mirrors generate`" → the actual
     declared consumer/evidence chain goes STALE and the gate fails closed.
   - `trace check` exit 1 with the mutation-specific findings:
     `SR-SENTINEL-V0-W08 artifact_uncovered`, `SR-SENTINEL-V0-W08 sr_unsatisfied`,
     plus the FEAT uncovered line — the change propagates through the native graph.
   - `navigate freshness` still `{"findings": []}`: with `.factory/contracts.yaml`
     absent (KB-BOOT-05 deferral), the contract-driven freshness engine has no
     edges to invalidate. Contract-catalog freshness proof is therefore deferred
     with the catalog card, not faked here.

c. Restoration: dossier restored from the byte backup → `cmp` identical,
   `git diff` empty on that path; trace check returned to the pre-experiment
   78-finding baseline (W08-specific findings gone).

## 3. Final clean state verification

- `docs/features/FEAT-SENTINEL-V0.md` byte-identical to HEAD ec3ebc5 (cmp-verified).
- Working tree: only the 3 pre-existing uncommitted spec amendments (PM-authority
  safety carve-outs, present before this task) plus pre-existing untracked files.
  Nothing added or left mutated by this task.
- `uv run pytest`: 130 passed (full suite); `uv run pytest tests/contracts
  tests/test_indoor_alley_contract_probe.py`: 36 passed.
- `register check` remains exit 1 with 33 pending (expected bootstrap gate state).

## 4. Findings for review

1. (Open decision) The KB-BOOT-05 dossier lacks the `## Related requirements`
   section the native mirror producer requires; mirrors check fails closed. Either
   this card or a follow-up card should let the producer generate the section
   (one producer run, zero hand edits) so mirrors goes green.
2. Freshness invalidation can only be exercised after the contract catalog card
   lands (no declared edges exist at pin 04c8833e; superseded by merged pin cb76075). Not faked in this card.
3. Membership gate exit 2 and register check exit 1 are designed bootstrap states,
   verified against CLI source — documented, not "fixed".

No provider, CI, live-drone, G3 or G4 evidence is claimed anywhere. G3 stays
BLOCKED; G4 stays deferred.
