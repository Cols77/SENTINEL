"""Task V2: per-kind Jev acceptance thresholds for the room loop, and one re-ask.

The real Jev check (docs/reports/room-agent-jev-check.md) refused
directionally-correct choices at the controller's single 0.8 bar, e.g. the
right READ_DRONE for "what is your battery level?" at confidence 0.54.
"""
from __future__ import annotations

import pytest

from sentinel.room.controller import AcceptancePolicy, RoomController
from sentinel.room.models import CandidateKind, TerminalState
from tests.room.test_controller import Judge
from tests.room.test_queries import drone, goal, room  # noqa: F401  (fixture)



def spread(criteria, value, probability):
    """``value`` at ``probability``, the rest shared evenly (a clear margin)."""
    rest = [label for label in criteria if label != value]
    share = (1 - probability) / max(1, len(rest))
    return {"value": value, "probabilities": {**{label: share for label in rest}, value: probability}}


def attempts(store):
    return [e.payload for e in store.events("s1") if e.event_type == "ROOM_CHOICE_ATTEMPT"]


def outcomes(store):
    return [(e.payload["candidate_id"], e.payload["code"]) for e in store.events("s1")
            if e.event_type == "ROOM_TOOL_OUTCOME"]


def test_the_room_loop_policy_thresholds_by_kind():
    policy = AcceptancePolicy()
    assert policy.act_at_for(CandidateKind.OBSERVE) == .5
    assert policy.act_at_for(CandidateKind.REANCHOR) == .5
    for kind in (CandidateKind.READ_DRONE, CandidateKind.READ_OBSERVATIONS, CandidateKind.READ_COUNTS):
        assert policy.act_at_for(kind) == .5
    assert policy.act_at_for(CandidateKind.ANSWER) == .6
    # Terminal-risk kinds (CLARIFY/ESCALATE end the session) and anything
    # unlisted keep the strict bar.
    for kind in (CandidateKind.CLARIFY, CandidateKind.ESCALATE, CandidateKind.RETURN_HOME,
                 CandidateKind.LAND_AT_ZONE, None):
        assert policy.act_at_for(kind) == .8
    assert policy.reask_once is True


def test_thresholds_are_configurable_and_validated():
    custom = AcceptancePolicy(observe_act_at=.55, answer_act_at=.7, reask_once=False)
    assert custom.act_at_for(CandidateKind.OBSERVE) == .55 and custom.act_at_for(CandidateKind.ANSWER) == .7
    assert custom.as_config()["observe_act_at"] == .55
    for bad in ({"observe_act_at": 1.5}, {"answer_act_at": float("nan")}, {"reask_once": 1}):
        with pytest.raises(ValueError):
            AcceptancePolicy(**bad)
    strict = AcceptancePolicy.strict(.8, .15)
    assert {strict.act_at_for(kind) for kind in CandidateKind} == {.8} and not strict.reask_once


def test_without_a_policy_the_controller_keeps_one_strict_bar_and_never_reasks(room):
    """The original behaviour, unchanged: READ_DRONE at 0.54 is refused and
    Jev is asked once for the turn."""
    goal(room)
    drone(room)
    judge = Judge(lambda state, criteria: spread(criteria, "READ_DRONE", .54))
    outcome = RoomController(room, judge).step("s1")
    assert not outcome.accepted and len(judge.calls) == 1
    assert outcomes(room)[-1] == ("READ_DRONE", "LOW_CONFIDENCE")
    assert attempts(room)[-1]["act_at"] == .8 and "reask" not in attempts(room)[-1]


def test_the_room_loop_policy_accepts_the_right_read_at_the_confidence_jev_gave_it(room):
    """Case 4 of the real check: READ_DRONE at 0.54 is accepted at the 0.5
    read-only bar; the answer at 0.72 is accepted at the 0.6 answer bar."""
    goal(room)
    drone(room)
    judge = Judge(lambda state, criteria: spread(criteria, "READ_DRONE", .54),
                  lambda state, criteria: spread(criteria, "ANSWER", .72))
    controller = RoomController(room, judge, acceptance=AcceptancePolicy())
    assert controller.run_until_terminal("s1") == TerminalState.COMPLETED
    assert outcomes(room)[0] == ("READ_DRONE", "READ_SUCCEEDED") and outcomes(room)[1][0] == "ANSWER"
    assert len(judge.calls) == 2  # no re-ask was needed
    first, second = attempts(room)
    assert first["act_at"] == .5 and second["act_at"] == .6
    assert first["acceptance_policy"] == AcceptancePolicy().as_config()


def test_a_choice_below_its_bar_is_reasked_once_with_the_refusal_as_feedback(room):
    goal(room)
    drone(room)
    judge = Judge(lambda state, criteria: spread(criteria, "READ_DRONE", .41),
                  lambda state, criteria: spread(criteria, "READ_DRONE", .66))
    outcome = RoomController(room, judge, acceptance=AcceptancePolicy()).step("s1")
    assert outcome.accepted and len(judge.calls) == 2
    feedback = judge.calls[1][0]["previous_refusal"]
    assert feedback["refused_choice"] == "READ_DRONE" and feedback["refusal"] == "LOW_CONFIDENCE"
    assert feedback["confidence"] == pytest.approx(.41) and feedback["required_confidence"] == .5
    assert "previous_refusal" not in judge.calls[0][0]
    attempt = attempts(room)[-1]
    assert attempt["reask"]["first_assessment"]["outcome"] == "LOW_CONFIDENCE"
    assert attempt["assessment"]["outcome"] == "ACCEPTED"
    assert outcomes(room)[-1][1] == "READ_SUCCEEDED"


def test_the_reask_happens_once_then_the_turn_counts_as_refused(room):
    goal(room)
    drone(room)
    judge = Judge(lambda state, criteria: spread(criteria, "READ_DRONE", .3),
                  lambda state, criteria: spread(criteria, "READ_DRONE", .35))
    outcome = RoomController(room, judge, acceptance=AcceptancePolicy()).step("s1")
    assert not outcome.accepted and len(judge.calls) == 2
    assert outcomes(room)[-1] == ("READ_DRONE", "LOW_CONFIDENCE")
    assert attempts(room)[-1]["reask"]["feedback"]["refused_choice"] == "READ_DRONE"


def test_a_terminal_clarify_keeps_the_strict_bar(room):
    """CLARIFY ends the session (BLOCKED): at 0.7 it is below the strict 0.8
    bar and refused, even with the room-loop policy; the re-ask then picks
    the read."""
    goal(room)
    drone(room)
    judge = Judge(lambda state, criteria: spread(criteria, "CLARIFY", .7),
                  lambda state, criteria: spread(criteria, "READ_DRONE", .6))
    outcome = RoomController(room, judge, acceptance=AcceptancePolicy()).step("s1")
    attempt = attempts(room)[-1]
    assert attempt["reask"]["first_act_at"] == .8
    assert attempt["reask"]["first_assessment"]["selected_label"] == "CLARIFY"
    assert outcome.accepted and outcomes(room)[-1] == ("READ_DRONE", "READ_SUCCEEDED")


def test_a_provider_error_is_reasked_once_then_refused(room):
    """Task 4b item 4: a provider error gets the one re-ask too."""
    goal(room)
    drone(room)
    judge = Judge(RuntimeError("provider down"), RuntimeError("provider down"))
    outcome = RoomController(room, judge, acceptance=AcceptancePolicy()).step("s1")
    assert not outcome.accepted and len(judge.calls) == 2
    assert judge.calls[1][0]["previous_refusal"]["refusal"] == "PROVIDER_ERROR"
    assert outcomes(room)[-1][1] == "PROVIDER_ERROR"


def test_a_provider_error_then_a_good_reply_is_accepted(room):
    goal(room)
    drone(room)
    judge = Judge(RuntimeError("provider down"), lambda state, criteria: spread(criteria, "READ_DRONE", .7))
    outcome = RoomController(room, judge, acceptance=AcceptancePolicy()).step("s1")
    assert outcome.accepted and len(judge.calls) == 2
    assert attempts(room)[-1]["provider_error"] is None
    assert outcomes(room)[-1] == ("READ_DRONE", "READ_SUCCEEDED")


@pytest.mark.parametrize("bad,code", [
    ("not a mapping", "MALFORMED_ANSWER"),
    ({"value": "TAKEOFF", "probabilities": {"TAKEOFF": .99}}, "ILLEGAL_OR_MALFORMED"),
    ({"value": "READ_DRONE"}, "ILLEGAL_OR_MALFORMED"),
    ({"value": "READ_DRONE", "probabilities": {"READ_DRONE": .2, "CLARIFY": .7}}, "MISSING_OR_NON_MAX_SELECTION"),
])
def test_a_malformed_reply_is_reasked_once_with_shape_feedback(room, bad, code):
    goal(room)
    drone(room)
    class Raw(Judge):
        """Returns non-mapping replies verbatim (the base Judge asserts labels)."""
        def choice(self, state, instruction, *, criteria, name):
            if not self.calls:
                self.calls.append((state, dict(criteria)))
                self.responses.pop(0)
                return bad
            return super().choice(state, instruction, criteria=criteria, name=name)
    judge = Raw(None, lambda state, criteria: spread(criteria, "READ_DRONE", .7))
    outcome = RoomController(room, judge, acceptance=AcceptancePolicy()).step("s1")
    assert outcome.accepted and len(judge.calls) == 2
    feedback = judge.calls[1][0]["previous_refusal"]
    assert feedback["refusal"] == code and "probability" in feedback["note"]
    assert attempts(room)[-1]["reask"]["first_assessment"]["outcome"] == code


def test_a_malformed_reply_is_reasked_only_once(room):
    goal(room)
    drone(room)
    illegal = {"value": "TAKEOFF", "probabilities": {"TAKEOFF": .99}}
    judge = Judge(illegal, illegal)
    outcome = RoomController(room, judge, acceptance=AcceptancePolicy()).step("s1")
    assert not outcome.accepted and len(judge.calls) == 2
    assert outcomes(room)[-1] == ("JEV", "ILLEGAL_OR_MALFORMED")


def test_the_strict_policy_still_never_reasks_a_malformed_reply(room):
    goal(room)
    judge = Judge({"value": "TAKEOFF", "probabilities": {"TAKEOFF": .99}})
    assert not RoomController(room, judge).step("s1").accepted and len(judge.calls) == 1
