# Mission Control visual polish

Branch: `design/mission-control-polish`, based on `f598969` after the Task 4b decision-log commits landed.

## Design intent

Mission Control is read by an operator and observers on a shared screen during a flight. The visual direction is a calm flight instrument panel: strong text, restrained surfaces, teal for confirmed outcomes, amber for recovery, and the emergency token only for emergency outcomes and controls. The central state and answer readout is the signature element. The existing static modules, local IBM Plex fonts, Sentinel tokens, three-column desktop layout, accessibility features, and 1100 px collapse remain the boundaries.

## Changes

- Put setup and approval, live state and logs, and regions and detections in distinct columns. At 1024 px, live state appears first.
- Made the localization state a text badge. Land and EMERGENCY STOP stay in a sticky room header; the latter uses the kit's emergency icon and token.
- Gave the count answer, return-home result, and completed landing stronger hierarchy. Incomplete and emergency outcomes have their own neutral or emergency styling.
- Made approval a deliberate amber-framed step; added rhythm and bounded scrolling to the decision and recovery logs. Scrolled log and answer detail positions survive the existing redraws.
- Rendered region states as compact chips without changing each item's text; detections retain class, confidence, and location.

The 27 room-agent and 5 shell `data-testid` attributes remain. Visible strings, element types used by the checklist, request handling, polling, fetch calls, and operational data were not changed.

## Shared-file reconciliation

| File | Additive change | Why |
| --- | --- | --- |
| `examples/survey-console/console.css` | `.mission-layout[hidden]{display:none}` | The survey grid's author-level `display:grid` overrode the `hidden` attribute, leaving survey panels visible below the room view. |
| `examples/survey-console/shell.mjs` | None | No shell markup change was needed. |

## Verification

- `node --test tests/survey_console/*.test.mjs`: **116 passed**.
- `pytest -q -p no:warnings tests/ui`: **197 passed** with the Rehearsal server stopped. One earlier concurrent run had a five-second timeout in `test_clean_run_waits_for_operator_then_counts_real_artifacts`; the isolated test passed in 0.96 s and the subsequent full suite passed.
- Playwright Rehearsal scenario (a): completed and landed; exact count answer, `Returned home`, `Landing: LANDED (REQUESTED)`, three observed regions, and final `4. Answer` decision checked on the final code.
- Playwright Rehearsal scenario (b): COASTING, RECOVERY_REQUIRED, one yaw rotation, accepted re-anchor, final answer and return-home result checked. Controlled Land returned `Land: ACKNOWLEDGED` and `SAFE_LANDED · LANDED`. Emergency returned `Emergency: ACKNOWLEDGED` and `ABORTED`.
- At 1440×900 and 1024×768, the room view had no horizontal page overflow; the sticky safety header remained visible on scroll.

The current scenario (b) emits `Re-anchor accepted at (217, 190) cm`; the E2E checklist still expects `(230, 190) cm`. This branch does not change the projection or the checklist. A single polled frame did not contain both the RECOVERY_REQUIRED badge and the later yaw log entry, so the two actual states are captured separately. Playwright paused the browser clock after observing the RECOVERY_REQUIRED frame to preserve it for the screenshot, then resumed it.

## Screenshots

| State | 1440 px | 1024 px |
| --- | --- | --- |
| Before, scenario (a) complete | [before](mission-control-polish/before-1440-completed.png) | [before](mission-control-polish/before-1024-completed.png) |
| After, scenario (a) complete | [after](mission-control-polish/after-1440-completed.png) | [after](mission-control-polish/after-1024-completed.png) |

- [Approval card](mission-control-polish/after-approval-card.png)
- [Scenario (b): RECOVERY_REQUIRED](mission-control-polish/after-b-recovery-state-frozen.png)
- [Scenario (b): yaw and accepted re-anchor log](mission-control-polish/after-b-yaw-log.png)
- [Emergency state](mission-control-polish/after-emergency-1440.png)
