"""Jev sees mission progress in a fixed-choice room decision."""

from types import SimpleNamespace

from sentinel.room.coverage import CoverageProjection
from sentinel.room.decision_prompt import build_decision_instruction
from sentinel.room.goals import GoalLedger, GoalObjective
from sentinel.room.models import CandidateKind, GoalKind, GoalStatus, RoomCandidate
from tests.room.test_supervisor import safety


def _objective():
    return GoalObjective("g1:c1", 1, GoalKind.COUNT, ("phone", "computer"),
                         None, None, "ROOM", "REPORTED", None, None, ())


def test_instruction_names_request_objective_coverage_and_localization():
    snapshot = SimpleNamespace(
        request_text="stale session request",
        ledger=GoalLedger("Count the phones and computers on the table", (_objective(),), GoalStatus.PENDING),
    )
    coverage = CoverageProjection(
        (("table-left", "phone", "OBSERVED"),
         ("table-left", "computer", "OBSERVED"),
         ("table-center", "phone", "UNVISITED"),
         ("table-right", "computer", "OCCLUDED")),
        False, ("table-center:phone", "table-right:computer"), ("frame-1",),
    )
    localization = {"state": "COASTING", "last_tag_fix_age_s": 8.2,
                    "position_uncertainty_cm": 13.1, "margin_before_mandatory_recovery_pct": 29}
    observe = RoomCandidate("OBSERVE:g1:c1:left-rear-middle-toward_targets", CandidateKind.OBSERVE,
                            2, "digest", "Observe left", "Observe left")

    instruction = build_decision_instruction(snapshot, coverage, localization, (observe,))

    assert "Count the phones and computers on the table" in instruction
    assert "stale session request" not in instruction
    assert "g1:c1" in instruction and "COUNT" in instruction
    assert "phone, computer" in instruction and "ROOM" in instruction
    assert "Explored cells: 1 of 3" in instruction
    assert "Resolved cell/class pairs: 2 of 4" in instruction
    assert "table-center:phone" in instruction and "table-right:computer" in instruction
    assert "COASTING" in instruction and "8.2" in instruction and "13.1" in instruction
    assert observe.candidate_id in instruction
    assert "Prefer observing unexplored zones" in instruction
    assert "Answer only when no admissible observation adds coverage" in instruction
    assert "Re-anchor when the position is stale" in instruction
    assert "Escalate only when nothing else can make progress" in instruction
    assert "candidate ID" in instruction and "never coordinates" in instruction


def test_instruction_reports_no_declared_coverage_and_no_localization():
    snapshot = SimpleNamespace(request_text="What is the battery?", ledger=None)
    coverage = CoverageProjection((), False, (), ())

    instruction = build_decision_instruction(snapshot, coverage, None, ())

    assert "What is the battery?" in instruction
    assert "Coverage cells: none declared" in instruction
    assert "Localization: unavailable" in instruction
    assert "Admissible OBSERVE candidate IDs: none" in instruction


def test_controller_sends_mission_coverage_in_actual_jev_instruction(safety):
    from tests.room.test_count_return import (SESSION, PerceptionRig, count_objective,
                                              make_controller, room_site, set_ledger)
    from tests.room.test_controller import Judge

    supervisor, store, *_ = safety
    supervisor.site = room_site(coverage=True)
    supervisor.validate_bindings = lambda site: None
    set_ledger(store, SESSION, (count_objective("g1", 1, "ROOM"),),
               request_text="count computers across the room")

    class RecordingJudge(Judge):
        def __init__(self):
            super().__init__("READ_DRONE")
            self.instructions = []

        def choice(self, state, instruction, *, criteria, name):
            self.instructions.append(instruction)
            return super().choice(state, instruction, criteria=criteria, name=name)

    judge = RecordingJudge()
    controller, _executor = make_controller(safety, judge, PerceptionRig(store, SESSION))

    controller.step(SESSION)

    assert len(judge.instructions) == 1
    instruction = judge.instructions[0]
    assert "count computers across the room" in instruction
    assert "cell1:computer" in instruction and "cell2:computer" in instruction
    assert "Explored cells: 0 of 2" in instruction
    assert "Localization:" in instruction
    assert "Admissible OBSERVE candidate IDs:" in instruction
    assert "Answer only when no admissible observation adds coverage" in instruction


def test_large_mission_prompt_bounds_details_and_keeps_aggregate_progress():
    rows = tuple((f"cell-{index:04d}", "phone", "UNVISITED") for index in range(1000))
    uncovered = tuple(f"cell-{index:04d}:phone" for index in range(1000))
    coverage = CoverageProjection(rows, False, uncovered, ())
    objectives = tuple(GoalObjective(f"goal-{index}", index, GoalKind.COUNT, ("phone",),
                                     None, None, "ROOM", "REPORTED", None, None, ())
                       for index in range(20))
    snapshot = SimpleNamespace(request_text="stale", ledger=GoalLedger(
        "Count the phones " + "in this room " * 1000, objectives, GoalStatus.PENDING))
    candidates = tuple(RoomCandidate(f"OBSERVE:goal-0:pose-{index}", CandidateKind.OBSERVE,
                                     1, "digest", "view", "view") for index in range(100))

    instruction = build_decision_instruction(snapshot, coverage, None, candidates)

    assert len(instruction) < 5000
    assert "1000" in instruction and "cell-0000:phone" in instruction
    assert "cell-0999:phone" not in instruction
    assert "988 more" in instruction
    assert "12 more objectives" in instruction
    assert "92 more" in instruction
    assert instruction == build_decision_instruction(snapshot, coverage, None, candidates)


def test_search_is_progress_even_when_count_coverage_is_complete():
    search = GoalObjective("search-1", 1, GoalKind.FIND_OBSERVE, ("phone",),
                           None, None, "ROOM", "OBSERVED", None, None, ())
    snapshot = SimpleNamespace(request_text="find the phone", ledger=GoalLedger(
        "find the phone", (search,), GoalStatus.PENDING))
    coverage = CoverageProjection((("table", "phone", "OBSERVED"),), True, (), ())
    observe = RoomCandidate("OBSERVE:search-1:unvisited-pose", CandidateKind.OBSERVE,
                            1, "digest", "Search pose", "Search pose")

    instruction = build_decision_instruction(snapshot, coverage, None, (observe,))

    assert "unvisited declared search viewpoint" in instruction
    assert "search-1" in instruction and observe.candidate_id in instruction


def test_unresolvable_cell_is_resolved_without_claiming_it_was_explored():
    snapshot = SimpleNamespace(request_text="count phones", ledger=None)
    coverage = CoverageProjection((("table", "phone", "NOT_RESOLVABLE"),), True, (), ())

    instruction = build_decision_instruction(snapshot, coverage, None, ())

    assert "Explored cells: 0 of 1" in instruction
    assert "Resolved cells: 1 of 1" in instruction


def test_jev_sees_why_observations_failed_and_what_positioning_did():
    from sentinel.room.queries import ToolOutcome

    snapshot = SimpleNamespace(request_text="count phones", ledger=None, outcomes=(
        ToolOutcome("OBSERVE:g1:front", CandidateKind.OBSERVE, "d", "OBSERVE_SUCCEEDED"),
        ToolOutcome("OBSERVE:g1:rear", CandidateKind.OBSERVE, "d", "TOOL_FAILURE", "TOOL_FAILURE",
                    detail="look 2 of 3 did not capture: positioning was lost"),
        ToolOutcome("READ_DRONE", CandidateKind.READ_DRONE, "d", "READ_SUCCEEDED"),
    ))
    localization = {"state": "COASTING", "last_tag_fix_age_s": 3.0, "position_uncertainty_cm": 20.0,
                    "margin_before_mandatory_recovery_pct": 40,
                    "recent_positioning_events": ["Tag search yawed to heading 105 deg: position re-anchored"]}

    instruction = build_decision_instruction(snapshot, CoverageProjection((), False, (), ()), localization, ())

    attempts = instruction.split("Recent observation attempts")[1].split("\n")[0]
    assert "OBSERVE:g1:front: succeeded" in attempts
    assert "OBSERVE:g1:rear: failed (TOOL_FAILURE: look 2 of 3 did not capture: positioning was lost)" in attempts
    assert "READ_DRONE" not in attempts
    assert "Tag search yawed to heading 105 deg: position re-anchored" in instruction
    assert "do not simply repeat it" in instruction
