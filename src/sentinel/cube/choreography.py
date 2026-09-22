"""A choreography from a prompt: a model writes the stops, code decides whether they can be flown.

The model is given the operator's prompt, the commanded area, a coarse map of where the tags can be
seen (``cube.coverage``) and one tool, ``fly_choreography``. It writes the waypoints and a reason.
Code then checks every stop exactly as a hand-written mission is checked: inside the commanded
area and altitude band, not BLIND for the tags, legs no longer than the site allows. The return to
the takeoff point is added by code (``Mission.return_to_start``), never left to the model.

No network here: ``request_choreography`` takes the transport as an argument, so the replayed
fixture and the tests exercise the same path as a live call (``scripts/choreo_run.py``).
"""
from __future__ import annotations

import json
import math
import re
from typing import Callable

from sentinel.cube.coverage import BLIND
from sentinel.cube.mission import (MAX_WAYPOINTS, RETURN_NAME, Mission, Waypoint)
from sentinel.cube.site import MAX_LEG_CM

TOOL_NAME = "fly_choreography"
#: The RETURN stop takes the last of the mission's MAX_WAYPOINTS.
MAX_CHOREO_STOPS = MAX_WAYPOINTS - 1
#: Two consecutive stops closer than this are one stop (the Tello's minimum move is 20 cm).
MIN_LEG_CM = 20
#: One call, and one more to correct the stops the checks refused.
MAX_CALLS = 2
NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,11}$")

SYSTEM_INSTRUCTIONS = (
    "You plan a short indoor drone choreography inside a measured box. Coordinates are in "
    "centimetres: x grows to the drone's right, y grows toward the wall with the markers the "
    "drone uses to know where it is, z is the height. The drone always faces that wall; it "
    "translates in straight lines between the stops you give, in order. Call the tool exactly "
    "once. Use only stops inside the bounds, and prefer cells marked O over M; never use a cell "
    "marked B (the markers cannot be seen from there). Do not add a final return to the takeoff "
    "point: the system adds it. Give a one-sentence reason the operator will read before "
    "approving the flight."
)

Post = Callable[[dict], dict]


def tool_schema(site) -> dict:
    env = site.envelope()
    axis = lambda lo, hi: {"type": "integer", "minimum": lo, "maximum": hi}
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Fly these stops in order, then the system returns to the takeoff point.",
            "parameters": {
                "type": "object",
                "properties": {
                    "waypoints": {
                        "type": "array", "minItems": 1, "maxItems": MAX_CHOREO_STOPS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "pattern": NAME_PATTERN.pattern},
                                "x_cm": axis(env.min_x_cm, env.max_x_cm),
                                "y_cm": axis(env.min_y_cm, env.max_y_cm),
                                "z_cm": axis(env.min_z_cm, env.max_z_cm),
                            },
                            "required": ["name", "x_cm", "y_cm", "z_cm"],
                            "additionalProperties": False,
                        },
                    },
                    "reason": {"type": "string"},
                },
                "required": ["waypoints", "reason"],
                "additionalProperties": False,
            },
        },
    }


def coverage_map_text(site, coverage, z_cm: int, step_cm: int = 30) -> str:
    rows = coverage.grid(z_cm, step_cm=step_cm)
    xs = [x for x, _ in rows[0][1]]
    lines = [f"Marker coverage at z = {z_cm} cm (O good, M usable, B blind); rows from the far "
             "side (small y) to the near side (large y):",
             "        " + " ".join(f"x{x:<4}" for x in xs)]
    for y, row in rows:
        lines.append(f"  y{y:<4} " + " ".join(f"{v.level[0]:<5}" for _, v in row))
    return "\n".join(lines)


def build_messages(prompt: str, site, coverage, *, z_cm: int, errors: list[str] | None = None,
                   previous: dict | None = None) -> list[dict]:
    env = site.envelope()
    context = (f"Bounds: x {env.min_x_cm}-{env.max_x_cm}, y {env.min_y_cm}-{env.max_y_cm}, "
               f"z {env.min_z_cm}-{env.max_z_cm}. At most {MAX_CHOREO_STOPS} stops; consecutive "
               f"stops {MIN_LEG_CM}-{MAX_LEG_CM} cm apart; names in capitals, unique, not "
               f"{RETURN_NAME} or START.\n" + coverage_map_text(site, coverage, z_cm))
    messages = [{"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": f"{context}\n\nOperator's request: {prompt}"}]
    if errors:
        messages.append({"role": "assistant", "content": json.dumps(previous or {})})
        messages.append({"role": "user", "content": "The system refused that plan:\n- " +
                         "\n- ".join(errors) + "\nCall the tool again with a corrected plan."})
    return messages


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def mission_from_tool_call(arguments: dict, site, coverage, *, leg_speed_cm_s: int = 50,
                           max_trim_rounds: int = 3) -> tuple[Mission | None, list[str]]:
    """The mission the stops describe, or None and every reason it cannot be flown."""
    errors: list[str] = []
    raw = arguments.get("waypoints") if isinstance(arguments, dict) else None
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CHOREO_STOPS:
        return None, [f"waypoints must be a list of 1-{MAX_CHOREO_STOPS} stops"]
    env = site.envelope()
    waypoints, seen = [], set()
    for index, item in enumerate(raw):
        label = f"stop {index + 1}"
        if not isinstance(item, dict):
            errors.append(f"{label} is not an object")
            continue
        name = item.get("name")
        x, y, z = (_int(item.get(k)) for k in ("x_cm", "y_cm", "z_cm"))
        if not isinstance(name, str) or not NAME_PATTERN.match(name) or name in (RETURN_NAME, "START"):
            errors.append(f"{label}: name {name!r} must be 1-12 capitals/digits/_ and not RETURN or START")
            continue
        label = name
        if name in seen:
            errors.append(f"{name}: the name is used twice")
        seen.add(name)
        if None in (x, y, z):
            errors.append(f"{name}: x_cm, y_cm and z_cm must be whole numbers")
            continue
        if not (env.min_x_cm <= x <= env.max_x_cm and env.min_y_cm <= y <= env.max_y_cm
                and env.min_z_cm <= z <= env.max_z_cm):
            errors.append(f"{name} ({x}, {y}, {z}) is outside the bounds")
            continue
        verdict = coverage.verdict(x, y, z)
        if verdict.level == BLIND:
            errors.append(f"{name} ({x}, {y}, {z}): the markers can be lost from there")
            continue
        waypoints.append(Waypoint(name, x, y, z))
    for a, b in zip(waypoints, waypoints[1:]):
        leg = math.dist((a.x_cm, a.y_cm, a.z_cm), (b.x_cm, b.y_cm, b.z_cm))
        if leg < MIN_LEG_CM:
            errors.append(f"{a.name} -> {b.name} is {leg:.0f} cm, under the {MIN_LEG_CM} cm minimum")
        elif leg > MAX_LEG_CM:
            errors.append(f"{a.name} -> {b.name} is {leg:.0f} cm, over the {MAX_LEG_CM} cm maximum")
    if errors:
        return None, errors
    return Mission(None, None, tuple(waypoints), max_trim_rounds=max_trim_rounds,
                   leg_speed_cm_s=leg_speed_cm_s, return_to_start=True), []


def tool_arguments(raw: dict) -> dict | None:
    """The ``fly_choreography`` arguments from a chat/completions response, or None."""
    try:
        for call in raw["choices"][0]["message"].get("tool_calls") or []:
            if call["function"]["name"] == TOOL_NAME:
                arguments = call["function"]["arguments"]
                return json.loads(arguments) if isinstance(arguments, str) else arguments
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return None


def request_choreography(prompt: str, site, coverage, post: Post, *, model: str, z_cm: int,
                         leg_speed_cm_s: int = 50, max_trim_rounds: int = 3) -> dict:
    """Ask for a choreography, check it, ask once more with the refusals if needed.

    Returns {"mission", "arguments", "errors", "calls": [raw responses]}; "mission" is None when
    the second answer is refused too. ``post`` sends one chat/completions payload."""
    calls, errors, arguments = [], None, None
    for _ in range(MAX_CALLS):
        payload = {"model": model, "temperature": 0, "tools": [tool_schema(site)],
                   "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
                   "messages": build_messages(prompt, site, coverage, z_cm=z_cm, errors=errors,
                                              previous=arguments)}
        raw = post(payload)
        calls.append(raw)
        arguments = tool_arguments(raw)
        if arguments is None:
            errors = [f"the reply did not call {TOOL_NAME}"]
            continue
        mission, errors = mission_from_tool_call(arguments, site, coverage,
                                                 leg_speed_cm_s=leg_speed_cm_s,
                                                 max_trim_rounds=max_trim_rounds)
        if mission is not None:
            return {"mission": mission, "arguments": arguments, "errors": [], "calls": calls}
    return {"mission": None, "arguments": arguments, "errors": errors, "calls": calls}


def mission_document(mission: Mission, *, prompt: str, reason: str) -> dict:
    """The cube-mission-v1 file ``cube-run`` / ``scripts/cube_trial.py`` fly."""
    return {
        "schema_version": "cube-mission-v1",
        "_comment": f"Choreography from the prompt {prompt!r}. Model's reason: {reason}",
        "max_trim_rounds": mission.max_trim_rounds, "leg_speed_cm_s": mission.leg_speed_cm_s,
        "return_to_start": mission.return_to_start,
        "waypoints": [{"name": w.name, "x_cm": w.x_cm, "y_cm": w.y_cm, "z_cm": w.z_cm}
                      for w in mission.waypoints],
    }
