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
    assert decision["action"] == CENTER and decision["label"] == "Observe center region, facing the tag wall"
    assert decision["refused"] == "LOW_CONFIDENCE" and decision["probability"] == .44
    assert decision["result"] == "LOW_CONFIDENCE"
    # A confidence refusal leaves the region offered (Task 4b item 1).
    assert state["regions"][0]["status"] == "OFFERED"


def test_demo360_looks_get_distinct_labels_including_the_tag_facing_look():
    # Live 2026-09-26 (l3-20260926-101602): every demo360 pose hovers at the
    # room's centre, so only the look distinguishes the turn rows -- and the
    # tag-facing look had no suffix at all, reading as a bare "Center region"
    # next to three "facing the rear wall" rows.
    from sentinel.ui.room_agent_projection import region_label

    assert region_label("center-rear-middle-toward_targets") == "Center region, facing the tag wall"
    assert region_label("center-middle-middle-backward") == "Center region, facing the rear wall"
    assert region_label("center-middle-middle-left") == "Center region, facing the left wall"
    assert region_label("center-middle-middle-right") == "Center region, facing the right wall"


def test_why_this_step_keeps_the_whole_candidate_description():
    # Operator 2026-09-26: "Why this step" cut the text at 160 characters --
    # the interruption notes and distance the description ends with are
    # exactly the part that was lost. The panel scrolls; the projection must
    # not pre-truncate what the audit already carries in full.
    tail = "2 earlier attempt(s) at this pose were interrupted by a mandatory safety action"
    long_description = ("Observe declared pose center-rear-middle-toward_targets for objective g1: "
                        "adds a view of 3 unexplored cells (floor-x210-y110, floor-x290-y110, "
                        "floor-x250-y160); could resolve 6 unresolved cell/class pairs; approximate "
                        "direct distance from the drone's current position: 95 cm; " + tail)
    assert len(long_description) > 160
    candidates = [{"candidate_id": CENTER, "description": long_description}]
    state = fold(("ROOM_CHOICE_ATTEMPT", {"candidates": candidates, "provider_error": None, "assessment": {
        "selected_label": CENTER, "final_label": CENTER, "outcome": "ACCEPTED", "confidence": .9,
        "selected_probability": .9, "confidence_source": "SCALAR"}}))
    assert state["decisions"][0]["reason"] == long_description


def test_a_kind_mass_acceptance_shows_the_label_probability_and_the_observe_mass():
    state = fold(attempt(selected_label=RIGHT, final_label=RIGHT, outcome="ACCEPTED", confidence=.85,
                         selected_probability=.44, confidence_source="KIND_MASS"))
    decision = state["decisions"][0]
    assert decision["refused"] is None and decision["label"] == "Observe right region, facing the tag wall"
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


def test_a_look_interrupted_by_a_safety_action_leaves_the_region_offered():
    # Live l3-20260926-111405: a zone interrupted by localization recovery is
    # retryable, not failed; the turn row still shows what happened.
    state = fold(
        attempt(selected_label=CENTER, final_label=CENTER, outcome="ACCEPTED", confidence=.9,
                selected_probability=.9, confidence_source="SCALAR"),
        ("ROOM_TOOL_OUTCOME", {"candidate_id": CENTER, "code": "OBSERVE_PREEMPTED",
                               "refusal_code": "OBSERVE_PREEMPTED",
                               "detail": "look 2 of 3 interrupted by RECOVER (LOCALIZATION_RECOVERY_REQUIRED)"}))
    decision = state["decisions"][0]
    assert decision["action"] == CENTER and decision["result"] == "OBSERVE_PREEMPTED"
    center = next(r for r in state["regions"] if r["pose_id"].startswith("center"))
    assert center["status"] == "OFFERED"


def test_a_provider_error_turn_is_shown_as_refused_without_a_label():
    state = fold(("ROOM_CHOICE_ATTEMPT", {"candidates": CANDIDATES, "provider_error": "TimeoutError", "assessment": {
        "selected_label": None, "final_label": "ESCALATE", "outcome": "MALFORMED_ANSWER", "confidence": None,
        "selected_probability": None, "confidence_source": "MISSING"}}))
    decision = state["decisions"][0]
    assert decision["refused"] == "PROVIDER_ERROR" and decision["label"] == "No usable choice"
