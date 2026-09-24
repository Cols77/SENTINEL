# Handoff: visual polish of Mission Control (frontend-design pass)

**Goal:** a **visual-only** polish of the Mission Control console before the 2026-09-25 demo. It should read well on a shared screen during a live drone flight. The behaviour, data, wording and wiring stay exactly as they are.

## Where to work

- **Code:** branch `claude/room-agent-ui` (worktree `C:\coding\sentinel\.worktrees\jev-agentic-room-loop`). Create your own branch from its head, for example `design/mission-control-polish`, and keep commits small.
- **Wait for** the "Task 4b" commits to land on `claude/room-agent-ui` (check `git log`). They change `examples/survey-console/room-agent-model.mjs` and `src/sentinel/ui/room_agent_projection.py` (decision-log text).
- **Codex is also active:**
  - `codex/room-agent-coverage` (room-agent back end);
  - `handoff/lightcode-mission-control-docs` (the Mission Control UI itself: `shell.mjs`, `console.css`, the survey panels).
- **Never commit to Codex's branches.**
- **File ownership:**
  - The room-agent panel is ours: `room-agent.css`, `room-agent.mjs`. Polish it freely.
  - For shared files (`console.css`, `shell.mjs`), keep changes minimal and additive, and list them in your final report so Codex can reconcile.

## What the UI is

- **A static ES-module app,** served by the Python back end (`src/sentinel/ui/app.py`):
  - `/console/` → `examples/survey-console/`;
  - `/assets/sentinel-ui/` → `assets/sentinel-ui/`.
- **No build step:** plain HTML, CSS and `.mjs` files. Reload the page to see changes.
- **Entry points:** `examples/survey-console/index.html` → `app.mjs` → `shell.mjs` (header, architecture selector, survey panels) and `room-agent.mjs` (the "Room agent (Jev adaptive)" panel).
- **Styles:**
  - `assets/sentinel-ui/tokens/tokens.css` (design tokens; derived from `tokens.json`, which is authoritative);
  - `examples/survey-console/console.css` (shell and survey);
  - `examples/survey-console/room-agent.css` (room-agent panel, about 80 lines, the most in need of polish).
- **The design system** is in `assets/sentinel-ui/`. Read `README.md` and `component-specs.md` there first. It covers:
  - dark surface tokens, accent `#47D7C8`, warning `#F2BA55`, emergency `#F06565`;
  - brand red `#A40722`, which **never** means recording, connection or emergency;
  - IBM Plex Sans and Mono;
  - body text 16 px, metadata 14 px, headings 24 and 32 px;
  - 14 `currentColor` icons in `assets/sentinel-ui/icons/` (drone, battery, camera, emergency-stop, warning…);
  - placeholders and overlay treatments.

## How to see every state (free: no drone, no paid calls)

```powershell
$env:PYTHONPATH = "src;."
& "C:\Users\33630\AppData\Local\Temp\claude\C--coding-sentinel\2541e4bd-1eaa-4102-a60d-558629bb5271\scratchpad\ui\venv-c0\Scripts\python.exe" -m sentinel.ui --mode rehearsal --port 8793 --data-dir artifacts/mc-design
```

- **The Python environment:** the shared `.venv` lacks nicegui and fastapi; use the venv above.
- **Ports:** 8771–8773 and 8790 may be taken by other sessions; pick another port if so.
- **Walkthrough:** open `http://127.0.0.1:8793/console/`, set the architecture selector to **Room agent (Jev adaptive)** and the mode to **Rehearsal** (scripted Jev, fake drone; free). Then run `tests/ui/e2e/room_agent_e2e.md`:
  - **Scenario (a):** count, answer, return home.
  - **Scenario (b):** tag loss, then COASTING → RECOVERY_REQUIRED → yaw rotation → accepted re-anchor. It shows every localization state and the full re-anchor log.
  - **Land** during a run.
  - **EMERGENCY STOP.** It latches the session; restart the server afterwards.
- **Do not select `JEV_REHEARSAL` or `LIVE`:** they make paid calls or fly the drone.
- **Screenshots:** take them through the Playwright MCP (the project forbids installing Python Playwright), at 1440×900 and 1024×768, before and after.

## Scope: what to polish

Priorities for a live, shared-screen demo, in order:

1. **Glanceable status.** Make run status, localization state (ANCHORED / COASTING / RECOVERY_REQUIRED / LOST), fix age, battery and pose readable from across a room.
   - Localization is today a bold word in a `<dl>`. Turn it into a clear state badge using the status tokens.
   - Always keep the text too: never colour alone (a design-system rule).
2. **Safety controls.** **Land** and **EMERGENCY STOP** must be unmistakable and always visible while a run is active.
   - Use the emergency token and the `emergency-stop.svg` icon.
   - Do not move them out of the header, or make them smaller or harder to reach.
3. **The answer.** The count answer (for example "phones: 1 confirmed, 1 uncertain; computers: 2 confirmed"), "Returned home" and the landing state are the demo's punchline. Give them hierarchy.
4. **The approval card** (Propose → "Approve this run?"). It must read as a deliberate dispatch step. Approving flies a real drone in Live mode.
5. **Logs.** The Jev decision log and the re-anchor/recovery log need clearer rhythm:
   - timestamps or turn numbers;
   - YAW_ROTATION and REANCHOR_ACCEPTED entries visually distinct (they are the story of "it lost its position and found it again");
   - sensible max-heights and scrolling.
6. **Regions and detections.** Show OBSERVED / OFFERED / LOW_CONFIDENCE states as compact chips. Show detections with class, confidence and location.
7. **Overall layout.** Keep the three-column grid (setup | state and logs | results). It collapses to one column under 1100 px. Tidy the spacing, the card surfaces and the alignment with the survey panels, so switching architectures looks like one product.

## Hard constraints (the tests and E2E depend on them)

- **Do not rename, remove or move `data-testid` attributes.** There are 27 in `room-agent.mjs` and 5 in `shell.mjs`. The Playwright checklist `tests/ui/e2e/room_agent_e2e.md` and the node tests select by them.
- **Do not change visible text strings** asserted by tests. Examples:
  - `Returned home`, `Landing: LANDED (REQUESTED)`, `No room-agent run yet.`, `Awaiting answer…`, `Not landed`;
  - `fix age 0.4 s`, `battery 88%`, `x 250 cm · y 190 cm · z 100 cm · heading 90 deg`;
  - the decision-log line format `1. Observe left region — … (p 0.99) → OBSERVE_SUCCEEDED`;
  - the re-anchor log texts listed in the E2E doc.

  They come from `room-agent-model.mjs` (`roomViewModel`, `answerText`, `landingText`, …) and the Python projection. Style them; don't reword them.
- **Keep the element types** the tests use: `li[data-kind=...]` inside the re-anchor log, `select` elements for the architecture, mode and scenario selectors, a `textarea` for the request.
- **No new runtime dependencies,** no build step, no external CDN or fonts beyond what `assets/sentinel-ui` already ships. The demo laptop may be offline.
- **Accessibility:** keep labels, focus rings, the skip link and the `aria-live` announcer. Status must never be conveyed by colour alone. Honour `prefers-reduced-motion`.
- **No behaviour changes:** no new controls, no reordering of the safety flow, no changes to polling or fetch code (`http-client.mjs`, `controller.mjs`, `mission-app.mjs`).

## Verification before you hand back

```powershell
node --test "tests/survey_console/*.test.mjs"   # Node 22 rejects the bare directory form
$env:PYTHONPATH = "src;."
& "C:\Users\33630\AppData\Local\Temp\claude\C--coding-sentinel\2541e4bd-1eaa-4102-a60d-558629bb5271\scratchpad\ui\venv-c0\Scripts\python.exe" -m pytest -q -p no:warnings tests/ui
```

- Both suites must pass as they did before your change.
- Re-run the Rehearsal E2E checklist (scenarios a and b, Land) through the Playwright MCP. Every expected value in `tests/ui/e2e/room_agent_e2e.md` must still match.
- Attach the before/after screenshots at both widths, plus one mid-run shot in scenario (b) showing RECOVERY_REQUIRED and the yaw rotation in the log.

## Deliverable

A branch with small, visual-only commits (`style(ui): ...`), screenshots, and a short report containing:
- what changed;
- which shared files (`console.css`, `shell.mjs`) were touched, and why;
- anything you would change but didn't because it would alter behaviour or text.
