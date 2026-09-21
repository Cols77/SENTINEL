# Handoff to Dzivha — camera model and the survey offline bridge

**Written:** 2026-09-21, plan phase 4 (workstream D). **For:** Dzivha, owner of
`fixtures/perception/src/sentinel_perception/`. Factual only; nothing in her package was edited to
produce this handoff.

---

## 1. Camera model contradiction

`sentinel_perception.config.CameraConfig` carries `hfov_deg=70.2`, `vfov_deg=55.6` -- a diagonal
field-of-view figure (the Tello datasheet's 82.6 deg, read as a diagonal) turned into separate
horizontal/vertical angles. `depth.py`'s `Intrinsics.from_fov` builds pixel intrinsics from that pair
and assumes a **centred, level** optical axis (`cx = width / 2`, `cy = height / 2`); `depth.py`'s
`to_world` rotates a bearing by yaw only -- there is no pitch term anywhere in that path.

The measured chessboard calibration in `calibration/tag-map-calibrated.json.camera_calibration`
(158 views, RMS 0.311 px) gives a different camera:

| | `CameraConfig` (FOV-derived) | `tag-map-calibrated.json` (measured) |
|---|---|---|
| fx, fy | implied by 70.2 deg x 55.6 deg | 892.58, 893.74 |
| cx, cy | width/2, height/2 (centred) | 467.75, 353.83 (off-centre) |
| Field of view | 70.2 deg x 55.6 deg | 56.5 deg x 43.9 deg |
| Optical axis pitch | 0 deg (level) | 8.6 deg down from horizontal |

Coherence spec section 10.5 makes the measured numbers the single camera model for real Tello
frames (`sentinel.camera.TELLO_MEASURED`, re-exported by `sentinel.survey.observability`); the
survey package's ground-plane geometry (`sentinel.survey.geometry`) is built against it, not against
`CameraConfig`/`Intrinsics.from_fov`.

**Consequence for your bearings and `to_world`:** on real footage the true field of view is
narrower than `CameraConfig` assumes (56.5 x 43.9 deg, not 70.2 x 55.6), the true principal point is
off-centre, and the true optical axis is pitched 8.6 deg down. `Intrinsics.from_fov`'s centred-axis
assumption and `to_world`'s yaw-only rotation cannot represent that pitch, so every bearing and
world position `to_world` produces from real Tello pixels is systematically biased -- not a rounding
error, a structural gap in the model. This is a statement about the code as it stands today, not a
request to change anything before you've read it.

## 2. What the offline bridge imports from your package, and the stability it needs

`scripts/survey_detect_gdino.py` (this phase, main-repo side) runs inside your
`fixtures/perception` uv project and imports, read-only:

- `sentinel_perception.detection.GroundingDinoDetector` -- constructed as
  `GroundingDinoDetector(model_id, model_sha, box_threshold=..., text_threshold=...)`; its
  `.detect(image_path, queries, frame_index, sector)` return shape
  (`DetectionResult.detections`: `Detection(label, query, score, box_xyxy, frame_index, sector)`,
  `box_xyxy` absolute pixels in the *decoded image's own* size) is depended on exactly.
- `sentinel_perception.detection.detect_multipass`, `clean`, `deduplicate` -- signatures and the
  `(kept, rejection_counts)` / `list[Detection]` return shapes are depended on exactly; the bridge
  calls them the same way `cli.py:cmd_detect` does (one `detect_multipass` pass over all five class
  phrases per frame, `clean()` then `deduplicate()` once per frame).
- `sentinel_perception.models.load_manifest` (raises `FileNotFoundError` when `models/manifest.json`
  is absent -- expected, see section 4) and `models.stage` (context manager) -- the bridge reads
  `manifest["detector_openvocab"]["id"]` / `["sha"]` and wraps detection in `stage("grounding-dino")`
  exactly as `cmd_detect` does.

Please flag before changing any of those four shapes (`DetectionResult`/`Detection`'s fields,
`clean`/`deduplicate`'s signatures, or the `detector_openvocab` manifest key) -- the bridge has no
test coverage inside your environment (it cannot import `sentinel`, so it is tested by contract, not
by import, from the main repo) and would fail silently or loudly depending on which shape moved.

## 3. The WP3 boundary exception -- read before any provider profile is approved

`docs/superpowers/specs/2026-09-20-survey-counting-design.md` section 6.1 and
`docs/superpowers/specs/2026-09-20_operator-consent-record.md` entry C-20e currently state that the
survey flow records `score_kind: VLM_CONFIDENCE_BAND` honestly and **never** claims `DETECTOR_RAW`.

This phase's code changes that statement's truth in code, ahead of the design doc: the bridge above
writes `score_kind: "DETECTOR_RAW"` items with a real Grounding DINO similarity score, and
`sentinel.survey.detector.normalise` now has a branch that bands a `DETECTOR_RAW` score into
high/medium/low via two constants (`DETECTOR_RAW_HIGH_PROVISIONAL = 0.35`,
`DETECTOR_RAW_MEDIUM_PROVISIONAL = 0.25`) explicitly marked PROVISIONAL -- placeholders the provider
spike (plan workstream E) is meant to pin with a measured score distribution, not validated
thresholds.

The design doc and consent record have **not** been revised to match (that revision, design rev 6
plus a new consent entry, is plan workstream F's job, a later phase). As of this handoff there is a
live inconsistency between what section 6.1 says and what the code now does. **No survey provider
profile -- including yours, if Grounding DINO is ever proposed as a survey-flow provider rather than
staying confined to the alley/WP3 pipeline -- may be approved on the strength of this phase's code
alone**, before that reconciliation lands.

## 4. Model weights are not staged

`scripts/probe_models.py` is what fetches the Grounding DINO weights your detector needs (a
multi-GB Hugging Face download) and writes `models/manifest.json`. Nobody has run it in this
worktree: `models/manifest.json` does not exist here. `scripts/survey_detect_gdino.py` cannot run
past `load_manifest()` until someone runs `probe_models.py` (or otherwise stages that manifest) in
the `fixtures/perception` environment. This is a plain statement of what has and hasn't happened,
not a request directed at you specifically.
