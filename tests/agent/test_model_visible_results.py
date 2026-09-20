"""What a tool result carries must reach the model.

Found 2026-09-20 by reading, after a paid live run failed: WP0 section 5 added
typed ids to mutating tool results (``plan_id``, ``task_id``, ...). The schema
validated them and ``AgentLoop`` returned them -- and ``prompt._bounded_previous``
then copied only ``kind``/``run_status``/``phase``/``state_version`` into what
the model is shown. The amendment shipped and never reached its reader.

These tests pin the renderer, not the loop: a field the wire schema lets a
result carry is either rendered (bounded) or deliberately dropped here.
"""
from __future__ import annotations

from sentinel.adapter.llm.prompt import _bounded_previous

TASK_ID = "77777777-7777-4777-8777-777777777777"
PLAN_ID = "180711e2-522b-535c-9cc6-3762b1efedf8"


def _previous(result: dict) -> dict:
    return {"tool_name": "task.create", "status": "ACCEPTED", "safe_summary": "ok", "result": result}


def test_a_created_task_id_reaches_the_model():
    rendered = _bounded_previous(_previous({"kind": "task", "task_id": TASK_ID}))
    assert rendered["result"] == {"kind": "task", "task_id": TASK_ID}


def test_a_proposed_plan_id_reaches_the_model():
    rendered = _bounded_previous(_previous({"kind": "plan", "plan_id": PLAN_ID}))
    assert rendered["result"] == {"kind": "plan", "plan_id": PLAN_ID}


def test_an_assignment_result_keeps_both_ids():
    rendered = _bounded_previous(_previous({"kind": "assignment", "task_id": TASK_ID, "patroller_id": "PATROLLER_A"}))
    assert rendered["result"] == {"kind": "assignment", "task_id": TASK_ID, "patroller_id": "PATROLLER_A"}


def test_a_ranking_result_keeps_its_ordered_patrollers_bounded():
    ids = [f"PATROLLER_{n}" for n in range(40)]
    rendered = _bounded_previous(_previous({"kind": "ranking", "patroller_ids": ids}))
    assert rendered["result"]["patroller_ids"] == ids[:16]


def test_a_null_id_is_rendered_as_null_not_dropped():
    """The legacy fixed-port path has no read model and yields a null id. The
    model must be able to tell "no id" from "field missing"."""
    rendered = _bounded_previous(_previous({"kind": "task", "task_id": None}))
    assert rendered["result"] == {"kind": "task", "task_id": None}


def test_event_list_items_reach_the_model_bounded():
    events = [{"sequence_no": n, "event_type": "DETECTION_RECORDED", "summary": "x" * 900, "smuggled": "no"}
              for n in range(50)]
    rendered = _bounded_previous(_previous({"kind": "event_list", "events": events}))
    shown = rendered["result"]["events"]
    assert len(shown) == 32
    assert shown[0] == {"sequence_no": 0, "event_type": "DETECTION_RECORDED", "summary": "x" * 512}


def test_malformed_event_items_are_skipped_not_raised():
    rendered = _bounded_previous(_previous({"kind": "event_list", "events": ["nope", {"event_type": "X"}, None]}))
    assert rendered["result"]["events"] == []


def test_unknown_result_fields_are_still_dropped():
    rendered = _bounded_previous(_previous({"kind": "task", "task_id": TASK_ID, "secret": "s", "payload": {"a": 1}}))
    assert rendered["result"] == {"kind": "task", "task_id": TASK_ID}


def test_a_non_string_id_is_dropped_rather_than_rendered():
    rendered = _bounded_previous(_previous({"kind": "task", "task_id": {"nested": "object"}}))
    assert rendered["result"] == {"kind": "task"}


# --- what the first request of the failing live scenario now carries ----------

import json  # noqa: E402

from sentinel.coordinator.policy import one_call  # noqa: E402
from tests.agent import test_live_gate_real_loop as live_gate  # noqa: E402


def _probe(tmp_path):
    seen = []

    def policy(request):
        seen.append(request)
        return one_call(request, "finish", {"status": "NEEDS_REVIEW", "summary": "probe"})

    outcome = live_gate.run_real_loop_scenario("qualifying_evidence_to_task", policy, tmp_path=tmp_path)
    return seen[0], outcome


def test_evidence_names_each_id_by_the_task_create_argument_it_fills(tmp_path):
    """``task.create`` requires ``perception_result_id``, ``evidence_artifact_id``
    and ``section_id``. The evidence summary used to call them ``result_id`` and
    ``station``, leaving a model to guess the mapping from prose."""
    request, _ = _probe(tmp_path)
    summary = request["evidence_context"][0]["summary"]
    artifact_id = request["evidence_context"][0]["artifact_id"]
    assert "section_id=LEFT_ALLEY_MIDDLE" in summary
    assert f"evidence_artifact_id={artifact_id}" in summary
    assert live_gate._UUID_RE.search(summary.split("perception_result_id=")[1])
    assert len(summary) <= 512


def test_a_live_outcome_is_persisted_with_arguments_and_the_run_database(tmp_path, monkeypatch):
    _, outcome = _probe(tmp_path / "run")
    monkeypatch.setenv(live_gate.BATCH_ENV, "unit test/batch")
    target = live_gate._persist_live_outcome("qualifying_evidence_to_task", outcome, tmp_path / "out")
    assert target.name == "unit_test_batch-qualifying_evidence_to_task.json"
    record = json.loads(target.read_text(encoding="utf-8"))
    assert record["model_results"][0]["tool_calls"][0]["arguments"] == {"status": "NEEDS_REVIEW", "summary": "probe"}
    assert record["requests"][0]["legal_tools"]
    assert target.with_suffix(".sqlite3").is_file()
