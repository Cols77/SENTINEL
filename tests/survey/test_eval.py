# tests/survey/test_eval.py
"""Rev-4 amendment against the plan body's Task 9 test:

The plan body's ``_script`` built already-normalised ``Detection``-shaped
dicts (``class``, ``score_kind``, ``bbox_coordinate_space``, ...) as the
``ScriptedDetector`` script directly. That is not what ``ScriptedDetector``
consumes here (Task 3, rev 4 V7): ``detector.detect`` runs the script's
items through ``detector.normalise`` -- the same as a real provider
response would be -- so the script must be *raw provider items*
(``label``/``x``/``y``/``width``/``height``/``confidence``), exactly the
shape ``tests/survey/test_loop.py::_raw_item`` builds. This file reuses
that helper (and ``_StaticFlight``/``_writer``) rather than inventing a
second, inconsistent fixture shape.
"""
import json
import math

from sentinel.survey.detector import ScriptedDetector
from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.eval import compare_modes, load_scene, score_episode
from sentinel.survey.loop import run_survey
from sentinel.survey.options import generate_options
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer
from sentinel.survey.store import SurveyStore

from tests.survey.test_loop import _raw_item, _StaticFlight, _writer

ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)
OPTIONS = generate_options(ENVELOPE)


def _scene_file(tmp_path, objects, scene_id="scene"):
    path = tmp_path / f"{scene_id}.json"
    path.write_text(json.dumps({
        "scene_id": scene_id, "objects": objects,
        "frames_dir": "frames", "responses_dir": "responses"}), encoding="utf-8")
    return path


def _script(objects):
    """One raw provider item per stop per object, positioned so
    ``geometry.ground_depth``/``position_of`` recover each object's true
    ``(x_cm, y_cm)`` from a camera at ``_StaticFlight``'s own stop
    positions (``i * 75 - 112.5`` for ``i`` in ``range(4)``, ``y=0``,
    ``heading=0``) -- see ``test_loop.py::_raw_item``."""
    script: dict[str, list[dict]] = {f"f{i}.png": [] for i in range(4)}
    for obj in objects:
        for i in range(4):
            camera_x = i * 75.0 - 112.5
            script[f"f{i}.png"].append(_raw_item(obj["x_cm"], obj["y_cm"], camera_x))
    return script


def test_a_perfect_run_scores_zero_count_error_and_full_localisation(tmp_path):
    objects = [{"class": "computer", "x_cm": 0.0, "y_cm": 300.0}]
    scene = load_scene(_scene_file(tmp_path, objects, "one-computer"))
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count the computers",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_script(objects)),
                         on_approval=lambda call: True, writer=_writer([]))
    score = score_episode(scene, outcome, store, "RUN1")
    assert score["count_error"]["computer"] == 0
    assert score["localisation"] == 1.0
    assert score["illegal_calls"] == 0
    assert score["report_consistent"] is True
    assert score["dropped_labels"] == ()
    assert score["depth_spread_pct"] is not None


def test_a_missed_object_scores_as_an_undercount(tmp_path):
    objects = [{"class": "computer", "x_cm": 0.0, "y_cm": 300.0},
               {"class": "computer", "x_cm": 900.0, "y_cm": 300.0}]
    scene = load_scene(_scene_file(tmp_path, objects, "two-computers"))
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    # The provider only ever finds the first computer -- the second is
    # missed entirely, which must score as an undercount, not a crash.
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_script(objects[:1])),
                         on_approval=lambda call: True, writer=_writer([]))
    score = score_episode(scene, outcome, store, "RUN1")
    assert score["count_error"]["computer"] == -1
    assert score["localisation"] == 0.5


def test_a_dropped_label_is_attributed_separately_from_a_geometry_failure(tmp_path):
    """R8: ``dropped_labels`` lets a reviewer tell "the provider used a
    label the class map does not know" apart from "the geometry failed" --
    score_episode must surface what run_survey already accumulated, not
    silently drop it."""
    objects = [{"class": "computer", "x_cm": 0.0, "y_cm": 300.0}]
    scene = load_scene(_scene_file(tmp_path, objects, "one-computer-plus-noise"))
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    script = _script(objects)
    script["f0.png"].append({"label": "sandwich", "confidence": "high",
                             "description": "a sandwich", "x": 0.05, "y": 0.05,
                             "width": 0.1, "height": 0.1})
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(script),
                         on_approval=lambda call: True, writer=_writer([]))
    score = score_episode(scene, outcome, store, "RUN1")
    assert score["dropped_labels"] == ("sandwich",)


def test_all_three_modes_are_scored_over_the_same_scene(tmp_path):
    objects = [{"class": "computer", "x_cm": 0.0, "y_cm": 300.0}]
    scene = load_scene(_scene_file(tmp_path, objects, "one-computer"))

    class _Judge:
        """A real judge shape (rev 4 V1: state and instruction positional,
        criteria and name keyword-only), not a permissive ``**kwargs``
        fake."""

        def choice(self, state, instruction, *, criteria, name):
            label = sorted(criteria)[0]
            return {"value": label, "confidence": 0.9,
                    "probabilities": {label: 0.9, **{k: 0.02 for k in criteria
                                                     if k != label}}}

    class _Adapter:
        def propose(self, request, context):
            actionable = [c for c in context.legal.candidates
                          if c.tool_name == "survey.propose_sweep"]
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.propose_sweep",
                 "arguments": {"option_id": actionable[0].arguments["option_id"],
                               "reason": "the widest coverage of the request"}}]}

    class _Sink:
        def write(self, record):
            pass

    scores = compare_modes(
        scene,
        {"F": RuleProposer(), "J": JevProposer(_Judge(), _Sink()),
         "E": LlmProposer(_Adapter(), _Sink())},
        options=OPTIONS, flight_factory=_StaticFlight,
        detector=ScriptedDetector(_script(objects)), tmp_path=tmp_path)
    assert set(scores) == {"F", "E", "J"}
    for mode in ("F", "E", "J"):
        assert scores[mode]["illegal_calls"] == 0
        assert scores[mode]["report_consistent"] is True
