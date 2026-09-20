"""The sweep's report must be derivable from committed raw rows.

Raised by the P3 session on 2026-09-20: the live sweep committed only the
rendered Markdown, so the aggregates that P3b's promotion criteria rest on
could not be audited, re-scored under different bands, or re-derived -- except
by paying for another live batch.
"""
from __future__ import annotations

import json

from scripts.shadow_sweep import (
    RecordingJudge,
    fixture_truth,
    load_judgment_turns,
    read_records,
    render_report,
    run_sweep,
    write_records,
)
from sentinel.shadow_eval import score


class _ScriptedJudge:
    """Answers with the first legal label at a fixed confidence."""

    model = "scripted"

    def choice(self, state, instruction, *, criteria=None, options=None, name=None):
        labels = list(criteria or options or [])
        return {"value": labels[0], "probabilities": {labels[0]: 0.91, labels[-1]: 0.09},
                "confidence": 0.91}


def test_report_is_derivable_from_the_committed_rows(tmp_path):
    turns = load_judgment_turns()[:6]
    truth = fixture_truth(turns)
    records = run_sweep("scripted", _ScriptedJudge(), turns, repeats=2)

    path = tmp_path / "sweep.jsonl"
    write_records(path, records)
    reloaded = read_records(path)

    assert reloaded == json.loads(json.dumps(records, sort_keys=True))
    # the same aggregates come out of the rows alone
    assert render_report("scripted", score(reloaded, truth), n_turns=len(turns), repeats=2) == \
        render_report("scripted", score(records, truth), n_turns=len(turns), repeats=2)


def test_rows_carry_the_raw_answer_so_bands_can_be_rescored(tmp_path):
    turns = load_judgment_turns()[:3]
    records = run_sweep("scripted", _ScriptedJudge(), turns, repeats=1)

    for record in records:
        raw = record["raw_answer"]
        assert raw is not None, "a promotion claim must be re-derivable from raw judge output"
        # both candidate confidence sources are preserved: the judge's own
        # `confidence` field and the per-option probabilities the band policy
        # falls back to. Reading the wrong one is a live hypothesis for Jev's
        # 0% HIGH-band coverage.
        assert "confidence" in raw and "probabilities" in raw
        assert record["legal_labels"], "the option set the judge chose among"


def test_recording_judge_is_transparent_to_other_attributes():
    class Judge:
        model = "m"

        def choice(self, state, instruction, **kwargs):
            return {"value": "x", "probabilities": {"x": 1.0}, "confidence": 1.0}

        def boolean(self, state, instruction, *, name):
            return {"value": True, "probability": 1.0}

    recorder = RecordingJudge(Judge())
    assert recorder.model == "m"
    assert recorder.boolean({}, "q", name="n")["value"] is True
    assert recorder.last_raw is None, "only choice() is recorded"
    recorder.choice({}, "q", options=["x"])
    assert recorder.last_raw["value"] == "x"
