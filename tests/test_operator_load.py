from sentinel.operator_load import operator_touches

APPROVE_PLAN = {"event_type": "PLAN_APPROVED"}
CONFIRM_ASSIGN = {"event_type": "PATROLLER_ASSIGNMENT_CONFIRMED"}


def judgment(point, chooser):
    return {"record": "judgment", "decision_point": point, "executor": {"chooser": chooser, "label": "x"}}


def test_clean_run_is_approvals_only_with_two_touches():
    audit = [{"record": "rule", "decision_point": "RANK", "label": "rank"}, judgment("EVIDENCE_GATE", "RULE")]
    assert operator_touches(audit, [APPROVE_PLAN, CONFIRM_ASSIGN, {"event_type": "TASK_CREATED"}]) == {
        "approvals": 2, "choices": 0, "escalations": 0, "total": 2, "approvals_only": True, "broken_by": []}


def test_an_escalation_and_the_operator_choice_that_answers_it_break_the_target():
    audit = [judgment("SELECT_PLAN", "OPERATOR"),
             {"record": "operator_choice", "decision_point": "SELECT_PLAN", "label": "PLAN_ROUTE_ONLY_2_ZONE"}]
    result = operator_touches(audit, [APPROVE_PLAN, CONFIRM_ASSIGN])
    assert (result["approvals"], result["choices"], result["escalations"], result["total"]) == (2, 1, 1, 3)
    assert result["approvals_only"] is False and result["broken_by"] == ["SELECT_PLAN"]


def test_rejections_count_as_operator_touches_and_escalate_choice_is_not_a_choice():
    audit = [judgment("EVIDENCE_GATE", "OPERATOR"),
             {"record": "operator_choice", "decision_point": "EVIDENCE_GATE", "label": "escalate"}]
    result = operator_touches(audit, [{"event_type": "PLAN_REJECTED"}, APPROVE_PLAN,
                                      {"event_type": "OPERATOR_ASSIGNMENT_REJECTED"}])
    assert result["approvals"] == 3 and result["choices"] == 0 and result["escalations"] == 1
    assert result["broken_by"] == ["EVIDENCE_GATE"]
