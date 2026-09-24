# Room agent E2E (Playwright): Mission Control, Rehearsal

Python Playwright is not installed on the demo laptop and must not be installed
(operator rule). The orchestrating session runs these steps through the
Playwright MCP browser. The selectors below are stable `data-testid`s from
`examples/survey-console/room-agent.mjs`. The run is API-tested as well, in
`tests/ui/test_room_agent.py` (scenarios a and b, Land, Emergency, Live gating).

## 0. Launch Mission Control (Rehearsal)

From the worktree root, in PowerShell:

```powershell
$env:PYTHONPATH = "src;."
& "C:\Users\33630\AppData\Local\Temp\claude\C--coding-sentinel\2541e4bd-1eaa-4102-a60d-558629bb5271\scratchpad\ui\venv-c0\Scripts\python.exe" `
  -m sentinel.ui --mode rehearsal --port 8793 --data-dir artifacts/mission-control-e2e
```

Wait for `Uvicorn running on http://127.0.0.1:8793`. Each run uses a fresh
`--data-dir` (or delete it first) so the page starts with no room-agent run.
Rehearsal is paced for watching: one run takes about 30 s (scenario a) to 45 s
(scenario b).

## 1. Scenario (a): count, answer, return home

| # | Action | Selector | Expected |
|---|--------|----------|----------|
| 1 | Navigate to `http://127.0.0.1:8793/console/` | | Title `SENTINEL · Mission Control` |
| 2 | Select option `room_agent` | `[data-testid=architecture-select]` | The option label is `Room agent (Jev adaptive)`; `[data-testid=room-agent-panel]` is visible |
| 3 | Select option `REHEARSAL` | `[data-testid=room-mode-select]` | (the default) |
| 4 | Select option `a` | `[data-testid=room-scenario-select]` | (the default) |
| 5 | Fill `count the phones and computers in the room` | `[data-testid=room-request]` | |
| 6 | Click | `[data-testid=room-propose]` | `[data-testid=room-approval]` appears; `[data-testid=room-proposal-request]` contains `count the phones and computers in the room` |
| 7 | Click | `[data-testid=room-approve]` | `[data-testid=room-status]` contains `RUNNING` |
| 8 | While running | `[data-testid=room-localization-state]` | `ANCHORED`; `[data-testid=room-fix-age]` matches `fix age \d+\.\d s` |
| 9 | While running | `[data-testid=room-decision-log]` | Items like `1. Observe center region — Observe declared pose ... (p 0.99) → OBSERVE_SUCCEEDED` |
| 10 | Wait (up to 90 s) for text `COMPLETED` | `[data-testid=room-status]` | `Rehearsal: fake drone, fake detector, scripted Jev · COMPLETED · LANDED` |
| 11 | Assert | `[data-testid=room-answer]` | exactly `phones: 1 confirmed, 1 uncertain; computers: 2 confirmed` |
| 12 | Assert | `[data-testid=room-returned-home]` | exactly `Returned home` |
| 13 | Assert | `[data-testid=room-landing]` | exactly `Landing: LANDED (REQUESTED)` |
| 14 | Assert | `[data-testid=room-regions]` | three items, each ending `OBSERVED · <n> detections` (Center, Left, Right) |
| 15 | Assert | `[data-testid=room-detections]` | contains `phone (low)` |
| 16 | Assert | `[data-testid=room-decision-log]` | last item starts with `4. Answer` |

## 2. Scenario (b): tag loss, yaw rotation, accepted re-anchor

| # | Action | Selector | Expected |
|---|--------|----------|----------|
| 1 | Select option `b` | `[data-testid=room-scenario-select]` | |
| 2 | (the request box still holds the request; refill if empty) | `[data-testid=room-request]` | `count the phones and computers in the room` |
| 3 | Click | `[data-testid=room-propose]` | `[data-testid=room-approval]` appears |
| 4 | Click | `[data-testid=room-approve]` | `[data-testid=room-status]` contains `RUNNING` |
| 5 | Wait (up to 120 s) for text `COMPLETED` | `[data-testid=room-status]` | `... · COMPLETED · LANDED` |
| 6 | Assert | `[data-testid=room-reanchor-log]` | contains, in this order: `Localization ANCHORED -> COASTING at heading 75 deg`, `Localization COASTING -> RECOVERY_REQUIRED`, `Recovery stationary burst at heading 75 deg: no tags in view`, `Yaw rotation ccw 15 deg -> heading 90 deg`, `Recovery yaw burst at heading 90 deg: tags 0, 2`, `Re-anchor accepted at (217, 190) cm, heading 90 deg (mapped_joint_tags)`, `Return home (MISSION_TERMINAL)` |
| 7 | Assert | `[data-testid=room-reanchor-heading]` | `Re-anchor and recovery (1 yaw rotations, 1 accepted)` (at least one of each) |
| 8 | Assert | `li[data-kind=YAW_ROTATION]` inside `[data-testid=room-reanchor-log]` | count >= 1 |
| 9 | Assert | `li[data-kind=REANCHOR_ACCEPTED]` inside `[data-testid=room-reanchor-log]` | count >= 1 |
| 10 | Assert | `[data-testid=room-answer]` | `phones: 1 confirmed, 1 uncertain; computers: 2 confirmed` |
| 11 | Assert | `[data-testid=room-returned-home]` | `Returned home` |
| 12 | Assert | `[data-testid=room-localization-state]` | `ANCHORED` |

## 3. Optional: controlled Land during a run

| # | Action | Selector | Expected |
|---|--------|----------|----------|
| 1 | Select `a`, propose, approve (as above) | | `RUNNING` |
| 2 | After ~8 s, click **Emergency land** | `[data-testid=room-land]` | `[data-testid=room-message]` becomes `Land: ACKNOWLEDGED` |
| 3 | Wait for text `LANDED` | `[data-testid=room-status]` | `... · SAFE_LANDED · LANDED` |
| 4 | Assert | `[data-testid=room-landing]` | `Landing: LANDED (REQUESTED)` (never `LANDING_FAILED`) |
| 5 | Assert | `[data-testid=room-reanchor-log]` | contains `Operator Land: LANDED` |

The independent motor-stop transport is available through the safety API; test it
last, then restart Mission Control.

## MCP call sketch

```
browser_navigate   url=http://127.0.0.1:8793/console/
browser_select_option target=[data-testid=architecture-select] values=["room_agent"]
browser_type       target=[data-testid=room-request] text="count the phones and computers in the room"
browser_click      target=[data-testid=room-propose]
browser_click      target=[data-testid=room-approve]
browser_wait_for   text="COMPLETED"            (repeat; each call waits 5 s)
browser_evaluate   () => ['room-status','room-answer','room-returned-home','room-landing','room-reanchor-heading','room-reanchor-log']
                         .map(t => t + ': ' + document.querySelector(`[data-testid=${t}]`).innerText).join('\n')
```

**Jev rehearsal (real Jev model over OpenRouter, fake drone), verified 2026-09-24 via Playwright MCP**
(start Mission Control with `--i-authorise-paid-calls`, select `JEV_REHEARSAL`):
- scenario (b), "count the phones and computers in the room":
  - Jev chose observe right, then left, then center (p 0.56–0.60);
  - mid-mission it went COASTING → RECOVERY_REQUIRED → a stationary burst with no tags → `Yaw rotation ccw 15 deg -> heading 90 deg` → tags 0 and 2 → re-anchor accepted;
  - it answered `phones: 1 confirmed, 1 uncertain; computers: 2 confirmed`;
  - `COMPLETED · LANDED`, `Returned home`.
- scenario (a), "find the phone": observe center → `ANSWERED_SEARCH`, a high-confidence phone on the demo surface at left-front plus an uncertain one; returned home and LANDED.
- Getting there required fixing, in `fc2cec2`..`22a09a8`:
  - the live Jev model and route;
  - the goal-compile gate (0.5 plus one re-ask);
  - resolving named classes in code;
  - single-option confirmations.

Verified by hand on 2026-09-23 through the Playwright MCP browser: scenario (a)
answered `phones: 1 confirmed, 1 uncertain; computers: 2 confirmed`, `Returned
home`, `Landing: LANDED (REQUESTED)`; scenario (b) logged the COASTING and
RECOVERY_REQUIRED transitions, one `ccw 15` yaw to heading 90, the tags 0 and 2
burst and the accepted re-anchor, then the same answer and return; a Land at
~8 s gave `Land: ACKNOWLEDGED` and `SAFE_LANDED · LANDED`.
