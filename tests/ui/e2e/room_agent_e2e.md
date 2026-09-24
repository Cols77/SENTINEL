# Room agent Mission Control UI checklist

The Room agent page presents a Live flight workflow. The server can still run in
rehearsal mode for local UI inspection, but that does not make a rehearsal option
available in the Room agent controls. Scripted rehearsal scenarios remain covered
by the API tests in `tests/ui/test_room_agent.py`.

## Launch for visual inspection

From this worktree, start Mission Control with a room Live pack and use an
isolated data directory. Open `/console/` and select **Room agent (Jev adaptive)**
in the architecture selector. Without a pack, the Live proposal stays disabled;
the layout and camera tabs can still be inspected.

## Responsive layout

Check at desktop, tablet, and phone widths, including 1440, 1024, 800, and 375
CSS pixels. The flight pack strip, camera, status, setup, results, and logs must
fit without horizontal page scrolling or clipped controls. On phones, the camera
and its three view buttons should appear directly below flight preparation.

## Camera views

| View | Expected |
|---|---|
| Live camera | Live decoded frames and current AprilTag outlines appear during a flight. Before dispatch, an empty camera state is shown. |
| Gemini capture | The latest saved PNG capture and its detection overlays appear when available. Before the first capture, the view says there is no saved capture. |
| Recorded review | When recorded review was selected before proposing a Live flight, the tab offers post-flight analysis. After analysis, it shows the annotated video and detection timeline together. Otherwise it explains why the review is unavailable. |

Only one view is visible at a time, and the selected button has `aria-pressed=true`.
Switching views must not remove the flight status or change the running mission.

## Live controls

The setup area shows `LIVE · REAL TELLO` and has no rehearsal mode selector.
The single **Emergency land** button requests a controlled landing when a run is
active. A Live proposal requires a valid staged pack. Selecting **Recorded
review** in the post-flight review selector before proposing enables the review
workflow for that flight; it does not change the live detector or mission count.

Use the automated Node and Python tests for scenario outcomes and safety API
behavior. Physical flight and post-flight video generation require a compatible
Tello, room Live pack, and local analysis dependencies.
