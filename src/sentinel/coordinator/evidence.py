"""Hash/provenance-bound perception outcome policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sentinel.contracts import ContractValidationError, validate_document
from sentinel.contracts.validators import ContractViolation, validate_bbox


@dataclass(frozen=True)
class EvidenceDecision:
    status: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "reason": self.reason}


class EvidencePolicy:
    """Apply provider-error precedence and fail closed on stale evidence."""

    def evaluate(
        self,
        result: Mapping[str, Any],
        *,
        artifact_sha256: str,
        threshold: float,
        judged: bool = True,
    ) -> EvidenceDecision:
        # Provider failure is a terminal operational fault, not a scoreless
        # result. Apply this precedence after the cheap provenance bindings so
        # an unrelated artifact cannot manufacture a failure outcome.
        if result.get("artifact_sha256") != artifact_sha256:
            return EvidenceDecision("NEEDS_REVIEW", "artifact hash does not match the selected artifact")
        if judged and result.get("source_kind") != "LIVE":
            return EvidenceDecision("NEEDS_REVIEW", "judged evidence must be LIVE")
        if result.get("status") == "PROVIDER_ERROR":
            return EvidenceDecision("FAILED", "provider error precedes score interpretation")
        try:
            validate_document(result)
            validate_bbox(result.get("bbox_xyxy_normalized"))
        except (ContractValidationError, ContractViolation) as exc:
            return EvidenceDecision("NEEDS_REVIEW", f"invalid evidence: {exc}")
        score = result.get("raw_score")
        if result.get("status") in {"UNSCORED", "NOT_DETECTED"} or score is None or score < threshold:
            return EvidenceDecision("NEEDS_REVIEW", "scoreless or below-threshold evidence")
        return EvidenceDecision("COMPLETED", "evidence is provenance-bound and above threshold")
