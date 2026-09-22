from __future__ import annotations

from dataclasses import asdict
import hashlib
import math

import pytest

from sentinel.jev_choice import ChoiceAssessment, assess_choice


LABELS = ("alpha", "beta", "escalate")
GATE_KWARGS = {
    "escalation_label": "escalate",
    "act_at": 0.60,
    "min_margin": 0.15,
}


def assess(answer: object) -> ChoiceAssessment:
    return assess_choice(answer, LABELS, **GATE_KWARGS)


def test_legal_top_ranked_selection_is_accepted_with_canonical_probabilities() -> None:
    assessment = assess({
        "value": "alpha",
        "confidence": 0.95,
        "probabilities": {"escalate": 0.05, "beta": 0.10, "alpha": 0.85},
    })

    assert assessment == ChoiceAssessment(
        selected_label="alpha",
        final_label="alpha",
        outcome="ACCEPTED",
        confidence=0.95,
        selected_probability=0.85,
        confidence_source="SCALAR",
        margin=0.75,
        probabilities=(("alpha", 0.85), ("beta", 0.10), ("escalate", 0.05)),
    )


@pytest.mark.parametrize("confidence", [None, pytest.param("absent", id="absent")])
def test_absent_or_none_scalar_confidence_falls_back_to_selected_probability(confidence) -> None:
    answer = {
        "value": "alpha",
        "probabilities": {"alpha": 0.80, "beta": 0.10, "escalate": 0.10},
    }
    if confidence != "absent":
        answer["confidence"] = confidence

    assessment = assess(answer)

    assert assessment.outcome == "ACCEPTED"
    assert assessment.confidence == assessment.selected_probability == 0.80
    assert assessment.confidence_source == "SELECTED_PROBABILITY"


@pytest.mark.parametrize(("answer", "expected"), [
    pytest.param(["not a mapping"], "MALFORMED_ANSWER", id="non-mapping"),
    pytest.param(
        {"value": "unknown", "confidence": 0.8,
         "probabilities": {"alpha": 0.8, "beta": 0.1, "escalate": 0.1}},
        "ILLEGAL_OR_MALFORMED", id="illegal-label",
    ),
    ({"value": "alpha", "confidence": True,
      "probabilities": {"alpha": 0.8, "beta": 0.1, "escalate": 0.1}},
     "ILLEGAL_OR_MALFORMED"),
    ({"value": "alpha", "confidence": math.inf,
      "probabilities": {"alpha": 0.8, "beta": 0.1, "escalate": 0.1}},
     "ILLEGAL_OR_MALFORMED"),
    ({"value": "alpha", "confidence": 0.8,
      "probabilities": {"alpha": math.nan, "beta": 0.1, "escalate": 0.1}},
     "ILLEGAL_OR_MALFORMED"),
    ({"value": "alpha", "confidence": 0.8,
      "probabilities": {"alpha": 1.1, "beta": 0.0, "escalate": 0.0}},
     "ILLEGAL_OR_MALFORMED"),
])
def test_malformed_or_non_finite_provider_values_fail_closed(answer: object, expected: str) -> None:
    assessment = assess(answer)

    assert assessment.final_label == "escalate"
    assert assessment.outcome == expected


class BadStr(str):
    __hash__ = None


class BadFloat(float):
    def __float__(self):
        raise RuntimeError("provider conversion must not escape")


@pytest.mark.parametrize("answer", [
    {
        "value": BadStr("alpha"),
        "confidence": 0.8,
        "probabilities": {"alpha": 0.8, "beta": 0.1, "escalate": 0.1},
    },
    {
        "value": "alpha",
        "confidence": BadFloat(0.8),
        "probabilities": {"alpha": 0.8, "beta": 0.1, "escalate": 0.1},
    },
])
def test_provider_subclasses_cannot_raise_during_label_or_probability_validation(answer) -> None:
    assessment = assess(answer)

    assert assessment.outcome == "ILLEGAL_OR_MALFORMED"


def test_selection_failure_precedes_malformed_scalar_confidence() -> None:
    assessment = assess({
        "value": "alpha",
        "confidence": "not a number",
        "probabilities": {"alpha": 0.3, "beta": 0.6, "escalate": 0.1},
    })

    assert assessment.outcome == "MISSING_OR_NON_MAX_SELECTION"


def test_low_confidence_precedes_thin_margin() -> None:
    assessment = assess({
        "value": "alpha",
        "confidence": 0.50,
        "probabilities": {"alpha": 0.51, "beta": 0.40, "escalate": 0.09},
    })

    assert assessment.outcome == "LOW_CONFIDENCE"


def test_missing_selected_probability_is_a_deterministic_selection_failure() -> None:
    assessment = assess({
        "value": "alpha",
        "confidence": 0.9,
        "probabilities": {"beta": 0.9, "escalate": 0.1},
    })

    assert assessment.selected_label == "alpha"
    assert assessment.final_label == "escalate"
    assert assessment.outcome == "MISSING_OR_NON_MAX_SELECTION"
    assert assessment.selected_probability is None


def test_non_max_selected_label_is_a_deterministic_selection_failure() -> None:
    assessment = assess({
        "value": "alpha",
        "confidence": 0.9,
        "probabilities": {"alpha": 0.3, "beta": 0.6, "escalate": 0.1},
    })

    assert assessment.final_label == "escalate"
    assert assessment.outcome == "MISSING_OR_NON_MAX_SELECTION"
    assert assessment.selected_probability == 0.3


def test_thin_margin_escalates_after_valid_selection_and_confidence() -> None:
    assessment = assess({
        "value": "alpha",
        "confidence": 0.9,
        "probabilities": {"alpha": 0.51, "beta": 0.40, "escalate": 0.09},
    })

    assert assessment.selected_label == "alpha"
    assert assessment.final_label == "escalate"
    assert assessment.outcome == "THIN_MARGIN"
    assert assessment.margin == pytest.approx(0.11)


def test_explicit_escalation_is_an_accepted_legal_selection() -> None:
    assessment = assess({
        "value": "escalate",
        "confidence": 0.90,
        "probabilities": {"alpha": 0.05, "beta": 0.05, "escalate": 0.90},
    })

    assert assessment.selected_label == assessment.final_label == "escalate"
    assert assessment.outcome == "ACCEPTED"


def test_assessment_is_immune_to_later_provider_response_mutation() -> None:
    answer = {
        "value": "alpha",
        "confidence": None,
        "probabilities": {"alpha": 0.80, "beta": 0.10, "escalate": 0.10},
    }
    assessment = assess(answer)
    digest = hashlib.sha256(repr(asdict(assessment)).encode()).hexdigest()

    answer["value"] = "beta"
    answer["probabilities"]["alpha"] = 0.0
    answer["probabilities"]["beta"] = 1.0

    assert assessment.probabilities == (("alpha", 0.8), ("beta", 0.1), ("escalate", 0.1))
    assert hashlib.sha256(repr(asdict(assessment)).encode()).hexdigest() == digest
