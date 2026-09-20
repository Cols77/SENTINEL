"""Offline re-score of committed J5 logs by probability band. No network."""
from __future__ import annotations
import json, sys
from pathlib import Path

def band_of(p: float, *, uncertain: tuple[float, float]) -> str:
    lo, hi = uncertain
    if lo <= p <= hi:
        return "UNCERTAIN"
    return "CONFIDENT_TRUE" if p > hi else "CONFIDENT_FALSE"

def rescore(rows: list[dict], *, uncertain: tuple[float, float]) -> dict:
    out = {"false_safe": 0, "false_escalation": 0, "escalated": 0, "confident_correct": 0, "scored": 0}
    for r in rows:
        ref = r["reference"].get("hazard_relevant")
        if ref is None:
            continue
        out["scored"] += 1
        band = band_of(r["answers"]["hazard_relevant"]["probability"], uncertain=uncertain)
        if band == "UNCERTAIN":
            out["escalated"] += 1
        elif (band == "CONFIDENT_TRUE") == ref:
            out["confident_correct"] += 1
        elif ref:
            out["false_safe"] += 1
        else:
            out["false_escalation"] += 1
    return out

def main() -> int:
    base = Path("artifacts/jev")
    lines = ["# J5 offline band re-score", "",
             "No new calls. Input: committed `v2-live-*.jsonl`. n is tiny (15 scored cases x 3 repeats); directional only.", "",
             "| arm | uncertain band | scored | confident correct | escalated | false-safe | false-escalation |",
             "|---|---|---|---|---|---|---|"]
    for arm in ("jev", "llm"):
        text = (base / f"v2-live-{arm}.jsonl").read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        for band in ((0.45, 0.55), (0.40, 0.60), (0.35, 0.65)):
            m = rescore(rows, uncertain=band)
            lines.append(f"| {arm} | {band[0]:.2f}-{band[1]:.2f} | {m['scored']} | {m['confident_correct']} | "
                         f"{m['escalated']} | {m['false_safe']} | {m['false_escalation']} |")
    (base / "v2-live-band-rescore.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    sys.exit(main())
