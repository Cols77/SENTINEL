"""Code-owned instructions shared by decision-point advisors."""

DECISION_PROMPT_REVISION = "select_plan.complete_constraints.v2"

SELECT_PLAN_INSTRUCTION = (
    "Choose a catalogue plan only when its described capabilities fully satisfy every "
    "explicit operator constraint and requested deliverable. Partial, approximate, or "
    "merely closest available coverage does not satisfy the request. Choose escalation "
    "when no candidate fully satisfies it, the constraints conflict, or a required "
    "capability or fact is unavailable. Use only the supplied facts."
)


__all__ = ["DECISION_PROMPT_REVISION", "SELECT_PLAN_INSTRUCTION"]
