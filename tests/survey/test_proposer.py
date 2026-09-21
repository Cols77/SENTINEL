from sentinel.coordinator.legal_tools import Candidate, LegalSet
from sentinel.judgment import JevJudge, StaticJudge
from sentinel.survey.candidates import ESCALATE_LABEL, PROPOSE_SWEEP, REPORT_LABEL, REVIEW_RESULTS
from sentinel.survey.context import SurveyContext
from sentinel.survey.proposer import JevProposer, LlmProposer, RuleProposer, chosen_label

LEGAL = LegalSet(PROPOSE_SWEEP, (
    Candidate("survey.propose_sweep", {"option_id": "RAIL_MID_HEIGHT", "reason": ""},
              "RAIL_MID_HEIGHT", "four stops across the middle"),
    Candidate("survey.propose_sweep", {"option_id": "RAIL_HIGH_WIDE", "reason": ""},
              "RAIL_HIGH_WIDE", "six stops at the top"),
    Candidate("survey.escalate", {"reason": ""}, ESCALATE_LABEL, "unclear"),
))
CONTEXT = SurveyContext("RUN1", LEGAL, {})
REQUEST = {"run_id": "RUN1", "request_text": "do a rail sweep and count the computers"}

# Rev-4 V3: at REVIEW_RESULTS the legal set carries a report candidate with
# empty arguments and follow-up propose_sweep candidates whose *labels* are
# prefixed ("follow_up:<option_id>") but whose *arguments["option_id"]* are
# the bare option id. A correct LlmProposer validates against the latter and
# must be able to choose survey.submit_report too.
REVIEW_LEGAL = LegalSet(REVIEW_RESULTS, (
    Candidate("survey.submit_report", {}, REPORT_LABEL, "report now"),
    Candidate("survey.propose_sweep",
              {"option_id": "CLOSE_LOOK_CENTRE", "reason": "", "addresses": ["O1"]},
              "follow_up:CLOSE_LOOK_CENTRE", "a close look"),
    Candidate("survey.escalate", {"reason": ""}, ESCALATE_LABEL, "unclear"),
))
REVIEW_CONTEXT = SurveyContext("RUN1", REVIEW_LEGAL, {})


class _Sink:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class _Judge:
    """Permissive double. Accepts the real (state, instruction, **kwargs)
    call shape -- not just **kwargs -- because that shape is exactly what
    rev-4 V1 fixes."""

    def __init__(self, value, confidence, probabilities=None):
        self.value, self.confidence = value, confidence
        self.probabilities = probabilities or {}
        self.calls = []

    def choice(self, state, instruction, **kwargs):
        self.calls.append({"state": state, "instruction": instruction, **kwargs})
        return {"value": self.value, "confidence": self.confidence,
                "probabilities": self.probabilities}


def test_the_rule_proposer_takes_the_first_non_escalate_option():
    assert chosen_label(RuleProposer().decide(REQUEST, CONTEXT)) == "RAIL_MID_HEIGHT"


def test_jev_acting_above_the_threshold_and_margin_picks_its_option():
    judge = _Judge("RAIL_HIGH_WIDE", 0.82,
                   {"RAIL_HIGH_WIDE": 0.82, "RAIL_MID_HEIGHT": 0.10, ESCALATE_LABEL: 0.08})
    result = JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)
    assert chosen_label(result) == "RAIL_HIGH_WIDE"


def test_jev_below_the_confidence_floor_escalates():
    judge = _Judge("RAIL_HIGH_WIDE", 0.40,
                   {"RAIL_HIGH_WIDE": 0.40, "RAIL_MID_HEIGHT": 0.35, ESCALATE_LABEL: 0.25})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_jev_with_too_thin_a_margin_escalates_even_when_confident():
    """A near-tie is exactly the case the operator should see."""
    judge = _Judge("RAIL_HIGH_WIDE", 0.70,
                   {"RAIL_HIGH_WIDE": 0.36, "RAIL_MID_HEIGHT": 0.34, ESCALATE_LABEL: 0.30})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_jev_is_never_offered_escalate_as_a_candidate():
    """Escalation is derived from the gates, not handed to the model as a
    free abstention -- see the Jev handoff, section 2.2."""
    judge = _Judge("RAIL_MID_HEIGHT", 0.9, {"RAIL_MID_HEIGHT": 0.9, "RAIL_HIGH_WIDE": 0.1})
    JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)
    assert ESCALATE_LABEL not in judge.calls[0]["criteria"]


def test_jev_records_a_derived_justification_because_it_returns_no_rationale():
    judge = _Judge("RAIL_HIGH_WIDE", 0.82,
                   {"RAIL_HIGH_WIDE": 0.82, "RAIL_MID_HEIGHT": 0.10})
    sink = _Sink()
    result = JevProposer(judge, sink).decide(REQUEST, CONTEXT)
    reason = result["tool_calls"][0]["arguments"]["reason"]
    assert "six stops at the top" in reason
    assert any(r["record"] == "survey_route" for r in sink.records)


def test_an_unknown_label_from_any_advisor_escalates():
    judge = _Judge("RAIL_INVENTED", 0.99, {"RAIL_INVENTED": 0.99})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_a_judge_that_raises_fails_closed_to_escalate():
    class _Boom:
        def choice(self, state, instruction, **kwargs):
            raise RuntimeError("provider down")

    assert chosen_label(JevProposer(_Boom(), _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_jev_calls_choice_with_the_required_name_argument_and_positional_state_and_instruction():
    """Rev-4 V1 (CRITICAL): judge.choice's real signature
    (judgment.py:178, identically at :305) requires a keyword-only `name`.
    The plan body called it with instruction=/state=/criteria= only, which
    raises TypeError -- swallowed by `except Exception` -- so mode J would
    escalate on every single turn. This proves the fixed call shape."""
    judge = _Judge("RAIL_HIGH_WIDE", 0.82, {"RAIL_HIGH_WIDE": 0.82, "RAIL_MID_HEIGHT": 0.10})
    JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)
    call = judge.calls[0]
    assert call["state"] is not None
    assert isinstance(call["instruction"], str) and call["instruction"]
    assert call["name"] == PROPOSE_SWEEP.lower()
    assert set(call["criteria"]) == {"RAIL_MID_HEIGHT", "RAIL_HIGH_WIDE"}


def test_jev_falls_back_to_the_label_probability_when_confidence_is_none():
    """Rev-4 V8e: JevPolicy in coordinator/policies.py falls back to
    probabilities[label] when confidence is None, rather than escalating
    outright."""
    judge = _Judge("RAIL_HIGH_WIDE", None,
                   {"RAIL_HIGH_WIDE": 0.82, "RAIL_MID_HEIGHT": 0.10})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == "RAIL_HIGH_WIDE"


def test_jev_escalates_when_confidence_is_none_and_the_label_has_no_probability_either():
    judge = _Judge("RAIL_HIGH_WIDE", None, {"RAIL_MID_HEIGHT": 0.10})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_jev_proposer_works_against_the_real_jev_judge_class(monkeypatch):
    """Rev-4 V1 requires exercising a real judge class, not only a
    permissive **kwargs fake. JevJudge accepts an injected `transport`, so
    this makes no network call."""
    monkeypatch.setenv("SENTINEL_TEST_JEV_KEY", "unused")

    def transport(payload):
        assert payload["questions"][PROPOSE_SWEEP.lower()]["criteria"]
        return {"answers": {PROPOSE_SWEEP.lower(): {
            "choice": "RAIL_HIGH_WIDE",
            "probabilities": {"RAIL_HIGH_WIDE": 0.82, "RAIL_MID_HEIGHT": 0.10},
            "confidence": 0.82}}}

    judge = JevJudge(api_key_env="SENTINEL_TEST_JEV_KEY", transport=transport)
    result = JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)
    assert chosen_label(result) == "RAIL_HIGH_WIDE"


def test_the_static_judge_has_no_criteria_parameter_so_pairing_it_with_jev_mode_fails_closed():
    """StaticJudge.choice (judgment.py) takes `options`, not `criteria` --
    JevProposer always passes `criteria`, so pairing them raises TypeError
    at the judge boundary and fails closed to escalate rather than crashing
    past it. Real use of mode J requires a criteria-capable judge (JevJudge
    or LlmJudge)."""
    judge = StaticJudge(choices={PROPOSE_SWEEP.lower(): (
        "RAIL_HIGH_WIDE", {"RAIL_HIGH_WIDE": 0.82, "RAIL_MID_HEIGHT": 0.10})})
    assert chosen_label(JevProposer(judge, _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_escalating_at_the_propose_point_synthesises_a_call_even_with_no_escalate_candidate():
    """Rev-4 V2: _escalate must not crash even if, for some reason, the
    current LegalSet carries no escalate Candidate at all."""
    bare_legal = LegalSet(PROPOSE_SWEEP, ())
    context = SurveyContext("RUN1", bare_legal, {})
    result = RuleProposer().decide(REQUEST, context)
    assert chosen_label(result) == ESCALATE_LABEL
    assert result["tool_calls"][0]["tool_name"] == "survey.escalate"


def test_the_llm_proposer_accepts_a_legal_label_with_a_reason():
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.propose_sweep",
                 "arguments": {"option_id": "RAIL_HIGH_WIDE", "reason": "you asked for wide"}}]}

    result = LlmProposer(_Adapter(), _Sink()).decide(REQUEST, CONTEXT)
    assert chosen_label(result) == "RAIL_HIGH_WIDE"
    assert result["tool_calls"][0]["arguments"]["reason"] == "you asked for wide"


def test_the_llm_proposer_escalates_on_a_blank_reason():
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.propose_sweep",
                 "arguments": {"option_id": "RAIL_HIGH_WIDE", "reason": "  "}}]}

    assert chosen_label(LlmProposer(_Adapter(), _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_the_llm_proposer_escalates_when_the_model_tries_to_finish():
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS",
                    "tool_calls": [{"tool_name": "finish", "arguments": {}}]}

    assert chosen_label(LlmProposer(_Adapter(), _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL


def test_the_llm_proposer_can_choose_a_bare_follow_up_option_id_at_the_review_point():
    """Rev-4 V3 (CRITICAL): at REVIEW_RESULTS the candidate *label* is
    "follow_up:<option_id>" but arguments["option_id"] carries the bare id.
    Validating against labels (as the plan body did) rejects every legal
    follow-up as unknown_option, which then hits V2's StopIteration."""
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.propose_sweep",
                 "arguments": {"option_id": "CLOSE_LOOK_CENTRE", "reason": "closer look"}}]}

    result = LlmProposer(_Adapter(), _Sink()).decide(REQUEST, REVIEW_CONTEXT)
    assert result["tool_calls"][0]["tool_name"] == "survey.propose_sweep"
    assert result["tool_calls"][0]["arguments"]["option_id"] == "CLOSE_LOOK_CENTRE"
    assert result["tool_calls"][0]["arguments"]["addresses"] == ["O1"]
    assert chosen_label(result) != ESCALATE_LABEL


def test_the_llm_proposer_can_choose_to_submit_the_report_at_the_review_point():
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.submit_report", "arguments": {}}]}

    result = LlmProposer(_Adapter(), _Sink()).decide(REQUEST, REVIEW_CONTEXT)
    assert result["tool_calls"][0]["tool_name"] == "survey.submit_report"
    assert chosen_label(result) != ESCALATE_LABEL


def test_the_llm_proposer_rejects_submit_report_at_the_propose_point():
    """survey.submit_report is never a legal tool at PROPOSE_SWEEP."""
    class _Adapter:
        def propose(self, request, context):
            return {"status": "TOOL_CALLS", "tool_calls": [
                {"tool_name": "survey.submit_report", "arguments": {}}]}

    assert chosen_label(LlmProposer(_Adapter(), _Sink()).decide(REQUEST, CONTEXT)) == ESCALATE_LABEL
