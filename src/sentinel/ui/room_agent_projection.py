"""Fold room-session ledger events into Mission Control's room-agent view.

Only allowlisted, display-safe facts leave this module: pose, localization
state and fix age, regions, captures and detections, every re-anchor or
recovery step (each yaw rotation with its heading, each tag burst with the
tags seen), Jev's chosen action with a one-line reason each turn, the final
answer, return home and landing. Frame paths, hashes of private files,
provider payloads and credentials are never copied.
"""
from __future__ import annotations

from copy import deepcopy
import math
import threading

from sentinel.room.association import associate_static
from sentinel.room.grounding import FrameCountEvidence
from sentinel.room.goals import GOAL_ACT_AT

HOME_TOLERANCE_CM = 25.0
_REGION_LABELS = {"center": "Center", "left": "Left", "right": "Right"}
#: Mirrors ``sentinel.room.candidates.CONFIDENCE_GATE_REFUSALS``.
_CHOICE_GATE_REFUSALS = frozenset({"LOW_CONFIDENCE", "THIN_MARGIN", "MALFORMED_ANSWER", "ILLEGAL_OR_MALFORMED",
                                   "MISSING_OR_NON_MAX_SELECTION", "PROVIDER_ERROR", "STALE_DECISION"})
_PLURAL = {"computer": "computers", "phone": "phones", "person": "people", "pen": "pens"}


def region_label(pose_id: str) -> str:
    head = pose_id.split("-", 1)[0]
    return f"{_REGION_LABELS.get(head, head.title())} region"


def _candidate_label(candidate_id: str) -> str:
    if candidate_id.startswith("OBSERVE:"):
        return f"Observe {region_label(candidate_id.rsplit(':', 1)[-1]).lower()}"
    if candidate_id.startswith("REANCHOR"):
        return "Re-anchor on the mapped tags"
    if candidate_id.startswith("ANSWER"):
        return "Answer"
    return candidate_id.replace("_", " ").capitalize()


def answer_summary(count_projection, class_order=()) -> str | None:
    """``phones: 1 confirmed, 1 uncertain; computers: 2 confirmed`` (request order)."""
    parts = []
    order = {name: index for index, name in enumerate(class_order)}
    for objective in count_projection or ():
        counts = sorted(objective.get("counts", ()), key=lambda c: order.get(c.get("class_name"), len(order)))
        for count in counts:
            name = _PLURAL.get(count.get("class_name"), count.get("class_name"))
            text = f"{name}: {count.get('confirmed', 0)} confirmed"
            if count.get("uncertain"):
                text += f", {count['uncertain']} uncertain"
            if count.get("unplaced"):
                text += f", {count['unplaced']} unplaced"
            parts.append(text)
    return "; ".join(parts) or None


class RoomAgentProjection:
    """Incremental, thread-safe fold of one session's events."""

    def __init__(self, *, home_xy_cm=(250.0, 190.0)):
        self._lock = threading.RLock()
        self.home_xy_cm = home_xy_cm
        self.after_sequence = 0
        self.state = dict(
            phase="PREPARING",
            pose=None,
            localization=dict(state="UNAVAILABLE", fix_age_s=None, source=None, estimator_failures=None),
            battery_percent=None,
            airborne=False,
            goal=None,
            goal_rejection=None,
            regions={},
            captures=0,
            detections=[],
            unplaced=0,
            reanchor_log=[],
            decisions=[],
            answer=None,
            returning=False,
            returned_home=False,
            landing=None,
            took_off=False,
        )
        self._last_heading = None
        self._last_localization = None
        self._offered_regions = []
        self._class_order = []
        self._count_frames = []
        self._last_goal_choice = None

    # -- external steps (bursts are observed at the stack, not the ledger) ----
    def note_burst(self, *, heading_deg, tags, reports, purpose="recovery burst"):
        with self._lock:
            tag_text = f"tags {', '.join(str(t) for t in tags)}" if tags else "no tags in view"
            self._log("TAG_BURST", f"{purpose.capitalize()} at heading {heading_deg:.0f} deg: {tag_text}",
                      heading_deg=heading_deg, tags=list(tags), reports=reports)

    def note(self, kind, text, **fields):
        with self._lock:
            self._log(kind, text, **fields)

    def observe_localization(self, state, heading_deg=None):
        """A tracker state seen at the stack (every refresh): log each transition."""
        with self._lock:
            self._observe_localization(state, heading_deg)

    def _observe_localization(self, state, heading_deg=None):
        previous = self._last_localization
        self._last_localization = state
        if previous is None or state == previous:
            return
        heading = f" at heading {float(heading_deg):.0f} deg" if heading_deg is not None else ""
        self._log("LOCALIZATION", f"Localization {previous} -> {state}{heading}", state=state,
                  previous=previous, heading_deg=heading_deg)

    def set_live_localization(self, *, state, fix_age_s, heading_deg=None, position=None):
        with self._lock:
            self.state["localization"].update(state=state, fix_age_s=fix_age_s)
            self._observe_localization(state, heading_deg)
            if position is not None:
                pose = dict(self.state["pose"] or {})
                pose.update(x_cm=round(position[0], 1), y_cm=round(position[1], 1), z_cm=round(position[2], 1))
                if heading_deg is not None:
                    pose["heading_deg"] = round(heading_deg, 1)
                self.state["pose"] = pose

    def finish(self, *, terminal_state, landing_state=None, landing_reason=None, landing_pose=None,
               took_off=None, reason=None):
        with self._lock:
            s = self.state
            if took_off is not None:
                s["took_off"] = took_off
            if landing_state is not None:
                s["landing"] = dict(state=landing_state, reason=landing_reason)
            if landing_pose is not None and landing_state == "LANDED":
                dx = landing_pose[0] - self.home_xy_cm[0]
                dy = landing_pose[1] - self.home_xy_cm[1]
                s["returned_home"] = s["returned_home"] or (s["returning"] and math.hypot(dx, dy) <= HOME_TOLERANCE_CM)
            s["phase"] = "LANDED" if landing_state == "LANDED" else terminal_state
            s["terminal_state"] = terminal_state
            s["terminal_reason"] = reason

    # -- the ledger -----------------------------------------------------------
    def fold(self, events) -> None:
        with self._lock:
            for event in events:
                if event.sequence_no <= self.after_sequence:
                    continue
                self.after_sequence = event.sequence_no
                handler = getattr(self, "_on_" + event.event_type.lower(), None)
                if handler is not None:
                    handler(event.payload)

    def public(self) -> dict:
        with self._lock:
            state = deepcopy(self.state)
        regions = sorted(state.pop("regions").values(), key=lambda r: r["order"])
        for row in regions:
            row.pop("order", None)
        state["regions"] = regions
        state["reanchor_log"] = state["reanchor_log"][-60:]
        state["decisions"] = state["decisions"][-40:]
        state["detections"] = state["detections"][-60:]
        state["yaw_rotations"] = sum(1 for row in state["reanchor_log"] if row["kind"] == "YAW_ROTATION")
        state["reanchors_accepted"] = sum(1 for row in state["reanchor_log"] if row["kind"] == "REANCHOR_ACCEPTED")
        return state

    def _log(self, kind, text, **fields):
        log = self.state["reanchor_log"]
        log.append(dict(seq=len(log) + 1, kind=kind, text=text, **fields))

    def _region(self, pose_id):
        regions = self.state["regions"]
        if pose_id not in regions:
            regions[pose_id] = dict(pose_id=pose_id, label=region_label(pose_id), status="OFFERED",
                                    order=len(regions), detections=0)
        return regions[pose_id]

    def _on_goal_ledger_accepted(self, payload):
        objectives = (payload.get("ledger") or {}).get("objectives", ())
        self._class_order = [c for o in objectives for c in (o.get("target_classes") or ())]
        self.state["goal"] = "; ".join(
            f"{o.get('kind')} {', '.join(o.get('target_classes') or ())} in {str(o.get('spatial_scope', '')).lower()}"
            f" then {str(o.get('terminal_condition', '')).lower().replace('_', ' ')}"
            for o in objectives) or None

    def _on_goal_compile_rejected(self, payload):
        # This is the preflight reason recorded in the ledger. The terminal
        # reason only says GOAL_LEDGER_NOT_PENDING and loses this detail.
        choice = self._last_goal_choice or {}
        assessment = choice.get("assessment") or {}
        state = choice.get("state") or {}
        clause = next((row.get("text") for row in state.get("clauses") or ()
                       if row.get("clause_id") == state.get("clause_id")), None)
        self.state["goal_rejection"] = dict(
            status=payload.get("status"), code=payload.get("reason"),
            choice=choice.get("name"), clause=clause, attempt=choice.get("attempt"),
            selected_label=assessment.get("selected_label"),
            confidence=assessment.get("confidence"),
            selected_probability=assessment.get("selected_probability"),
            required_confidence=GOAL_ACT_AT,
        )

    def _on_goal_choice_attempt(self, payload):
        self._last_goal_choice = payload

    def _on_drone_state_recorded(self, payload):
        state = payload.get("state") or {}
        s = self.state
        if state.get("x_cm") is not None:
            s["pose"] = dict(x_cm=state.get("x_cm"), y_cm=state.get("y_cm"), z_cm=state.get("z_cm"),
                             heading_deg=state.get("heading_deg"))
        if state.get("heading_deg") is not None:
            self._last_heading = float(state["heading_deg"])
        loc = state.get("localization_state")
        if loc:
            s["localization"].update(state=loc, fix_age_s=state.get("pose_age_s"), source=state.get("pose_source"))
            self._observe_localization(loc, state.get("heading_deg"))
        if state.get("battery_percent") is not None:
            s["battery_percent"] = state["battery_percent"]
        if state.get("airborne"):
            s["airborne"] = True
            s["took_off"] = True
            if s["phase"] == "PREPARING":
                s["phase"] = "FLYING"
        elif s["took_off"]:
            s["airborne"] = False

    def _on_checkpoint_completed(self, payload):
        for row in payload.get("command_states") or ():
            command = row.get("command") or {}
            arguments = dict(command.get("arguments") or ())
            kind = command.get("kind")
            if kind == "CAPTURE" and row.get("status") == "COMPLETED":
                self.state["captures"] += 1
            if kind == "ROTATE" and row.get("status") == "COMPLETED" and "phase" not in arguments:
                # The supervisor's recovery yaw (checkpoint rotations carry a phase).
                degrees = float(arguments.get("degrees", 0))
                sign = 1 if arguments.get("direction") == "ccw" else -1
                before = self._last_heading
                after = None if before is None else (before + sign * degrees) % 360
                if after is not None:
                    self._last_heading = after
                text = f"Yaw rotation {arguments.get('direction')} {degrees:.0f} deg"
                text += f" -> heading {after:.0f} deg" if after is not None else ""
                self._log("YAW_ROTATION", text, direction=arguments.get("direction"), degrees=degrees,
                          heading_deg=after)

    def _on_safety_assessed(self, payload):
        transition, reason = payload.get("transition"), payload.get("reason")
        s = self.state
        if transition == "RECOVER":
            last = next((row for row in reversed(s["reanchor_log"]) if row["kind"] == "RECOVERY"), None)
            if last is None or last.get("reason") != reason or s["reanchor_log"][-1] is not last:
                self._log("RECOVERY", f"Recovery required ({reason}): supervisor yaw-scan recovery",
                          reason=reason)
        elif transition == "RETURN_NOW" and not s["returning"]:
            s["returning"] = True
            s["phase"] = "RETURNING"
            self._log("RETURN", f"Return home ({reason})", reason=reason)
        elif transition == "LAND_CONTINGENCY" and reason != "LANDING_APPROACH":
            self._log("CONTINGENCY", f"Land in place ({reason})", reason=reason)
        if payload.get("expected_reanchor"):
            self._log("EXPECTED_REANCHOR", f"Expected re-anchor credited at {payload['expected_reanchor']}",
                      pose_id=payload["expected_reanchor"])

    def _on_pose_reanchored(self, payload):
        position = payload.get("position") or {}
        heading = payload.get("heading_deg")
        if heading is not None:
            self._last_heading = float(heading)
        self._log("REANCHOR_ACCEPTED",
                  f"Re-anchor accepted at ({position.get('x_cm', 0):.0f}, {position.get('y_cm', 0):.0f}) cm, "
                  f"heading {float(heading or 0):.0f} deg ({payload.get('source')})",
                  heading_deg=heading, source=payload.get("source"),
                  x_cm=position.get("x_cm"), y_cm=position.get("y_cm"))
        self.state["localization"].update(state=payload.get("state", "ANCHORED"), fix_age_s=payload.get("fix_age_s"))
        self._observe_localization(payload.get("state", "ANCHORED"), heading)

    def _on_room_choice_attempt(self, payload):
        assessment = payload.get("assessment") or {}
        outcome = assessment.get("outcome")
        # Task 4b item 5: a refused turn shows what Jev actually chose (its
        # label, probability and the refusal code), never the escalation
        # label the gate substitutes as final_label.
        refused = None
        if payload.get("provider_error"):
            refused = "PROVIDER_ERROR"
        elif outcome and not str(outcome).startswith("ACCEPTED"):
            refused = outcome
        if refused:
            label = assessment.get("selected_label")  # None: no usable label at all
        else:
            label = assessment.get("final_label") or assessment.get("selected_label")
        candidates = {c.get("candidate_id"): c for c in payload.get("candidates") or ()}
        for candidate_id in candidates:
            if candidate_id and candidate_id.startswith("OBSERVE:"):
                self._region(candidate_id.rsplit(":", 1)[-1])
        chosen = candidates.get(label) or {}
        reason = chosen.get("predicted_benefit") or chosen.get("description") or ""
        reason = " ".join(str(reason).split())
        if len(reason) > 160:
            reason = reason[:157] + "..."
        if payload.get("provider_error"):
            reason = f"provider error: {payload['provider_error']}"
        decisions = self.state["decisions"]
        kind_mass = assessment.get("confidence") if assessment.get("confidence_source") == "KIND_MASS" else None
        decisions.append(dict(turn=len(decisions) + 1, action=label,
                              label=_candidate_label(label) if label else "No usable choice",
                              outcome=outcome, confidence=assessment.get("confidence"),
                              probability=assessment.get("selected_probability"), refused=refused,
                              kind_mass=kind_mass, reason=reason or "(no reason given)"))

    def _on_room_tool_outcome(self, payload):
        candidate_id = payload.get("candidate_id") or ""
        code = payload.get("code")
        s = self.state
        if s["decisions"] and s["decisions"][-1]["action"] == candidate_id:
            s["decisions"][-1]["result"] = code
        if candidate_id.startswith("OBSERVE:"):
            region = self._region(candidate_id.rsplit(":", 1)[-1])
            if code == "OBSERVE_SUCCEEDED":
                region["status"] = "OBSERVED"
                frames = payload.get("count_evidences") or (
                    (payload["count_evidence"],) if payload.get("count_evidence") else ())
                self._count_frames.extend(FrameCountEvidence.from_payload(frame) for frame in frames)
                estimates = tuple(estimate for frame in self._count_frames for estimate in frame.estimates)
                objects = associate_static((), estimates)
                low = {estimate_id for frame in self._count_frames
                       for estimate_id in frame.low_confidence_estimate_ids}
                region_by_estimate = {estimate.estimate_id: region_label(frame.pose_id)
                                      for frame in self._count_frames for estimate in frame.estimates}
                for row in s["regions"].values():
                    row["detections"] = 0
                s["detections"] = []
                for obj in objects:
                    label = region_by_estimate[obj.member_estimate_ids[0]]
                    s["detections"].append(dict(
                        class_name=obj.class_name, region=label,
                        x_cm=round(obj.x_cm, 1), y_cm=round(obj.y_cm, 1),
                        confidence="low" if all(member in low for member in obj.member_estimate_ids) else "high"))
                    region_row = next((row for row in s["regions"].values() if row["label"] == label), None)
                    if region_row is not None:
                        region_row["detections"] += 1
                s["unplaced"] = sum(len(frame.unplaced) for frame in self._count_frames)
            elif region["status"] != "OBSERVED" and code not in _CHOICE_GATE_REFUSALS:
                # A confidence-gate refusal leaves the region offered (Task
                # 4b item 1); only a physical or tool refusal marks it.
                region["status"] = "FAILED" if code in ("TOOL_FAILURE",) else code or "FAILED"
        if payload.get("answer"):
            s["answer"] = dict(text=payload["answer"], code=code,
                               summary=answer_summary(payload.get("count_projection"), self._class_order),
                               terminal_state=payload.get("terminal_state"))
