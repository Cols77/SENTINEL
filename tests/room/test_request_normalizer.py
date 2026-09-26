"""The GLM request-normalizer pre-pass: the module, the fail-open CLI seam,
and its wiring in ``room_session live`` (offline: every provider call is a fake)."""
from __future__ import annotations

import json
import time

import pytest

import scripts.room_session as cli
from sentinel.room import live_session, live_stack
from sentinel.room import request_normalizer as rn
from sentinel.survey.live_detect import LIVE_CLASSES
from tests.room import test_cli as cli_fakes
from tests.room import test_live_session as live_fakes


def _reply(text, model=rn.DEFAULT_MODEL):
    return {"model": model, "choices": [{"message": {"content": text}}]}


# -- the module ----------------------------------------------------------------
def test_normalizer_returns_the_model_sentence_and_never_puts_the_key_in_the_body(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-test-key")
    seen = {}

    def transport(url, headers, payload):
        seen.update(url=url, headers=headers, payload=payload)
        return _reply('  "Count the mugs in the room."\n')

    text, model = rn.normalize_request("how many cups are around?", authorised=True, transport=transport)
    assert text == "Count the mugs in the room."
    assert model == rn.DEFAULT_MODEL == "z-ai/glm-5.3"
    assert "sk-secret-test-key" not in json.dumps(seen["payload"])
    assert seen["headers"]["Authorization"] == "Bearer sk-secret-test-key"
    assert seen["payload"]["model"] == rn.DEFAULT_MODEL and "tools" not in seen["payload"]
    system = seen["payload"]["messages"][0]["content"]
    for name in LIVE_CLASSES:
        assert name in system
    assert seen["payload"]["messages"][1]["content"] == "how many cups are around?"
    assert rn.DEFAULT_TIMEOUT_S <= 8.0


def test_normalizer_refuses_without_authorisation():
    def transport(*_):
        raise AssertionError("no call may be made without authorisation")

    with pytest.raises(rn.NormalizerUnavailable):
        rn.normalize_request("how many cups?", authorised=False, transport=transport)


@pytest.mark.parametrize("raw", [_reply("   "), _reply("Count the mugs", model="other/model"), {"choices": []}])
def test_normalizer_raises_on_empty_substituted_or_malformed_replies(monkeypatch, raw):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with pytest.raises(rn.NormalizerUnavailable):
        rn.normalize_request("how many cups?", authorised=True, transport=lambda *_: raw)


def test_normalizer_is_bounded_by_its_deadline(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    def slow(*_):
        time.sleep(1.0)
        return _reply("Count the mugs in the room")

    started = time.monotonic()
    with pytest.raises(rn.NormalizerUnavailable):
        rn.normalize_request("how many cups?", authorised=True, transport=slow, timeout_s=0.1)
    assert time.monotonic() - started < 0.8


# -- the CLI seam --------------------------------------------------------------
class _Run:
    def __init__(self):
        self.events = []

    def append_event(self, event_type, payload, *, now):
        self.events.append((event_type, payload, now))


def _wall():
    from datetime import datetime, timezone
    return datetime(2026, 9, 26, tzinfo=timezone.utc)


@pytest.mark.parametrize("error", [ConnectionError("network down"), TimeoutError("timed out"),
                                   rn.NormalizerUnavailable("deadline")])
def test_seam_fails_open_to_the_original_text(error, capsys):
    run = _Run()

    def normalize(text, **_):
        raise error

    result = cli._normalize_request_text(run, request_text="how many cups?", authorised=True,
                                         wall_clock=_wall, normalize=normalize)
    assert result == "how many cups?"
    assert run.events == []
    err = capsys.readouterr().err
    assert "request normalization unavailable" in err and len(err.strip().splitlines()) == 1


def test_seam_makes_no_call_when_not_authorised():
    def normalize(*_a, **_k):
        raise AssertionError("no normalization call without authorisation")

    run = _Run()
    assert cli._normalize_request_text(run, request_text="how many cups?", authorised=False,
                                       wall_clock=_wall, normalize=normalize) == "how many cups?"
    assert run.events == []


def test_seam_records_a_changed_request(capsys):
    run = _Run()
    result = cli._normalize_request_text(
        run, request_text="how many cups are around?", authorised=True, wall_clock=_wall,
        normalize=lambda text, **_: ("Count the mugs in the room", "z-ai/glm-5.3"))
    assert result == "Count the mugs in the room"
    assert run.events == [("REQUEST_NORMALIZED", {"original": "how many cups are around?",
                                                  "normalized": "Count the mugs in the room",
                                                  "model": "z-ai/glm-5.3"}, _wall())]
    err = capsys.readouterr().err
    assert "how many cups are around?" in err and "Count the mugs in the room" in err


def test_seam_records_nothing_when_the_text_is_unchanged():
    run = _Run()
    result = cli._normalize_request_text(run, request_text="Count the mugs in the room", authorised=True,
                                         wall_clock=_wall,
                                         normalize=lambda text, **_: (text, "z-ai/glm-5.3"))
    assert result == "Count the mugs in the room" and run.events == []


# -- wiring in _cmd_live ---------------------------------------------------------
@pytest.fixture
def _offline(monkeypatch):
    monkeypatch.setattr(live_stack, "provider_environ", lambda: {})
    monkeypatch.setattr(live_stack, "build_tello_client",
                        lambda **_: (_ for _ in ()).throw(RuntimeError("no real Tello")))
    monkeypatch.setattr(live_stack, "_http_get",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no network")))


def _wire(monkeypatch, *, default_jev: bool):
    """Returns (captured, jev_factory). With *default_jev* the fake judge's
    factory is installed AS ``cli._default_jev`` so the CLI treats it as the real one."""
    captured = {"normalize_calls": [], "run_request": None, "answer_request": None}
    judge = live_fakes.PolicyJudge("jev-vendor-x")

    def jev_factory():
        return judge

    if default_jev:
        monkeypatch.setattr(cli, "_default_jev", jev_factory)
        monkeypatch.setattr(live_stack, "jev_latency_probe", lambda *_a, **_k: (None, None))

    def fake_normalize(text, **kwargs):
        captured["normalize_calls"].append(text)
        return "Count the mugs in the room", "z-ai/glm-5.3"

    monkeypatch.setattr(rn, "normalize_request", fake_normalize)
    real_run = live_session.run_count_return

    def spy_run(**kwargs):
        captured["run_request"] = kwargs["request_text"]
        return real_run(**kwargs)

    monkeypatch.setattr(live_session, "run_count_return", spy_run)

    def spy_answer(run, run_id, *, request_text, authorised):
        captured["answer_request"] = request_text

    monkeypatch.setattr(cli, "_write_operator_answer", spy_answer)
    return captured, jev_factory


def _fly(tmp_path, jev_factory, *, authorise=True):
    argv, manifest_path = cli_fakes._flight_setup(tmp_path)
    if not authorise:
        argv = [a for a in argv if a != "--i-authorise-paid-calls"]
    code = cli.main(argv, drone_probe=cli_fakes._fake_probe("tello-1"),
                    provider_probe=cli_fakes._fake_probe("gemini-1"),
                    client_factory=live_fakes.FlightStackFactory(), jev_factory=jev_factory,
                    detector_factory=cli_fakes._fake_collaborator("detector-vendor-y"),
                    request_text="how many cups are around?")
    return code, manifest_path


def test_live_flight_uses_the_normalized_request_and_answers_with_the_original(tmp_path, monkeypatch, capsys,
                                                                                _offline):
    captured, jev_factory = _wire(monkeypatch, default_jev=True)
    code, manifest_path = _fly(tmp_path, jev_factory)
    assert code == 0, capsys.readouterr().err
    assert captured["normalize_calls"] == ["how many cups are around?"]
    assert captured["run_request"] == "Count the mugs in the room"
    assert captured["answer_request"] == "how many cups are around?"
    events = cli_fakes._run_events(tmp_path, "runs", manifest_path)
    recorded = [{k: e.payload[k] for k in ("original", "normalized", "model")}
                for e in events if e.event_type == "REQUEST_NORMALIZED"]
    assert recorded == [{"original": "how many cups are around?", "normalized": "Count the mugs in the room",
                         "model": "z-ai/glm-5.3"}]


def test_live_flight_with_an_injected_jev_never_normalizes(tmp_path, monkeypatch, capsys, _offline):
    captured, jev_factory = _wire(monkeypatch, default_jev=False)
    code, _ = _fly(tmp_path, jev_factory)
    assert code == 0, capsys.readouterr().err
    assert captured["normalize_calls"] == []
    assert captured["run_request"] == "how many cups are around?"


def test_live_flight_without_the_paid_calls_flag_never_normalizes(tmp_path, monkeypatch, capsys, _offline):
    captured, jev_factory = _wire(monkeypatch, default_jev=True)
    code, _ = _fly(tmp_path, jev_factory, authorise=False)
    # consent requires the flag, so the run refuses before any flight; no call is made
    assert code == 2
    assert captured["normalize_calls"] == [] and captured["run_request"] is None


def test_live_flight_fails_open_when_the_normalizer_errors(tmp_path, monkeypatch, capsys, _offline):
    captured, jev_factory = _wire(monkeypatch, default_jev=True)

    def broken(text, **_):
        raise TimeoutError("GLM timed out")

    monkeypatch.setattr(rn, "normalize_request", broken)
    code, manifest_path = _fly(tmp_path, jev_factory)
    err = capsys.readouterr().err
    assert code == 0, err
    assert captured["run_request"] == "how many cups are around?"
    assert captured["answer_request"] == "how many cups are around?"
    assert "request normalization unavailable" in err
    events = cli_fakes._run_events(tmp_path, "runs", manifest_path)
    assert not [e for e in events if e.event_type == "REQUEST_NORMALIZED"]
