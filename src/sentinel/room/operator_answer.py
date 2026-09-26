"""The operator's answer: one plain-language reply at mission end.

After a room-agent run's terminal state is durably recorded
(``RunArtifacts.finalize`` has written ``terminal.json``/``summary.json``),
this module makes at most ONE GLM call that reads the operator's original
request and the mission's findings (per-zone observations, grounded counts,
zones refused, landing outcome) and writes a short answer phrased against
what was asked. The result goes to ``runs/<run_id>/operator_answer.json``.

Rules:

- Never on the safety path. It runs only after the terminal record exists,
  and ``finalize_operator_answer`` never raises: any failure (no key, no
  authorization, network, malformed reply, unreadable store) degrades to a
  deterministic answer assembled from the counts.
- Paid only when authorised: the caller passes the run's
  ``--i-authorise-paid-calls`` decision; without it no request is sent.
- ``summary.json``/``terminal.json`` are never rewritten (the artifact
  verifier cross-checks them); the answer is its own file, written
  atomically, so a failure mid-write cannot corrupt the run.
- The model may only restate the findings it is given; the deterministic
  counts are stored beside its text so a reader can check one against the other.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from sentinel.room.artifacts import atomic_write_bytes
from sentinel.survey.llm_adapter import AdapterUnavailable, LlmAdapter, Transport

ANSWER_FILENAME = "operator_answer.json"
DEFAULT_MODEL = "z-ai/glm-5.3"
DEFAULT_BASE_URL = "https://openrouter.ai/api"
DEFAULT_KEY_ENV = "OPENROUTER_API_KEY"
DEFAULT_TIMEOUT_S = 30.0
MAX_ANSWER_CHARS = 2000

SYSTEM_INSTRUCTIONS = (
    "You report the result of one short indoor drone mission to the operator who asked for it. "
    "You are given the operator's request and the mission's findings: which viewpoints were "
    "observed or refused, the grounded object counts (confirmed and uncertain), and how the "
    "flight ended. Answer the request directly in three to six plain sentences. Use only the "
    "numbers in the findings; never invent an object, a count or a place. Say which parts of "
    "the room were not observed and that objects there may have been missed: the findings' "
    "zones_not_observed lists them, and when it is non-empty you must never claim the whole "
    "room was covered. Call uncertain detections uncertain. If the mission did not answer "
    "the request, say so plainly; when the flight ended early, explain what failed in plain "
    "words from the findings (each zone's reason and detail text, and how the flight ended), "
    "without guessing beyond them. Zone names read position-then-look: in "
    "'center-middle-middle-left' the first three words say where the drone hovered and the "
    "last word says which way it faced; describe the viewpoint in those terms (for example "
    "'facing the left wall from the centre of the room'), never by repeating the raw name. "
    "No markdown, no lists."
)


class _AnswerClient(LlmAdapter):
    """The survey adapter's transport, key handling and call cap, for one
    plain chat completion (no tools)."""

    def answer(self, messages: list[dict]) -> tuple[str, str | None]:
        if self.calls_made >= self._max_calls:
            raise AdapterUnavailable("operator answer: at most one paid call per run")
        self.calls_made += 1
        # GLM 5.3 reasons mandatorily on this endpoint ({"enabled": false} is
        # a 400), and at the default effort it thinks past any reasonable
        # max_tokens and returns an empty content field (live run
        # l3-20260925-163311: 1620 reasoning tokens, finish_reason "length",
        # "empty chat response" -> fallback). effort "low" measured 0
        # reasoning tokens and clean prose on the same findings; "exclude"
        # keeps any chain-of-thought out of the reply.
        raw = self._post({"model": self.model, "messages": messages, "temperature": 0,
                          "max_tokens": 1500, "reasoning": {"effort": "low", "exclude": True}})
        self.last_raw = raw
        try:
            text = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterUnavailable(f"malformed chat response: {exc}") from exc
        if not isinstance(text, str) or not text.strip():
            raise AdapterUnavailable("empty chat response")
        resolved = raw.get("model") if isinstance(raw, dict) else None
        # Same contract as the survey adapter: a paid call answered by a
        # different model than the pinned one is a failure (OpenRouter has
        # silently substituted models before), and the deterministic
        # fallback is written instead of the substitute's text.
        if resolved is not None and str(resolved) != self.model:
            raise AdapterUnavailable(
                f"operator answer: provider resolved model {resolved!r}, expected {self.model!r}")
        return text.strip()[:MAX_ANSWER_CHARS], (str(resolved) if resolved is not None else None)


# -- findings ------------------------------------------------------------------
def _zone(candidate_id: str) -> str:
    return candidate_id.rsplit(":", 1)[-1]


def collect_findings(events: Iterable, terminal: Mapping[str, object] | None = None) -> dict:
    """The mission's findings from its durable event log (``StoredEvent``s,
    or anything with ``event_type``/``payload``) plus the terminal record."""
    zones: dict[str, dict] = {}
    final: dict | None = None
    for event in events:
        event_type = getattr(event, "event_type", None)
        payload = getattr(event, "payload", None) or {}
        if event_type == "ROOM_CHOICE_ATTEMPT":
            # Every choice lists the whole OBSERVE menu, so a declared zone
            # the mission never flew to still shows up in zones_not_observed
            # (live run l3-20260925-163311: a search stopped at its first
            # sighting and the answer claimed the whole room was covered).
            for candidate in payload.get("candidates") or ():
                offered = str((candidate or {}).get("candidate_id") or "")
                if offered.startswith("OBSERVE:"):
                    zones.setdefault(_zone(offered), {"zone": _zone(offered), "status": None,
                                                      "detections": {}, "frames": 0})
            continue
        if event_type != "ROOM_TOOL_OUTCOME":
            continue
        candidate_id = str(payload.get("candidate_id") or "")
        code = payload.get("code")
        if candidate_id.startswith("OBSERVE:"):
            zone = zones.setdefault(_zone(candidate_id), {"zone": _zone(candidate_id), "status": None,
                                                          "detections": {}, "frames": 0})
            if code == "OBSERVE_SUCCEEDED":
                zone["status"] = "OBSERVED"
                frames = payload.get("count_evidences") or (
                    [payload["count_evidence"]] if payload.get("count_evidence") else [])
                seen: Counter = Counter()
                for frame in frames:
                    for estimate in (frame or {}).get("estimates") or ():
                        seen[estimate.get("class_name") or "unknown"] += 1
                    people = int((frame or {}).get("people_confirmed") or 0)
                    if people:
                        seen["person"] += people
                zone["frames"] += len(frames)
                # raw per-frame detections, not de-duplicated objects: the
                # grounded counts below are the answer
                for name, value in seen.items():
                    zone["detections"][name] = zone["detections"].get(name, 0) + value
            elif zone["status"] != "OBSERVED":
                zone["status"] = code or "FAILED"
                if payload.get("detail"):
                    zone["detail"] = str(payload["detail"])
        if payload.get("terminal_state") or payload.get("answer"):
            final = payload
    counts = []
    coverage_summary = None
    for objective in (final or {}).get("count_projection") or ():
        coverage = objective.get("coverage") or {}
        for count in objective.get("counts") or ():
            counts.append({
                "class_name": count.get("class_name"),
                "confirmed": count.get("confirmed", 0),
                "uncertain": count.get("uncertain", 0),
                "unplaced": count.get("unplaced", 0),
                "scope": objective.get("scope"),
            })
        if coverage:
            coverage_summary = {"complete": coverage.get("complete"),
                                "uncovered": list(coverage.get("uncovered") or ())}
    terminal = dict(terminal or {})
    findings = {
        "zones": list(zones.values()),
        "zones_observed": [z["zone"] for z in zones.values() if z["status"] == "OBSERVED"],
        "zones_not_observed": [{"zone": z["zone"], "reason": z["status"] or "NOT_VISITED",
                                **({"detail": z["detail"]} if z.get("detail") else {})}
                               for z in zones.values() if z["status"] != "OBSERVED"],
        "counts": counts,
        "system_answer": (final or {}).get("answer"),
        "answer_code": (final or {}).get("code"),
        "terminal_state": terminal.get("state") or (final or {}).get("terminal_state"),
        "terminal_reason": terminal.get("reason"),
        "took_off": terminal.get("took_off"),
        "landing_state": terminal.get("landing_state"),
    }
    if coverage_summary is not None:
        findings["coverage"] = coverage_summary
    return findings


# -- the answer ----------------------------------------------------------------
def build_messages(request_text: str, findings: Mapping[str, object]) -> list[dict]:
    user = {"operator_request": request_text or "", "findings": dict(findings)}
    return [{"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": json.dumps(user, default=str, sort_keys=True)}]


def _plural(name: str, n: int) -> str:
    return name if n == 1 or name.endswith("s") else name + "s"


def fallback_answer(request_text: str, findings: Mapping[str, object]) -> str:
    """The deterministic answer written when no model call is made or it fails."""
    parts = [f'You asked: "{request_text}".'] if request_text else []
    counts = findings.get("counts") or []
    if counts:
        lines = []
        for count in counts:
            name = str(count.get("class_name") or "object")
            confirmed = int(count.get("confirmed") or 0)
            uncertain = int(count.get("uncertain") or 0)
            text = f"{confirmed} {_plural(name, confirmed)} confirmed"
            if uncertain:
                text += f" and {uncertain} uncertain"
            lines.append(text)
        parts.append("Found " + "; ".join(lines) + ".")
    else:
        parts.append("The mission produced no grounded count answer.")
        raw: Counter = Counter()
        for zone in findings.get("zones") or ():
            raw.update(zone.get("detections") or {})
        if raw:
            parts.append("Raw per-frame sightings (not de-duplicated): "
                         + ", ".join(f"{name} x{n}" for name, n in sorted(raw.items())) + ".")
    observed = findings.get("zones_observed") or []
    missed = findings.get("zones_not_observed") or []
    if observed:
        parts.append(f"Observed {len(observed)} viewpoint(s): {', '.join(observed)}.")
    if missed:
        parts.append("Not observed (objects there may be missed): "
                     + ", ".join(f"{m['zone']} ({m['reason']})" for m in missed) + ".")
    state = findings.get("terminal_state")
    landing = findings.get("landing_state")
    if state:
        parts.append(f"Mission ended {state}" + (f", landing {landing}." if landing else "."))
    return " ".join(parts)


def compose_operator_answer(request_text: str, findings: Mapping[str, object], *, authorised: bool,
                            model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                            api_key_env: str = DEFAULT_KEY_ENV, timeout_s: float = DEFAULT_TIMEOUT_S,
                            transport: Transport | None = None) -> dict:
    """``{text, source, model, error}``; ``source`` is ``"llm"`` or ``"fallback"``."""
    fallback = fallback_answer(request_text, findings)
    if not authorised:
        return {"text": fallback, "source": "fallback", "model": None,
                "error": "paid calls not authorised"}
    try:
        client = _AnswerClient(api_key_env=api_key_env, base_url=base_url, model=model,
                               timeout_s=timeout_s, transport=transport, max_calls=1)
        text, resolved = client.answer(build_messages(request_text, findings))
    except Exception as exc:  # never let the answer step fail the mission end
        return {"text": fallback, "source": "fallback", "model": model, "error": str(exc)[:300]}
    return {"text": text, "source": "llm", "model": resolved or model, "error": None}


def write_operator_answer(run_dir: str | Path, document: Mapping[str, object]) -> Path:
    path = Path(run_dir) / ANSWER_FILENAME
    data = json.dumps(dict(document), default=str, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write_bytes(path, data)
    return path


def finalize_operator_answer(run_dir: str | Path, *, request_text: str, events: Iterable = (),
                             terminal: Mapping[str, object] | None = None, authorised: bool,
                             transport: Transport | None = None, now: datetime | None = None,
                             **compose_kwargs) -> dict | None:
    """Collect, compose and persist the operator's answer. Never raises:
    returns the written document, or ``None`` if even the fallback could
    not be written."""
    try:
        try:
            findings = collect_findings(events, terminal)
        except Exception as exc:
            findings = {"counts": [], "zones_not_observed": [], "zones_observed": [],
                        "terminal_state": (terminal or {}).get("state"), "findings_error": str(exc)[:300]}
        answer = compose_operator_answer(request_text, findings, authorised=authorised,
                                         transport=transport, **compose_kwargs)
        document = {"request_text": request_text, "operator_answer": answer["text"],
                    "source": answer["source"], "model": answer["model"], "error": answer["error"],
                    "findings": findings,
                    "written_at": (now or datetime.now(timezone.utc)).isoformat()}
        write_operator_answer(run_dir, document)
        return document
    except Exception:
        return None


def read_operator_answer(run_dir: str | Path) -> dict | None:
    try:
        document = json.loads((Path(run_dir) / ANSWER_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None
