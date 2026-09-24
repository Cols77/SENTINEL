# Mission Control Live demo handoff — 2026-09-24

## Objective and current state

The operator wants a **real Live** CanningSt Tello observation through the integrated Mission Control UI: a few areas, visible movement, re-anchor, and yaw variation. Rehearsal is not the requested demo. Do not run the software test suites again; the operator asked for the fix and a Live flight.

Work on `integration/mission-control-room-jev-ui`, which already combines the room Jev branch and the polished Mission Control UI. The UI is running at `http://127.0.0.1:8794/console/`; its Live mode is available and no run is active. The other UI servers on ports 8772 and 8773 belong to earlier readiness and OpenRouter sessions and are not part of this flight.

**No observation flight succeeded today.** Two authorized Live attempts took off and landed before the first capture. The operator confirmed the aircraft physically grounded and the cube clear after each. The last motors-off camera check also failed to see tags. Wi-Fi 2 subsequently disconnected from `TELLO-57CD36`; Windows reported that the network was unavailable on reconnection. Do not approve another Live run until the link and tag view are stable.

## Code and configuration already committed

| Commit | Change |
| --- | --- |
| `53ac701` | Integrated room Jev and Mission Control polish on this branch. |
| `4e1e393` / `56d6784` | CanningSt battery landing floor is 20%; minimum flight-envelope height is 0 cm because this Tello's height reading is faulty. |
| `c0aa8d4` | Live CLI `--disable-battery-guard` is active by default. It skips projected battery cost, while keeping battery telemetry checks, the telemetry allowance, and the 20% landing floor. `--enable-battery-guard` restores predictive admission. The UI inherits the CLI default. |
| `336c574` | Live startup exceptions now print their exact type and message to server stderr before the existing landing/abort handling. |

No software test suite was run after the CLI change, per the operator's instruction. `git diff --check` passed before each commit. Live run artifacts and authorization packs are in ignored `runs/`; do not commit their secrets or digests.

## Live evidence

1. `l3-20260924-130522`: motors-off check passed at 93%; UI preflight read 91%. The Tello took off, then landed with terminal `ABORTED`, reason `START_FAILED`, safety reason `TELEMETRY_UNAVAILABLE`. No capture, yaw, or re-anchor. The pose database has 24 reports, all showing mapped tags 0 and 2. This happened before exact startup-exception logging was added, so the underlying exception is unknown.
2. `l3-20260924-131550`: motors-off check passed at 85%. The Tello took off, then landed with `ABORTED` / `START_FAILED`. Server stderr reported `HomeMeasurementError: home measurement: FAILED`. Its 12 pose reports showed only tag 0, so the required joint-tag home fix was unavailable. No capture, yaw, or re-anchor.
3. After a pad adjustment, a motors-off video burst received 94 frames but detected no tags. This was a camera/view check, not a flight. Shortly afterward Wi-Fi 2 lost `TELLO-57CD36`. The Windows WLAN event at 13:26:35 local says the TP-Link USB adapter was disconnected by its driver; repeated join attempts at 13:26–13:28 failed with “the specified network is unavailable.” This does not yet identify whether the aircraft AP, adapter, or radio conditions caused the instability.

The latest prepared pack, `runs/live.pack.json`, points to unflown `l3-20260924-132245`. Its site assurances expire at **13:52:45 Sydney time**; prepare a fresh pack if that time has passed. The current UI server re-reads the pack at approval.

## Next Live attempt

1. Confirm the aircraft is physically grounded, powered, on the pad facing **both** mapped tag stands, with a clear cube; confirm Wi-Fi 2 stays on its `TELLO-57CD36` network. Earlier the operator confirmed filming consent for today.
2. Run the motors-off connectivity check for the current pack. If stale, prepare a fresh pack with `rtk proxy uv run python scripts/room_live_prepare.py --run-root runs --local-ip 192.168.10.2 --new-run`. Use the command printed by that script for its motors-off check; keep the authorization digest private.
3. Confirm a motors-off camera burst sees joint tags 0 and 2 before takeoff. A single-tag or tagless view has already caused a failed airborne home measurement.
4. Through `http://127.0.0.1:8794/console/`, select Live and the known request `count the phones and computers in the room`, approve, monitor battery and pose, and use Land after a few observations if needed. Keep Emergency available. If startup aborts, read the new `Live start failed: ...` stderr line before changing code or attempting another flight.

The remaining blocker is the unstable aircraft Wi-Fi and tag visibility. Do not claim that movement, observations, re-anchor, or yaw variations were demonstrated; they were not.
