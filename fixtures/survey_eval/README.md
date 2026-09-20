# Independent synthetic survey acceptance

All manifests are synthetic arithmetic fixtures. They do not claim a real
capture, provider measurement, detection accuracy, or production-loop result.

Version 1 requires an explicit scene ID, synthetic provenance, surveyed x/y
bounds, pinhole calibration, unique truth IDs with classes and positions,
nominal physical widths, and recorded frames indexed by option and stop.
The camera is level; heading zero is +y and positive heading turns toward +x.
Surface z is the plane through the object's reference point. Projection uses
axial depth, not Euclidean camera-to-object range.

Frame detections are explicit replay payloads, including empty lists. Empty
detections do not mean empty ground truth. Occlusion IDs are independently
specified truth metadata. A missing or unanalysed frame makes its entire option
unevaluable. There is no substitution from another option or inference that an
unknown observation is empty.

The close-pair manifest contains two independently individuated truth objects
30 cm apart and repeats one frame ID in the replay schedule. Coverage remains
two unique truth objects. Merge outputs with only one object must expose an
undercount; duplicate outputs cannot match the same truth twice.

The off-axis manifest places a phone at (150,600), observed from y=0 and y=300.
Its projected width changes from 17.075 to 34.15 px, crossing the fixed 20 px
resolution threshold through an actual change in pose. The other truth object
is explicitly occluded. Far coverage is zero; the near follow-up adds the phone;
whole-scene coverage is 1/2. The missing/unanalysed manifest must refuse replay
for both options.

`sentinel.survey_eval.score_episode` accepts an explicit outcome plus optional
room-localized object hypotheses and class count intervals. Precision/recall
are object-hypothesis matching metrics, not bbox/provider metrics. Matching is
maximum-cardinality within a caller-specified centimetre radius. Count intervals
preserve uncertainty and open upper bounds. A run without a completed report has
no count error or report-validity claim.

`compare_modes(scene, episodes)` scores explicitly supplied episode payloads.
It does not run F/E/J policies or integrate the pending production survey loop.
Callers must gather those payloads and retain the real run's status/evidence.
