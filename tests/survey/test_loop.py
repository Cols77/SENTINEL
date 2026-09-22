# tests/survey/test_loop.py
"""Rev-3/rev-4 corrections against the plan body's original fixtures and
loop:

R3: the plan body's ``_fly_and_ingest`` set ``row["band"] = "high"``
unconditionally after reading sightings back from the store -- the
non-low-band CONFIRM gate in ``merge.py`` was therefore only ever exercised
by hand-built dicts, never through a real ingest. The band must be stored
at ingest and read back unchanged.

R7: ``on_approval`` must receive the full rendered ``Candidate.description``
(stops, extent, range, resolvable classes, duration), not just
``{option_id, reason}``.

R4: the writer gets exactly one rewrite attempt before ``REPORT_REJECTED``.

V8b: there is no non-retain path that calls the provider any more (rev 3
R5), so ``batch.raw`` is always stored.

The detections here must be *ground-plane mergeable* -- Task 4 replaced
triangulation with ``geometry.ground_depth``/``position_of`` (rev 3 R1), so
a bbox has to sit below the image's vertical centre (``PRINCIPAL_Y``) to
resolve to a depth at all; a bbox that does not is Task 4's already-tested
"unpositioned, stays UNCERTAIN forever" path, not a bug here.
"""
import math

import pytest

from sentinel.survey.candidates import ESCALATE_LABEL, REPORT_LABEL
from sentinel.survey.consent import record_consent
from sentinel.survey.detector import ScriptedDetector
from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.flight import FlightFrame
from sentinel.survey.loop import DELTA_Z_CM, get_results, get_site, run_survey
from sentinel.survey.observability import FOCAL_PX, IMAGE_HEIGHT, IMAGE_WIDTH, PRINCIPAL_X, PRINCIPAL_Y
from sentinel.survey.options import generate_options
from sentinel.survey.proposer import RuleProposer
from sentinel.survey.store import SurveyStore

ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)
OPTIONS = generate_options(ENVELOPE)
RAIL = next(o for o in OPTIONS if o.option_id == "RAIL_MID_HEIGHT")


class _StaticFlight:
    """Four stops along a rail, all looking straight ahead."""

    def __init__(self, stops=4):
        self.stops = stops
        self.flights = 0

    def fly(self, option):
        self.flights += 1
        return [FlightFrame(stop_index=i, look_deg=0.0, x_cm=i * 75.0 - 112.5, y_cm=0.0,
                            z_cm=option.z_cm, heading_deg=0.0, position_kind="COMMANDED",
                            image_path=f"frames/f{i}.png", artifact_id=f"A{i}",
                            sha256="0" * 64)
                for i in range(self.stops)]


def _raw_item(target_x, target_y, camera_x_cm, label="laptop", band="high",
             delta_z_cm=DELTA_Z_CM, width_px=40, height_px=40):
    """A raw provider item that ``normalise`` maps into a detection whose
    bbox is below the image's vertical centre, so ``geometry.ground_depth``
    can place it at (roughly) ``target_x, target_y`` for a camera at
    ``camera_x_cm, y=0`` looking along +y."""
    bearing = math.atan2(target_x - camera_x_cm, target_y)
    u = PRINCIPAL_X + FOCAL_PX * math.tan(bearing)
    v = PRINCIPAL_Y + delta_z_cm * FOCAL_PX / target_y
    return {"label": label, "confidence": band, "description": "a laptop",
            "x": (u - width_px / 2) / IMAGE_WIDTH, "y": (v - height_px / 2) / IMAGE_HEIGHT,
            "width": width_px / IMAGE_WIDTH, "height": height_px / IMAGE_HEIGHT}


def _one_computer_script(target_y=RAIL.range_cm, band="high"):
    return {f"f{i}.png": [_raw_item(0.0, target_y, i * 75.0 - 112.5, band=band)]
            for i in range(4)}


def _writer(body_calls):
    def write(results):
        body_calls.append(results)
        confirmed = next(c["confirmed"] for c in results["class_counts"]
                         if c["class"] == "computer")
        return f"I found {confirmed} computers."
    return write


def test_a_clean_run_flies_once_counts_and_reports(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    calls = []
    outcome = run_survey(store=store, run_id="RUN1", request_text="count the computers",
                         options=OPTIONS, policy=RuleProposer(), flight=flight,
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=_writer(calls))
    assert outcome["status"] == "REPORTED"
    assert flight.flights == 1
    assert outcome["counts"]["computer"]["confirmed"] == 1
    assert "1 computers" in outcome["report"]


def test_aborted_partial_flight_keeps_evidence_but_never_reports_success(tmp_path):
    class PartialFrames(list):
        status = "ABORTED"

    class PartialFlight(_StaticFlight):
        def fly(self, option):
            return PartialFrames(super().fly(option)[:1])

    store = SurveyStore.open(tmp_path / "partial.sqlite3")
    calls = []
    outcome = run_survey(store=store, run_id="PARTIAL", request_text="count the computers",
                         options=OPTIONS, policy=RuleProposer(), flight=PartialFlight(),
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=_writer(calls))
    assert outcome["status"] == "ABORTED"
    assert outcome["flights"] == 0
    assert outcome["report"] is None and calls == []
    assert store.connection.execute("SELECT status FROM sweeps").fetchone()[0] == "ABORTED"
    assert store.connection.execute("SELECT count(*) FROM frames").fetchone()[0] == 1
    assert store.connection.execute("SELECT count(*) FROM sightings").fetchone()[0] == 1


def test_a_refused_proposal_ends_the_run_without_flying(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    outcome = run_survey(store=store, run_id="RUN1", request_text="count the computers",
                         options=OPTIONS, policy=RuleProposer(), flight=flight,
                         detector=ScriptedDetector({}),
                         on_approval=lambda call: False, writer=_writer([]))
    assert outcome["status"] == "REFUSED"
    assert flight.flights == 0


def test_on_approval_receives_the_full_rendered_candidate_description(tmp_path):
    """rev 3 R7: approving a bare option id is not approval -- the operator
    must see the same stops/extent/range/resolvable-classes/duration text
    the candidate carries."""
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    seen = []

    def _capture(call):
        seen.append(call)
        return False  # refuse so the test never needs frames/detections

    run_survey(store=store, run_id="RUN1", request_text="count the computers",
              options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
              detector=ScriptedDetector({}), on_approval=_capture, writer=_writer([]))

    assert len(seen) == 1
    call = seen[0]
    assert call["tool_name"] == "survey.propose_sweep"
    assert "description" in call
    assert "stops" in call["description"]
    assert "range" in call["description"]
    assert "resolves" in call["description"]


def test_the_run_never_flies_a_third_time(tmp_path):
    """The cap is enforced by code, not by the policy's good manners."""
    class _AlwaysFollowUp:
        def decide(self, request, context):
            from sentinel.survey.proposer import _call
            actionable = [c for c in context.legal.candidates
                          if c.tool_name == "survey.propose_sweep"]
            return _call(context, actionable[0].label, "again")

    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=_AlwaysFollowUp(), flight=flight,
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=_writer([]))
    assert flight.flights == 2
    assert outcome["status"] == "REPORTED"


def test_an_escalation_at_propose_ends_the_run_for_the_operator(tmp_path):
    class _Escalating:
        def decide(self, request, context):
            from sentinel.survey.proposer import _escalate
            return _escalate(context, "the request names no coverage I have")

    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    outcome = run_survey(store=store, run_id="RUN1", request_text="have a look round",
                         options=OPTIONS, policy=_Escalating(), flight=flight,
                         detector=ScriptedDetector({}),
                         on_approval=lambda call: True, writer=_writer([]))
    assert outcome["status"] == "ESCALATED"
    assert flight.flights == 0
    assert outcome["escalations"][0]["label"] == ESCALATE_LABEL


def test_a_provider_error_is_reported_not_silently_dropped(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    script = _one_computer_script()
    del script["f2.png"]
    calls = []
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(script),
                         on_approval=lambda call: True, writer=_writer(calls))
    assert calls[0]["unanalysed"] == 1
    assert outcome["status"] == "REPORTED"


def test_a_writer_that_invents_a_number_is_refused(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True,
                         writer=lambda results: "I found 47 computers.")
    assert outcome["status"] == "REPORT_REJECTED"
    assert outcome["report"] is None


def test_the_writer_gets_one_rewrite_before_rejection(tmp_path):
    """rev 3 R4: the writer is allowed one rewrite attempt before
    REPORT_REJECTED."""
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    attempts = []

    def writer(results):
        attempts.append(results)
        if len(attempts) == 1:
            return "I found 999 computers."
        confirmed = next(c["confirmed"] for c in results["class_counts"]
                         if c["class"] == "computer")
        return f"I found {confirmed} computers."

    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=writer)
    assert len(attempts) == 2
    assert outcome["status"] == "REPORTED"
    assert "1 computers" in outcome["report"]


def test_a_low_band_sighting_cannot_produce_a_confirmed_object_through_the_real_store(tmp_path):
    """rev 3 R3: the plan body's ``_fly_and_ingest`` hard-coded every
    sighting's band to "high" after reading it back from the store, so the
    non-low-band CONFIRM gate was only ever exercised by hand-built dicts.
    Four stops would satisfy CONFIRM_MIN_STOPS on their own; only a real,
    stored "low" band must keep this UNCERTAIN."""
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_one_computer_script(band="low")),
                         on_approval=lambda call: True, writer=_writer([]))
    assert outcome["counts"].get("computer", {}).get("confirmed", 0) == 0


def test_get_site_reports_the_options_the_loop_actually_offers():
    """rev 4 V8f: survey.get_site must be a real function, not just a name
    in the spec."""
    site = get_site(OPTIONS)
    assert {option["option_id"] for option in site["options"]} == {o.option_id for o in OPTIONS}
    rail = next(option for option in site["options"] if option["option_id"] == "RAIL_MID_HEIGHT")
    assert rail["range_cm"] == RAIL.range_cm
    assert "computer" in rail["resolvable_classes"]
    assert site["perimeter"]["min_x_cm"] == ENVELOPE.min_x_cm
    assert site["perimeter"]["max_x_cm"] == ENVELOPE.max_x_cm


def test_get_results_is_the_function_the_loop_routes_through(tmp_path):
    """rev 4 V8f: run_survey must call survey.get_results itself, not a
    private duplicate of the same logic."""
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    run_survey(store=store, run_id="RUN1", request_text="count",
              options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
              detector=ScriptedDetector(_one_computer_script()),
              on_approval=lambda call: True, writer=_writer([]))
    results = get_results(store, "RUN1", OPTIONS)
    assert results["flights_used"] == 1
    assert any(c["class"] == "computer" and c["confirmed"] == 1 for c in results["class_counts"])


def test_without_recorded_consent_the_run_ends_and_the_detector_is_never_called(tmp_path):
    """rev 3 R5 (Task 8): absent consent, the run ends CONSENT_REQUIRED
    before any capture is written, and the detector is never called."""
    class _BoomDetector:
        def detect(self, image_path, classes):
            raise AssertionError("the detector must never be called without consent")

    store = SurveyStore.open(tmp_path / "s.sqlite3")
    flight = _StaticFlight()
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=flight,
                         detector=_BoomDetector(), on_approval=lambda call: True,
                         writer=_writer([]), location="garden", consent_date="2026-09-21")
    assert outcome["status"] == "CONSENT_REQUIRED"
    assert outcome["flights"] == 0
    assert flight.flights == 0


def test_recorded_consent_lets_a_provider_backed_run_proceed_normally(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    record_consent(store, "garden", "2026-09-21", recorded_by="operator")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=_writer([]),
                         location="garden", consent_date="2026-09-21")
    assert outcome["status"] == "REPORTED"
    assert outcome["counts"]["computer"]["confirmed"] == 1


def test_a_run_with_no_location_given_is_unaffected_by_the_consent_gate(tmp_path):
    """Design spec 13.4: hermetic/replay runs over already-consented frames
    proceed without touching this gate. Every test above this one in this
    file relies on exactly this default."""
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=_writer([]))
    assert outcome["status"] == "REPORTED"


def _single_stop_script(target_y=RAIL.range_cm, band="high"):
    """One detection at the first stop only, so ``merge.py``'s
    ``CONFIRM_MIN_STOPS`` gate (3) leaves a single, *positioned*
    UNCERTAIN object at the REVIEW_RESULTS turn -- unlike the
    unpositioned "no box" case Task 4 already covers, this one carries a
    real ``x_cm``/``y_cm`` a follow-up option can actually ``address``."""
    return {"f0.png": [_raw_item(0.0, target_y, -112.5, band=band)]}


def test_review_decision_quality_is_zero_when_the_report_is_chosen_over_a_stronger_follow_up(tmp_path):
    """rev 4 V8c: no test anywhere in this suite ever drove
    ``_review_decision_quality`` through a real uncertain object at
    REVIEW_RESULTS (every fixture before this one produces only CONFIRMED
    objects), so ``outcome["decision_quality"]`` was untested for any value
    other than None. ``RuleProposer`` always picks the first actionable
    candidate, which ``review_candidates`` orders as ``report`` before any
    follow-up (``candidates.review_candidates``): it addresses nothing,
    while a follow-up that would view the single uncertain object here was
    on offer. decision_quality must be 0.0, not None and not 1.0."""
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_single_stop_script()),
                         on_approval=lambda call: True, writer=_writer([]))
    assert outcome["counts"].get("computer", {}).get("confirmed", 0) == 0
    assert outcome["decision_quality"] == 0.0


def test_review_decision_quality_is_one_when_the_best_addressing_follow_up_is_chosen(tmp_path):
    """Same single-stop, UNCERTAIN, positioned object as above, but the
    policy chooses the follow-up whose ``addresses`` list actually names
    it: decision_quality must be 1.0."""
    class _AddressBestFollowUp:
        """Picks whichever propose_sweep candidate addresses the most
        uncertain objects. At PROPOSE_SWEEP no candidate carries an
        ``addresses`` argument at all, so every candidate ties at 0 and
        the first (``RAIL_MID_HEIGHT``, matching this scene's geometry)
        is kept; at REVIEW_RESULTS this is the real decision under test."""

        def decide(self, request, context):
            from sentinel.survey.proposer import _call, _escalate
            sweeps = [c for c in context.legal.candidates
                     if c.tool_name == "survey.propose_sweep"]
            if not sweeps:
                return _escalate(context, "no sweep candidate offered")
            best = max(sweeps, key=lambda c: len(c.arguments.get("addresses", ())))
            return _call(context, best.label, "addresses the uncertain object")

    store = SurveyStore.open(tmp_path / "s.sqlite3")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=_AddressBestFollowUp(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_single_stop_script()),
                         on_approval=lambda call: True, writer=_writer([]))
    assert outcome["decision_quality"] == 1.0


# -- Task 11/V12: a per-frame delta_z_cm, falling back to loop.DELTA_Z_CM ----

class _OneFrameWithDeltaZ:
    """A single stop carrying its own measured delta_z_cm, as a real flight
    port (survey.cube_bridge.CubeFlight) would."""

    def fly(self, option):
        return [FlightFrame(stop_index=0, look_deg=0.0, x_cm=0.0, y_cm=0.0,
                            z_cm=option.z_cm, heading_deg=0.0, position_kind="MEASURED",
                            image_path="frames/f0.png", artifact_id="A0",
                            sha256="0" * 64, delta_z_cm=42.0)]


def test_a_frame_without_delta_z_cm_falls_back_to_the_run_constant(tmp_path):
    """FlightFrame.delta_z_cm defaults to None -- every fixture and flight
    port written before Task 11 (``_StaticFlight`` included) must keep
    storing exactly ``loop.DELTA_Z_CM``, unchanged."""
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    outcome = run_survey(store=store, run_id="RUN1", request_text="count",
                         options=OPTIONS, policy=RuleProposer(), flight=_StaticFlight(),
                         detector=ScriptedDetector(_one_computer_script()),
                         on_approval=lambda call: True, writer=_writer([]))
    assert outcome["status"] == "REPORTED"
    frames = store.frames("RUN1")
    assert frames and all(f["delta_z_cm"] == DELTA_Z_CM for f in frames)


def test_a_frame_with_delta_z_cm_uses_its_own_value(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    run_survey(store=store, run_id="RUN1", request_text="count",
              options=OPTIONS, policy=RuleProposer(), flight=_OneFrameWithDeltaZ(),
              detector=ScriptedDetector({"f0.png": []}),
              on_approval=lambda call: True, writer=_writer([]))
    frames = store.frames("RUN1")
    assert frames[0]["delta_z_cm"] == 42.0
