"""Behavior and complete fixed-choice transcripts for ordered goal compilation."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import sqlite3

import pytest

from sentinel.room.goals import EntityRef, GoalCompiler, GoalLedger, GoalObjective
from sentinel.room.models import CapabilityProfile, GoalKind, GoalStatus, RoomSnapshot
from sentinel.room.store import RoomStore, VersionConflict


SAFE = ("CLARIFICATION_REQUIRED", "UNSUPPORTED")
FAMILIES = tuple(kind.value for kind in GoalKind)
CLASSES = ("CLASS:computer", "CLASS:person", "CLASS:phone", "CLASS:pen", "CLASS_GROUP")


class ScriptedJudge:
    def __init__(self, script):
        self.script = list(script)
        self.expected_calls = len(self.script)
        self.calls = []

    def choice(self, state, instruction, *, criteria, name):
        self.calls.append((name, deepcopy(state), dict(criteria), instruction))
        expected_name, response = self.script.pop(0)
        assert name == expected_name
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict):
            return response
        assert response in criteria, (response, tuple(criteria))
        return {"value": response, "probabilities": {
            label: 0.99 if label == response else 0.001 for label in criteria
        }}


def snapshot(**changes):
    return replace(RoomSnapshot("session-1", 0, 0, 7, 3, 4, 0, 0, 0, 0, 0), **changes)


def setup(tmp_path, request, script, *, limit=4096):
    store = RoomStore.open(tmp_path / "goals.sqlite3")
    store.create_session("session-1", request, CapabilityProfile(()))
    judge = ScriptedJudge(script)
    compiler = GoalCompiler(judge, store, input_limit_bytes=limit, prompt_revision="goals-test.v1")
    return compiler, judge, store


def objective(clause, family, classes=None, *, qualifier="NONE", relation="NONE",
              scope="ROOM", axes=(), binding="NEW_ENTITY", terminal="REPORTED"):
    steps = [("next_objective", f"NEXT_OBJECTIVE:{clause}"), ("goal_family", family)]
    if classes is not None:
        steps.append(("target_classes", f"CLASS:{classes}"))
        steps.extend((("qualifier", qualifier), ("relation", relation)))
    steps.append(("spatial_scope", scope))
    steps.extend((f"scope_{axis}", value) for axis, value in zip(("x", "y", "z"), axes))
    steps.extend((("entity_reference", binding), ("terminal_condition", terminal)))
    return steps


FINISH = [("next_objective", "DONE"), ("consistency", "CONSISTENT")]


def assert_audit(store, judge, ledger, *, accepted=True):
    events = store.events("session-1")
    attempts = [event for event in events if event.event_type == "GOAL_CHOICE_ATTEMPT"]
    assert len(judge.calls) == judge.expected_calls
    assert len(attempts) == len(judge.calls)
    assert [event.payload["name"] for event in attempts] == [call[0] for call in judge.calls]
    for event, call in zip(attempts, judge.calls):
        assert event.payload["criteria"] == call[2]
        assert event.payload["state"] == call[1]
        assert event.payload["prompt_revision"] == "goals-test.v1"
        assert event.payload["schema_version"] == "room.goal-compile.v1"
        assert event.payload["input_limit_bytes"] == 4096
        assert event.payload["snapshot"] == call[1]["snapshot"]
        assert "outcome" in event.payload["assessment"]
        assert event.payload["latency_ms"] >= 0
    assert len({event.correlation_id for event in events}) == 1
    assert [event.state_version for event in events] == list(range(1, len(events) + 1))
    assert events[-1].event_type == ("GOAL_LEDGER_ACCEPTED" if accepted else "GOAL_COMPILE_REJECTED")
    assert events[-1].payload["status"] == ledger.status
    if accepted:
        assert events[-1].payload["goal_revision"] == 1
        assert events[-1].payload["ledger"]["request_text"] == ledger.request_text
        assert events[-1].payload["snapshot"]["pose_revision"] == 7
    else:
        assert not any(event.event_type == "GOAL_LEDGER_ACCEPTED" for event in events)
    assert judge.script == []


@pytest.mark.parametrize("text,limit", [("", 32), (" \n\t", 32), ("éé", 3)])
def test_empty_and_utf8_oversize_input_are_rejected_before_judge(tmp_path, text, limit):
    request = text
    compiler, judge, store = setup(tmp_path, request, [], limit=limit)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert ledger.objectives == ()
    assert judge.calls == []
    event, = store.events("session-1")
    assert event.event_type == "GOAL_COMPILE_REJECTED"
    assert event.payload["input_limit_bytes"] == limit
    assert event.payload["input_bytes"] == len(request.encode("utf-8"))
    assert event.payload["prompt_revision"] == "goals-test.v1"


def test_room_count_and_return_preserves_terminal_condition_and_immutable_contracts(tmp_path):
    request = "Count the computers in the room and return home."
    script = objective("c1", "COUNT", "computer", terminal="RETURN_HOME") + FINISH
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.PENDING
    goal, = ledger.objectives
    assert isinstance(goal, GoalObjective)
    assert goal.kind == GoalKind.COUNT
    assert goal.target_classes == ("computer",)
    assert goal.spatial_scope == "ROOM"
    assert goal.terminal_condition == "RETURN_HOME"
    assert goal.ordinal == 1
    for value, attribute in ((ledger, "status"), (goal, "ordinal"), (goal.subject_ref, "ref_id")):
        with pytest.raises(FrozenInstanceError):
            setattr(value, attribute, "changed")
    assert tuple(judge.calls[0][2]) == ("NEXT_OBJECTIVE:c1", "DONE", *SAFE)
    assert tuple(judge.calls[1][2]) == (*FAMILIES, *SAFE)
    assert tuple(judge.calls[2][2]) == (*CLASSES, *SAFE)
    assert_audit(store, judge, ledger)


def test_four_corners_keep_request_order_and_all_axis_regions_accessible(tmp_path):
    request = ("Count computers at the front left bottom; count people at the front right top; "
               "count phones at the rear right bottom; count pens at the rear left top.")
    classes = ("computer", "person", "phone", "pen")
    regions = (("LEFT", "FRONT", "BOTTOM"), ("RIGHT", "FRONT", "TOP"),
               ("RIGHT", "REAR", "BOTTOM"), ("LEFT", "REAR", "TOP"))
    script = []
    for index, (target, axes) in enumerate(zip(classes, regions), 1):
        script += objective(f"c{index}", "COUNT", target, scope="REGION", axes=axes)
    compiler, judge, store = setup(tmp_path, request, script + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    assert [goal.target_classes for goal in ledger.objectives] == [(name,) for name in classes]
    assert [goal.ordinal for goal in ledger.objectives] == [1, 2, 3, 4]
    assert [goal.spatial_scope for goal in ledger.objectives] == ["/".join(axes) for axes in regions]
    next_calls = [call for call in judge.calls if call[0] == "next_objective"]
    assert [tuple(call[2]) for call in next_calls] == [
        (*(f"NEXT_OBJECTIVE:c{i}" for i in range(start, 5)), "DONE", *SAFE)
        for start in range(1, 6)
    ]
    for name, values in (("scope_x", ("LEFT", "CENTER", "RIGHT")),
                         ("scope_y", ("REAR", "MIDDLE", "FRONT")),
                         ("scope_z", ("BOTTOM", "MIDDLE", "TOP"))):
        assert all(tuple(call[2]) == (*values, *SAFE) for call in judge.calls if call[0] == name)
    assert_audit(store, judge, ledger)


def test_drone_state_read_binds_current_snapshot_without_object_classification(tmp_path):
    request = "What is your battery and current position?"
    script = objective("c1", "ANSWER_STATE", scope="CURRENT_VIEW", binding="CURRENT_DRONE") + FINISH
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    goal, = ledger.objectives
    assert goal.kind == GoalKind.ANSWER_STATE
    assert goal.target_classes == ()
    assert goal.target_ref.ref_id == "drone:session-1:state:0:pose:7"
    assert goal.target_ref.entity_role == "CURRENT_DRONE"
    assert_audit(store, judge, ledger)


def test_closer_inspection_preserves_validation_goal(tmp_path):
    request = "Look closer at the phone to validate the detection."
    script = objective("c1", "FIND_OBSERVE", "phone", scope="CURRENT_VIEW", terminal="VALIDATED") + FINISH
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    goal, = ledger.objectives
    assert goal.kind == GoalKind.FIND_OBSERVE
    assert goal.terminal_condition == "VALIDATED"
    assert_audit(store, judge, ledger)


def test_man_holding_phone_then_follow_uses_verified_designated_person_exactly(tmp_path):
    request = "Find a man that holds a smartphone and follow him"
    script = objective("c1", "FIND_OBSERVE", "person", qualifier="MAN", relation="HOLDS_PHONE",
                       binding="DESIGNATED_PERSON", terminal="RELATION_VERIFIED")
    script += objective("c2", "FOLLOW", "person", binding="REF:g1:c1:subject", terminal="FOLLOW_BOUNDED_RETURN")
    compiler, judge, store = setup(tmp_path, request, script + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    verified, follow = ledger.objectives
    assert verified.qualifier == "man"
    assert verified.relation == "HOLDS_PHONE"
    assert verified.target_classes == ("person",)
    assert verified.target_ref.entity_role == "RELATED_PHONE"
    assert verified.subject_ref.entity_role == "VERIFIED_DESIGNATED_PERSON"
    assert follow.kind == GoalKind.FOLLOW
    assert follow.target_ref == verified.subject_ref
    assert follow.qualifier == "man"
    assert follow.depends_on_objective_ids == (verified.objective_id,)
    assert judge.calls[0][1]["clauses"] == [
        {"clause_id": "c1", "ordinal": 1, "text": "Find a man that holds a smartphone"},
        {"clause_id": "c2", "ordinal": 2, "text": "follow him"},
    ]
    expected = [
        ("NEXT_OBJECTIVE:c1", "NEXT_OBJECTIVE:c2", "DONE", *SAFE), (*FAMILIES, *SAFE),
        (*CLASSES, *SAFE), ("NONE", "MAN", "DESIGNATED", *SAFE),
        ("NONE", "HOLDS_PHONE", "NEXT_TO_DRONE", *SAFE), ("ROOM", "CURRENT_VIEW", "REGION", *SAFE),
        ("DESIGNATED_PERSON", *SAFE), ("OBSERVED", "VALIDATED", "RELATION_VERIFIED", "RETURN_HOME", *SAFE),
        ("NEXT_OBJECTIVE:c2", "DONE", *SAFE), (*FAMILIES, *SAFE), (*CLASSES, *SAFE),
        ("NONE", "MAN", "DESIGNATED", *SAFE), ("NONE", "HOLDS_PHONE", "NEXT_TO_DRONE", *SAFE),
        ("ROOM", "CURRENT_VIEW", "REGION", *SAFE), ("REF:g1:c1:subject", *SAFE),
        ("FOLLOW_BOUNDED_RETURN", *SAFE), ("DONE", *SAFE), ("CONSISTENT", *SAFE),
    ]
    assert [tuple(call[2]) for call in judge.calls] == expected
    assert_audit(store, judge, ledger)


def test_phone_next_to_you_then_land_binds_same_hypothesis_and_snapshot(tmp_path):
    request = "Find the phone next to you and land nearby it"
    script = objective("c1", "FIND_OBSERVE", "phone", relation="NEXT_TO_DRONE",
                       binding="CURRENT_DRONE", terminal="OBSERVED")
    script += objective("c2", "LAND_NEAR", "phone", binding="REF:g1:c1:subject",
                        terminal="LANDED_AT_CERTIFIED_ZONE")
    compiler, judge, store = setup(tmp_path, request, script + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    observe, land = ledger.objectives
    assert observe.relation == "NEXT_TO_DRONE"
    assert observe.target_ref == EntityRef("drone:session-1:state:0:pose:7", None, "CURRENT_DRONE", None)
    assert observe.subject_ref.relative_to == observe.target_ref.ref_id
    assert observe.subject_ref.entity_role == "NEAREST_PHONE"
    assert land.target_ref == observe.subject_ref
    assert land.depends_on_objective_ids == (observe.objective_id,)
    assert "next to you" in judge.calls[0][1]["clauses"][0]["text"]
    expected = [
        ("NEXT_OBJECTIVE:c1", "NEXT_OBJECTIVE:c2", "DONE", *SAFE), (*FAMILIES, *SAFE),
        (*CLASSES, *SAFE), ("NONE", "MAN", "DESIGNATED", *SAFE),
        ("NONE", "HOLDS_PHONE", "NEXT_TO_DRONE", *SAFE), ("ROOM", "CURRENT_VIEW", "REGION", *SAFE),
        ("CURRENT_DRONE", *SAFE), ("OBSERVED", "VALIDATED", "RELATION_VERIFIED", "RETURN_HOME", *SAFE),
        ("NEXT_OBJECTIVE:c2", "DONE", *SAFE), (*FAMILIES, *SAFE), (*CLASSES, *SAFE),
        ("NONE", "MAN", "DESIGNATED", *SAFE), ("NONE", "HOLDS_PHONE", "NEXT_TO_DRONE", *SAFE),
        ("ROOM", "CURRENT_VIEW", "REGION", *SAFE), ("REF:g1:c1:subject", *SAFE),
        ("LANDED_AT_CERTIFIED_ZONE", *SAFE), ("DONE", *SAFE), ("CONSISTENT", *SAFE),
    ]
    assert [tuple(call[2]) for call in judge.calls] == expected
    assert_audit(store, judge, ledger)


def test_direct_land_request_creates_observation_dependency_before_landing(tmp_path):
    request = "Land near the phone"
    script = objective("c1", "LAND_NEAR", "phone", binding="NEW_TARGET", terminal="LANDED_AT_CERTIFIED_ZONE")
    compiler, judge, store = setup(tmp_path, request, script + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    observe, land = ledger.objectives
    assert observe.kind == GoalKind.FIND_OBSERVE
    assert observe.terminal_condition == "OBSERVED"
    assert land.target_ref == observe.subject_ref
    assert land.depends_on_objective_ids == (observe.objective_id,)
    assert [goal.ordinal for goal in ledger.objectives] == [1, 2]
    assert_audit(store, judge, ledger)


@pytest.mark.parametrize("alias,canonical", [("human", "person"), ("humans", "person"),
                                           ("people", "person"), ("smartphone", "phone"),
                                           ("cell phone", "phone")])
def test_supported_aliases_are_normalized_in_judge_context(tmp_path, alias, canonical):
    request = f"Count {alias}"
    compiler, judge, store = setup(tmp_path, request, objective("c1", "COUNT", canonical) + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.objectives[0].target_classes == (canonical,)
    assert judge.calls[1][1]["normalized_clause"] == f"Count {canonical}"
    assert_audit(store, judge, ledger)


def test_devices_require_explicit_jev_selected_supported_subset(tmp_path):
    request = "Count the devices in the room"
    script = [("next_objective", "NEXT_OBJECTIVE:c1"), ("goal_family", "COUNT"),
              ("target_classes", "CLASS_GROUP"), ("class_group", "INCLUDE:computer"),
              ("class_group", "INCLUDE:phone"), ("class_group", "GROUP_DONE"),
              ("qualifier", "NONE"), ("relation", "NONE"), ("spatial_scope", "ROOM"),
              ("entity_reference", "NEW_ENTITY"), ("terminal_condition", "REPORTED")] + FINISH
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.objectives[0].target_classes == ("computer", "phone")
    group_calls = [call for call in judge.calls if call[0] == "class_group"]
    assert tuple(group_calls[0][2]) == ("INCLUDE:computer", "INCLUDE:person", "INCLUDE:phone", "INCLUDE:pen", "GROUP_DONE", *SAFE)
    assert "INCLUDE:computer" not in group_calls[1][2]
    assert "INCLUDE:phone" not in group_calls[2][2]
    assert_audit(store, judge, ledger)


@pytest.mark.parametrize("text,status", [("Count devices", "CLARIFICATION_REQUIRED"),
                                            ("Count giraffes", "UNSUPPORTED")])
def test_ambiguous_or_unsupported_class_cannot_default_to_physical_goal(tmp_path, text, status):
    request = text
    script = [("next_objective", "NEXT_OBJECTIVE:c1"), ("goal_family", "COUNT"), ("target_classes", status)]
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == status
    assert ledger.objectives == ()
    assert ledger.physical_objectives == ()
    assert_audit(store, judge, ledger, accepted=False)


def test_ambiguous_pronoun_rolls_back_prior_goals_before_reference_choice(tmp_path):
    request = "Find a person on the left; find a person on the right; follow him"
    script = objective("c1", "FIND_OBSERVE", "person", terminal="OBSERVED")
    script += objective("c2", "FIND_OBSERVE", "person", terminal="OBSERVED")
    script += objective("c3", "FOLLOW", "person", terminal="FOLLOW_BOUNDED_RETURN")[:-2]
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert ledger.objectives == ()
    assert ledger.physical_objectives == ()
    assert len([call for call in judge.calls if call[0] == "entity_reference"]) == 2
    assert_audit(store, judge, ledger, accepted=False)


def test_final_inconsistency_discards_tentative_ledger(tmp_path):
    request = "Count phones only on the left and only on the right"
    script = objective("c1", "COUNT", "phone") + [("next_objective", "DONE"), ("consistency", "CLARIFICATION_REQUIRED")]
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert ledger.objectives == ()
    assert judge.calls[-1][1]["request_text"] == request
    assert len(judge.calls[-1][1]["tentative_objectives"]) == 1
    assert_audit(store, judge, ledger, accepted=False)


@pytest.mark.parametrize("response", [
    RuntimeError("provider unavailable"),
    {"value": "NEXT_OBJECTIVE:c1", "confidence": 0.1, "probabilities": {"NEXT_OBJECTIVE:c1": 0.99}},
    {"value": "NEXT_OBJECTIVE:c1", "probabilities": {"NEXT_OBJECTIVE:c1": 0.51, "DONE": 0.49}},
    {"value": "arbitrary command", "probabilities": {"arbitrary command": 0.99}},
])
def test_provider_failure_and_gate_rejection_are_audited_without_defaults(tmp_path, response):
    request = "Follow him"
    compiler, judge, store = setup(tmp_path, request, [("next_objective", response)])
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert ledger.physical_objectives == ()
    assert_audit(store, judge, ledger, accepted=False)


@pytest.mark.parametrize("selection", ["DONE", "NEXT_OBJECTIVE:c2"])
def test_early_done_or_out_of_order_selection_cannot_drop_or_reorder_request(tmp_path, selection):
    request = "Count phones; count people"
    compiler, judge, store = setup(tmp_path, request, [("next_objective", selection)])
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert ledger.objectives == ()
    assert_audit(store, judge, ledger, accepted=False)


def test_accepted_revision_and_all_attempts_roll_back_on_persistence_failure(tmp_path):
    request = "Count phones"
    compiler, judge, store = setup(tmp_path, request, objective("c1", "COUNT", "phone") + FINISH)
    store.connection.execute(
        "CREATE TRIGGER fail_goal_revision BEFORE INSERT ON room_events "
        "WHEN NEW.event_type = 'GOAL_LEDGER_ACCEPTED' "
        "BEGIN SELECT RAISE(ABORT, 'goal revision refused'); END",
    )
    with pytest.raises(sqlite3.IntegrityError, match="goal revision refused"):
        compiler.compile("session-1", request, snapshot())
    assert judge.script == []
    assert store.events("session-1") == ()
    assert store.session_version("session-1") == 0


def test_snapshot_identity_and_version_must_match_session_before_judge(tmp_path):
    compiler, judge, store = setup(tmp_path, "Count phones", [])
    with pytest.raises(ValueError, match="session"):
        compiler.compile("session-1", "Count phones", snapshot(session_id="other"))
    with pytest.raises(VersionConflict):
        compiler.compile("session-1", "Count phones", snapshot(state_version=3))
    assert judge.calls == []
    assert store.events("session-1") == ()


def test_pronoun_ambiguity_cannot_be_hidden_by_judge_class_selection(tmp_path):
    request = "Find the phone; find the pen; land near it"
    script = objective("c1", "FIND_OBSERVE", "phone", terminal="OBSERVED")
    script += objective("c2", "FIND_OBSERVE", "pen", terminal="OBSERVED")
    script += objective("c3", "LAND_NEAR", "phone", terminal="LANDED_AT_CERTIFIED_ZONE")[:-2]
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert len(judge.calls) == len(script)
    assert ledger.physical_objectives == ()
    assert store.events("session-1")[-1].payload["reason"] == "AMBIGUOUS_ENTITY_REFERENCE"
    assert_audit(store, judge, ledger, accepted=False)


def test_explicit_man_qualifier_cannot_be_dropped_even_by_high_confidence_choice(tmp_path):
    request = "Find a man that holds a smartphone"
    script = objective("c1", "FIND_OBSERVE", "person", terminal="OBSERVED")[:4]
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert len(judge.calls) == len(script)
    assert store.events("session-1")[-1].payload["reason"] == "EXPLICIT_QUALIFIER_DROPPED"
    assert_audit(store, judge, ledger, accepted=False)


def test_next_to_you_attachment_cannot_lose_snapshot_relation(tmp_path):
    request = "Find the phone next to you"
    script = objective("c1", "FIND_OBSERVE", "phone", terminal="OBSERVED")[:5]
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert len(judge.calls) == len(script)
    assert store.events("session-1")[-1].payload["reason"] == "RELATIVE_POSE_ATTACHMENT_DROPPED"
    assert_audit(store, judge, ledger, accepted=False)


def test_return_sentence_attaches_to_preceding_goal_without_inventing_goal_kind(tmp_path):
    request = "Count the phones. Return home."
    script = objective("c1", "COUNT", "phone", terminal="RETURN_HOME") + FINISH
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    goal, = ledger.objectives
    assert goal.terminal_condition == "RETURN_HOME"
    assert tuple(judge.calls[0][2]) == ("NEXT_OBJECTIVE:c1", "DONE", *SAFE)
    assert "Return home" in judge.calls[0][1]["clauses"][0]["text"]
    assert_audit(store, judge, ledger)


def test_following_one_target_cannot_bind_a_multiple_class_group(tmp_path):
    request = "Follow the devices"
    script = [("next_objective", "NEXT_OBJECTIVE:c1"), ("goal_family", "FOLLOW"),
              ("target_classes", "CLASS_GROUP"), ("class_group", "INCLUDE:computer"),
              ("class_group", "INCLUDE:phone"), ("class_group", "GROUP_DONE")]
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert len(judge.calls) == len(script)
    assert ledger.physical_objectives == ()
    assert store.events("session-1")[-1].payload["reason"] == "PHYSICAL_TARGET_MUST_BE_SINGULAR"
    assert_audit(store, judge, ledger, accepted=False)


def test_unresolved_pronoun_in_observation_does_not_create_a_new_entity(tmp_path):
    request = "Find it"
    script = objective("c1", "FIND_OBSERVE", "phone", terminal="OBSERVED")[:-2]
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    assert ledger.status == GoalStatus.CLARIFICATION_REQUIRED
    assert len(judge.calls) == len(script)
    assert store.events("session-1")[-1].payload["reason"] == "AMBIGUOUS_ENTITY_REFERENCE"
    assert_audit(store, judge, ledger, accepted=False)


def test_closer_pronoun_binds_existing_phone_observation_dependency(tmp_path):
    request = "Find the phone and look closer at it"
    script = objective("c1", "FIND_OBSERVE", "phone", terminal="OBSERVED")
    script += objective("c2", "FIND_OBSERVE", "phone", binding="REF:g1:c1:subject", terminal="VALIDATED")
    compiler, judge, store = setup(tmp_path, request, script + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    find, closer = ledger.objectives
    assert closer.target_ref == find.subject_ref
    assert closer.depends_on_objective_ids == (find.objective_id,)
    assert closer.terminal_condition == "VALIDATED"
    assert_audit(store, judge, ledger)


def test_landing_near_related_phone_does_not_inherit_person_qualifier(tmp_path):
    request = "Find a man that holds a phone and land near it"
    script = objective("c1", "FIND_OBSERVE", "person", qualifier="MAN", relation="HOLDS_PHONE",
                       binding="DESIGNATED_PERSON", terminal="RELATION_VERIFIED")
    script += objective("c2", "LAND_NEAR", "phone", binding="REF:g1:c1:phone",
                        terminal="LANDED_AT_CERTIFIED_ZONE")
    compiler, judge, store = setup(tmp_path, request, script + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    relation, land = ledger.objectives
    assert land.target_ref == relation.target_ref
    assert land.qualifier is None
    assert land.depends_on_objective_ids == (relation.objective_id,)
    assert_audit(store, judge, ledger)


def test_hierarchical_choices_receive_accepted_parameters_for_the_current_clause(tmp_path):
    request = "Find a man that holds a smartphone"
    script = objective("c1", "FIND_OBSERVE", "person", qualifier="MAN", relation="HOLDS_PHONE",
                       binding="DESIGNATED_PERSON", terminal="RELATION_VERIFIED") + FINISH
    compiler, judge, store = setup(tmp_path, request, script)
    ledger = compiler.compile("session-1", request, snapshot())
    relation_state = next(call[1] for call in judge.calls if call[0] == "relation")
    assert relation_state["accepted_choices"][-3:] == [
        {"clause_id": "c1", "name": "goal_family", "label": "FIND_OBSERVE"},
        {"clause_id": "c1", "name": "target_classes", "label": "CLASS:person"},
        {"clause_id": "c1", "name": "qualifier", "label": "MAN"},
    ]
    assert_audit(store, judge, ledger)


def test_pronoun_holding_relation_preserves_actor_and_creates_verified_follow_result(tmp_path):
    request = "Find a man; verify him holding a phone; follow him"
    script = objective("c1", "FIND_OBSERVE", "person", qualifier="MAN", terminal="OBSERVED")
    script += objective("c2", "FIND_OBSERVE", "person", relation="HOLDS_PHONE",
                        binding="REF:g1:c1:subject", terminal="RELATION_VERIFIED")
    script += objective("c3", "FOLLOW", "person", binding="REF:g1:c2:subject",
                        terminal="FOLLOW_BOUNDED_RETURN")
    compiler, judge, store = setup(tmp_path, request, script + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    found, verified, follow = ledger.objectives
    assert verified.qualifier == "man"
    assert verified.relation == "HOLDS_PHONE"
    assert verified.subject_ref.entity_role == "VERIFIED_DESIGNATED_PERSON"
    assert verified.subject_ref.source_objective_id == verified.objective_id
    assert verified.subject_ref.relative_to == found.subject_ref.ref_id
    assert verified.target_ref == EntityRef("g1:c2:phone", "g1:c2", "RELATED_PHONE", None)
    assert verified.target_ref != verified.subject_ref
    assert verified.depends_on_objective_ids == (found.objective_id,)
    assert follow.target_ref == verified.subject_ref
    assert follow.depends_on_objective_ids == (verified.objective_id,)
    assert follow.qualifier == "man"
    common = [(*FAMILIES, *SAFE), (*CLASSES, *SAFE), ("NONE", "MAN", "DESIGNATED", *SAFE),
              ("NONE", "HOLDS_PHONE", "NEXT_TO_DRONE", *SAFE), ("ROOM", "CURRENT_VIEW", "REGION", *SAFE)]
    observe_terminals = ("OBSERVED", "VALIDATED", "RELATION_VERIFIED", "RETURN_HOME", *SAFE)
    assert [tuple(call[2]) for call in judge.calls] == [
        ("NEXT_OBJECTIVE:c1", "NEXT_OBJECTIVE:c2", "NEXT_OBJECTIVE:c3", "DONE", *SAFE),
        *common, ("NEW_ENTITY", *SAFE), observe_terminals,
        ("NEXT_OBJECTIVE:c2", "NEXT_OBJECTIVE:c3", "DONE", *SAFE),
        *common, ("REF:g1:c1:subject", *SAFE), observe_terminals,
        ("NEXT_OBJECTIVE:c3", "DONE", *SAFE),
        *common, ("REF:g1:c2:subject", *SAFE), ("FOLLOW_BOUNDED_RETURN", *SAFE),
        ("DONE", *SAFE), ("CONSISTENT", *SAFE),
    ]
    assert_audit(store, judge, ledger)


def test_direct_landing_separates_phone_target_from_current_drone_attachment(tmp_path):
    request = "Land near the phone next to you"
    script = objective("c1", "LAND_NEAR", "phone", relation="NEXT_TO_DRONE",
                       binding="NEW_TARGET", terminal="LANDED_AT_CERTIFIED_ZONE")
    script.insert(-2, ("relative_reference", "CURRENT_DRONE"))
    compiler, judge, store = setup(tmp_path, request, script + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    observe, land = ledger.objectives
    assert observe.target_ref == EntityRef("drone:session-1:state:0:pose:7", None, "CURRENT_DRONE", None)
    assert observe.subject_ref.entity_role == "NEAREST_PHONE"
    assert observe.subject_ref.relative_to == observe.target_ref.ref_id
    assert land.target_ref == observe.subject_ref
    assert land.depends_on_objective_ids == (observe.objective_id,)
    assert [tuple(call[2]) for call in judge.calls if call[0] in ("relative_reference", "entity_reference")] == [
        ("CURRENT_DRONE", *SAFE), ("NEW_TARGET", *SAFE),
    ]
    assert_audit(store, judge, ledger)


def test_landing_it_next_to_you_preserves_phone_identity_and_pose_dependency(tmp_path):
    request = "Find the phone; land near it next to you"
    first = objective("c1", "FIND_OBSERVE", "phone", terminal="OBSERVED")
    landing = objective("c2", "LAND_NEAR", "phone", relation="NEXT_TO_DRONE",
                        binding="REF:g1:c1:subject", terminal="LANDED_AT_CERTIFIED_ZONE")
    landing.insert(-2, ("relative_reference", "CURRENT_DRONE"))
    compiler, judge, store = setup(tmp_path, request, first + landing + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    found, relative, land = ledger.objectives
    assert relative.kind == GoalKind.FIND_OBSERVE
    assert relative.relation == "NEXT_TO_DRONE"
    assert relative.subject_ref == found.subject_ref
    assert relative.target_ref == EntityRef("drone:session-1:state:0:pose:7", None, "CURRENT_DRONE", None)
    assert relative.depends_on_objective_ids == (found.objective_id,)
    assert land.target_ref == found.subject_ref
    assert land.depends_on_objective_ids == (found.objective_id, relative.objective_id)
    assert [tuple(call[2]) for call in judge.calls if call[0] in ("relative_reference", "entity_reference")] == [
        ("NEW_ENTITY", *SAFE), ("CURRENT_DRONE", *SAFE), ("REF:g1:c1:subject", *SAFE),
    ]
    assert_audit(store, judge, ledger)


def test_successive_compilations_use_distinct_persisted_goal_revisions(tmp_path):
    first_script = objective("c1", "COUNT", "phone") + FINISH
    second_script = objective("c1", "COUNT", "pen") + FINISH
    compiler, judge, store = setup(tmp_path, "Count phones", first_script + second_script)
    first = compiler.compile("session-1", "Count phones", snapshot())
    second = compiler.compile("session-1", "Count pens", snapshot(
        state_version=store.session_version("session-1"), goal_revision=1,
    ))
    assert first.objectives[0].objective_id == "g1:c1"
    assert second.objectives[0].objective_id == "g2:c1"
    assert first.objectives[0].subject_ref.ref_id != second.objectives[0].subject_ref.ref_id
    accepted = [event for event in store.events("session-1") if event.event_type == "GOAL_LEDGER_ACCEPTED"]
    assert [event.payload["goal_revision"] for event in accepted] == [1, 2]
    assert judge.script == []


@pytest.mark.parametrize("stale_revision", [0, 2])
def test_current_state_version_cannot_hide_stale_or_invented_goal_revision(tmp_path, stale_revision):
    script = objective("c1", "COUNT", "phone") + FINISH
    compiler, judge, store = setup(tmp_path, "Count phones", script)
    compiler.compile("session-1", "Count phones", snapshot())
    original_events = store.events("session-1")
    current_version = store.session_version("session-1")
    store.close()
    reopened = RoomStore.open(tmp_path / "goals.sqlite3")
    compiler = GoalCompiler(judge, reopened, input_limit_bytes=4096, prompt_revision="goals-test.v1")
    with pytest.raises(VersionConflict, match="goal revision"):
        compiler.compile("session-1", "Count pens", snapshot(
            state_version=current_version, goal_revision=stale_revision,
        ))
    assert len(judge.calls) == len(script)
    assert reopened.events("session-1") == original_events
    assert reopened.session_version("session-1") == current_version


def test_observation_it_next_to_you_keeps_phone_subject_and_drone_relation_object(tmp_path):
    request = "Find the phone; verify it next to you"
    first = objective("c1", "FIND_OBSERVE", "phone", terminal="OBSERVED")
    verification = objective("c2", "FIND_OBSERVE", "phone", relation="NEXT_TO_DRONE",
                             binding="REF:g1:c1:subject", terminal="OBSERVED")
    verification.insert(-2, ("relative_reference", "CURRENT_DRONE"))
    compiler, judge, store = setup(tmp_path, request, first + verification + FINISH)
    ledger = compiler.compile("session-1", request, snapshot())
    found, relative = ledger.objectives
    assert relative.subject_ref == found.subject_ref
    assert relative.target_ref == EntityRef("drone:session-1:state:0:pose:7", None, "CURRENT_DRONE", None)
    assert relative.depends_on_objective_ids == (found.objective_id,)
    assert_audit(store, judge, ledger)
