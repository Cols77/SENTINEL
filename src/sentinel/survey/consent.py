# src/sentinel/survey/consent.py
"""Filming consent, enforced rather than documented.

rev 3 R5 replaces this module's plan-body design entirely (design spec
13.4). Rev 2 promised that without consent "person regions are blurred
before the frame is written, so no unblurred image reaches disk or the
provider" -- that guarantee was false and could not be made true in that
shape: the plan body's ``blur_person_regions`` nulled two text fields on
the detection records *after* the frame had already been written and sent,
and a region cannot be blurred before the provider says where it is.

There is no partial path now, because every partial path leaks an
unblurred frame to a third-party API:

| Consent state | Behaviour |
|---|---|
| recorded for this location and date | frames are sent to the provider and retained |
| absent | no frame leaves the process and none is retained; the run ends
  ``CONSENT_REQUIRED`` before any capture is written, and the detector is
  never called (see ``loop.run_survey``) |

``consent_policy`` returns ``RETAIN`` or raises ``ConsentMissing`` -- there
is no third ("blur and proceed") value, and ``blur_person_regions`` is
deleted, not fixed.
"""
from __future__ import annotations


class ConsentMissing(RuntimeError):
    """No filming consent is recorded for this location and date."""


def record_consent(store, location: str, consent_date: str, recorded_by: str) -> None:
    """Records that everyone who may appear in frame at ``location`` on
    ``consent_date`` has been asked and has agreed. Never called on the
    operator's behalf -- this is the operator's own record, made by them or
    at their explicit instruction, never inferred from a previous location
    or date."""
    with store.connection:
        store.connection.execute(
            "INSERT OR REPLACE INTO filming_consent(location, consent_date, recorded_by) "
            "VALUES (?, ?, ?)", (location, consent_date, recorded_by))


def has_consent(store, location: str, consent_date: str) -> bool:
    row = store.connection.execute(
        "SELECT 1 FROM filming_consent WHERE location = ? AND consent_date = ?",
        (location, consent_date)).fetchone()
    return row is not None


def consent_policy(store, location: str, consent_date: str) -> str:
    """``RETAIN`` when consent is recorded for exactly this location and
    date; otherwise raises ``ConsentMissing`` -- there is no other outcome
    (rev 3 R5)."""
    if has_consent(store, location, consent_date):
        return "RETAIN"
    raise ConsentMissing(
        f"no filming consent recorded for {location} on {consent_date}; "
        "the operator must record it before this run can fly")
