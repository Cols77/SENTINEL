"""Record the operator's site assurances for one live session.

Task B item 7 (L3 runbook). ``room_session.py live`` needs, for each of the
site's two terminal contingencies, one CURRENT ``ContingencyAssurance`` for
this run's session, in the operator's clock domain, assured on the same
monotonic clock the live process reads (``time.monotonic``, which on Windows
is the system-wide tick count). This writes those files after the operator
has made the check the site's ``assurance_policy_id`` names (the floor inside
the cube is clear). It asserts nothing by itself: the operator runs it only
after checking.

Usage::

    python scripts/room_assurance.py --site fixtures/room_agent/v1/canningst-site.json \
        --session-id <run-id> --clock-domain-id operator-<run-id> --out-dir runs --prefix <run-id>
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from sentinel.room.site import ContingencyAssurance, parse_room_site


def write_assurances(*, site_path, session_id: str, clock_domain_id: str, out_dir, prefix: str,
                     evidence_ref: str, valid_s: float | None = None,
                     clock=time.monotonic, wall_clock=lambda: datetime.now(timezone.utc)) -> list[Path]:
    site = parse_room_site(json.loads(Path(site_path).read_text(encoding="utf-8")))
    now_ms = int(clock() * 1000)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for entry in site.terminal_contingencies:
        lifetime_ms = entry.max_assurance_age_ms if valid_s is None else min(entry.max_assurance_age_ms,
                                                                               int(valid_s * 1000))
        assurance = ContingencyAssurance(
            assurance_id=f"{session_id}-{entry.trigger.value.lower()}", contingency_id=entry.contingency_id,
            session_id=session_id, clock_domain_id=clock_domain_id, state="CURRENT",
            assured_at=wall_clock().isoformat(), assured_monotonic_ms=now_ms,
            expires_monotonic_ms=now_ms + lifetime_ms, evidence_refs=(evidence_ref,))
        name = "nav" if entry.trigger.value == "NAVIGATION_UNAVAILABLE" else "energy"
        path = out / f"{prefix}.{name}.assurance.json"
        path.write_text(json.dumps({**asdict(assurance), "evidence_refs": list(assurance.evidence_refs)},
                                   indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="room_assurance")
    parser.add_argument("--site", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--clock-domain-id", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--evidence-ref", default="operator-floor-check")
    parser.add_argument("--valid-s", type=float, default=None,
                        help="shorter lifetime than the site's max_assurance_age_ms, if wanted")
    args = parser.parse_args(argv)
    try:
        paths = write_assurances(site_path=args.site, session_id=args.session_id,
                                 clock_domain_id=args.clock_domain_id, out_dir=args.out_dir, prefix=args.prefix,
                                 evidence_ref=args.evidence_ref, valid_s=args.valid_s)
    except (OSError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    for path in paths:
        print(path.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
