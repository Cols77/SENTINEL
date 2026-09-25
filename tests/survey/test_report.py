# tests/survey/test_report.py
"""rev 3 R4 + rev 4 V5 replace this module's check_report entirely: it must
match only numbers adjacent to a class noun (a regex generated from an
explicit class-forms map, so "people" is recognised for "person"), and must
allow the writer one rewrite before REPORT_REJECTED (tested via loop.py).
"""
import pytest

from sentinel.survey.envelope import SweepEnvelope
from sentinel.survey.options import generate_options
from sentinel.survey.report import ReportMismatch, check_report, class_counts
from sentinel.survey.store import SurveyStore

COUNTS = [
    {"class": "computer", "confirmed": 6, "uncertain": 1, "method": "MERGED_MULTI_VIEW"},
    {"class": "person", "confirmed": 2, "uncertain": 0, "method": "SINGLE_VIEW_LOWER_BOUND"},
    {"class": "mug", "confirmed": 0, "uncertain": 0, "method": "NOT_OBSERVABLE"},
]


def test_a_report_matching_the_store_is_accepted():
    check_report("At least 6 computers, possibly 7. At least 2 people. "
                 "Mugs are not observable at this range.", COUNTS)


def test_a_number_the_store_does_not_hold_is_rejected():
    with pytest.raises(ReportMismatch) as excinfo:
        check_report("I counted 9 computers.", COUNTS)
    assert "9" in str(excinfo.value)


def test_claiming_certainty_about_a_not_observable_class_is_rejected():
    """rev 4 V5: the original test's "3 mugs" already failed the (now
    removed) blanket-digit check and so never reached the mug-specific
    rule it names. "2 mugs" is otherwise a permitted-looking count, so this
    only passes if the NOT_OBSERVABLE class rule itself fires."""
    with pytest.raises(ReportMismatch):
        check_report("6 computers, 2 people and 2 mugs.", COUNTS)


def test_a_lower_bound_class_may_not_be_stated_as_exact():
    with pytest.raises(ReportMismatch):
        check_report("There are exactly 2 people.", COUNTS)


def test_an_empty_report_is_rejected():
    with pytest.raises(ReportMismatch):
        check_report("   ", COUNTS)


def test_a_number_not_adjacent_to_a_class_noun_is_never_checked():
    """rev 3 R4: the original check_report rejected ANY integer not in the
    permitted set, which rejects the spec's own prescribed wording. Only
    numbers adjacent to a class noun are checked now."""
    check_report("At least 6 computers, possibly 7. At least 2 people. "
                 "6 of 8 frames analysed. Mugs are not observable at this range.", COUNTS)


def test_the_plural_people_is_recognised_as_a_person_count_not_only_person_itself():
    """rev 4 V5: entry["class"] in body.lower() is a substring check that
    fails for "person" against "people" -- prove the fix actually maps the
    plural form, by making a wrong count attached to "people" get caught."""
    with pytest.raises(ReportMismatch) as excinfo:
        check_report("I saw 9 people.", COUNTS)
    assert "9" in str(excinfo.value)


ENVELOPE = SweepEnvelope(-150, 150, 0, 100, 80, 180, 300)
OPTIONS = generate_options(ENVELOPE)


def _detection(class_name="person", bbox=None):
    return {"class": class_name, "matched_prompt": class_name, "raw_score": None,
            "score_kind": "VLM_CONFIDENCE_BAND",
            "bbox": bbox or {"x": 460, "y": 500, "width": 40, "height": 80},
            "description": "a person", "provider_item_id": None}


def test_people_are_counted_as_the_max_seen_in_any_one_frame_not_one_per_sighting(tmp_path):
    """rev 4 V6: three frames, each seeing exactly one person, must report a
    count of 1 -- not 3, which is what one-object-per-sighting would give."""
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the people")
    option = OPTIONS[0]
    sweep_id = store.add_sweep("RUN1", option.option_id, stops=3)
    for index in range(3):
        frame_id = store.add_frame(
            sweep_id, stop_index=index, look_deg=0, x_cm=float(index * 75), y_cm=0.0,
            z_cm=option.z_cm, heading_deg=0.0, position_kind="COMMANDED",
            artifact_id=f"A{index}", sha256="0" * 64, image_path=f"frames/f{index}.png",
            delta_z_cm=55.0)
        store.add_sighting(frame_id, _detection(), band="high")
        store.set_frame_analysis(frame_id, "OK")

    counts = class_counts(store, "RUN1", OPTIONS)
    person = next(entry for entry in counts if entry["class"] == "person")
    assert person["method"] == "SINGLE_VIEW_LOWER_BOUND"
    assert person["confirmed"] + person["uncertain"] == 1


def test_class_counts_uses_the_flown_sweeps_range_not_every_offered_option(tmp_path):
    """rev 4 V8a: ``class_counts`` used to take ``min(option.range_cm for
    option in options)`` over every offered option, including ones that
    never flew. Here a close option that would resolve phones exists but
    was never flown -- only the far option (which does not resolve phones)
    was actually flown -- so phone must come back NOT_OBSERVABLE."""
    from sentinel.survey.envelope import SweepOption
    from sentinel.survey.observability import resolvable_classes as _resolvable

    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count the phones")
    far = SweepOption("FAR_ONLY", "flew far", ((0, 0),), 120, (0,), 200, 10,
                      _resolvable(600.0), 600)
    near_never_flown = SweepOption("NEAR_NEVER_FLOWN", "never flew this close", ((0, 0),), 120,
                                   (0,), 200, 10, _resolvable(150.0), 150)
    assert "phone" not in far.resolvable_classes, "fixture must not already resolve phones far out"
    assert "phone" in near_never_flown.resolvable_classes, "fixture must resolve phones up close"

    store.add_sweep("RUN1", far.option_id, stops=1)

    counts = class_counts(store, "RUN1", (far, near_never_flown))
    phone = next(entry for entry in counts if entry["class"] == "phone")
    assert phone["method"] == "NOT_OBSERVABLE"
