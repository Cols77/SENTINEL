# SENTINEL DJITelloPy Backend Specification

**Date:** 2026-09-15  
**Status:** Revised implementation baseline

## Decision

SENTINEL will reuse DJITelloPy for the Tello protocol and PyAV video path, but it will not expose DJITelloPy directly to the flight runner. A small compatibility adapter boundary will patch the upstream wildcard socket behavior so command, state, and video sockets bind to the selected USB Wi-Fi adapter address.

The earlier standalone UDP client implementation is not part of the revised mainline. Its worktree remains available for comparison and recovery, while this branch starts from the clean repository setup commit.

## Upstream boundary

- Base: [DJITelloPy](https://github.com/damiafuentes/DJITelloPy), MIT licensed.
- Pin the upstream revision and retain its license/attribution.
- Reuse its command formatting, response handling, state parsing, H.264/PyAV frame path, and Tello command coverage.
- Keep the compatibility changes narrowly scoped: explicit local-IP binds, lifecycle cleanup, decoder error visibility, and an emergency send path that is not blocked behind a normal response wait.
- Do not reuse the official human-pose example as drone localization.

## SENTINEL-owned contracts

The following remain owned and tested by SENTINEL rather than inherited from the backend:

- deterministic `DryRunClient` with no socket creation;
- 1 m x 1 m x 1 m software envelope, x/y ±50 cm and z 150–250 cm;
- preflight battery/telemetry/network-route checks;
- selected-adapter binding and preservation of the primary internet route;
- command audit records and JSONL reports;
- terminal emergency state: emergency prevents all later flight commands and never triggers a normal landing;
- conservative command sequence and explicit operator confirmation for real flight.

Horizontal x/y compliance is a planned open-loop estimate, not physical localization. Optional calibrated AprilTag/ArUco or Tello EDU mission-pad localization is deferred.

## Runtime modes

The CLI continues to expose:

```text
python -m sentinel run --dry-run
python -m sentinel run --real --confirm
python -m sentinel emergency
```

Real mode requires the Tello access point to already be reachable through the dedicated USB adapter. SENTINEL must not disable or reconfigure the primary internet adapter.

DJITelloPy 2.5.0 has process-global command/state receiver threads. SENTINEL permits one real backend per process, closes the Tello object with its upstream lifecycle method, and rejects a second real backend rather than silently reusing sockets bound to another adapter.

## Video acceptance

The real backend must start and stop the Tello stream, bind its receiver to the selected local IP, decode at least one frame through the reused PyAV path, expose dimensions and decoder failures, and close the receiver during cleanup. Tests use injected/fake backend objects and do not require hardware or a live video stream.

## Compatibility and licensing

If upstream code is later vendored, preserve the upstream MIT license and identify the pinned upstream revision in `NOTICE.md`. The current implementation keeps the compatibility patch in the adapter module and leaves DJITelloPy as a pinned dependency. SENTINEL application code must not depend on undocumented global DJITelloPy state outside that adapter module.

## Acceptance criteria

1. `python -m pytest` passes without a drone, USB adapter, or network.
2. Dry-run opens no sockets and exercises the same runner command sequence.
3. Backend contract tests prove selected local-IP binding for command/state/video and prove emergency is terminal.
4. The runner rejects movements that cross any cube face before backend transmission.
5. CLI reports include backend identity, video-frame metadata, adapter metadata, command history, and final status.
6. No real-drone success claim is made until a supervised hardware run with the USB adapter produces fresh evidence.
