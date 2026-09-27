# SENTINEL

SENTINEL is a supervised physical-AI demonstrator for mountain inspection. An orchestrator agent dispatches a Tello drone, selected video frames are analysed for rocks or pebble clusters, and the resulting evidence drives a proposed patroller task.

The complete product and database contract is defined in the [SENTINEL V0 Coherence Specification](docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md), whose [indoor-alley survey design](docs/superpowers/specs/2026-09-16-sentinel-v0-indoor-alley-survey-design.md) and [WP0–WP4](docs/superpowers/specs/) specifications define the executable V0 profile. This README is only a quick-start overview.

## Survey Mission Control

The lecture-room survey console now has a local Rehearsal/Live host and a closed schema-v3 browser projection. Rehearsal runs the production survey loop against synthetic boundary inputs without a drone. Start `uv run python -m sentinel.ui --mode rehearsal --data-dir artifacts/my-rehearsal`, open http://127.0.0.1:8771/console/, and request “count the computers”. Every flight proposal requires an explicit approval.

Text chat remains the primary control path. On browsers that expose speech recognition, an explicit hold-to-talk control can submit chat; the exact phrase “Sentinel, land now” alone starts a cancellable three-second controlled-Land countdown. The browser speech provider may process microphone audio remotely after the operator presses the control. SENTINEL does not retain recordings, listen passively, approve flights by voice, or expose voice Emergency.

See [Mission Control setup and Live prerequisites](examples/survey-console/README.md), [the HTTP contract](examples/survey-console/contract.md), and [verification handoff](docs/handoffs/2026-09-21-mission-control.md). Live hardware readiness has not been established by software tests.

To start Live Mission Control with the configured Canning St physical site profile:

```sh
sentinel mission-control --mode live --site-profile CanningSt --data-dir artifacts/local-live
```

Live startup refuses before binding unless the selected profile is enabled and physically valid. The profile selector can switch profiles while the session is pristine; after mission activity or evidence, it locks. See the setup guide for readiness, provider, consent, and calibration details.

### Room agent (Jev adaptive) in Mission Control

Pick **Room agent (Jev adaptive)** in the *Architecture* selector, choose a run mode, type a request (for example "count the phones and computers in the room"), press *Propose run*, then *Approve*. Nothing that can fly, open the drone link or make a paid call is built before you approve. The panel shows the localization state and fix age, each re-anchor and recovery step (yaw rotations with their heading, tag bursts with the tags seen), Jev's action and reason on each turn, the regions, detections, answer, return home and landing. *Land* lands through the room executor (it is recorded as LANDED). *EMERGENCY STOP* stops the motors and latches the session; restart Mission Control after using it.

- **Rehearsal** (fake drone, fake detector, scripted Jev) and **Jev rehearsal** (the same fakes with the real Jev over OpenRouter):

  ```powershell
  $env:PYTHONPATH = "src;."
  python -m sentinel.ui --mode rehearsal --data-dir artifacts/room-rehearsal            # add --i-authorise-paid-calls for Jev rehearsal
  ```

  Scenario (a) spreads phones and computers over three regions, with one low-confidence phone. Scenario (b) yaws the aircraft away from the tags after the second observation, so the recovery's mapped-tag yaw scan runs before the mission resumes.

- **Live** (real Tello, real detector, real Jev) runs `scripts/room_session.py live` with all of its gates. **One command prepares every authorization** — consent, grant registry, TRIAL grant, manifest + authorization digest, the two site assurances, and the pack Mission Control reads:

  ```powershell
  $env:PYTHONPATH = "src;."
  python scripts/room_live_prepare.py --run-root runs --consent-confirmed [--local-ip 192.168.10.2] [--scenario <name>] [--new-run]
  ```

  `--consent-confirmed` records today's filming consent for the scenario's venue; pass it only when everyone who may appear in frame has agreed. `--scenario` picks the flight geometry and venue (also shown in Mission Control's flight drop-down): the default is **`lecture4high`** (lecture room; one board look at tag-fix height, then the left, right and rear walls 30 cm up), with `lecture4`, `lecture`, `demo5`, `demo360`, `demo` and `canningst` also available. For the lecture4 demo scenarios the TRIAL grant and the floor-check assurances are valid **12 hours** from prepare; every other scenario keeps the 30-minute assurance window, so re-run prepare before each of its flights. The script prints the exact Mission Control launch command; for the lecture room it is:

  ```powershell
  $env:PYTHONPATH = "src;."
  python -m sentinel.ui --mode live --site-profile LectureRoom --data-dir artifacts/room-live `
    --room-live-config runs/live.pack.json --i-authorise-paid-calls
  ```

  Then open http://127.0.0.1:8771/console/, pick **Room agent (Jev adaptive)** and the flight scenario, type the request, *Propose run*, *Approve*. **Morning flight:** follow [the one-page checklist](docs/room-agent-morning-flight.md). By hand instead: prepare the L3 inputs with [the runbook](docs/room-agent-live-runbook.md) — the TRIAL grant (`room_capability.py issue-trial`), the manifest preview, and the two site assurances — then write a pack and start Mission Control with it:

  ```powershell
  @{ manifest = "runs/$RUN.manifest.json"; authorization_digest = $DIGEST; grant_registry = "runs/registry.json"
     consent_db = "runs/consent.sqlite3"; location = "CanningSt"; date = $TODAY; clock_domain_id = "operator-$RUN"
     assurances = @("runs/$RUN.nav.assurance.json", "runs/$RUN.energy.assurance.json"); run_root = "runs"
     local_ip = "192.168.10.2" } | ConvertTo-Json | Set-Content -Encoding utf8 runs/$RUN.pack.json
  $env:PYTHONPATH = "src;."
  python -m sentinel.ui --mode live --site-profile CanningSt --data-dir artifacts/room-live `
    --room-live-config runs/$RUN.pack.json --i-authorise-paid-calls
  ```

  The site, tag map and CanningSt profiles default to `fixtures/room_agent/v1/` and `docs/wp5-flight3-map.json`. The pack can override `site`, `tag_map`, `drift_profile`, `energy_profile`, `command_limits`, `profile_files`, `connectivity_timeout_s` (default 20) and `connectivity_max_age_s` (default 90). `local_ip` is optional and can also be typed in the panel. The automatic adapter selection now probes a 192.168.10.x adapter a second time before it refuses. While the Live run is flying, pressing `E` in the Mission Control terminal also stops the motors.

- **Takeoff check.** Run this by hand, with the Tello on the pad and the cube clear: `python scripts/room_takeoff_check.py --execute [--local-ip 192.168.10.2]`. It connects over the live stack, takes off, hovers about 3 s at the takeoff height and lands. `E` stops the motors. Without `--execute` it refuses.

The browser E2E steps and their selectors are listed in [tests/ui/e2e/room_agent_e2e.md](tests/ui/e2e/room_agent_e2e.md).

## V0 demonstration loop

```text
User request → orchestrator → authorised Tello mission
             → selected frame → rock/pebble detection
             → event-driven agent resume → task proposal
             → operator confirmation → simulated patroller assignment
```

V0 is complete when the loop runs end to end and its history can be reconstructed from the append-only event stream. The minimum loop uses one drone, one orchestrator and a simulated patroller roster. Patroller data, travel time, and the fixed six-zone alley geometry must be visibly labelled as simulated when used.

## Architecture and state

The local coordinator owns SQLite in WAL mode and is the only database writer. Drone workers, the UI and the agent communicate through APIs and events.

The database separates:

- **Factual state:** drone status, captured observations, detector results and completed tool calls.
- **Agent state:** current phase, pending call, target, retry count and event resume cursor. Do not store private chain-of-thought.
- **Derived state:** targets, confidence decisions and assignment recommendations, each linked to evidence and policy/model versions.

Every observation, detection and patroller record carries provenance (`LIVE`, `SIMULATED`, `REPLAYED`, `OPERATOR_PROVIDED` or `INFERRED`) plus source, timestamps, confidence where applicable and `run_id`.

## Safety boundaries

- Real movement is restricted to a validated mission envelope and choreography.
- The operator activates the envelope during setup; valid text-triggered inspections may then dispatch inside it.
- Emergency stop remains available throughout the run.
- Heartbeat, battery, timeout and idempotency checks fail closed.
- The agent cannot write drone state or detections directly.
- A proposed patroller assignment remains inactive until the operator confirms it.
- No GPS arrival or precise horizontal position is claimed for a standard Tello; V0 uses section-level localisation.
- Continuous video is for operator visibility. Only selected, quality-gated frames are persisted and sent to perception.

Use a clear area substantially larger than the movement envelope, an operator who can intervene independently, and propeller guards where appropriate.

## Installation

Python 3.11 or newer is required:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

The real backend uses [DJITelloPy](https://github.com/damiafuentes/DJITelloPy) behind a pinned local compatibility boundary, with PyAV for video decoding. DJITelloPy owns process-global command/state receiver threads, so SENTINEL permits one real backend per process.

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
python -m sentinel yaw-scan --real --confirm --local-ip $env:SENTINEL_LOCAL_IP --tello-ip 192.168.10.1 --video-path fixtures/perception/live/yaw-scan-20260916.h264 --dwell-seconds 2 --turn cw:90 --turn cw:90 --turn cw:90 --turn cw:90
```

This is command-level position control only: Tello can drift in x/y/z because
the SDK path has no position hold or external localization. The controller
sends no translational or altitude-change command after takeoff, refuses the
scan below 40% battery, and escalates to the independent emergency path if an
airborne stream, telemetry, yaw, or landing operation fails. Obtain supervised
operator authorization for each real sequence.

Run on a real Tello only after physical preflight and explicit confirmation:

```powershell
python -m sentinel run --real --confirm
```

Send an emergency motor stop from an independent terminal:

```powershell
python -m sentinel emergency
```

Real mode accepts `--local-ip`, `--interface-name` and `--interface-index`. SENTINEL does not disable adapters, change route metrics or switch Wi-Fi networks. Reports are JSONL files under `artifacts/` by default.

## Perception

The first hosted perception route is a selected detector provider (DJITelloPy-framed frames) gated by a named joint Colin+Dzivha approval over a versioned, reproducible test-image-set hash and a matching evaluation manifest. Approval is shared between Colin and Dzivha; execution of the provider work is Dzivha's. Store the raw response, provider, model, prompt and policy versions; scores from different models are not automatically comparable.

A replay path may support rehearsal or emergency presentation only when it is persistently labelled `REPLAYED FALLBACK`. It does not prove live perception.

## Development

```powershell
python -m pytest
python -m sentinel run --dry-run --log artifacts/verification.jsonl
```

No automated test sends real flight commands. Do not describe a real-drone run as validated without fresh supervised evidence.

## Roadmap

- **V0:** one complete inspection-to-assignment loop on the fixed six-zone indoor-alley profile.
- **V0.1:** a numbered planned-route overlay and capture points on the coordinator UI, plus a drawn revisit proposal for re-inspection. These render coordinator facts only; they never calculate or command flight movement.
- **V0.2:** possible-casualty detection and human-reviewed response.
- **V0.3:** one bounded closer observation for ambiguous detections.
- **V1+:** six-zone alley reopen/survey operations, multi-source state and coordinated multi-drone inspection.

Later releases must preserve the event, provenance, tool and approval contracts established by V0.

## References

- [SENTINEL V0 Coherence Specification](docs/superpowers/specs/2026-09-16-sentinel-v0-coherence-spec.md) — canonical V0 product/integration profile
- [V0 Indoor Alley Survey Design](docs/superpowers/specs/2026-09-16-sentinel-v0-indoor-alley-survey-design.md) — approved decision record for the room-demo
- [WP0–WP4 V0 specifications](docs/superpowers/specs/) — shared contracts, coordinator, NiceGUI, perception and Tello worker
- [Demo State Database Specification](docs/SENTINEL_Demo_State_Database_Spec_v0.2.md)
- [DJI Tello-Python](https://github.com/dji-sdk/Tello-Python) — protocol/sample reference
- [DJI Tello SDK 2.0 guide](https://dl-cdn.ryzerobotics.com/downloads/Tello/Tello%20SDK%202.0%20User%20Guide.pdf)
- [DJITelloPy](https://github.com/damiafuentes/DJITelloPy) — runtime control and video API
