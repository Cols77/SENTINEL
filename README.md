# SENTINEL

SENTINEL is a supervised physical-AI demonstrator for mountain inspection. An orchestrator agent dispatches a Tello drone, selected video frames are analysed for rocks or pebble clusters, and the resulting evidence drives a proposed patroller task.

The complete product and database contract is defined in the [SENTINEL Demo State Database Specification](docs/SENTINEL_Demo_State_Database_Spec_v0.2.md). This README is only a quick-start overview.

## V0 demonstration loop

```text
User request → orchestrator → authorised Tello mission
             → selected frame → rock/pebble detection
             → event-driven agent resume → task proposal
             → operator confirmation → simulated patroller assignment
```

V0 is complete when the loop runs end to end and its history can be reconstructed from the append-only event stream. The minimum loop uses one drone, one orchestrator and a simulated patroller roster. Trail mapping, patroller data and other mountain context must be visibly labelled as simulated when used.

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

The first hosted perception candidate is Grounding DINO on selected still frames, with Replicate as the first endpoint to test and Roboflow as an alternative. OpenAI vision and OpenRouter/Qwen are evaluation candidates for semantic verification, not assumed detector replacements.

The initial target-confirmation threshold is a configurable raw detector score of `0.90`. It is not a calibrated probability. Store the raw response, provider, model, prompt and policy versions; scores from different models are not automatically comparable.

A replay path may support rehearsal or emergency presentation only when it is persistently labelled `REPLAYED FALLBACK`. It does not prove live perception.

## Development

```powershell
python -m pytest
python -m sentinel run --dry-run --log artifacts/verification.jsonl
```

No automated test sends real flight commands. Do not describe a real-drone run as validated without fresh supervised evidence.

## Roadmap

- **V0:** one complete inspection-to-assignment loop.
- **V0.1:** repeatable local observation poses and coarse positioning.
- **V0.2:** possible-casualty detection and human-reviewed response.
- **V0.3:** one bounded closer observation for ambiguous detections.
- **V1+:** trail-opening operations, multi-source state and coordinated multi-drone inspection.

Later releases must preserve the event, provenance, tool and approval contracts established by V0.

## References

- [Demo State Database Specification](docs/SENTINEL_Demo_State_Database_Spec_v0.2.md)
- [DJI Tello-Python](https://github.com/dji-sdk/Tello-Python) — protocol/sample reference
- [DJI Tello SDK 2.0 guide](https://dl-cdn.ryzerobotics.com/downloads/Tello/Tello%20SDK%202.0%20User%20Guide.pdf)
- [DJITelloPy](https://github.com/damiafuentes/DJITelloPy) — runtime control and video API
