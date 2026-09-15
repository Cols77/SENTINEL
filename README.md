# SENTINEL

SENTINEL is a conservative, supervised DJI Tello command test harness. It supports deterministic dry-run execution and a real-drone backend built around a pinned, locally maintained DJITelloPy compatibility boundary.

## Safety boundary

The test plans a 1 m × 1 m × 1 m cube:

- x: −50 to +50 cm;
- y: −50 to +50 cm;
- z: 150 to 250 cm above ground.

The x/y position is an open-loop estimate derived from acknowledged movement commands. It is not physical localization. Use a clear area substantially larger than the cube, propeller guards where appropriate, and an operator who can intervene independently.

## Installation

Python 3.11 or newer is required. Create an environment and install the package with development dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

The real backend reuses [DJITelloPy](https://github.com/damiafuentes/DJITelloPy) and PyAV for Tello control and video decoding. The compatibility boundary binds command, state, and video sockets to the selected local adapter address. Upstream attribution is retained in `NOTICE.md`.

DJITelloPy 2.5.0 owns process-global command/state receiver threads. SENTINEL therefore permits one real backend per process and fails closed if a second one is attempted; start a fresh process for the next real run.

## Commands

Run the complete sequence without opening a network socket:

```powershell
python -m sentinel run --dry-run
```

Run on a real Tello only after the physical preflight and explicit confirmation:

```powershell
python -m sentinel run --real --confirm
```

Send the emergency motor-stop command from an independent terminal:

```powershell
python -m sentinel emergency
```

Real mode also accepts `--local-ip`, `--interface-name`, and `--interface-index`. Automatic selection prefers an active interface that can reach `192.168.10.1` and is not the primary internet default-route interface. SENTINEL does not disable adapters, change route metrics, or switch Wi-Fi networks.

The Tello command, state, and video ports are 8889, 8890, and 11111. The run starts the video stream, validates at least one decoded frame, and stops the stream before the flight sequence.

Reports are JSONL files under `artifacts/` by default. A normal failure may attempt a landing. Emergency stop is an immediate motor stop and is terminal: SENTINEL will not send a later landing or flight command after it.

## Position estimation

Human pose recognition from the original DJI sample is intentionally not used for drone localization. A future optional vision layer may use calibrated AprilTag/ArUco markers or Tello EDU mission-pad telemetry, but the first conservative runner remains open-loop in x/y.

## Development

```powershell
python -m pytest
python -m sentinel run --dry-run --log artifacts/verification.jsonl
```

No automated test sends real flight commands. Do not describe a real-drone run as validated until it has been supervised with the dedicated USB Wi-Fi adapter and fresh evidence has been recorded.

## References

- [DJI Tello-Python](https://github.com/dji-sdk/Tello-Python) — protocol/sample reference, not the runtime base.
- [DJI Tello SDK 2.0 guide](https://dl-cdn.ryzerobotics.com/downloads/Tello/Tello%20SDK%202.0%20User%20Guide.pdf).
- [DJITelloPy](https://github.com/damiafuentes/DJITelloPy) — Python 3 control and video API used behind SENTINEL’s adapter.
