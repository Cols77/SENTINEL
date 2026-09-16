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

Run a preflight-only connectivity probe. It sends only the SDK command-mode
handshake and read-only battery/height/speed queries; it does not take off,
land, move, rotate, or start video:

```powershell
python -m sentinel probe --real --confirm --local-ip $env:SENTINEL_LOCAL_IP --tello-ip 192.168.10.1
```

The constrained yaw-scan controller permits takeoff, H.264 recording, bounded
`cw`/`ccw` rotation about Z, and landing. It has no front/left/right/back tilt
or translation tool. The default sequence is four `cw:90` turns; every turn is
capped at 90 degrees and the total yaw budget is capped at 360 degrees:

```powershell
python -m sentinel yaw-scan --real --confirm --local-ip $env:SENTINEL_LOCAL_IP --tello-ip 192.168.10.1 --video-path fixtures/perception/live/yaw-scan.h264 --dwell-seconds 2 --turn cw:90 --turn cw:90 --turn cw:90 --turn cw:90
```

This is command-level position control only: Tello can drift in x/y/z because
the SDK path has no position hold or external localization. The controller
sends no translational or altitude-change command after takeoff, refuses the
scan below 40% battery, and escalates to the independent emergency path if an
airborne stream, telemetry, yaw, or landing operation fails. Obtain supervised
operator authorization for each real sequence.

Run on a real Tello only after the physical preflight and explicit confirmation.
On Windows PowerShell, load the machine-local `env.ps1` into the **current**
PowerShell session. Dot-sourcing (`. .\env.ps1`) is required; running
`.\env.ps1` without the leading dot does not preserve the environment variable
after the script exits.

```powershell
# env.ps1 (machine-local; do not commit)
$env:SENTINEL_LOCAL_IP = "192.168.10.2"

. .\env.ps1
if ([string]::IsNullOrWhiteSpace($env:SENTINEL_LOCAL_IP)) {
    throw "SENTINEL_LOCAL_IP was not loaded"
}
$env:SENTINEL_LOCAL_IP
```

Create stable absolute artifact paths and run with unbuffered console output:

```powershell
$artifactDir = Join-Path (Get-Location) "artifacts"
New-Item -ItemType Directory -Force $artifactDir | Out-Null
$finalLog = Join-Path $artifactDir "real-run.jsonl"
$progressLog = Join-Path $artifactDir "real-run.progress.jsonl"

& .\.venv\Scripts\python.exe -u -m sentinel run --real --confirm --local-ip $env:SENTINEL_LOCAL_IP --tello-ip 192.168.10.1 --log $finalLog --progress-log $progressLog
```

The single-line invocation avoids PowerShell continuation errors. If you use
the multiline form, each backtick must be the final character on its line. A
PowerShell `>>` prompt means the command is still incomplete; press `Ctrl+C`
and paste the complete command again.

While the real run is active, watch the **progress** log from a second
PowerShell window:

```powershell
Get-Content -Path ./artifacts/real-run.progress.jsonl -Wait
```

The progress log is flushed after every phase and should begin with
`run_started`. The final `real-run.jsonl` report is written only when the run
finishes or fails; it is not the live log.

Send the emergency motor-stop command from an independent terminal:

```powershell
.\.venv\Scripts\python.exe -u -m sentinel emergency `
    --local-ip $env:SENTINEL_LOCAL_IP
```

The standalone emergency path uses only local adapter/route validation and
local socket binding; it does not wait for the normal `command`/`ok` probe
before attempting the motor-stop datagram.

Real mode also accepts `--local-ip`, `--interface-name`, and `--interface-index`. Automatic selection prefers an active interface that can reach `192.168.10.1` and is not the primary internet default-route interface. SENTINEL does not disable adapters, change route metrics, or switch Wi-Fi networks.

The Tello command, state, and video ports are 8889, 8890, and 11111. The run starts the video stream, validates at least one decoded frame, and stops the stream before the flight sequence.

Reports are JSONL files under `artifacts/` by default. Real runs also flush a
progress log at `artifacts/run.progress.jsonl` unless `--progress-log` is
specified. It records adapter selection, connection, telemetry, video, and
flight phases before the final report exists. The console prints the same
phase messages.

Real mode fails closed: adapter reachability, Tello connectivity, telemetry,
speed readback, route preservation, and video-frame validation must all pass
before takeoff. A normal failure after takeoff may attempt a landing.

Emergency stop is an immediate motor-stop datagram and is terminal: SENTINEL
will not send a later landing or flight command after it. The Tello protocol
does not acknowledge `emergency`; a successful command means the local OS
accepted the complete UDP datagram, not that motor stoppage has been observed.

## Wi-Fi troubleshooting

SENTINEL expects the dedicated USB adapter to be connected to the Tello network
while the normal Wi-Fi adapter remains connected to the internet. The expected
Windows state is:

```text
Wi-Fi   -> MBS-Data (or another normal network)
Wi-Fi 2 -> TELLO-XXXXXX
Wi-Fi 2 IPv4 -> 192.168.10.2/24
```

Check the adapter and Wi-Fi association:

```powershell
netsh interface show interface
netsh wlan show interfaces
netsh wlan show networks interface="Wi-Fi 2"
Get-NetIPAddress -InterfaceAlias "Wi-Fi 2" -AddressFamily IPv4
```

`Wi-Fi 2` must be **connected** to `TELLO-XXXXXX`. An address in
`169.254.0.0/16` means Windows assigned a link-local fallback address; it is
not a usable Tello address. If the Tello network is not listed, power on the
drone, wait for its SSID to appear, and reconnect the TP-Link adapter.

If the adapter is connected but still has `169.254.*`, configure IPv4 on
`Wi-Fi 2` as `192.168.10.2` with prefix length `24` (subnet mask
`255.255.255.0`). Leave the default gateway and DNS fields blank. Do not move
the internet default route to the Tello adapter.

Common failures:

- **No console output and no progress file:** the command did not reach
  SENTINEL. Confirm the prompt is `C:\coding\sentinel`, use
  `& .\.venv\Scripts\python.exe`, and dot-source `env.ps1` in that same
  PowerShell window.
- **Progress file exists but the final report does not:** the run is still
  active or stopped during setup. Read the progress file with `-Wait`.
- **`169.254.*` or `Wi-Fi 2` disconnected:** connect the USB adapter to the
  Tello SSID and configure `192.168.10.2/24`.
- **`selected adapter is inactive` or `no safe Tello adapter`:** the address
  from `env.ps1` is not currently assigned to an active adapter.
- **`could not resolve the default route source IP`:** update to the current
  SENTINEL code; Windows adapter-name/index compatibility is handled by the
  network selector.
- **Ping and ARP work, but `command` times out:** Windows may be blocking the
  Tello's UDP responses. The default firewall policy may be
  `BlockInbound,AllowOutbound`. In an elevated PowerShell window, add a narrow
  rule for the SENTINEL interpreter, the Tello address, the Tello adapter, and
  the command/state/video ports:

  ```powershell
  $python = (Resolve-Path ./.venv/Scripts/python.exe).Path
  New-NetFirewallRule -DisplayName "SENTINEL Tello UDP" -Direction Inbound -Action Allow -Program $python -Protocol UDP -LocalPort 8889,8890,11111 -RemoteAddress 192.168.10.1 -InterfaceAlias "Wi-Fi 2" -Profile Any
  ```

  Remove it after testing if it is no longer needed:

  ```powershell
  Remove-NetFirewallRule -DisplayName "SENTINEL Tello UDP"
  ```

  If `New-NetFirewallRule` returns **Access denied**, close that window and
  open **Windows PowerShell → Run as administrator**, accept the UAC prompt, and
  run the rule command again. If it still fails, local firewall-rule changes
  are restricted by Group Policy; an administrator must create the equivalent
  narrow rule, or the test must be run on a machine where local rules are
  permitted. Do not disable the firewall globally.

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
