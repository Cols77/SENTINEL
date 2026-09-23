"""Task 4b item 6: replay recorded Jev questions to calibrate the gates.

Reads every recorded ``GOAL_CHOICE_ATTEMPT`` (goal compilation) and
``ROOM_CHOICE_ATTEMPT`` (room turn) from one or more
``runs/*/session.sqlite3`` files, re-sends each recorded (state,
instruction, criteria, name) to the real Jev ``--repeats`` times, and prints,
per goal question name or room turn kind:

- agreement: the share of replies choosing the label Jev chose when it was
  recorded;
- the mean and lowest probability Jev put on the label it chose;
- the share of replies each threshold in ``--thresholds`` would accept, using
  ``assess_choice`` with that bar and no margin requirement (the margin and
  the room loop's KIND_MASS gate are not modelled).

The room turn's instruction is not persisted; it is the controller's fixed
per-turn string (``ROOM_INSTRUCTION``), and a room attempt's criteria are
rebuilt from its recorded candidates. A re-asked room turn's persisted
state is the first ask's state (without ``previous_refusal``).

Paid: the judge is ``sentinel.room.live_stack.default_jev`` (OpenRouter), so
nothing is sent without ``--i-authorise-paid-calls``. ``--dry-run`` lists
what would be sent and builds no judge.

    python scripts/room_jev_replay.py runs/*/session.sqlite3 --dry-run
    python scripts/room_jev_replay.py runs/l3-*/session.sqlite3 --repeats 3 --i-authorise-paid-calls
"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import glob
import json
from pathlib import Path
import sqlite3
import sys

from sentinel.jev_choice import assess_choice

PAID_CALLS_FLAG = "--i-authorise-paid-calls"
EVENT_TYPES = ("GOAL_CHOICE_ATTEMPT", "ROOM_CHOICE_ATTEMPT")
#: The controller's per-turn instruction (``RoomController._decide``).
ROOM_INSTRUCTION = "Choose one legal action. Reads are historical. Never compose factual values."
ROOM_NAME = "room_action"
DEFAULT_THRESHOLDS = (0.4, 0.5, 0.6, 0.7)


@dataclass(frozen=True)
class RecordedQuestion:
    source: str
    session_id: str
    sequence_no: int
    event_type: str
    key: str  # goal question name, or "room:<kind of the recorded choice>"
    name: str
    state: dict
    instruction: str
    criteria: dict
    recorded_label: str | None
    escalation_label: str


def _room_kind(candidates: Sequence[dict], label: str | None) -> str:
    for candidate in candidates:
        if candidate.get("candidate_id") == label:
            return str(candidate.get("kind") or "UNKNOWN")
    return "NO_LEGAL_LABEL"


def _question(source: str, row: sqlite3.Row) -> RecordedQuestion | None:
    payload = json.loads(row["payload_json"])
    assessment = payload.get("assessment") or {}
    recorded = assessment.get("selected_label")
    if row["event_type"] == "GOAL_CHOICE_ATTEMPT":
        if not isinstance(payload.get("criteria"), dict) or not payload.get("name"):
            return None
        return RecordedQuestion(source, row["session_id"], row["sequence_no"], row["event_type"],
                                payload["name"], payload["name"], payload.get("state") or {},
                                payload.get("instruction") or "", payload["criteria"], recorded,
                                "CLARIFICATION_REQUIRED")
    candidates = payload.get("candidates") or []
    criteria = {c["candidate_id"]: c.get("description", "") for c in candidates if c.get("candidate_id")}
    if not criteria:
        return None
    return RecordedQuestion(source, row["session_id"], row["sequence_no"], row["event_type"],
                            f"room:{_room_kind(candidates, recorded)}", ROOM_NAME, payload.get("state") or {},
                            ROOM_INSTRUCTION, criteria, recorded, "ESCALATE")


def load_questions(paths: Iterable[str | Path]) -> list[RecordedQuestion]:
    """Every recorded choice attempt, read-only (the session file is never
    migrated or written)."""
    questions = []
    for path in paths:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT session_id, sequence_no, event_type, payload_json FROM room_events "
                f"WHERE event_type IN ({','.join('?' for _ in EVENT_TYPES)}) ORDER BY session_id, sequence_no",
                EVENT_TYPES).fetchall()
        finally:
            connection.close()
        questions.extend(q for q in (_question(str(path), row) for row in rows) if q is not None)
    return questions


@dataclass
class KeyStats:
    asks: int = 0
    agreed: int = 0
    unusable: int = 0
    probabilities: list[float] = field(default_factory=list)
    accepted: dict[float, int] = field(default_factory=dict)


def replay(questions: Sequence[RecordedQuestion], judge, *, repeats: int = 3,
           thresholds: Sequence[float] = DEFAULT_THRESHOLDS) -> dict[str, KeyStats]:
    stats: dict[str, KeyStats] = {}
    for question in questions:
        row = stats.setdefault(question.key, KeyStats(accepted={t: 0 for t in thresholds}))
        for _ in range(repeats):
            row.asks += 1
            try:
                answer = judge.choice(deepcopy(question.state), question.instruction,
                                      criteria=dict(question.criteria), name=question.name)
            except Exception:  # noqa: BLE001 -- provider detail never printed
                answer = None
            base = assess_choice(answer, question.criteria, escalation_label=question.escalation_label,
                                 act_at=0.0, min_margin=0.0)
            if base.outcome != "ACCEPTED":
                row.unusable += 1
                continue
            row.probabilities.append(base.selected_probability)
            row.agreed += base.selected_label == question.recorded_label
            for threshold in thresholds:
                gated = assess_choice(answer, question.criteria, escalation_label=question.escalation_label,
                                      act_at=threshold, min_margin=0.0)
                row.accepted[threshold] += gated.outcome == "ACCEPTED"
    return stats


def render(stats: dict[str, KeyStats], thresholds: Sequence[float] = DEFAULT_THRESHOLDS) -> str:
    header = ["question", "asks", "agree", "mean p", "min p", "unusable"] + [f"acc@{t:g}" for t in thresholds]
    lines = ["  ".join(header)]
    for key in sorted(stats):
        row = stats[key]
        probabilities = row.probabilities
        mean = f"{sum(probabilities) / len(probabilities):.2f}" if probabilities else "--"
        low = f"{min(probabilities):.2f}" if probabilities else "--"
        share = (lambda n: f"{n / row.asks:.0%}") if row.asks else (lambda n: "--")
        lines.append("  ".join([key, str(row.asks), share(row.agreed), mean, low, share(row.unusable)]
                               + [share(row.accepted[t]) for t in thresholds]))
    return "\n".join(lines)


def _paths(patterns: Sequence[str]) -> list[str]:
    found = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern)) if any(ch in pattern for ch in "*?[") else [pattern]
        found.extend(match for match in matches if Path(match).is_file())
    return found


def main(argv: Sequence[str] | None = None, *, judge_factory: Callable[[], object] | None = None,
         out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sessions", nargs="+", help="session.sqlite3 files (globs allowed)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--thresholds", default=",".join(f"{t:g}" for t in DEFAULT_THRESHOLDS))
    parser.add_argument("--dry-run", action="store_true", help="list what would be sent; no network call")
    parser.add_argument(PAID_CALLS_FLAG, dest="authorised", action="store_true",
                        help="required: every replay is a paid OpenRouter call")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    thresholds = tuple(float(value) for value in args.thresholds.split(",") if value.strip())
    paths = _paths(args.sessions)
    if not paths:
        parser.error("no session file matched")
    questions = load_questions(paths)
    if args.dry_run:
        for q in questions:
            print(f"{q.source} {q.session_id}#{q.sequence_no} {q.key} name={q.name} recorded={q.recorded_label} "
                  f"labels={len(q.criteria)} state_bytes={len(json.dumps(q.state))}", file=out)
        print(f"{len(questions)} questions x {args.repeats} repeats = {len(questions) * args.repeats} "
              f"paid calls would be sent (dry run: none sent).", file=out)
        return 0
    if not args.authorised:
        print(f"refusing: replaying to Jev makes paid calls; pass {PAID_CALLS_FLAG} (or --dry-run).",
              file=sys.stderr)
        return 2
    if judge_factory is None:
        from sentinel.room.live_stack import default_jev
        judge_factory = default_jev
    judge = judge_factory()
    stats = replay(questions, judge, repeats=args.repeats, thresholds=thresholds)
    print(render(stats, thresholds), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
