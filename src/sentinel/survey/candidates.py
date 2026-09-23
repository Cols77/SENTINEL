"""Build the complete legal tool calls at each decision point.

Every candidate's ``arguments`` are filled from the store and the site, so a
proposer's whole job is to pick one. That is what lets Jev -- which can
choose among enumerated options but cannot generate a UUID or a sentence --
drive this loop at all.

Rev-4 amendment V2 (CRITICAL): ``review_candidates`` must offer an
``escalate`` candidate too, exactly as ``propose_candidates`` does.
``LegalSet.by_label`` is an unguarded ``next(...)``, so any proposer that
tries to escalate at ``REVIEW_RESULTS`` -- and every one of them can -- would
raise ``StopIteration`` if no escalate candidate exists there. See
``proposer._escalate`` for the matching defensive fallback.
"""
from __future__ import annotations

from sentinel.coordinator.legal_tools import Candidate, LegalSet

from .merge import CONFIRM_MIN_STOPS

PROPOSE_SWEEP = "PROPOSE_SWEEP"
REVIEW_RESULTS = "REVIEW_RESULTS"
ESCALATE_LABEL = "escalate"
REPORT_LABEL = "report"
MAX_FLIGHTS = 2


def _describe(option) -> str:
    return (f"{option.description}; {len(option.stops)} stops at {option.z_cm} cm, "
            f"views {option.viewed_width_cm} cm at {option.range_cm} cm range, "
            f"~{option.estimated_seconds}s, resolves "
            f"{', '.join(sorted(option.resolvable_classes)) or 'nothing'}")


def _escalate_candidate(reason: str) -> Candidate:
    return Candidate("survey.escalate", {"reason": ""}, ESCALATE_LABEL, reason)


def propose_candidates(options) -> LegalSet:
    # An object is confirmed by agreement from CONFIRM_MIN_STOPS viewpoints, so a sweep
    # with fewer stops can confirm nothing on its own: as a FIRST sweep it guarantees a
    # count of zero whatever is in the room. Such options are follow-ups only.
    able = [option for option in options if len(option.stops) >= CONFIRM_MIN_STOPS]
    options = able or list(options)
    candidates = [
        Candidate("survey.propose_sweep",
                  {"option_id": option.option_id, "reason": ""},
                  option.option_id, _describe(option))
        for option in options
    ]
    candidates.append(_escalate_candidate(
        "the request does not name a coverage any option provides, or is self-contradictory"))
    return LegalSet(PROPOSE_SWEEP, tuple(candidates))


def _addresses(option, uncertain) -> list[str]:
    """Which uncertain objects this option would actually view: inside its
    horizontal extent, and at a range where the class resolves."""
    if not option.stops:
        return []
    left = min(x for x, _ in option.stops) - option.viewed_width_cm / 2
    right = max(x for x, _ in option.stops) + option.viewed_width_cm / 2
    out = []
    for item in uncertain:
        if item.get("x_cm") is None:
            continue
        if not (left <= item["x_cm"] <= right):
            continue
        if item["class"] not in option.resolvable_classes:
            continue
        out.append(item["object_id"])
    return out


def review_candidates(results: dict, options) -> LegalSet:
    report = Candidate("survey.submit_report", {}, REPORT_LABEL,
                       "report the counts now, with caveats")
    escalate = _escalate_candidate("the results do not support a confident automated choice")

    if results.get("flights_used", 0) >= MAX_FLIGHTS:
        return LegalSet(REVIEW_RESULTS, (report, escalate))

    uncertain = results.get("uncertain_objects", [])
    candidates = [report, escalate]
    flown = set(results.get("flown_option_ids", ()))
    for option in options:
        if option.option_id in flown:
            continue        # the same stops again add no viewpoint, so nothing to gain
        addressed = _addresses(option, uncertain)
        candidates.append(Candidate(
            "survey.propose_sweep",
            {"option_id": option.option_id, "reason": "", "addresses": addressed},
            f"follow_up:{option.option_id}",
            f"{_describe(option)}; would view {len(addressed)} of "
            f"{len(uncertain)} uncertain objects"))
    return LegalSet(REVIEW_RESULTS, tuple(candidates))
