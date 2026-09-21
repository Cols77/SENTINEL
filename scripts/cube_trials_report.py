"""Waypoint-trial performance across runs: how well the system positions itself.

    uv run python scripts/cube_trials_report.py                   # every run in artifacts/trials
    uv run python scripts/cube_trials_report.py DIR [DIR ...] --markdown report.md

Each run directory holds the mission log (mission.jsonl) and the run report (run.jsonl) that
scripts/cube_trial.py writes. For every stop it reports the open-loop error (the first fix after
the leg, before any trim) and the final error (after the trims), both against the commanded
waypoint.

These errors are measured by the pose system itself. The stand test puts its static bias at
2.6-5.6 cm, so an error below ~5 cm is inside the measurement's own uncertainty. An independent
check needs tape marks or an external camera.
"""
import argparse
import json
import statistics
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_run(directory: Path) -> dict:
    stops = read_jsonl(directory / "mission.jsonl")
    reports = [r for r in read_jsonl(directory / "run.jsonl") if "status" in r]
    report = reports[-1] if reports else {}
    start = next((s for s in stops if s.get("waypoint") == "START"), None)
    return {"name": directory.name, "status": report.get("status", "NO REPORT"),
            "error": report.get("error"), "start": start,
            "stops": [s for s in stops if s.get("waypoint") != "START"],
            "mode": next((s.get("pose_mode") for s in stops), None)}


def pct(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else None


def fmt(value, unit=""):
    return "-" if value is None else f"{value:.1f}{unit}"


def summarise(values):
    return {"n": len(values), "median": statistics.median(values) if values else None,
            "p95": pct(values, 0.95), "max": max(values) if values else None}


def report(runs: list[dict]) -> str:
    lines = ["# Waypoint trials", ""]
    lines += ["| run | mode | outcome | start fix (cm) | start heading err | stops reached |",
              "|---|---|---|---|---|---|"]
    for run in runs:
        start = run["start"] or {}
        outcome = run["status"] + (f" ({run['error'][:60]})" if run["error"] else "")
        lines.append(f"| {run['name']} | {run['mode']} | {outcome} | {start.get('fix_cm', '-')} | "
                     f"{fmt(start.get('heading_error_deg'), ' deg')} | "
                     f"{sum(s.get('status') == 'LOCALISED' for s in run['stops'])}/{len(run['stops'])} |")
    lines += ["", "## Every stop", "",
              "| run | waypoint | status | open-loop err | final err | trims | verdict | localise s |",
              "|---|---|---|---|---|---|---|---|"]
    for run in runs:
        for stop in run["stops"]:
            lines.append(f"| {run['name']} | {stop['waypoint']} | {stop.get('status')} | "
                         f"{fmt(stop.get('open_loop_error_cm'), ' cm')} | "
                         f"{fmt(stop.get('arrival_error_cm'), ' cm')} | {stop.get('trim_rounds_sent', 0)} | "
                         f"{stop.get('tolerance') or '-'} | {fmt(stop.get('localise_s'))} |")

    completed = [r for r in runs if r["status"] == "PASS"]
    localised = [s for r in runs for s in r["stops"] if s.get("status") == "LOCALISED"]
    lines += ["", "## Capability", "",
              f"- Runs: {len(runs)}, completed {len(completed)} "
              f"({100 * len(completed) / len(runs):.0f} %)" if runs else "- Runs: 0"]
    causes: dict[str, int] = {}
    for run in runs:
        if run["status"] != "PASS":
            cause = (run["error"] or run["status"]).split(":")[0]
            causes[cause] = causes.get(cause, 0) + 1
    if causes:
        lines.append("- Early landings: " + ", ".join(f"{k} x{v}" for k, v in causes.items()))
    if localised:
        verdicts = [s.get("tolerance") for s in localised]
        lines.append("- Final verdicts over %d localised stops: INSIDE %d, OUTER %d, OUTSIDE %d" % (
            len(localised), verdicts.count("INSIDE"), verdicts.count("OUTER"), verdicts.count("OUTSIDE")))
        lines.append(f"- Mean trim rounds per stop: "
                     f"{statistics.mean(s.get('trim_rounds_sent', 0) for s in localised):.2f}")
    lines += ["", "| waypoint | stops | open-loop median / p95 | final median / p95 / max |",
              "|---|---|---|---|"]
    names = list(dict.fromkeys(s["waypoint"] for s in localised)) + ["ALL"]
    for name in names:
        group = [s for s in localised if name == "ALL" or s["waypoint"] == name]
        opened = summarise([s["open_loop_error_cm"] for s in group if s.get("open_loop_error_cm") is not None])
        final = summarise([s["arrival_error_cm"] for s in group if s.get("arrival_error_cm") is not None])
        lines.append(f"| {name} | {len(group)} | {fmt(opened['median'])} / {fmt(opened['p95'])} cm | "
                     f"{fmt(final['median'])} / {fmt(final['p95'])} / {fmt(final['max'])} cm |")
    lines += ["", "Errors are measured by the pose system itself (static bias 2.6-5.6 cm)."]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dirs", nargs="*")
    parser.add_argument("--root", default="artifacts/trials")
    parser.add_argument("--markdown", default=None)
    args = parser.parse_args()
    dirs = [Path(d) for d in args.dirs] or sorted(p for p in Path(args.root).glob("*") if p.is_dir())
    text = report([load_run(d) for d in dirs])
    print(text)
    if args.markdown:
        Path(args.markdown).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
