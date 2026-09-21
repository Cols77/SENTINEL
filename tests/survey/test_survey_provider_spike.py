"""tests for scripts/survey_provider_spike.py (plan phase 4, section E).

Only the refusal path and the call-count arithmetic are tested here, per
the plan: this script is prepared, not run, in this phase, and is NEVER
invoked with --i-authorise-paid-calls actually reaching a real call in any
test below. ``environ={}`` is passed to every ``main()`` call so no real
.env file or credential is ever read."""
import pytest

from scripts.survey_provider_spike import (CALLS_PER_FRAME, SURVEY_CLASSES,
                                            available_paid_providers, main, paid_call_plan,
                                            refuse_reason)


# -- call-count arithmetic ---------------------------------------------------

def test_paid_call_plan_moondream_is_one_call_per_class_per_frame():
    plan = paid_call_plan(5, ("moondream",))
    assert plan["per_provider"]["moondream"]["calls"] == 5 * len(SURVEY_CLASSES)
    assert plan["total_calls"] == 5 * len(SURVEY_CLASSES)


def test_paid_call_plan_gemini_is_one_call_per_frame():
    plan = paid_call_plan(5, ("gemini",))
    assert plan["per_provider"]["gemini"]["calls"] == 5
    assert plan["total_calls"] == 5


def test_paid_call_plan_both_providers_sums_calls_and_cost():
    n_frames = 8
    plan = paid_call_plan(n_frames, ("moondream", "gemini"))
    assert plan["total_calls"] == n_frames * (len(SURVEY_CLASSES) + 1)
    assert plan["estimated_cost_usd"] == pytest.approx(
        plan["per_provider"]["moondream"]["cost_usd"] + plan["per_provider"]["gemini"]["cost_usd"])


def test_paid_call_plan_with_no_available_providers_is_zero_calls():
    plan = paid_call_plan(8, ())
    assert plan["total_calls"] == 0
    assert plan["estimated_cost_usd"] == 0
    assert plan["per_provider"] == {}


def test_available_paid_providers_reads_only_the_named_env_vars():
    assert available_paid_providers({"MOONDREAM_API_KEY": "x"}) == ("moondream",)
    assert available_paid_providers({"GEMINI_API_KEY": "x"}) == ("gemini",)
    assert set(available_paid_providers({"MOONDREAM_API_KEY": "x", "GEMINI_API_KEY": "y"})) == {
        "moondream", "gemini"}
    assert available_paid_providers({}) == ()


def test_a_gdino_responses_directory_is_never_a_paid_provider(tmp_path):
    """A GDINO responses dir is free/local and must never inflate the paid
    call count or the available-provider list -- CALLS_PER_FRAME has no
    'gdino' entry, and available_paid_providers never looks at the CLI
    argument, only at env vars."""
    assert "gdino" not in CALLS_PER_FRAME
    assert available_paid_providers({"MOONDREAM_API_KEY": "x"}) == ("moondream",)


# -- refuse_reason (pure) -----------------------------------------------------

class _Args:
    def __init__(self, i_authorise_paid_calls=False):
        self.i_authorise_paid_calls = i_authorise_paid_calls


def test_refuse_reason_without_the_flag_always_refuses():
    assert refuse_reason(_Args(i_authorise_paid_calls=False), garden_stills=False,
                         consent_ok=True) is not None


def test_refuse_reason_with_the_flag_and_no_garden_stills_proceeds():
    assert refuse_reason(_Args(i_authorise_paid_calls=True), garden_stills=False,
                         consent_ok=True) is None


def test_refuse_reason_with_the_flag_but_garden_stills_and_no_consent_still_refuses():
    reason = refuse_reason(_Args(i_authorise_paid_calls=True), garden_stills=True,
                           consent_ok=False)
    assert reason is not None


def test_the_two_refusal_messages_are_distinct():
    no_flag = refuse_reason(_Args(i_authorise_paid_calls=False), garden_stills=True,
                            consent_ok=False)
    no_consent = refuse_reason(_Args(i_authorise_paid_calls=True), garden_stills=True,
                               consent_ok=False)
    assert no_flag != no_consent


# -- main()'s refusal gate: no decode, no provider call, ever ---------------

def test_main_without_the_flag_refuses_before_any_decode_or_call(monkeypatch, capsys):
    import scripts.survey_provider_spike as spike

    def _boom(*a, **k):
        raise AssertionError("must not decode or call before the refusal gate")

    monkeypatch.setattr(spike, "_decode_stills", _boom)
    monkeypatch.setattr(spike, "_run_moondream", _boom)
    monkeypatch.setattr(spike, "_run_gemini", _boom)

    exit_code = main([], environ={})

    assert exit_code != 0
    out = capsys.readouterr().out
    assert "total paid calls" in out
    assert "refus" in out.lower()


def test_main_with_the_flag_but_garden_stills_and_no_consent_still_refuses(
        monkeypatch, capsys, tmp_path):
    import scripts.survey_provider_spike as spike

    def _boom(*a, **k):
        raise AssertionError("must not decode or call when consent is missing")

    monkeypatch.setattr(spike, "_decode_stills", _boom)
    monkeypatch.setattr(spike, "_run_moondream", _boom)
    monkeypatch.setattr(spike, "_run_gemini", _boom)

    garden = tmp_path / "garden"
    garden.mkdir()

    exit_code = main(
        ["--i-authorise-paid-calls", "--garden-stills", str(garden)], environ={})

    assert exit_code != 0
    assert "consent" in capsys.readouterr().out.lower()
