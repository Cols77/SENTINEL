# SENTINEL DJITelloPy Backend Implementation Plan

> Use the subagent-driven-development workflow. Each task has a disjoint write set; review and verification happen before integration claims.

## Goal

Build SENTINEL from the clean setup baseline with a DJITelloPy-derived real backend, deterministic dry-run mode, conservative flight-envelope validation, dual-adapter routing protection, video validation, and emergency controls.

## Task 1 — Package, domain, and dry-run

Write first: package smoke tests, envelope boundary tests, client protocol tests, and dry-run isolation tests.

Implement:

- `pyproject.toml`, `.gitignore`, package entry points;
- `models.py`, `envelope.py`, `client.py`;
- `dry_run.py` with deterministic telemetry, synthetic frame, command records, and terminal emergency latch.

Required tests: package import, all six envelope faces, 20–500 cm movement validation, dry-run command sequence, no network/socket construction, and post-emergency command rejection.

## Task 2 — Dual-adapter selector

Write selector tests against injected adapter/route snapshots before implementation.

Implement `network.py` with:

- Tello-subnet preference;
- explicit local-IP/name/index overrides;
- ambiguity and no-candidate failures;
- route snapshot comparison that fails closed if the internet default route changes;
- injectable reachability/probe hooks;
- no interface disablement, route-metric mutation, or SSID switching.

## Task 3 — DJITelloPy compatibility adapter and backend

Write backend contract tests with fake sockets and an injected Tello factory before implementation.

Implement:

- pinned DJITelloPy/PyAV dependency with an attributable adapter boundary;
- explicit selected-local-IP binding for command, state, and video sockets;
- narrow `DjitellopyClient` adapter implementing `DroneClient`;
- response/state normalization and RGB frame metadata;
- start/stop/read-frame lifecycle;
- decoder error/stall observability;
- dedicated, non-blocking emergency sender and terminal emergency latch.

Do not copy the official fixed-length H.264 frame-boundary heuristic as a correctness guarantee. Preserve upstream attribution and test the adapter without importing real hardware dependencies where possible.

## Task 4 — Runner and safety orchestration

Write runner tests for preflight, altitude entry, approved sequence, bounds, cleanup, recoverable failures, and emergency behavior.

Implement `runner.py` so it owns sequencing and uses only `DroneClient` plus `FlightEnvelope`. The approved sequence is:

```text
command / telemetry / speed 20 / video frame / streamoff
takeoff
up 20 until measured height >= 150 cm
forward 50, right 50, back 50, left 50, up 50, down 50
cw 90, ccw 90, stop, land
```

Normal failures may attempt landing; emergency failures must not send landing or any later flight command.

## Task 5 — Reporting, emergency fallback, CLI, and documentation

Write CLI and reporting tests first. Implement JSONL reporting, standalone emergency command, Windows hotkey boundary, CLI exit codes, README, and `docs/safety.md`.

## Task 6 — Review and verification

- Run focused tests after every task and the full suite before integration.
- Run `python -m sentinel run --dry-run` and inspect its report.
- Run `git diff --check` and confirm artifacts are ignored.
- Perform a final spec review and a separate quality review.
- Do not push to `main` or claim real-drone validation until the revised branch is reviewed and the user has the USB adapter for supervised hardware testing.

## Expected final history

The revised branch starts at the clean setup commit and contains only the new implementation commits. The previous direct-UDP feature worktree remains recoverable but is not merged into this mainline.
