"""A choreography from a prompt: a model writes the stops, code checks them, the sim flies them.

    uv run python scripts/choreo_run.py --replay fixtures/choreo/square-diagonal.json --sim 20
    uv run python scripts/choreo_run.py --prompt "..." --live --i-authorise-paid-calls   # PAID

The model gets the prompt, the commanded area, the tag-coverage map and one tool
(``fly_choreography``). ``sentinel.cube.choreography`` refuses any stop outside the bounds, any
stop the tags can be lost from and any impossible leg, asks once more with the refusals, and
appends the return to the takeoff point itself. The checked mission is written to
artifacts/choreo/<stamp>/mission.json, with the prompt, the raw replies and the verdicts in
choreo.json. Flying it for real is a separate, operator-confirmed step:

    uv run python scripts/cube_trial.py --real --confirm --site docs/wp5-flight3-site.json \\
        --tag-map docs/wp5-flight3-map.json --mission artifacts/choreo/<stamp>/mission.json
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from sentinel.cube.choreography import mission_document, request_choreography
from sentinel.cube.closed_loop_sim import DriftModel, monte_carlo
from sentinel.cube.coverage import CoverageModel
from sentinel.cube.mission import load_mission
from sentinel.cube.site import load_site

BASE_URL = "https://openrouter.ai/api"
KEY_ENV = "OPENROUTER_API_KEY"


def live_post(model: str, timeout_s: float = 120.0):
    import httpx

    def post(payload: dict) -> dict:
        key = os.environ.get(KEY_ENV)
        if not key:
            raise RuntimeError(f"{KEY_ENV} is not set")
        response = httpx.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=timeout_s,
                              headers={"Authorization": f"Bearer {key}"})
        response.raise_for_status()
        raw = response.json()
        if raw.get("model") not in (None, model):
            raise RuntimeError(f"pinned {model!r} but the provider resolved {raw.get('model')!r}")
        return raw
    return post


def replay_post(responses: list[dict]):
    queue = list(responses)

    def post(_payload: dict) -> dict:
        if not queue:
            raise RuntimeError("the replay fixture has no more recorded replies")
        return queue.pop(0)
    return post


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--replay", type=Path, help="recorded prompt + replies (free)")
    source.add_argument("--live", action="store_true", help="ask the model (PAID, at most 2 calls)")
    parser.add_argument("--prompt", help="the operator's request (default: the fixture's prompt)")
    parser.add_argument("--i-authorise-paid-calls", action="store_true")
    parser.add_argument("--llm-model", default="z-ai/glm-5.3")
    parser.add_argument("--site", type=Path, default=None,
                        help="default: the fixture's site, else docs/wp5-flight3-site.json")
    parser.add_argument("--calibration-from", type=Path,
                        default=Path("calibration/tag-map-calibrated.json"))
    parser.add_argument("--z-cm", type=int, default=90, help="altitude of the coverage map shown")
    parser.add_argument("--leg-speed", type=int, default=50)
    parser.add_argument("--max-trim-rounds", type=int, default=3)
    parser.add_argument("--sim", type=int, default=0, help="closed-loop sim runs of the result")
    parser.add_argument("--out-root", type=Path, default=Path("artifacts/choreo"))
    args = parser.parse_args(argv)

    fixture = json.loads(args.replay.read_text(encoding="utf-8")) if args.replay else {}
    prompt = args.prompt or fixture.get("prompt")
    if not prompt:
        parser.error("--prompt is required with --live")
    if args.live and not args.i_authorise_paid_calls:
        parser.error("--live makes paid calls (at most 2); pass --i-authorise-paid-calls to go ahead")
    site_path = args.site or Path(fixture.get("site", "docs/wp5-flight3-site.json"))
    site = load_site(site_path)
    coverage = CoverageModel.from_files(site, args.calibration_from)
    post = live_post(args.llm_model) if args.live else replay_post(fixture["responses"])

    result = request_choreography(prompt, site, coverage, post, model=args.llm_model,
                                  z_cm=args.z_cm, leg_speed_cm_s=args.leg_speed,
                                  max_trim_rounds=args.max_trim_rounds)
    out = args.out_root / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    reason = (result["arguments"] or {}).get("reason", "")
    record = {"prompt": prompt, "site": str(site_path), "source": "live" if args.live else
              f"replay {args.replay}", "model": args.llm_model if args.live else fixture.get("source"),
              "calls": len(result["calls"]), "raw_replies": result["calls"],
              "arguments": result["arguments"], "refusals": result["errors"],
              "accepted": result["mission"] is not None}
    print(f"prompt: {prompt}")
    print(f"{len(result['calls'])} call(s); model's reason: {reason or '-'}")
    if result["mission"] is None:
        (out / "choreo.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print("REFUSED:\n  " + "\n  ".join(result["errors"]))
        print(f"record in {out / 'choreo.json'}")
        return 1
    mission_path = out / "mission.json"
    mission_path.write_text(json.dumps(mission_document(result["mission"], prompt=prompt, reason=reason),
                                       indent=2) + "\n", encoding="utf-8")
    for wp in result["mission"].waypoints:
        print(f"  {wp.name:<8} ({wp.x_cm}, {wp.y_cm}, {wp.z_cm})  "
              f"{coverage.verdict(wp.x_cm, wp.y_cm, wp.z_cm).level}")
    print("  RETURN   to the measured takeoff point (added by code)")
    if args.sim:
        summary = monte_carlo(load_mission(mission_path), site, "reanchor", DriftModel(), args.sim,
                              15.0, coverage)
        record["sim"] = summary
        back = summary["waypoints"].get("RETURN", {})
        print(f"sim: {summary['completed']}/{summary['runs']} flights completed; RETURN true error "
              f"median {back.get('median_cm', '-')} cm, inside 15 cm {back.get('within_tolerance', '-')}")
    (out / "choreo.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"mission: {mission_path}\nfly it (operator-confirmed): uv run python scripts/cube_trial.py "
          f"--real --confirm --site {site_path} --tag-map <map generated from that site> "
          f"--mission {mission_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
