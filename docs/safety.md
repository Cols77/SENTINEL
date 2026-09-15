# SENTINEL supervised-flight checklist

## Before `--real`

- Charge the Tello and confirm at least 40% battery.
- Connect the USB Wi-Fi adapter to the Tello access point.
- Confirm the main Wi-Fi adapter still owns the internet default route.
- Use a clear indoor area substantially larger than the planned 1 m cube.
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
- Stop the test if the drone, video, telemetry, adapter, or surrounding area behaves unexpectedly.
- Run each real test in a fresh process; the pinned DJITelloPy receiver threads are process-global and SENTINEL refuses unsafe reuse.

## Network safety

The USB adapter must already be connected to the Tello network. SENTINEL selects and binds to that adapter; it must not disable the primary adapter, change route metrics, or disconnect the internet connection. If automatic selection is ambiguous, use an explicit local IP or interface override and verify the route before takeoff.

## Emergency semantics

`stop` requests a hover. `emergency` stops the motors immediately. After an emergency command SENTINEL treats the run as terminal and sends no landing or further flight command.
