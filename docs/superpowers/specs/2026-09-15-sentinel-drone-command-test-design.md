# SENTINEL Drone Command and Flight-Envelope Test Design

**Date:** 2026-09-15  
**Status:** Approved

## Goal

Create a Python 3 project named SENTINEL that can run a conservative, supervised Tello command test inside a planned 1 m x 1 m x 1 m flight envelope whose lower face is 1.5 m above the ground. The same test must run deterministically without hardware in dry-run mode.

## Context and references

The supplied [DJI Tello-Python repository](https://github.com/dji-sdk/Tello-Python) contains useful protocol examples, but its README describes the samples as Python 2.7 code. Ryze's [Tello SDK 2.0 User Guide](https://dl-cdn.ryzerobotics.com/downloads/Tello/Tello%20SDK%202.0%20User%20Guide.pdf) defines the UDP command, state, and video ports and the command semantics used by SENTINEL. The guide also links the official [`Tello3.py`](https://dl-cdn.ryzerobotics.com/downloads/tello/20180222/Tello3.py) Python reference sample.

SENTINEL will implement a small Python 3 UDP client around those protocol semantics rather than porting the upstream Python 2.7 video application wholesale. The pose-recognition sample in the upstream repository is intentionally excluded: it detects human body joints and gestures, not the drone's position in a room.

## Scope

### Included

- Python 3.11+ command-line application.
- Real Tello control over UDP.
- Deterministic dry-run client with no network access.
- Connection and SDK-mode validation.
- Battery, height, speed, and SDK telemetry reads.
- Video stream start, receipt and decode of at least one frame, and stream stop.
- Takeoff, bounded vertical and horizontal movements, yaw rotations, hover/stop, and landing.
- Software-tracked flight-envelope enforcement.
- Structured run logs and nonzero exit codes for failed validation.
- Immediate operator emergency-stop hotkey and a separate emergency command.
- Unit and dry-run integration tests suitable for CI.

### Explicitly excluded from the first version

- Flips, `go`, `curve`, RC control, mission-pad navigation, and swarm control.
- Human pose estimation.
- Camera-based or motion-capture localization.
- A claim that horizontal position is physically measured. Horizontal compliance is an open-loop command-plan check.
- Automated EDI or cloud control.

## Flight-envelope model

The launch point is the horizontal origin. Coordinates are tracked in integer centimetres:

```text
x: -50 .. +50 cm
y: -50 .. +50 cm
z: 150 .. 250 cm above ground
```

The software envelope is closed on all six faces. A movement is validated against its predicted endpoint before the command is sent. The Tello height query is used to update and check `z`; no SDK telemetry field provides absolute `x` or `y`, so those coordinates are estimated from the acknowledged movement commands.

The test uses 50 cm translations, which are inside the Tello SDK's 20–500 cm movement range and provide room for the drone to remain within the planned envelope. The runner enters the altitude band after takeoff using 20 cm upward commands, checking height after each command and aborting if the upper face would be exceeded.

## Architecture

### `DroneClient` interface

The runner depends on a narrow client interface with these behaviors:

- `connect()` and `close()`
- `send(command)` for protocol-level access
- `battery()`, `height_cm()`, and `sdk_version()` telemetry reads
- `set_speed(cm_per_second)`
- `takeoff()` and `land()`
- `move(direction, distance_cm)` for `forward`, `back`, `left`, `right`, `up`, and `down`
- `rotate(direction, degrees)` for `cw` and `ccw`
- `stop()` for hover
- `stream_on()`, `stream_off()`, and `read_frame(timeout)`
- `emergency()`

### Real client

`UdpTelloClient` will use:

- Tello command endpoint `192.168.10.1:8889` by default.
- State listener on UDP port `8890`.
- Video listener on UDP port `11111`.
- Explicit response timeouts and command logging.
- PyAV for decoding the raw H.264 video packets into a frame.

The real client will not start video by default outside the test sequence. It will shut the stream down during cleanup.

### Dry-run client

`DryRunClient` will implement the same interface without sockets. It will:

- Return deterministic telemetry values.
- Simulate the altitude changes needed to enter the envelope.
- Return one synthetic frame with positive width and height.
- Record every command, response, predicted position, and emergency event.
- Reject impossible client operations in the same way the real runner would report them.

### Flight-test runner

`FlightTestRunner` will own sequencing, preflight assertions, envelope checks, cleanup, and result aggregation. `FlightEnvelope` will own coordinate state and movement validation so that spatial rules are independently testable.

## Normal test sequence

The real mode requires explicit operator confirmation before takeoff. The sequence is:

1. Connect and enter SDK mode.
2. Read battery, height, speed, and SDK version where supported.
3. Fail preflight if battery is below the configured 40% minimum or required telemetry is invalid.
4. Set the movement speed to 20 cm/s and verify the speed acknowledgement and readback.
5. Start the video stream, wait for one decodable frame within the configured timeout, record its dimensions, and stop the stream.
6. Take off.
7. Read height and use 20 cm upward moves until the measured height is at least 150 cm, checking the upper boundary after each move.
8. Execute the bounded translation loop:

   ```text
   forward 50 cm
   right 50 cm
   back 50 cm
   left 50 cm
   up 50 cm
   down 50 cm
   ```

9. Rotate clockwise 90 degrees and counter-clockwise 90 degrees.
10. Send `stop` and verify an acknowledgement.
11. Land and verify an acknowledgement.
12. Stop any remaining stream, close sockets, and write the result log.

The runner will check the height after each vertical movement. Rotation and hover do not change the planned position. A failed command, invalid response, lost telemetry, or failed cleanup is recorded as a failed run.

## Emergency-stop behavior

`stop` is a hover command; `emergency` stops the motors immediately. SENTINEL will expose both behaviors explicitly.

### In-process emergency hotkey

Interactive real mode will start a lightweight keyboard listener and display:

```text
EMERGENCY: press E to stop motors immediately.
Fallback: run `python -m sentinel emergency` from another terminal.
```

Pressing `E` sends `emergency` without confirmation, records `EMERGENCY_ABORTED`, and prevents all later test commands. The runner will not send `land` after an emergency stop because the motors may already be stopped and the drone may be falling.

### Standalone fallback command

`python -m sentinel emergency` will send the emergency command directly to the configured Tello endpoint from a separate process. It is intended for use if the test process is blocked or its keyboard listener is unavailable.

Normal `Ctrl+C` or a recoverable command failure will follow the normal abort path: attempt `land`, record the outcome, and close the client. The application will not automatically issue `emergency` on ordinary timeouts.

## CLI and outputs

The first CLI surface will be:

```text
python -m sentinel run --dry-run
python -m sentinel run --real
python -m sentinel emergency
```

Real mode will support explicit configuration for Tello IP, command timeout, minimum battery, and log output path. Dry-run mode will never open a network socket.

Each run will produce a JSONL log under an ignored `artifacts/` directory. Entries will include mode, timestamps, command text, response, telemetry, predicted position, video-frame metadata, cleanup actions, and final status. Exit code `0` means the complete run passed; exit code `1` means validation failed; exit code `2` means an emergency abort occurred.

## Safety constraints

- Real flight requires a clear indoor test area substantially larger than the planned cube, propeller guards where appropriate, and a supervising operator.
- The operator must keep the Tello app or another independent control option available.
- The emergency shortcut and fallback command must be visible before takeoff.
- The code must never report physical horizontal localization; reports must call it open-loop planned position.
- The test will land on normal completion and normal recoverable failure.
- Emergency stop is intentionally manual and immediate because stopping motors in flight can cause a drop.

## Testing strategy

Tests will be written before implementation in short red-green cycles.

- `FlightEnvelope` unit tests will cover each axis, both movement directions, exact faces, and out-of-bounds endpoints.
- Runner tests will prove the approved command order and that every planned position remains in the envelope.
- Dry-run integration tests will prove connection, telemetry, video-frame validation, cleanup, and emergency behavior without network access.
- CLI tests will prove dry-run isolation and exit-code behavior.
- No CI test will send real flight commands. Real-drone verification will be a documented manual procedure using `--real`.

## Acceptance criteria

The project is ready for its first supervised hardware trial when:

1. `python -m pytest` passes without a drone or network.
2. `python -m sentinel run --dry-run` exits `0` and logs the complete sequence.
3. The real client can connect to a Tello, decode one video frame, and execute only the approved bounded commands.
4. A movement that would cross any cube face is rejected before transmission.
5. Pressing `E`, or invoking the fallback emergency command, records an emergency abort and prevents subsequent movement commands.
6. README and safety documentation clearly state that x/y bounds are open-loop and require supervision.
