# src/sentinel/survey/report.py
"""Code checks the report against the store, so the model can explain a
count but never invent one.

rev 3 R4 + rev 4 V5 replace this module's original ``check_report`` body:

- The original matched *any* integer in the report body against a single
  permitted set, so the spec's own prescribed wording ("6 of 8 frames
  analysed") was rejected and the run ended with no report at all. Only
  numbers immediately adjacent to a class noun are checked now.
- The class-noun regex is generated from an explicit ``_CLASS_FORMS`` map
  rather than hand-written, because a substring check (``"person" in
  body.lower()``) silently misses the plural "people".
- rev 4 V8a also amends ``class_counts``: the original used
  ``min(option.range_cm for option in options)``, which is the
  observability of a sweep that may *never have flown*. It now derives the
  range from the sweeps the store shows were actually flown for this run.
- rev 4 V6 amends ``class_counts`` for ``person``: people are counted as
  the largest number seen in any single frame (design spec 4.3), not one
  object per sighting -- ``merge.py`` deliberately routes each person
  sighting to its own single-sighting object (people move between stops
  and are never position-merged), so reading the ``objects`` table
  directly would over-count a person seen from three stops as three.
"""
from __future__ import annotations

import re



class ReportMismatch(ValueError):
    """The wording claims something the stored counts do not support."""


_EXACTNESS = re.compile(r"\bexactly\b", re.IGNORECASE)

#: rev 4 V5: explicit class-forms map, so the generated regex recognises
#: plurals (and, for person, the irregular plural "people") instead of a
#: substring check that misses them.
_CLASS_FORMS = {
    "computer": ("computer", "computers"),
    "person": ("person", "persons", "people"),
    "phone": ("phone", "phones"),
    "pen": ("pen", "pens"),
}

_FORM_TO_CLASS = {form: class_name for class_name, forms in _CLASS_FORMS.items() for form in forms}

#: rev 3 R4: match only a number immediately followed by a class noun (in
#: any of its recognised forms), never a bare integer anywhere in the body.
#: Longer forms are tried first so "phones" is not cut short by "phone".
_CLASS_NUMBER = re.compile(
    r"(\d+)\s+(" + "|".join(sorted((re.escape(form) for form in _FORM_TO_CLASS), key=len, reverse=True))
    + r")\b", re.IGNORECASE)


def _person_count(store, run_id: str) -> int:
    """rev 4 V6: the largest number of person sightings seen in any single
    frame, read straight from ``sightings`` -- never from ``objects``, whose
    per-sighting person rows would sum instead of max."""
    rows = store.connection.execute(
        "SELECT COUNT(*) AS n FROM sightings g JOIN frames f ON f.frame_id = g.frame_id "
        "JOIN sweeps s ON s.sweep_id = f.sweep_id "
        "WHERE s.run_id = ? AND g.class = 'person' GROUP BY g.frame_id", (run_id,))
    counts = [row["n"] for row in rows]
    return max(counts) if counts else 0


def class_counts(store, run_id: str, options) -> list[dict]:
    """rev 4 V8a: range comes from the sweeps this run actually flew, not
    from every option that was ever offered (some of which may never have
    flown, and would misrepresent what the run actually observed)."""
    flown_option_ids = {row["option_id"] for row in store.connection.execute(
        "SELECT DISTINCT option_id FROM sweeps WHERE run_id = ? AND status != 'ABORTED'",
        (run_id,))}
    flown = [option for option in options if option.option_id in flown_option_ids] or list(options)
    # rev 5: read from the closest flown option itself, which was generated
    # with the run's camera model, rather than recomputed under a default one.
    resolvable = set(min(flown, key=lambda option: option.range_cm).resolvable_classes)
    stored = store.counts(run_id)
    out = []
    for name in ("computer", "person", "phone", "pen"):
        if name not in resolvable:
            out.append({"class": name, "confirmed": 0, "uncertain": 0, "method": "NOT_OBSERVABLE"})
            continue
        if name == "person":
            # rev 4 V6: person is always a single-view lower bound, counted
            # as the max seen in any one frame -- never MERGED_MULTI_VIEW.
            out.append({"class": name, "confirmed": 0, "uncertain": _person_count(store, run_id),
                        "method": "SINGLE_VIEW_LOWER_BOUND"})
            continue
        entry = stored.get(name, {"confirmed": 0, "uncertain": 0})
        out.append({"class": name, "confirmed": entry["confirmed"], "uncertain": entry["uncertain"],
                    "method": "MERGED_MULTI_VIEW"})
    return out


def check_report(body: str, counts: list[dict]) -> None:
    if not body or not body.strip():
        raise ReportMismatch("the report is empty")

    permitted_by_class: dict[str, set[int]] = {}
    not_observable: set[str] = set()
    lower_bound_classes: set[str] = set()
    for entry in counts:
        class_name = entry["class"]
        if entry["method"] == "NOT_OBSERVABLE":
            not_observable.add(class_name)
            continue
        permitted_by_class[class_name] = {0, entry["confirmed"], entry["confirmed"] + entry["uncertain"]}
        if entry["method"] == "SINGLE_VIEW_LOWER_BOUND":
            lower_bound_classes.add(class_name)

    for match in _CLASS_NUMBER.finditer(body):
        number = int(match.group(1))
        class_name = _FORM_TO_CLASS[match.group(2).lower()]
        if class_name in not_observable:
            raise ReportMismatch(
                f"{class_name} is not observable at this range; the report may not give it a count")
        permitted = permitted_by_class.get(class_name, {0})
        if number not in permitted:
            raise ReportMismatch(
                f"the report states {number} {class_name}, which the store does not support "
                f"(permitted: {sorted(permitted)})")

    if _EXACTNESS.search(body):
        for class_name in lower_bound_classes:
            forms = _CLASS_FORMS[class_name]
            if any(re.search(rf"\b{re.escape(form)}\b", body, re.IGNORECASE) for form in forms):
                raise ReportMismatch(
                    f"{class_name} is a single-view lower bound and may not be reported as exact")


def unconfirmed_caveat(body: str, counts: list[dict]) -> str | None:
    """What a report left out about objects the store could not confirm.

    "I found 0 computers" with two unconfirmed ones on record is true to the
    letter and misleading in effect. A writer is not rejected for it -- the
    accepted-report contract is unchanged -- but the omission does not reach
    the operator either: the loop appends this sentence, written by code from
    the store, whenever the writer did not state the total itself."""
    stated: dict[str, set[int]] = {}
    for match in _CLASS_NUMBER.finditer(body):
        stated.setdefault(_FORM_TO_CLASS[match.group(2).lower()], set()).add(int(match.group(1)))
    left_out = []
    for entry in counts:
        if entry["method"] != "MERGED_MULTI_VIEW" or not entry["uncertain"]:
            continue
        if entry["confirmed"] + entry["uncertain"] in stated.get(entry["class"], set()):
            continue
        noun = _CLASS_FORMS[entry["class"]][-1] if entry["uncertain"] != 1 else entry["class"]
        left_out.append(f"{entry['uncertain']} more possible {noun}")
    if not left_out:
        return None
    return "Not confirmed: " + ", ".join(left_out) + "."

