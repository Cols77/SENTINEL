# src/sentinel/survey/loop.py
"""The survey run.

Eight steps, two decision points, at most two flights (design spec 3.3).
The policy chooses among pre-built candidate calls; this module submits
nothing the operator has not approved and writes no number the store does
not hold.

Rev-3/rev-4 corrections against the plan body's original code (see the
plan's "Rev 3 amendments" and "Rev 4 amendments" sections -- this module
implements those, not the task bodies' original code):

R3: the plan body's ``_fly_and_ingest`` set ``row["band"] = "high"`` on
every sighting read back from the store, so the non-low-band CONFIRM gate
in ``merge.py`` was only ever exercised in unit tests, never through a real
ingest. The band is stored at ingest time (``detector.normalise`` already
returns it alongside each detection -- see ``DetectionBatch.bands``) and
read back unchanged, never overwritten.

R7: ``on_approval`` receives the full rendered ``Candidate.description``
(stops, extent, range, resolvable classes, duration) alongside the tool
call, not just ``{option_id, reason}``. Approving a bare token is not
approval.

R4: the writer gets exactly one rewrite attempt before ``REPORT_REJECTED``.

V8a: ``class_counts`` (in ``report.py``) already derives its range from the
sweeps this run actually flew; nothing here re-derives it separately.

V8b: rev 3's R5 (consent.py, wired in by Task 8) means there is no longer
any non-retain path that calls the provider at all, so ``batch.raw`` is
always stored -- never conditioned on a retention flag.

V8f: ``survey.get_site`` and ``survey.get_results`` are implemented here as
real functions, and ``run_survey`` routes through ``get_results`` itself
rather than a private duplicate of the same query.

R8/V8c: ``decision_quality`` was specified in three places (this module's
docstring, the design spec, and ``eval.score_episode``) and computed in
none of them -- ``score_episode`` read an ``outcome["decision_quality"]``
key this module never set, so the metric was always ``None``. It is now
recorded at every ``REVIEW_RESULTS`` turn: the uncertain set at that turn,
the ``addresses`` list of the candidate the policy chose, and the
``addresses`` list of whichever candidate addressed the most uncertain
objects (the ``review_candidates`` are already built with that ``addresses``
argument -- see ``candidates.py``). A turn scores ``1.0`` when the chosen
candidate addresses at least as many uncertain objects as the best
available one did (which subsumes "addresses every uncertain object"), and
``0.0`` when a follow-up was available that addressed strictly more.
Turns with no uncertain objects contribute nothing. The run's
``decision_quality`` is the minimum over every scored turn, or ``None`` if
no review turn ever had an uncertain object to resolve.

R8: ``dropped_labels`` (``detector.normalise``'s untraceable-label list, rev
3 R6) is accumulated across every frame this run ingests and returned on
the outcome, so ``eval.score_episode`` can attribute an undercount to "the
provider used a label the class map does not know" rather than guessing.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .candidates import (ESCALATE_LABEL, MAX_FLIGHTS, REPORT_LABEL, propose_candidates,
                         review_candidates)
from .consent import ConsentMissing, consent_policy
from .context import SurveyContext
from .merge import merge_sightings
from .observability import SYNTHETIC_LEVEL, CameraModel
from .proposer import chosen_label
from .report import ReportMismatch, check_report, class_counts, unconfirmed_caveat
from .read_model import SurveyReadModel
from sentinel.mission_observer import (AbortEvent, DetectorResultEvent, FrameCapturedEvent,
                                        MergeEvent, MissionObserver, PublicEscalationEvent,
                                        ReportEvent, RunnerLifecycleEvent, notify)

#: The camera's altitude above the plane its subjects rest on (design spec
#: 5.2's worked example: Delta_z = 55 cm, D = 3 m -> v ~ 125 px). Fixed for
#: every frame in this replay-driven system; a future measured-altitude
#: flight port would carry its own per-frame value instead.
DELTA_Z_CM = 55.0


def get_site(options) -> dict[str, Any]:
    """rev 4 V8f: the tool surface the spec (section 3.3, step 2) advertises
    but the plan body never implemented -- the perimeter every offered
    option's stops actually span, plus each option's own metrics."""
    xs = [x for option in options for x, _y in option.stops]
    ys = [y for option in options for _x, y in option.stops]
    zs = [option.z_cm for option in options]
    perimeter = {
        "min_x_cm": min(xs) if xs else None, "max_x_cm": max(xs) if xs else None,
        "min_y_cm": min(ys) if ys else None, "max_y_cm": max(ys) if ys else None,
        "min_z_cm": min(zs) if zs else None, "max_z_cm": max(zs) if zs else None,
    }
    return {
        "perimeter": perimeter,
        "options": [
            {"option_id": option.option_id, "description": option.description,
             "stops": len(option.stops), "z_cm": option.z_cm,
             "viewed_width_cm": option.viewed_width_cm,
             "estimated_seconds": option.estimated_seconds,
             "resolvable_classes": list(option.resolvable_classes),
             "range_cm": option.range_cm}
            for option in options
        ],
    }


def get_results(store, run_id: str, options) -> dict[str, Any]:
    """rev 4 V8f: counts with bands and methods, the uncertain objects, the
    unanalysed-frame count, and per-class observability (design spec 3.3,
    step 6) -- a real function, not private logic the loop alone can see."""
    objects = [dict(row) for row in store.connection.execute(
        "SELECT * FROM objects WHERE run_id = ?", (run_id,))]
    return {
        "counts": store.counts(run_id),
        "class_counts": class_counts(store, run_id, options),
        "uncertain_objects": [o for o in objects if o["band"] == "UNCERTAIN"],
        "unanalysed": store.unanalysed_count(run_id),
        "flights_used": store.flights_used(run_id),
        "flown_option_ids": sorted({row["option_id"] for row in store.connection.execute(
            "SELECT option_id FROM sweeps WHERE run_id = ? AND status != 'ABORTED'", (run_id,))}),
    }


def _fly_and_ingest(store, run_id, option, flight, detector,
                    camera: CameraModel = SYNTHETIC_LEVEL, emit=None,
                    observer: MissionObserver | None = None) -> tuple[str, ...]:
    """Returns every raw label this sweep's frames could not map to a known
    class (rev 3 R6's ``dropped``), in the order seen, duplicates included --
    the caller (``run_survey``) accumulates these across the whole run for
    R8's ``dropped_labels`` attribution signal."""
    sweep_id = store.add_sweep(run_id, option.option_id, len(option.stops))
    if emit:
        emit("SWEEP_STARTED", {"option_id": option.option_id, "sweep_id": sweep_id})
    dropped: list[str] = []
    for frame in flight.fly(option):
        # Task 11/V12: a real flight port stamps its own per-frame delta_z
        # (height above the tape-measured surface); a replay frame with no
        # such field (every fixture written before Task 11) falls back to
        # the one run-level constant exactly as before.
        delta_z_cm = frame.delta_z_cm if frame.delta_z_cm is not None else DELTA_Z_CM
        frame_id = store.add_frame(
            sweep_id, stop_index=frame.stop_index, look_deg=frame.look_deg,
            x_cm=frame.x_cm, y_cm=frame.y_cm, z_cm=frame.z_cm,
            heading_deg=frame.heading_deg, position_kind=frame.position_kind,
            artifact_id=frame.artifact_id, sha256=frame.sha256,
            image_path=frame.image_path, delta_z_cm=delta_z_cm,
            tolerance=frame.tolerance)
        if emit:
            emit("FRAME_CAPTURED", {"frame_id": frame_id, "artifact_id": frame.artifact_id})
        notify(observer, FrameCapturedEvent(
            run_id=run_id, frame_id=frame_id, artifact_id=frame.artifact_id,
            image_path=frame.image_path, sha256=frame.sha256,
            pose={"x_cm": frame.x_cm, "y_cm": frame.y_cm, "z_cm": frame.z_cm,
                  "heading_deg": frame.heading_deg,
                  "position_kind": frame.position_kind}))
        # LiveFlight owns a separate follower process.  Its cache is the only
        # legal source for a real frame: consulting ``detector`` here would
        # pay for the same provider request a second time.  Rehearsal and
        # replay flights do not expose this hook and retain their old path.
        notify(observer, DetectorResultEvent(run_id=run_id, frame_id=frame_id,
                                             status="DETECTING"))
        started = time.perf_counter()
        try:
            cached = getattr(flight, "detection_for", None)
            if callable(cached):
                batch = cached(frame, option.resolvable_classes)
            else:
                batch = detector.detect(frame.image_path, option.resolvable_classes)
        except Exception as exc:
            notify(observer, DetectorResultEvent(
                run_id=run_id, frame_id=frame_id, status="ERROR",
                latency_ms=(time.perf_counter() - started) * 1000, error=str(exc)))
            raise
        cached_latency = (batch.raw or {}).get("detect_s") if callable(getattr(flight, "detection_for", None)) else None
        latency_ms = (float(cached_latency) * 1000.0
                      if cached_latency is not None else (time.perf_counter() - started) * 1000)
        notify(observer, DetectorResultEvent(
            run_id=run_id, frame_id=frame_id, status=batch.status,
            detections=tuple(batch.detections),
            latency_ms=latency_ms,
            error=(batch.raw or {}).get("error") or getattr(batch, "error", None)))
        store.set_frame_analysis(frame_id, "OK" if batch.status == "OK" else batch.status)
        # R3: the band detector.normalise attached to each detection is
        # stored as-is -- never overwritten to a fixed value.
        # V8b: batch.raw is always stored; rev 3's R5 leaves no path that
        # calls the provider without also being a retain path.
        bands = batch.bands or ("low",) * len(batch.detections)
        for detection, band in zip(batch.detections, bands):
            store.add_sighting(frame_id, detection, band, raw_response=batch.raw)
        dropped.extend(batch.dropped)
    if emit:
        emit("ANALYSIS_COMPLETED", {"sweep_id": sweep_id})
    store.set_sweep_status(sweep_id, "FLOWN")

    rows = store.sightings(run_id)
    for row in rows:
        row["bbox"] = json.loads(row["bbox_json"]) if row["bbox_json"] else None
    store.set_objects(run_id, merge_sightings(rows, delta_z_cm=DELTA_Z_CM, camera=camera))
    if emit:
        emit("MERGE_COMPLETED", {"sweep_id": sweep_id})
    public = SurveyReadModel(store).read(run_id)
    notify(observer, MergeEvent(run_id=run_id, sweep_id=sweep_id,
                                objects=tuple(public["objects"] if public else ())))
    return tuple(dropped)


def _approval_call(context: SurveyContext, result: dict, label: str) -> dict[str, Any]:
    """R7: the operator sees the candidate's full rendered description --
    stops, extent, range, resolvable classes, duration -- not just the bare
    tool call. Approving a token alone is not approval."""
    call = dict(result["tool_calls"][0])
    try:
        call["description"] = context.legal.by_label(label).description
    except StopIteration:
        call["description"] = ""
    return call


def _review_decision_quality(review, uncertain: list[dict], label: str | None) -> float | None:
    """R8/V8c. ``review`` is the ``REVIEW_RESULTS`` ``LegalSet`` the policy
    just decided over -- its ``survey.propose_sweep`` candidates each carry
    an ``addresses`` argument (``candidates._addresses``: the uncertain
    ``object_id``s that follow-up would actually view). ``None`` when
    nothing was uncertain at this turn, since there is nothing to score;
    otherwise ``1.0`` when the chosen candidate addresses at least as many
    uncertain objects as the best-available follow-up did, ``0.0`` when a
    follow-up was available that addressed strictly more (submitting the
    report, escalating, or picking a weaker follow-up while a stronger one
    was on the table)."""
    if not uncertain:
        return None
    follow_ups = [c for c in review.candidates if c.tool_name == "survey.propose_sweep"]
    best_addressed = max((len(c.arguments.get("addresses", ())) for c in follow_ups), default=0)
    # ``label`` is ``chosen_label``'s bare ``option_id`` (it reads
    # ``arguments["option_id"]`` for every ``survey.propose_sweep`` call, at
    # both decision points -- see ``proposer.chosen_label``). A follow-up
    # candidate's own ``.label`` is prefixed ``follow_up:<option_id>``
    # (``candidates.review_candidates``), so matching on ``c.label`` here
    # never finds the chosen candidate and silently forces every follow-up
    # choice to score as if nothing were addressed. Match on the bare
    # ``option_id`` each candidate actually carries in its arguments instead.
    chosen = next((c for c in follow_ups if c.arguments.get("option_id") == label), None)
    chosen_addressed = len(chosen.arguments.get("addresses", ())) if chosen is not None else 0
    return 1.0 if chosen_addressed >= best_addressed else 0.0


def _with_caveat(body: str, results) -> str:
    caveat = unconfirmed_caveat(body, results["class_counts"])
    return body if caveat is None else f"{body.rstrip()} {caveat}"


def _write_report(writer, results) -> tuple[str | None, ReportMismatch | None]:
    """R4: the writer is allowed exactly one rewrite before the run gives
    up and reports REPORT_REJECTED."""
    body = writer(results)
    try:
        check_report(body, results["class_counts"])
        return _with_caveat(body, results), None
    except ReportMismatch as mismatch:
        retry_results = {**results, "rejected_report": body, "rejection_reason": str(mismatch)}
        body = writer(retry_results)
        try:
            check_report(body, results["class_counts"])
            return _with_caveat(body, results), None
        except ReportMismatch as second_mismatch:
            return None, second_mismatch


def run_survey(*, store, run_id: str, request_text: str, options, policy,
               flight, detector, on_approval, writer,
               location: str | None = None, consent_date: str | None = None,
               max_flights: int = MAX_FLIGHTS,
               camera: CameraModel = SYNTHETIC_LEVEL, emit=None,
               observer: MissionObserver | None = None) -> dict[str, Any]:
    """rev 3 R5 (Task 8): when ``location`` is given, filming consent is
    gated *before anything else happens* -- before ``store.create_run``,
    before any sweep, before the detector is ever touched. Absent consent,
    the run ends ``CONSENT_REQUIRED`` and nothing downstream runs at all.
    ``location`` defaults to ``None`` so hermetic/replay runs over
    already-consented frames (design spec 13.4) are unaffected by the gate,
    matching every test written before this parameter existed.

    rev 5: ``camera`` is the model the frames were *produced* with. It
    defaults to the synthetic level camera because every scene in this
    repository is rendered with it; anything handling real Tello pixels
    passes ``observability.TELLO_MEASURED`` (coherence spec 10.5). The
    ``options`` passed in must have been generated with the same model."""
    if location is not None:
        try:
            consent_policy(store, location, consent_date)
        except ConsentMissing as missing:
            return {"status": "CONSENT_REQUIRED", "flights": 0, "counts": {}, "report": None,
                    "escalations": [{"point": "CONSENT", "error": str(missing)}],
                    "decision_quality": None, "dropped_labels": ()}

    store.create_run(run_id, request_text)
    notify(observer, RunnerLifecycleEvent(run_id=run_id, stage="SURVEY", status="STARTED"))
    request = {"run_id": run_id, "request_text": request_text, "site": get_site(options)}
    escalations: list[dict] = []
    # R8/V8c: one entry per REVIEW_RESULTS turn that had an uncertain object
    # to resolve -- see _review_decision_quality. R8: every dropped label
    # (rev 3 R6) seen across every frame this run ingests.
    decision_qualities: list[float] = []
    dropped_labels: list[str] = []

    legal = propose_candidates(options)
    result = policy.decide(request, SurveyContext(run_id, legal, {}))
    label = chosen_label(result)
    if label == ESCALATE_LABEL or label is None:
        escalations.append({"point": legal.decision_point, "label": ESCALATE_LABEL,
                            "result": result})
        notify(observer, PublicEscalationEvent(run_id=run_id, point=legal.decision_point,
                                               reason="policy escalation", payload=result))
        return {"status": "ESCALATED", "flights": 0, "counts": {}, "report": None,
                "escalations": escalations, "decision_quality": None, "dropped_labels": ()}

    context = SurveyContext(run_id, legal, {})
    while True:
        call = _approval_call(context, result, label)
        if not on_approval(call):
            notify(observer, AbortEvent(run_id=run_id, stage="APPROVAL", reason="operator refused"))
            return {"status": "REFUSED", "flights": store.flights_used(run_id),
                    "counts": store.counts(run_id), "report": None,
                    "escalations": escalations,
                    "decision_quality": min(decision_qualities) if decision_qualities else None,
                    "dropped_labels": tuple(dict.fromkeys(dropped_labels))}

        option = next(o for o in options if o.option_id == call["arguments"]["option_id"])
        try:
            dropped_labels.extend(_fly_and_ingest(store, run_id, option, flight, detector,
                                                  camera, emit, observer))
        except Exception as exc:
            notify(observer, AbortEvent(run_id=run_id, stage="FLIGHT", reason=str(exc)))
            raise

        results = get_results(store, run_id, options)
        if results["flights_used"] >= max_flights:
            break
        review = review_candidates(results, options)
        context = SurveyContext(run_id, review, results)
        result = policy.decide(request, context)
        label = chosen_label(result)
        quality = _review_decision_quality(review, results["uncertain_objects"], label)
        if quality is not None:
            decision_qualities.append(quality)
        if label is None or label == REPORT_LABEL:
            break
        if label == ESCALATE_LABEL:
            escalations.append({"point": review.decision_point, "label": ESCALATE_LABEL,
                                "result": result})
            notify(observer, PublicEscalationEvent(run_id=run_id, point=review.decision_point,
                                                   reason="policy escalation", payload=result))
            break

    decision_quality = min(decision_qualities) if decision_qualities else None
    dropped = tuple(dict.fromkeys(dropped_labels))
    results = get_results(store, run_id, options)
    body, mismatch = _write_report(writer, results)
    if mismatch is not None:
        notify(observer, ReportEvent(run_id=run_id, status="REJECTED", counts=results["counts"]))
        return {"status": "REPORT_REJECTED", "flights": results["flights_used"],
                "counts": results["counts"], "report": None,
                "escalations": escalations + [{"point": "REPORT", "error": str(mismatch)}],
                "decision_quality": decision_quality, "dropped_labels": dropped}
    store.add_report(run_id, body, results["class_counts"])
    if emit:
        emit("REPORT_ACCEPTED", {"summary": "Validated evidence-backed report"})
    notify(observer, ReportEvent(run_id=run_id, status="ACCEPTED", body=body,
                                 counts=results["counts"]))
    return {"status": "REPORTED", "flights": results["flights_used"],
            "counts": results["counts"], "report": body, "escalations": escalations,
            "decision_quality": decision_quality, "dropped_labels": dropped}
