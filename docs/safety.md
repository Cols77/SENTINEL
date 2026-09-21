# SENTINEL supervised-flight checklist

## Before `--real`

- Charge the Tello and confirm at least 40% battery.
- Connect the USB Wi-Fi adapter to the Tello access point.
- Confirm the main Wi-Fi adapter still owns the internet default route.
- Use a clear indoor area whose room safety envelope is substantially larger than the bounded six-zone alley route.
- Confirm the operator-selected layout mode (marker mode by default, or the explicit `ROUTE_ONLY_2_ZONE` fallback limited to the two middle zones). In marker mode, a missing expected marker is a failure — no silent fallback.
- Confirm every planned segment's approved height lies inside the room safety envelope, with any pre-planned ascent/descent also enclosed.
- Install propeller guards where appropriate.
- Keep an independent control option available.
- Confirm that `E` is the emergency hotkey, and know the fallback command:

  ```powershell
  python -m sentinel emergency
  ```

- Understand that the planned x/y position is open-loop, not measured localization.

## During the test

- Keep the operator beside the console.
- Press `E` only for an actual immediate motor-stop emergency. The Tello may fall.
- Use `Ctrl+C` for a recoverable abort that should attempt a controlled landing.
- Do not use flips, `go`, `curve`, RC control, or swarm features in this conservative test.
- A station capture is accepted only with its expected marker in marker mode; if an expected marker is missing, treat the operation as failed rather than continuing unverified.
- Stop the test if the drone, video, telemetry, adapter, or surrounding area behaves unexpectedly.
- Run each real test in a fresh process with a fresh preflight; the pinned DJITelloPy receiver threads are process-global and SENTINEL refuses unsafe reuse.

## Network safety

The USB adapter must already be connected to the Tello network. SENTINEL selects and binds to that adapter; it must not disable the primary adapter, change route metrics, or disconnect the internet connection. If automatic selection is ambiguous, use an explicit local IP or interface override and verify the route before takeoff.

## Emergency semantics

`stop` requests a hover. `emergency` stops the motors immediately. After an emergency command SENTINEL treats the run as terminal: the active latch is terminal and non-overridable, and SENTINEL sends no landing, cleanup or retry command afterward. A future run requires a fresh manual preflight.

## Force-run after a failed preflight

A failed preflight does not force a re-dispatch of the prior operation. The operator may force a **new** run via `FORCE_RUN_CREATE` only, naming the failed report, an operator reason, and a new run/plan ID, always with fresh preflight and `--real --confirm`. A force-run is never permitted while an active emergency latch is armed.

## Cube mission variant (`cube-run`)

- The operating cube and the commanded bounds come from the site file; print them with
  `cube-site-map`. The room must be substantially larger than the cube, and the space around the
  tag stands must be clear.
- Place the drone on the marked takeoff pad at the mission file's `start` point, **facing the
  tag line**. The whole mission is validated against the commanded bounds before takeoff.
- Commands are `move` and `rotate` only. The drone translates only while facing the tag line;
  look rotations happen at a stop and are undone before the next move.
- `--pose off`: open-loop only. `--pose passive`: pose is measured and logged at each stop and
  can trigger a controlled landing if the measured position leaves the cube; it sends nothing.
  `--pose reanchor`: pose may replace the position estimate at a hover and send at most two
  bounded trims; permitted only after SR-063 has been amended (`--sr063-amended`).
- Always run `cube-run --dry-run` on the exact site and mission files first.