"""Task 4b item 6: the offline calibration replay (fake judge, no network)."""
from __future__ import annotations

import io

import pytest

from scripts import room_jev_replay as replay_script
from sentinel.room.models import CapabilityProfile
from sentinel.room.store import RoomStore

GOAL_CRITERIA = {"REPORTED": "report", "RETURN_HOME": "return", "CLARIFICATION_REQUIRED": "c", "UNSUPPORTED": "u"}
ROOM_CANDIDATES = [
    {"candidate_id": "OBSERVE:g1:center", "kind": "OBSERVE", "description": "Observe center."},
    {"candidate_id": "OBSERVE:g1:right", "kind": "OBSERVE", "description": "Observe right."},
    {"candidate_id": "ESCALATE", "kind": "ESCALATE", "description": "Escalate."},
]


@pytest.fixture
def session_file(tmp_path):
    path = tmp_path / "session.sqlite3"
    store = RoomStore.open(path)
    store.create_session("s1", "count the phones", CapabilityProfile(()))
    events = [
        ("GOAL_CHOICE_ATTEMPT", {"name": "terminal_condition", "state": {"request_text": "count the phones"},
                                 "instruction": "Select exactly one label.", "criteria": GOAL_CRITERIA,
                                 "assessment": {"selected_label": "REPORTED", "outcome": "ACCEPTED"}}),
        ("ROOM_CHOICE_ATTEMPT", {"state": {"session_id": "s1"}, "candidates": ROOM_CANDIDATES,
                                 "assessment": {"selected_label": "OBSERVE:g1:center", "outcome": "LOW_CONFIDENCE"}}),
        ("ROOM_TOOL_OUTCOME", {"candidate_id": "OBSERVE:g1:center"}),
    ]
    for index, (kind, payload) in enumerate(events):
        store.append_event(session_id="s1", expected_version=index, event_type=kind, payload=payload,
                           correlation_id="fixture", causation_id=None, idempotency_key=f"fixture:{index}")
    store.close()
    return path


class FakeJudge:
    """Answers each question from a fixed table: label and its probability."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def choice(self, state, instruction, *, criteria, name):
        self.calls.append((name, instruction, dict(criteria), state))
        label, probability = self.table[name]
        rest = [key for key in criteria if key != label]
        share = (1 - probability) / len(rest)
        return {"value": label, "probabilities": {label: probability, **{key: share for key in rest}}}


def test_load_questions_reads_goal_and_room_attempts(session_file):
    goal, room = replay_script.load_questions([session_file])
    assert goal.key == "terminal_condition" and goal.criteria == GOAL_CRITERIA
    assert goal.instruction == "Select exactly one label." and goal.recorded_label == "REPORTED"
    assert room.key == "room:OBSERVE" and room.name == "room_action"
    assert room.instruction == replay_script.ROOM_INSTRUCTION
    assert room.criteria == {c["candidate_id"]: c["description"] for c in ROOM_CANDIDATES}
    assert room.recorded_label == "OBSERVE:g1:center"


def test_replay_reports_agreement_probability_and_threshold_shares(session_file):
    questions = replay_script.load_questions([session_file])
    judge = FakeJudge({"terminal_condition": ("REPORTED", .55), "room_action": ("OBSERVE:g1:right", .65)})
    stats = replay_script.replay(questions, judge, repeats=3)
    assert len(judge.calls) == 6
    goal, room = stats["terminal_condition"], stats["room:OBSERVE"]
    assert goal.asks == 3 and goal.agreed == 3 and room.agreed == 0
    assert min(goal.probabilities) == pytest.approx(.55)
    assert goal.accepted == {.4: 3, .5: 3, .6: 0, .7: 0}
    assert room.accepted == {.4: 3, .5: 3, .6: 3, .7: 0}
    text = replay_script.render(stats)
    assert "terminal_condition  3  100%  0.55  0.55  0%  100%  100%  0%  0%" in text
    assert "room:OBSERVE  3  0%  0.65" in text


def test_an_unusable_reply_is_counted_not_crashed(session_file):
    class Broken:
        def choice(self, *args, **kwargs):
            raise RuntimeError("private provider detail")
    stats = replay_script.replay(replay_script.load_questions([session_file]), Broken(), repeats=2)
    assert stats["terminal_condition"].unusable == 2 and stats["terminal_condition"].probabilities == []
    assert "private provider detail" not in replay_script.render(stats)


def test_it_refuses_without_the_paid_calls_flag(session_file, capsys):
    built = []
    code = replay_script.main([str(session_file)], judge_factory=lambda: built.append(1))
    assert code == 2 and built == []
    assert replay_script.PAID_CALLS_FLAG in capsys.readouterr().err


def test_dry_run_lists_questions_and_builds_no_judge(session_file):
    built, out = [], io.StringIO()
    code = replay_script.main([str(session_file), "--dry-run", "--repeats", "2"],
                              judge_factory=lambda: built.append(1), out=out)
    assert code == 0 and built == []
    text = out.getvalue()
    assert "terminal_condition" in text and "room:OBSERVE" in text
    assert "2 questions x 2 repeats = 4 paid calls would be sent (dry run: none sent)." in text


def test_authorised_run_uses_the_injected_judge_and_prints_the_table(session_file):
    judge, out = FakeJudge({"terminal_condition": ("REPORTED", .9), "room_action": ("OBSERVE:g1:center", .8)}), io.StringIO()
    code = replay_script.main([str(session_file), "--repeats", "1", replay_script.PAID_CALLS_FLAG],
                              judge_factory=lambda: judge, out=out)
    assert code == 0 and len(judge.calls) == 2
    assert out.getvalue().splitlines()[0].startswith("question  asks  agree")
