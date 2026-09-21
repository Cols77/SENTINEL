"""Code-owned instructions shared by decision-point advisors."""

DECISION_PROMPT_REVISION = "select_plan.complete_constraints.v3"

SELECT_PLAN_PLAN_CRITERION_PREFIX = (
    "Choose this catalogue plan when the operator request is fully matched by "
    "the future work it will perform: "
)

SELECT_PLAN_ESCALATION_CRITERION = (
    "Choose escalation only when no catalogue plan fully matches every explicit "
    "request constraint, the constraints conflict, or the request lacks enough "
    "detail to choose. Do not escalate merely because the selected work has not "
    "run yet or downstream results are absent."
)

SELECT_PLAN_INSTRUCTION = (
    "Choose a catalogue plan only when its described capabilities fully satisfy every "
    "explicit operator constraint and requested deliverable. Partial, approximate, or "
    "merely closest available coverage does not satisfy the request. Choose escalation "
    "when no candidate fully satisfies it, the constraints conflict, or a required "
    "capability or request fact is unavailable. This decision selects future work: "
    "an absent plan, task, detection, or result in the downstream facts is expected "
    "before execution and is not a reason to escalate. Use only the supplied facts."
)


__all__ = [
    "DECISION_PROMPT_REVISION",
    "SELECT_PLAN_ESCALATION_CRITERION",
    "SELECT_PLAN_INSTRUCTION",
    "SELECT_PLAN_PLAN_CRITERION_PREFIX",
]
