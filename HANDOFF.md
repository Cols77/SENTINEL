# SENTINEL handoff

## State

- Worktree: `C:\coding\sentinel\.worktrees\sentinel-djitellopy`
- Branch: `feature/djitellopy-backend`
- The revised implementation is based on the clean setup baseline; the earlier direct-UDP implementation remains preserved in `C:\coding\sentinel\.worktrees\sentinel-implementation`.
- No push to `main` was performed. No real-drone or USB-adapter validation was claimed.

## Implemented

- Python 3.11+ package and CLI with `run` and standalone `emergency` commands.
- Deterministic dry-run client with no socket construction.
- DJITelloPy 2.5.0 adapter with explicit selected-local-IP command, state, and video binding.
- One-real-client-per-process guard for DJITelloPy's process-global receivers.
- PyAV H.264 parser-based video reception and normalized frame metadata.
- 1 m cube checks: horizontal coordinates ±50 cm and altitude 150–250 cm, with measured height checks after vertical moves.
- Conservative preflight, battery/speed/telemetry checks, video-frame acceptance, route rechecks, controlled landing recovery, and terminal emergency stop.
- Windows `E` hotkey plus a second-terminal emergency sender.
- JSONL reports containing status, backend, commands, adapter, and video metadata without raw frame payloads.

## Verification

From the revised worktree:

```text
python -m pytest -q
61 passed in 0.82s
```

The source-path CLI dry-run also passed and emitted a 19-command report with a decoded synthetic 960x720 RGB frame. The real dependencies were not installed in this environment, and no live Tello stream or flight was exercised.

## Hardware next step

Install the package on the target machine, connect the USB Wi-Fi adapter to the Tello SSID, verify the main adapter still owns the internet route, then run one supervised real test with `--confirm`. Use an explicit `--local-ip` for the USB adapter if automatic selection is ambiguous. Keep a second terminal ready with `python -m sentinel emergency --local-ip <usb-ip>`; pressing `E` in the run terminal sends the same terminal motor-stop command.

Each real run must be started as a fresh process because the pinned DJITelloPy receiver threads are process-global.

## Follow-up completion

- Superseded direct flight-test material is archived under
  `docs/superpowers/specs/superseded/` with replacement headers, a replacement
  map, and links to the active V0 contracts. The broad historical worker
  specification remains in place as non-authoritative support, as required by
  the V0 design.
