import json

import pytest

from sentinel.judgment import BoundedJudge, StaticJudge

from sentinel.evaluation import QUESTION_SET


def test_static_judge_boolean_matches_configured_rule():
    judge = StaticJudge(
        booleans={"hazard_relevant": (True, 0.92)},
        choices={"priority": ("high", {"ignore": 0.05, "low": 0.1, "medium": 0.25, "high": 0.6})},
    )
    r = judge.boolean({"detection": "rock"}, "Is this a hazard?", name="hazard_relevant")
    assert r == {"value": True, "probability": 0.92}
    c = judge.choice({"mission": {}}, "Pick priority.", options=["ignore", "low", "medium", "high"], name="priority")
    assert c["value"] == "high"
    assert set(c["probabilities"]) == {"ignore", "low", "medium", "high"}


def test_static_judge_unknown_name_fails_closed():
    judge = StaticJudge(booleans={}, choices={})
    with pytest.raises(KeyError):
        judge.boolean({}, "q?", name="missing")


def test_jev_judge_unavailable_is_explicit(monkeypatch):
    # JevJudge must never be the silent default; constructing one without a key env is an error.
    from sentinel.judgment import JevJudge
    with pytest.raises(ValueError):
        JevJudge(api_key_env="")  # empty -> fail closed at construction


def test_jev_judge_boolean_via_injected_transport(monkeypatch):
    from sentinel.judgment import JevJudge

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    seen = {}

    def transport(payload):
        seen["payload"] = payload
        return {"answers": {"hazard_relevant": {"type": "noul", "noul": 0.8}}}

    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY", transport=transport)
    r = judge.boolean({"detection": "rock"}, "Is this a hazard?", name="hazard_relevant")
    assert r == {"value": True, "probability": 0.8}
    # decisions API: boolean questions map to type 'noul'
    assert seen["payload"]["questions"]["hazard_relevant"]["type"] == "noul"


def test_jev_judge_transport_observation_is_sanitized_and_pre_normalization(monkeypatch):
    from sentinel.judgment import JevJudge

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    observations = []

    def transport(payload):
        return {
            "model": "typesafe/jev-1.13-20260917",
            "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
            "answers": {"q": {"type": "noul", "noul": 0.8, "provider_content": "private"}},
            "credentials": "must-not-escape",
        }

    judge = JevJudge(
        api_key_env="SENTINEL_TEST_KEY",
        transport=transport,
        observation_callback=observations.append,
    )
    assert judge.boolean({}, "q?", name="q")["value"] is True
    assert observations == [{
        "model": "typesafe/jev-1.13-20260917",
        "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
    }]
    assert "private" not in repr(observations)
    assert "must-not-escape" not in repr(observations)


def test_jev_judge_transport_observation_rejects_invalid_numeric_usage(monkeypatch):
    from sentinel.judgment import JevJudge

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    observations = []
    judge = JevJudge(
        api_key_env="SENTINEL_TEST_KEY",
        transport=lambda payload: {
            "answers": {"q": {"type": "noul", "noul": 0.2}},
            "usage": {"prompt_tokens": True, "completion_tokens": float("inf"), "total_tokens": -1},
        },
        observation_callback=observations.append,
    )
    judge.boolean({}, "q?", name="q")
    assert observations == [{
        "model": None,
        "usage": {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None},
    }]


def test_jev_judge_posts_to_decisions_endpoint(monkeypatch):
    from sentinel.judgment import JevJudge

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY",
                     base_url="https://openrouter.ai/",
                     transport=lambda payload: {"answers": {"q": {"type": "noul", "noul": 0.3}}})
    judge.boolean({}, "q?", name="q")
    assert judge.endpoint_path == "/api/alpha/decisions"


def test_jev_judge_choice_validates_options(monkeypatch):
    from sentinel.judgment import JevJudge, JudgeUnavailable

    def transport(payload):
        return {"answers": {"priority": {"type": "choice", "choice": "high",
                                          "probabilities": {"ignore": 0.05, "high": 0.95}}}}

    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY", transport=transport)
    with pytest.raises(JudgeUnavailable):
        judge.choice({"mission": {}}, "Pick priority.", options=["ignore", "low", "high"], name="priority")


def test_jev_judge_choice_maps_options_to_criteria(monkeypatch):
    from sentinel.judgment import JevJudge

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    seen = {}

    def transport(payload):
        seen["payload"] = payload
        return {"answers": {"priority": {"type": "choice", "choice": "low",
                                          "probabilities": {"low": 1.0, "high": 0.0}}}}

    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY", transport=transport)
    r = judge.choice({"mission": {}}, "Pick priority.", options=["low", "high"], name="priority")
    assert r["value"] == "low"
    assert r["probabilities"] == {"low": 1.0, "high": 0.0}
    q = seen["payload"]["questions"]["priority"]
    assert q["type"] == "choice"
    assert q["criteria"] == {"low": None, "high": None}  # 'criteria', not 'options'


def test_jev_judge_missing_api_key_env_fails_closed(monkeypatch):
    from sentinel.judgment import JevJudge, JudgeUnavailable

    monkeypatch.delenv("SENTINEL_TEST_KEY", raising=False)
    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY", transport=lambda payload: {"answers": {}})
    with pytest.raises(JudgeUnavailable):
        judge.boolean({}, "q?", name="anything")


def test_jev_judge_unparseable_response_fails_closed(monkeypatch):
    from sentinel.judgment import JevJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY",
                     transport=lambda payload: {"answers": {}})  # name missing
    with pytest.raises(JudgeUnavailable):
        judge.boolean({}, "q?", name="anything")


def test_jev_judge_evaluate_batches_all_questions_in_one_post(monkeypatch):
    from sentinel.judgment import JevJudge

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    calls = []

    def transport(payload):
        calls.append(payload)
        return {"answers": {
            "hazard_relevant": {"type": "noul", "noul": 0.8},
            "priority": {"type": "choice", "choice": "high",
                         "probabilities": {"ignore": 0.05, "low": 0.1, "medium": 0.25, "high": 0.6},
                         "confidence": 0.7},
            "priority_scored": {"type": "score", "score": 2.5,
                                "legend": {"0": "not a hazard", "1": "minor", "2": "needs inspection", "3": "urgent"},
                                "probabilities": {"2": 0.4, "3": 0.5}, "confidence": 0.6},
        }}

    questions = [
        {"name": "hazard_relevant", "type": "noul", "instruction": "Is this a hazard?",
         "criteria": {"true": "operational hazard", "false": "not relevant"}},
        {"name": "priority", "type": "choice", "instruction": "Pick priority.",
         "criteria": {"ignore": None, "low": None, "medium": None, "high": None}},
        {"name": "priority_scored", "type": "score", "instruction": "Score urgency.",
         "criteria": ["not a hazard", "minor", "needs inspection", "urgent"]},
    ]
    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY", transport=transport)
    answers = judge.evaluate({"detection": "rock"}, questions)

    assert len(calls) == 1  # ONE request, all questions in parallel
    payload = calls[0]
    assert set(payload) == {"model", "state", "questions"}
    assert payload["state"] == {"detection": "rock"}
    q = payload["questions"]
    # internal 'instruction' is sent as 'instructions'
    assert q["hazard_relevant"] == {"type": "noul", "instructions": "Is this a hazard?",
                                    "criteria": {"true": "operational hazard", "false": "not relevant"}}
    assert q["priority"]["type"] == "choice"
    assert q["priority_scored"]["criteria"] == ["not a hazard", "minor", "needs inspection", "urgent"]

    assert answers["hazard_relevant"] == {"value": True, "probability": 0.8}
    assert answers["priority"]["value"] == "high"
    assert answers["priority"]["probabilities"]["high"] == 0.6
    assert answers["priority"]["confidence"] == 0.7
    assert answers["priority_scored"]["value"] == 2.5  # score may sit between levels
    assert answers["priority_scored"]["legend"]["0"] == "not a hazard"
    assert answers["priority_scored"]["confidence"] == 0.6


def test_jev_judge_evaluate_boolean_threshold_is_policy(monkeypatch):
    from sentinel.judgment import JevJudge

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY", boolean_threshold=0.7,
                     transport=lambda p: {"answers": {"q": {"type": "noul", "noul": 0.65}}})
    r = judge.evaluate({}, [{"name": "q", "type": "noul", "instruction": "q?", "criteria": None}])
    assert r["q"] == {"value": False, "probability": 0.65}  # threshold is policy, not the model


def test_jev_judge_evaluate_choice_outside_criteria_fails_closed(monkeypatch):
    from sentinel.judgment import JevJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY",
                     transport=lambda p: {"answers": {"priority": {"type": "choice", "choice": "medium",
                                                                   "probabilities": {"low": 1.0}}}})
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, [{"name": "priority", "type": "choice", "instruction": "Pick.",
                             "criteria": {"low": None, "high": None}}])


def test_jev_judge_evaluate_missing_answer_fails_closed(monkeypatch):
    from sentinel.judgment import JevJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY", transport=lambda p: {"answers": {}})
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, [{"name": "q", "type": "noul", "instruction": "q?", "criteria": None}])


def test_jev_judge_evaluate_noul_probability_out_of_range_fails_closed(monkeypatch):
    from sentinel.judgment import JevJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_KEY", "fake-not-a-real-key")
    judge = JevJudge(api_key_env="SENTINEL_TEST_KEY",
                     transport=lambda p: {"answers": {"q": {"type": "noul", "noul": 1.4}}})
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, [{"name": "q", "type": "noul", "instruction": "q?", "criteria": None}])


def test_static_judge_evaluate_answers_all_question_types():
    judge = StaticJudge(
        booleans={"hazard_relevant": (True, 0.92)},
        choices={"priority": ("high", {"ignore": 0.05, "low": 0.1, "medium": 0.25, "high": 0.6})},
        scores={"priority_scored": 3.0},
        boolean_threshold=0.5,
    )
    questions = [
        {"name": "hazard_relevant", "type": "noul", "instruction": "Is this a hazard?", "criteria": None},
        {"name": "priority", "type": "choice", "instruction": "Pick priority.",
         "criteria": {"ignore": None, "low": None, "medium": None, "high": None}},
        {"name": "priority_scored", "type": "score", "instruction": "Score urgency.",
         "criteria": ["not a hazard", "minor", "needs inspection", "urgent"]},
    ]
    answers = judge.evaluate({}, questions)
    assert answers["hazard_relevant"] == {"value": True, "probability": 0.92}
    assert answers["priority"]["value"] == "high"
    assert set(answers["priority"]["probabilities"]) == {"ignore", "low", "medium", "high"}
    assert answers["priority_scored"]["value"] == 3.0
    assert answers["priority_scored"]["legend"] == {"0": "not a hazard", "1": "minor",
                                                    "2": "needs inspection", "3": "urgent"}


def test_static_judge_evaluate_unconfigured_names_are_skipped():
    judge = StaticJudge(booleans={"hazard_relevant": (True, 0.9)})
    r = judge.evaluate({}, QUESTION_SET)
    assert set(r) == {"hazard_relevant"}  # deterministic double answers only what is configured


def test_static_judge_evaluate_direct_answers_take_precedence():
    judge = StaticJudge(answers={"human_review_needed": {"value": True, "probability": 0.99}})
    r = judge.evaluate({}, [{"name": "human_review_needed", "type": "noul", "instruction": "q?", "criteria": None}])
    assert r["human_review_needed"] == {"value": True, "probability": 0.99}


def test_bounded_judge_protocol_has_evaluate():
    assert hasattr(BoundedJudge, "evaluate")


def test_state_snapshot_is_pure_and_versioned():
    from sentinel.judgment import state_snapshot

    mountain = {
        "hazards": [{"id": "h1", "object": "rock", "confidence": 0.91,
                     "position_relation": "inside_trail_corridor",
                     "resolved": False}],
        "patrollers": [{"id": "p1", "status": "available"}],
        "trail_status": "closed_pending_inspection",
        "workflow": "daily_opening_inspection",
    }
    obs = {"detection_id": "h1", "source": "drone_1"}
    ctx_a = state_snapshot(mountain, obs)
    ctx_b = state_snapshot(mountain, obs)
    assert ctx_a == ctx_b  # purity: identical inputs, identical output
    assert ctx_a["context_version"] == "judgment_context.v1"
    # pre-resolved facts, not raw state:
    assert ctx_a["observation"]["duplicate_of_existing_detection"] is False
    assert ctx_a["operations"]["patroller_available"] is True
    assert "hazards" not in ctx_a  # raw collections are projected, not passed through


def test_state_snapshot_detects_duplicate():
    from sentinel.judgment import state_snapshot
    mountain = {"hazards": [{"id": "h1", "object": "rock", "confidence": 0.9,
                             "position_relation": "inside_trail_corridor", "resolved": True}],
                "patrollers": [], "trail_status": "open", "workflow": "daily_opening_inspection"}
    ctx = state_snapshot(mountain, {"detection_id": "h1", "source": "drone_1"})
    assert ctx["observation"]["duplicate_of_existing_detection"] is True


# ---------------------------------------------------------------------------
# LlmJudge: general LLM baseline behind the SAME bounded-judge port
# ---------------------------------------------------------------------------

def _llm_questions():
    return [
        {"name": "hazard_relevant", "type": "noul", "instruction": "Is this a hazard?",
         "criteria": {"true": "operational hazard", "false": "not relevant"}},
        {"name": "priority", "type": "choice", "instruction": "Pick priority.",
         "criteria": {"ignore": None, "low": None, "medium": None, "high": None}},
        {"name": "priority_scored", "type": "score", "instruction": "Score urgency.",
         "criteria": ["not a hazard", "minor", "needs inspection", "urgent"]},
    ]


def _llm_content_answers(**answers):
    import json
    return json.dumps({"answers": answers})


def _llm_response(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _good_llm_content():
    return _llm_content_answers(
        hazard_relevant={"probability": 0.8, "confidence": 0.7},
        priority={"choice": "high", "probabilities": {"ignore": 0.05, "low": 0.1, "medium": 0.25, "high": 0.6},
                  "confidence": 0.7},
        priority_scored={"score": 2.5, "probabilities": {"2": 0.4, "3": 0.5}, "confidence": 0.6},
    )


def test_llm_judge_requires_env_var_name():
    from sentinel.judgment import LlmJudge
    with pytest.raises(ValueError):
        LlmJudge(api_key_env="", base_url="https://x", model="m")


def test_llm_judge_missing_api_key_env_fails_closed(monkeypatch):
    from sentinel.judgment import LlmJudge, JudgeUnavailable

    monkeypatch.delenv("SENTINEL_TEST_LLM_KEY", raising=False)
    judge = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://x", model="m",
                     transport=lambda url, headers, payload: _llm_response("{}"))
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, _llm_questions())


def test_llm_judge_posts_openai_chat_completions_json_mode(monkeypatch):
    from sentinel.judgment import LlmJudge

    monkeypatch.setenv("SENTINEL_TEST_LLM_KEY", "fake-not-a-real-key")
    seen = {}

    def transport(url, headers, payload):
        seen["url"], seen["headers"], seen["payload"] = url, headers, payload
        return _llm_response(_good_llm_content())

    judge = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://llm.example/",
                     model="test-model", transport=transport)
    answers = judge.evaluate({"detection": "rock"}, _llm_questions())

    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer fake-not-a-real-key"
    payload = seen["payload"]
    assert payload["model"] == "test-model"
    assert payload["temperature"] == 0
    assert payload["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert "answers" in payload["messages"][0]["content"]  # fixed system instructions
    user = json.loads(payload["messages"][1]["content"])
    assert user["state"] == {"detection": "rock"}
    assert [q["name"] for q in user["questions"]] == ["hazard_relevant", "priority", "priority_scored"]

    # SAME answer shapes as JevJudge
    assert answers["hazard_relevant"] == {"value": True, "probability": 0.8}
    assert answers["priority"]["value"] == "high"
    assert answers["priority"]["probabilities"]["high"] == 0.6
    assert answers["priority"]["confidence"] == 0.7
    assert answers["priority_scored"]["value"] == 2.5
    assert answers["priority_scored"]["confidence"] == 0.6


def test_llm_judge_boolean_threshold_is_policy(monkeypatch):
    from sentinel.judgment import LlmJudge

    monkeypatch.setenv("SENTINEL_TEST_LLM_KEY", "fake-not-a-real-key")
    judge = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://x", model="m",
                     boolean_threshold=0.7,
                     transport=lambda u, h, p: _llm_response(_llm_content_answers(**{"q": {"probability": 0.65}})))
    r = judge.evaluate({}, [{"name": "q", "type": "noul", "instruction": "q?", "criteria": None}])
    assert r["q"] == {"value": False, "probability": 0.65}


def test_llm_judge_choice_outside_criteria_fails_closed(monkeypatch):
    from sentinel.judgment import LlmJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_LLM_KEY", "fake-not-a-real-key")
    judge = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://x", model="m",
                     transport=lambda u, h, p: _llm_response(_llm_content_answers(
                         **{"priority": {"choice": "medium", "probabilities": {"low": 1.0}, "confidence": 0.5}})))
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, [{"name": "priority", "type": "choice", "instruction": "Pick.",
                             "criteria": {"low": None, "high": None}}])


def test_llm_judge_probability_out_of_range_fails_closed(monkeypatch):
    from sentinel.judgment import LlmJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_LLM_KEY", "fake-not-a-real-key")
    judge = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://x", model="m",
                     transport=lambda u, h, p: _llm_response(_llm_content_answers(**{"q": {"probability": 1.4}})))
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, [{"name": "q", "type": "noul", "instruction": "q?", "criteria": None}])


def test_llm_judge_non_json_content_fails_closed(monkeypatch):
    from sentinel.judgment import LlmJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_LLM_KEY", "fake-not-a-real-key")
    judge = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://x", model="m",
                     transport=lambda u, h, p: _llm_response("not json at all"))
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, _llm_questions())


def test_llm_judge_missing_or_extra_question_names_fail_closed(monkeypatch):
    from sentinel.judgment import LlmJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_LLM_KEY", "fake-not-a-real-key")
    # missing 'priority_scored'
    partial = _llm_content_answers(hazard_relevant={"probability": 0.8},
                                   priority={"choice": "low", "probabilities": {"low": 1.0}, "confidence": 0.5})
    judge = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://x", model="m",
                     transport=lambda u, h, p: _llm_response(partial))
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, _llm_questions())

    # extra question name the caller never asked about
    extra = json.loads(_good_llm_content())
    extra["answers"]["bogus"] = {"probability": 0.5}
    judge2 = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://x", model="m",
                      transport=lambda u, h, p: _llm_response(json.dumps(extra)))
    with pytest.raises(JudgeUnavailable):
        judge2.evaluate({}, _llm_questions())


def test_llm_judge_http_error_fails_closed(monkeypatch):
    from sentinel.judgment import LlmJudge, JudgeUnavailable

    monkeypatch.setenv("SENTINEL_TEST_LLM_KEY", "fake-not-a-real-key")

    def transport(url, headers, payload):
        raise RuntimeError("HTTP 500")

    judge = LlmJudge(api_key_env="SENTINEL_TEST_LLM_KEY", base_url="https://x", model="m", transport=transport)
    with pytest.raises(JudgeUnavailable):
        judge.evaluate({}, _llm_questions())
