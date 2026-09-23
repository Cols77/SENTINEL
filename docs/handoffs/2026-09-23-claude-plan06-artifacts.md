# Claude Code Handoff — Plan 06 Task 1

This packet is safe to develop in parallel with Plan 03 because it creates a
separate artifact and capability boundary. Its branch must remain parked until
the roadmap reaches the Plan 06 count-and-return promotion gate.

Work in a new Git worktree and branch named `claude/plan06-artifacts`, based
exactly on `391db567d28442cf07a0173d262bfb4e0ea481a8`. Do not edit the
existing worktree. Do not merge, rebase, or push.

Implement only Plan 06 Task 1 from:

`docs/superpowers/plans/2026-09-22-jev-room-agent-06-live-rollout.md`

## Goal

Implement canonical session manifests, append-only run artifacts, and a
fail-closed capability-grant registry. Everything in this packet is offline.
No provider, network, drone client, or hardware operation may be constructed.

## Owned files

Create only:

- `src/sentinel/room/artifacts.py`
- `src/sentinel/room/capabilities.py`
- `scripts/room_capability.py`
- `docs/superpowers/specs/contracts/room-session-manifest-v1.schema.json`
- `docs/superpowers/specs/contracts/room-capability-grant-v1.schema.json`
- `fixtures/room_agent/v1/capability-grants.json`
- `tests/room/test_artifacts.py`
- `tests/room/test_capabilities.py`

If compliance appears to require modifying another path, stop and report the
missing interface. Do not widen the branch scope.

## Development workflow

Use strict test-driven development:

1. Write failing tests for the approved Task 1 requirements.
2. Run the focused tests and preserve the RED evidence.
3. Implement the smallest compliant solution.
4. Run all checks serially:

   ```powershell
   rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_artifacts.py tests/room/test_capabilities.py -q --tb=short -o addopts='' -p no:cacheprovider
   rtk proxy C:\coding\sentinel\.venv\Scripts\python.exe -m pytest tests/room/test_store.py tests/test_consent.py tests/test_config.py -q --tb=short -o addopts='' -p no:cacheprovider
   rtk git diff --check
   ```

   If any listed regression path does not exist, report it and run the closest
   repository tests discovered with `rtk rg --files tests`.

5. Run the focused tests a second time and confirm canonical outputs and
   normalized digests are identical.
6. Self-review every failure mode below.
7. Commit as:

   ```text
   feat(room): bind reproducible session artifacts
   ```

## Required behavior

- Canonical JSON accepts only finite numbers and computes the authorization
  digest from UTF-8, sorted-key, compact JSON while omitting only the derived
  `authorization_digest` field.
- Live validation recomputes the digest from the raw loaded bytes and rejects
  any mismatch, unknown field, or changed binding.
- Manifests bind every field required by the approved Plan 06 Task 1,
  including revisions, hashes, model identities, physical limits, provider
  deadlines, consent references, home policy, and capability grant.
- Requested and resolved provider identities remain distinct. An initially
  unresolved identity is `UNVERIFIED`; later observations append evidence and
  never rewrite the manifest.
- Credentials, authorization headers, tokens, and secrets never appear in
  manifests, events, terminal data, summaries, or CLI output.
- Default preview creates no file. Explicit `--manifest-out` atomically writes
  exactly the canonical bytes printed to stdout.
- Event IDs are monotonic and append-only. Each durable write is flushed and
  synchronized before success is reported.
- Interrupted finalization remains inspectable. A terminal record is immutable.
- A quiescent SQLite backup made with WAL enabled opens without recovery.
- Ordinary live admission requires a matching unexpired `PROMOTED` grant.
- Acceptance mode permits exactly one matching `TRIAL` capability and binds
  its protocol and evidence destination. It cannot authorize another
  capability or ordinary live mode.
- Grant hashes fail after changes to site, aircraft, profiles, evidence, or
  expiry.
- Synthetic `SIMULATION_ONLY` grants are rejected by live admission.
- There is no arbitrary total model-call cap in the manifest.

## Scope limits

- Do not implement Plan 06 Task 2 or later tasks.
- Do not integrate these modules with the live session CLI yet.
- Do not edit the controller, supervisor, executor, proposer, survey loop, or
  Plan 03 semantic files.
- Do not create real credentials, operator approvals, promoted grants, or
  hardware evidence.
- Do not run live calls, connectivity checks, or hardware commands.

## Return evidence

Report:

- completion status and commit SHA;
- exact changed paths;
- the first failing-test output and final passing counts;
- the repeated canonical-output/digest comparison;
- `git diff --check` result;
- any unresolved interface, platform, durability, or security concern.

This branch requires an independent Opus review before it may enter the
integration queue. Passing tests do not authorize live use or roadmap-order
merging.

