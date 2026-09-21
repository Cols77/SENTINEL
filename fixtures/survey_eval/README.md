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
30 cm apart, repeats one frame ID in the replay schedule, and includes a second
distinct frame of the same scene. Coverage remains
two unique truth objects. Merge outputs with only one object must expose an
undercount; duplicate outputs cannot match the same truth twice.

The off-axis manifest places a phone at (150,600), observed from y=0 and y=300.
Its projected width changes from 17.075 to 34.15 px, crossing the fixed 20 px
resolution threshold through an actual change in pose. The other truth object
is explicitly occluded. Far coverage is zero; the near follow-up adds the phone;
whole-scene coverage is 1/2. The far observation explicitly records an uncertain
phone label without a usable box. Near scoring must use observed truth: a
correctly reported phone has observed recall 1 and whole-scene coverage 1/2.
The rotated/elevated scene looks along +x with the camera 55 cm above a 75 cm
surface; the expected reference pixel is (138.5,485.2166667).
The missing/unanalysed manifest must refuse replay
for both options.

`sentinel.survey_eval.score_episode` accepts an explicit outcome plus optional
room-localized object hypotheses and class count intervals. Precision/recall
are object-hypothesis matching metrics, not bbox/provider metrics. Matching is
maximum-cardinality within a caller-specified centimetre radius. Count intervals
preserve uncertainty and open upper bounds. A terminal status alone does not
establish report emission; outcome.report must contain the emitted payload.
An escalated run may still contain a valid report. An absent report has no
validity/count claim; an empty dictionary without count evidence does not imply
zero objects. An explicit report with objects=[] does. Exact unique-count error
requires exact evidence for every relevant class; lower bounds leave it unset.

Pass selected_option_ids to score_episode for observed-only matching and
separate whole_scene_coverage. Selected replay must be complete. Without this
argument, evaluation_scope is whole_scene_hypotheses and observed_recall remains
unset; this cannot be used as production observed-recall evidence.

`compare_modes(scene, episodes)` scores explicitly supplied episode payloads.
It does not run F/E/J policies or integrate the pending production survey loop.
Callers must gather those payloads and retain the real run's status/evidence.

The separate scripts/survey_sweep.py bridge now runs an explicitly supplied
external production survey checkout with these scenes. It requires
--approve-sweeps for scripted offline approval, uses the actual production loop
and proposer classes with scripted E/J advisors, and records source snapshots.
It does not call a live model or fly hardware. See the evaluation handoff for
the command and remaining production defects.

Scoring treats absent hypothesis evidence differently from an explicit empty
list. Missing evidence leaves recall/localisation unset. Count classes must be
unique; predicted coordinates must be finite non-boolean numbers or explicit
None. Report evidence must be a structured dictionary; a production text report
is bridged as {body, objects, class_counts} after production consistency checks.
