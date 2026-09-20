from sentinel.survey.candidates import (ESCALATE_LABEL, PROPOSE_SWEEP, REPORT_LABEL,
                                        REVIEW_RESULTS, propose_candidates, review_candidates)
from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.options import generate_options

ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)
OPTIONS = generate_options(ENVELOPE)


def test_every_propose_candidate_carries_a_complete_ready_to_submit_call():
    legal = propose_candidates(OPTIONS)
    assert legal.decision_point == PROPOSE_SWEEP
    for candidate in legal.candidates:
        if candidate.label == ESCALATE_LABEL:
            continue
        assert candidate.tool_name == "survey.propose_sweep"
        assert set(candidate.arguments) == {"option_id", "reason"}
        assert candidate.arguments["option_id"] == candidate.label
        assert candidate.description


def test_escalate_is_always_available_at_propose():
    assert ESCALATE_LABEL in propose_candidates(OPTIONS).labels


def test_review_offers_reporting_plus_one_follow_up_per_option():
    results = {"uncertain_objects": [], "counts": {}, "unanalysed": 0, "flights_used": 1}
    legal = review_candidates(results, OPTIONS)
    assert legal.decision_point == REVIEW_RESULTS
    assert REPORT_LABEL in legal.labels
    assert any(c.tool_name == "survey.propose_sweep" for c in legal.candidates)


def test_escalate_is_always_available_at_review_too():
    """V2: review_candidates must include an escalate Candidate, or the
    loop's unguarded LegalSet.by_label(ESCALATE_LABEL) raises StopIteration
    the moment anything escalates at the review point."""
    results = {"uncertain_objects": [], "counts": {}, "unanalysed": 0, "flights_used": 1}
    legal = review_candidates(results, OPTIONS)
    assert ESCALATE_LABEL in legal.labels


def test_a_run_that_has_used_both_flights_can_only_report_or_escalate():
    results = {"uncertain_objects": [], "counts": {}, "unanalysed": 0, "flights_used": 2}
    legal = review_candidates(results, OPTIONS)
    assert set(legal.labels) == {REPORT_LABEL, ESCALATE_LABEL}


def test_each_follow_up_names_the_uncertain_objects_it_would_view():
    results = {"counts": {}, "unanalysed": 0, "flights_used": 1,
               "uncertain_objects": [{"object_id": "O1", "class": "phone",
                                      "x_cm": 0.0, "y_cm": 300.0}]}
    legal = review_candidates(results, OPTIONS)
    follow_ups = [c for c in legal.candidates if c.tool_name == "survey.propose_sweep"]
    assert follow_ups
    assert any("O1" in c.arguments["addresses"] for c in follow_ups)


def test_candidate_labels_are_unique():
    labels = propose_candidates(OPTIONS).labels
    assert len(labels) == len(set(labels))


def test_review_candidate_labels_are_unique():
    results = {"uncertain_objects": [], "counts": {}, "unanalysed": 0, "flights_used": 1}
    labels = review_candidates(results, OPTIONS).labels
    assert len(labels) == len(set(labels))
