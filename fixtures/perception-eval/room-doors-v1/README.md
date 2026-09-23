# ROOM_DOORS_V1 — the evaluation pack the provider profile cites

Eleven frames the drone captured itself, from the two flights that carried a live detector:
`20260922-163134-reanchor` (six) and `20260923-122157-reanchor` (five). They are the only frames
in existence that the real camera took, at the real stops, of the real room.

Rebuild everything with:

    uv run python fixtures/perception-eval/room-doors-v1/build_pack.py

## What is in here

| File | What it is |
|---|---|
| `manifest.json` | `perception-evaluation-manifest.v1`: one case per frame, each identified by its sha256 |
| `labels.json` | the truth for each case: how many of each class are really in the frame |
| `build_pack.py` | rebuilds both, and the profile, from the frames |

The frames themselves are **not** in the repository. They live under `artifacts/`, which is
gitignored, and they show people. The pack identifies each one by sha256, so the manifest is
verifiable against the frames without the repository carrying pictures of anyone.

## How `evaluation_pack_sha256` is derived

    sha256( json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) )

taken over `manifest.json`'s own canonical bytes. Since every case carries its frame's sha256,
that one hash covers the images, the labels' identity and the case list together. Change a frame,
a label reference or a split and the pack hash changes, and the profile that cites the old hash
no longer describes the pack.

Current value: `daf6b387c8fc40ce87ffee023a8ad8a92c30d5316e94f160db797cdc9669f59b`

## The labels

Read off the frames on 2026-09-23 by looking at them, not copied from any detector's answer — a
profile measured against its own output would measure nothing. The object at the foot of the doors
is the phone the operator confirmed on 2026-09-22. `DOORS_0922_RETURN` is a true negative because
the frame's bottom edge cuts above the floor and the phone is out of shot, not because it moved.

## What the recorded flight scored against it

The 2026-09-22 and 2026-09-23 runs already answered these exact frames, so the pack can be scored
without spending anything:

**9 of 11 exact class counts.** Both misses are the phone at a far stop — about 15 px wide — in
`DOORS_0922_NEAR_R_LO` and `DOORS_0922_FAR_R_HI`. Full-frame detection missed them; the 2 x 2 tiled
detector recovered the phone on those same frames on 2026-09-22. No false positive of any class on
any frame.

## What this pack does not establish

- **No computer appears in any of the eleven frames.** The demo counts computers. This pack cannot
  tell you anything about that, and neither can any number derived from it.
- Eleven frames, one room, one wall, two times of day. It is evidence, not a benchmark.
- No adversarial negatives: no printer, no television, no projector screen, no picture of a person
  — the confusions `CLASS_HINTS` was written to prevent are untested here.
- `decision_threshold` is not measured. This provider returns confidence *words*, not numbers.
