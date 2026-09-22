"""Pure, fail-closed validation for fixed-choice Jev responses."""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
import math
from typing import Literal


@dataclass(frozen=True)
class ChoiceAssessment:
    selected_label: str | None
    final_label: str
    outcome: str
    confidence: float | None
    selected_probability: float | None
    confidence_source: Literal["SCALAR", "SELECTED_PROBABILITY", "MISSING"]
    margin: float | None
    probabilities: tuple[tuple[str, float], ...]


def assess_choice(
    answer: object,
    legal_labels: Collection[str],
    *,
    escalation_label: str,
    act_at: float,
    min_margin: float,
) -> ChoiceAssessment:
    """Assess one provider choice without exposing provider exceptions."""
    labels = _legal_labels(legal_labels)
    if not isinstance(answer, Mapping):
        return _invalid(escalation_label, "MALFORMED_ANSWER")

    try:
        raw_label = answer.get("value")
        raw_probabilities = answer.get("probabilities")
        raw_confidence = answer.get("confidence")
    except Exception:  # noqa: BLE001 -- provider-controlled mappings fail closed
        return _invalid(escalation_label, "MALFORMED_ANSWER")

    label = raw_label if isinstance(raw_label, str) else None
    probabilities = _canonical_probabilities(raw_probabilities)
    if label not in labels or probabilities is None:
        return _invalid(
            escalation_label, "ILLEGAL_OR_MALFORMED", selected_label=label,
            probabilities=probabilities or (),
        )

    if any(key not in labels for key, _ in probabilities):
        return _invalid(
            escalation_label, "ILLEGAL_OR_MALFORMED", selected_label=label,
            probabilities=probabilities,
        )
    selected = _probability_for(probabilities, label)
    if selected is None:
        return _invalid(
            escalation_label, "MISSING_OR_NON_MAX_SELECTION", selected_label=label,
            probabilities=probabilities,
        )
    if selected != max(value for _, value in probabilities):
        return _invalid(
            escalation_label, "MISSING_OR_NON_MAX_SELECTION", selected_label=label,
            selected_probability=selected, probabilities=probabilities,
        )

    confidence, source = _scalar_or_selected_probability(raw_confidence, selected)
    if confidence is None:
        return _invalid(
            escalation_label, "ILLEGAL_OR_MALFORMED", selected_label=label,
            selected_probability=selected, probabilities=probabilities,
        )
    alternatives = [value for key, value in probabilities if key != label]
    margin = selected - max(alternatives, default=0.0)
    if confidence < act_at:
        return _assessment(
            label, escalation_label, "LOW_CONFIDENCE", confidence, selected,
            source, margin, probabilities,
        )
    if margin < min_margin:
        return _assessment(
            label, escalation_label, "THIN_MARGIN", confidence, selected,
            source, margin, probabilities,
        )
    return _assessment(
        label, label, "ACCEPTED", confidence, selected, source, margin,
        probabilities,
    )


def _assessment(
    selected_label: str | None,
    final_label: str,
    outcome: str,
    confidence: float | None,
    selected_probability: float | None,
    confidence_source: Literal["SCALAR", "SELECTED_PROBABILITY", "MISSING"],
    margin: float | None,
    probabilities: tuple[tuple[str, float], ...],
) -> ChoiceAssessment:
    return ChoiceAssessment(
        selected_label=selected_label,
        final_label=final_label,
        outcome=outcome,
        confidence=confidence,
        selected_probability=selected_probability,
        confidence_source=confidence_source,
        margin=margin,
        probabilities=probabilities,
    )


def _invalid(
    escalation_label: str,
    outcome: str,
    *,
    selected_label: str | None = None,
    confidence: float | None = None,
    selected_probability: float | None = None,
    probabilities: tuple[tuple[str, float], ...] = (),
) -> ChoiceAssessment:
    return _assessment(
        selected_label, escalation_label, outcome, confidence,
        selected_probability, "MISSING", None, probabilities,
    )


def _legal_labels(labels: Collection[str]) -> frozenset[str]:
    try:
        return frozenset(label for label in labels if isinstance(label, str))
    except Exception:  # noqa: BLE001 -- caller configuration cannot leak here
        return frozenset()


def _canonical_probabilities(
    raw: object,
) -> tuple[tuple[str, float], ...] | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        values = []
        for label, raw_probability in raw.items():
            probability = _finite_probability(raw_probability)
            if not isinstance(label, str) or probability is None:
                return None
            values.append((label, probability))
        return tuple(sorted(values))
    except Exception:  # noqa: BLE001 -- provider-controlled mappings fail closed
        return None


def _probability_for(
    probabilities: tuple[tuple[str, float], ...], label: str,
) -> float | None:
    for key, value in probabilities:
        if key == label:
            return value
    return None


def _scalar_or_selected_probability(
    raw: object, selected: float,
) -> tuple[float | None, Literal["SCALAR", "SELECTED_PROBABILITY", "MISSING"]]:
    if raw is None:
        return selected, "SELECTED_PROBABILITY"
    scalar = _finite_probability(raw)
    if scalar is None:
        return None, "MISSING"
    return scalar, "SCALAR"


def _finite_probability(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        resolved = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(resolved) or not 0.0 <= resolved <= 1.0:
        return None
    return resolved
