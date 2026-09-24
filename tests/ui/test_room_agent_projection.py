"""Task 4b item 5: the decision log shows Jev's real choice."""
from types import SimpleNamespace

from sentinel.ui.room_agent_projection import RoomAgentProjection

CENTER = "OBSERVE:g1:center-rear-middle-toward_targets"
RIGHT = "OBSERVE:g1:right-rear-middle-toward_targets"
CANDIDATES = [{"candidate_id": CENTER, "description": "Observe center."},
              {"candidate_id": RIGHT, "description": "Observe right."},
              {"candidate_id": "ESCALATE", "description": "Escalate this request to the operator."}]


def fold(*payloads):
    projection = RoomAgentProjection()
    projection.fold([SimpleNamespace(sequence_no=index + 1, event_type=kind, payload=payload)
                     for index, (kind, payload) in enumerate(payloads)])
    return projection.public()


def attempt(**assessment):
    return "ROOM_CHOICE_ATTEMPT", {"candidates": CANDIDATES, "provider_error": None, "assessment": assessment}


def test_a_refused_turn_shows_the_refused_label_its_probability_and_the_code():
    state = fold(
        attempt(selected_label=CENTER, final_label="ESCALATE", outcome="LOW_CONFIDENCE", confidence=.36,
                selected_probability=.44, confidence_source="SCALAR"),
        ("ROOM_TOOL_OUTCOME", {"candidate_id": CENTER, "code": "LOW_CONFIDENCE", "refusal_code": "LOW_CONFIDENCE"}))
    decision = state["decisions"][0]
    assert decision["action"] == CENTER and decision["label"] == "Observe center region"
    assert decision["refused"] == "LOW_CONFIDENCE" and decision["probability"] == .44
    assert decision["result"] == "LOW_CONFIDENCE"
    # A confidence refusal leaves the region offered (Task 4b item 1).
    assert state["regions"][0]["status"] == "OFFERED"


def test_a_kind_mass_acceptance_shows_the_label_probability_and_the_observe_mass():
    state = fold(attempt(selected_label=RIGHT, final_label=RIGHT, outcome="ACCEPTED", confidence=.85,
                         selected_probability=.44, confidence_source="KIND_MASS"))
    decision = state["decisions"][0]
    assert decision["refused"] is None and decision["label"] == "Observe right region"
    assert decision["probability"] == .44 and decision["kind_mass"] == .85


def test_an_ordinary_acceptance_has_no_kind_mass_and_a_tool_failure_still_marks_the_region():
    state = fold(
        attempt(selected_label=RIGHT, final_label=RIGHT, outcome="ACCEPTED", confidence=.9,
                selected_probability=.9, confidence_source="SCALAR"),
        ("ROOM_TOOL_OUTCOME", {"candidate_id": RIGHT, "code": "TOOL_FAILURE", "refusal_code": "TOOL_FAILURE"}))
    decision = state["decisions"][0]
    assert decision["kind_mass"] is None and decision["refused"] is None
    right = next(r for r in state["regions"] if r["pose_id"].startswith("right"))
    assert right["status"] == "FAILED"


def test_a_provider_error_turn_is_shown_as_refused_without_a_label():
    state = fold(("ROOM_CHOICE_ATTEMPT", {"candidates": CANDIDATES, "provider_error": "TimeoutError", "assessment": {
        "selected_label": None, "final_label": "ESCALATE", "outcome": "MALFORMED_ANSWER", "confidence": None,
        "selected_probability": None, "confidence_source": "MISSING"}}))
    decision = state["decisions"][0]
    assert decision["refused"] == "PROVIDER_ERROR" and decision["label"] == "No usable choice"
