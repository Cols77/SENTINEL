# tests/survey/test_consent.py
"""rev 3 R5 replaces this module's plan-body design entirely: rev 2's
promise that person regions were blurred before a frame was written was
false (the implementation nulled two text fields *after* the frame had
already been written and sent, and a region cannot be blurred before the
provider says where it is). ``blur_person_regions`` is deleted, not fixed.

There is no third value and no partial path any more:

| Consent state | Behaviour |
|---|---|
| recorded for this location and date | frames sent and retained |
| absent | no frame leaves the process, none is retained; the run ends
  CONSENT_REQUIRED before any capture is written |

``consent_policy`` returns ``RETAIN`` or raises ``ConsentMissing`` -- never
a "blur and proceed" third option.
"""
import pytest

from sentinel.survey.consent import ConsentMissing, consent_policy, has_consent, record_consent
from sentinel.survey.store import SurveyStore


def _store(tmp_path):
    store = SurveyStore.open(tmp_path / "s.sqlite3")
    store.create_run("RUN1", "count")
    return store


def test_without_recorded_consent_the_gate_refuses_rather_than_blurring(tmp_path):
    """rev 3 R5: there is no BLUR fallback any more -- absent consent always
    raises, full stop."""
    with pytest.raises(ConsentMissing):
        consent_policy(_store(tmp_path), "garden", "2026-09-21")


def test_recorded_consent_permits_the_run(tmp_path):
    store = _store(tmp_path)
    record_consent(store, "garden", "2026-09-21", recorded_by="operator")
    assert consent_policy(store, "garden", "2026-09-21") == "RETAIN"


def test_consent_does_not_carry_to_another_location(tmp_path):
    store = _store(tmp_path)
    record_consent(store, "garden", "2026-09-21", recorded_by="operator")
    assert not has_consent(store, "lecture_room", "2026-09-21")
    with pytest.raises(ConsentMissing):
        consent_policy(store, "lecture_room", "2026-09-21")


def test_consent_does_not_carry_to_another_date(tmp_path):
    store = _store(tmp_path)
    record_consent(store, "garden", "2026-09-21", recorded_by="operator")
    with pytest.raises(ConsentMissing):
        consent_policy(store, "garden", "2026-09-25")


def test_the_consent_missing_error_names_the_location_and_date(tmp_path):
    with pytest.raises(ConsentMissing) as excinfo:
        consent_policy(_store(tmp_path), "garden", "2026-09-21")
    assert "garden" in str(excinfo.value)
    assert "2026-09-21" in str(excinfo.value)


def test_blur_person_regions_is_deleted_not_just_unused():
    """rev 3 R5: blur_person_regions is deleted, not fixed -- its
    rev-2 guarantee could not be made true in that shape."""
    import sentinel.survey.consent as consent_module
    assert not hasattr(consent_module, "blur_person_regions")
