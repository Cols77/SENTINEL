#!/usr/bin/env python
"""Provider comparison spike (plan phase 4, section E) -- PREPARED, not run.

Design spec 6.2: "Settled by a day-one spike, not by this document." This
script is that spike: it decodes N evenly spaced stills from committed Tello
footage (or reads a directory of real garden stills), runs each AVAILABLE
detector over them, and writes raw responses plus a markdown comparison
table -- does every named object carry a usable location; miss rate;
invention rate; latency; behaviour on an empty scene.

It refuses to make a single real call -- printing the exact paid call count
and an estimated cost first -- unless ``--i-authorise-paid-calls`` is
passed; with garden stills it additionally requires recorded filming
consent (``sentinel.survey.consent``). Grounding DINO responses, when given
via ``--gdino-responses``, are read from disk: free, local, never a paid
call, never counted toward the total.

    uv run python scripts/survey_provider_spike.py --n-frames 8 \\
        [--gdino-responses <dir>] [--out <dir>]

Hard rule for this phase (see the plan's own Global Constraints): this
script must NEVER actually be run with ``--i-authorise-paid-calls`` by any
agent working this phase. It is written and tested (refusal path and
call-count arithmetic only) but never executed for real here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "src", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sentinel.env_file import load_env_file  # noqa: E402
from sentinel.survey.consent import ConsentMissing, consent_policy  # noqa: E402
from sentinel.survey.observability import NOMINAL_WIDTH_CM  # noqa: E402
from sentinel.survey.store import SurveyStore  # noqa: E402

#: The four survey classes (same set observability.resolvable_classes works
#: from) -- one Moondream call per class below; Gemini gets all of them in
#: one prompt.
SURVEY_CLASSES = tuple(sorted(NOMINAL_WIDTH_CM))

#: Real per-frame call count, independent of what MoondreamDetector.detect()
#: does internally: the real Moondream `detect` endpoint takes one `object`
#: per call (moondream.py's own module docstring, C1), so this spike calls
#: .detect(path, (cls,)) once per class -- 4 calls/frame. Gemini's adapter
#: already asks for all classes in one prompt -- 1 call/frame (gemini.py's
#: GeminiDetector.detect). This is the spike script's responsibility, not
#: either adapter's -- MoondreamDetector.detect(path, classes) already loops
#: over `classes` itself and is not wrong to be called once per class here.
CALLS_PER_FRAME = {"moondream": len(SURVEY_CLASSES), "gemini": 1, "gemini-openrouter": 1}

#: $/call estimates -- PLACEHOLDER, not a measured invoice. Design spec 6.2
#: cites Moondream at $0.30/M input tokens, $2.50/M output tokens, ~3
#: tokens/box; without a real per-image input-token count that is not yet a
#: per-call figure, so both providers share the same conservative
#: placeholder here. Revisit only once a real, authorised run produces an
#: actual invoice -- not by guessing harder.
#: gemini-openrouter: MEASURED on the 2026-09-22 spike (google/gemini-2.5-flash,
#: 960x720 still, 1352 tokens in, 18-61 out): $0.00045-0.00056 per call, rounded up.
EST_COST_PER_CALL_USD = {"moondream": 0.0006, "gemini": 0.0006, "gemini-openrouter": 0.0006}

#: name -> the .env / environment variable that must hold an API key for
#: that provider to count as AVAILABLE (mirrors scripts/survey_run.py's
#: _PROVIDER_ENV, key-var half only).
_PROVIDER_KEY_VAR = {"moondream": "MOONDREAM_API_KEY", "gemini": "GEMINI_API_KEY"}

#: model env var per provider, read only past the refusal gate.
_PROVIDER_MODEL_VAR = {"moondream": "MOONDREAM_MODEL", "gemini": "GEMINI_MODEL"}


def available_paid_providers(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Which paid providers have a usable API key in ``environ``. A
    Grounding DINO responses directory (``--gdino-responses``) is free and
    local and is never included here, whatever else is available."""
    return tuple(name for name, key_var in _PROVIDER_KEY_VAR.items() if environ.get(key_var))


def paid_call_plan(n_frames: int, providers: tuple[str, ...]) -> dict:
    """Pure. ``{'per_provider': {name: {'calls', 'cost_usd'}}, 'total_calls',
    'estimated_cost_usd'}`` -- the exact numbers ``main()`` must print
    before any call, real or refused, is made."""
    per_provider = {}
    for name in providers:
        calls = n_frames * CALLS_PER_FRAME[name.split(":")[0]]
        per_provider[name] = {"calls": calls,
                              "cost_usd": calls * EST_COST_PER_CALL_USD[name.split(":")[0]]}
    return {
        "per_provider": per_provider,
        "total_calls": sum(row["calls"] for row in per_provider.values()),
        "estimated_cost_usd": sum(row["cost_usd"] for row in per_provider.values()),
    }


def refuse_reason(args, garden_stills: bool, consent_ok: bool) -> str | None:
    """Pure-ish decision function: no I/O, no network call -- makes
    ``main()``'s exit-code branch testable without exercising any
    decode/HTTP code at all. Returns the refusal message, or ``None`` to
    proceed."""
    if not args.i_authorise_paid_calls:
        return ("refusing: pass --i-authorise-paid-calls to make real provider calls "
                "(see the call count and estimated cost printed above)")
    if garden_stills and not consent_ok:
        return "refusing: garden stills require recorded filming consent (none found)"
    return None


def _consent_ok(args) -> bool:
    """Only meaningful when ``--garden-stills`` is set (real, potentially
    person-carrying footage); a replay over the committed Tello fixture is
    not gated on consent, matching ``consent.py``'s own no-location =>
    no-gate rule."""
    if not args.garden_stills:
        return True
    if not args.consent_db:
        return False
    store = SurveyStore.open(args.consent_db)
    try:
        consent_policy(store, args.location, args.date)
        return True
    except ConsentMissing:
        return False


def _decode_stills(video_path: Path, n_frames: int, out_dir: Path) -> list[Path]:
    """Samples ``n_frames`` evenly spaced decoded frames from ``video_path``
    with PyAV and writes them as stills under ``out_dir``. Never reached
    unless ``refuse_reason()`` returned ``None`` -- not exercised in this
    phase. ``src/sentinel/video.py`` is a live Tello-UDP decoder, not a
    file-to-stills one (its ``_default_decoder``/``_default_packet`` show
    the PyAV construction pattern reused here); it is not edited by this
    script. Confirm against
    ``fixtures/perception/live/yaw-scan-20260916.manifest.json``'s
    ``"format": "h264-annex-b"`` before relying on this for a real run.
    """
    import av
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        container = av.open(str(video_path))
    except av.error.FFmpegError:
        container = av.open(str(video_path), format="h264")

    frames = list(container.decode(video=0))
    if not frames:
        raise RuntimeError(f"no decodable frames in {video_path}")
    step = max(1, len(frames) // max(1, n_frames))
    chosen = frames[::step][:n_frames]

    paths = []
    for index, frame in enumerate(chosen):
        path = out_dir / f"still{index:03d}.png"
        Image.fromarray(frame.to_ndarray(format="rgb24")).save(path)
        paths.append(path)
    return paths


def _http_session():
    """Same httpx adaptation as scripts/survey_run.py's _HttpxPostSession --
    only ever constructed past the refusal gate, never during a test."""
    import httpx

    class _Session:
        def post(self, url, *, headers, json, timeout):
            connect_s, read_s, total_s = timeout
            return httpx.post(url, headers=headers, json=json,
                              timeout=httpx.Timeout(total_s, connect=connect_s, read=read_s))

    return _Session()


def _run_moondream(api_key: str, model: str, stills: list[Path], out_dir: Path) -> dict:
    """One ``.detect(path, (cls,))`` call per class per still -- the real
    per-call shape (see ``CALLS_PER_FRAME``'s comment). Only reached past
    the refusal gate."""
    from sentinel.survey.providers import ProviderError
    from sentinel.survey.providers.moondream import MoondreamDetector

    detector = MoondreamDetector(api_key=api_key, model=model, session=_http_session())
    responses = {}
    for still in stills:
        per_class = {}
        for class_name in SURVEY_CLASSES:
            try:
                batch = detector.detect(str(still), (class_name,))
                per_class[class_name] = batch.raw
            except ProviderError as exc:
                per_class[class_name] = {"error": type(exc).__name__}
        responses[still.name] = per_class
        (out_dir / f"moondream_{still.stem}.json").write_text(
            json.dumps(per_class), encoding="utf-8")
    return responses


def _run_gemini(api_key: str, model: str, stills: list[Path], out_dir: Path,
                via_openrouter: bool = False) -> dict:
    """One ``.detect(path, SURVEY_CLASSES)`` call per still. Only reached
    past the refusal gate."""
    from sentinel.survey.providers import ProviderError
    from sentinel.survey.providers.gemini import GeminiDetector, OpenRouterGeminiDetector

    cls = OpenRouterGeminiDetector if via_openrouter else GeminiDetector
    detector = cls(api_key=api_key, model=model, session=_http_session())
    prefix = "gemini_or_" + model.replace("/", "_") if via_openrouter else "gemini"
    responses = {}
    for still in stills:
        try:
            batch = detector.detect(str(still), SURVEY_CLASSES)
            raw = batch.raw if batch.raw is not None else {"status": batch.status}
            # what the survey would store from this answer, beside the provider's own words
            raw = {**raw, "_status": batch.status, "_parsed": [
                {**detection, "band": band}
                for detection, band in zip(batch.detections, batch.bands or ())]}
        except ProviderError as exc:
            # the message names the provider and, for a model mismatch, both names;
            # it never carries the key or the image
            raw = {"error": type(exc).__name__, "detail": str(exc)}
        responses[still.name] = raw
        (out_dir / f"{prefix}_{still.stem}.json").write_text(
            json.dumps(raw, default=str), encoding="utf-8")
    return responses


def _read_gdino_responses(responses_dir: Path, stills: list[Path]) -> dict:
    """Reads scripts/survey_detect_gdino.py's bridged output for the same
    stills -- free, local, never a paid call, never gated by
    refuse_reason()."""
    responses = {}
    for still in stills:
        path = responses_dir / (still.stem + ".json")
        if path.exists():
            responses[still.name] = json.loads(path.read_text(encoding="utf-8"))
    return responses


def _count_objects(payload) -> str:
    if not isinstance(payload, dict):
        return "?"
    if isinstance(payload.get("_parsed"), list):
        return f"{len(payload['_parsed'])} ({payload.get('_status')})"
    if "error" in payload:
        return f"error: {payload['error']}"
    if isinstance(payload.get("objects"), list):
        return str(len(payload["objects"]))
    if payload and all(isinstance(v, dict) for v in payload.values()):
        # Moondream's per-class dict of raw responses.
        return str(sum(len(v.get("objects", [])) for v in payload.values()
                       if isinstance(v, dict)))
    return "?"


def _write_comparison_table(results: dict[str, dict], out_path: Path) -> None:
    """A minimal markdown table: one row per still, one column per
    provider, cell = how many objects that provider returned for it. The
    raw JSON beside this table is where a human checks miss/invention rate
    field by field; this table is only an at-a-glance summary."""
    providers = sorted(results)
    stills = sorted({name for provider_results in results.values() for name in provider_results})
    lines = ["| still | " + " | ".join(providers) + " |",
            "|---|" + "---|" * len(providers)]
    for still in stills:
        row = [still] + [_count_objects(results[provider].get(still)) for provider in providers]
        lines.append("| " + " | ".join(row) + " |")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", type=Path,
                        default=REPO_ROOT / "fixtures/perception/live/yaw-scan-20260916.h264",
                        help="committed Tello footage to sample stills from")
    parser.add_argument("--n-frames", type=int, default=8,
                        help="how many evenly spaced stills to sample from --video")
    parser.add_argument("--gdino-responses", type=Path, default=None,
                        help="directory of scripts/survey_detect_gdino.py output for the "
                             "same stills; read, never computed, never a paid call")
    parser.add_argument("--garden-stills", type=Path, default=None,
                        help="a directory of real garden stills instead of --video; "
                             "requires recorded filming consent")
    parser.add_argument("--consent-db", type=Path, default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--date", default=None)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "artifacts/provider-spike",
                        help="where raw responses and the comparison table are written")
    parser.add_argument("--openrouter-gemini", action="append", default=[], metavar="MODEL",
                        help="also run this Gemini model through OpenRouter (OPENROUTER_API_KEY), "
                             "e.g. google/gemini-2.5-flash; repeatable, one column per model")
    parser.add_argument("--i-authorise-paid-calls", action="store_true",
                        help="required before ANY real provider call is made -- "
                             "never pass this during plan phase 4")
    args = parser.parse_args(argv)

    if environ is None:
        load_env_file()
        env = dict(os.environ)
    else:
        env = dict(environ)

    n_frames = len(list(args.garden_stills.iterdir())) if args.garden_stills else args.n_frames
    providers = available_paid_providers(env)
    if args.openrouter_gemini and not env.get("OPENROUTER_API_KEY"):
        print("refusing: --openrouter-gemini needs OPENROUTER_API_KEY")
        return 1
    providers += tuple(f"gemini-openrouter:{model}" for model in args.openrouter_gemini)
    plan = paid_call_plan(n_frames, providers)

    print(f"frames          : {n_frames}")
    for name, row in plan["per_provider"].items():
        print(f"  {name:<10}: {row['calls']} calls, ~${row['cost_usd']:.4f}")
    if args.gdino_responses:
        print(f"  gdino     : read from {args.gdino_responses} (free, local, not counted)")
    print(f"total paid calls: {plan['total_calls']}")
    print(f"estimated cost  : ${plan['estimated_cost_usd']:.4f}")

    garden_stills = bool(args.garden_stills)
    reason = refuse_reason(args, garden_stills, _consent_ok(args))
    if reason is not None:
        print(reason)
        return 1

    # Reached only past the refusal gate -- never in this phase (hard rule
    # above: this script is never invoked with --i-authorise-paid-calls
    # here).
    args.out.mkdir(parents=True, exist_ok=True)
    if args.garden_stills:
        stills = sorted(args.garden_stills.iterdir())
    else:
        stills = _decode_stills(args.video, args.n_frames, args.out / "stills")

    results = {}
    if args.gdino_responses:
        results["gdino"] = _read_gdino_responses(args.gdino_responses, stills)
    if "moondream" in providers:
        results["moondream"] = _run_moondream(
            env["MOONDREAM_API_KEY"], env.get(_PROVIDER_MODEL_VAR["moondream"], ""),
            stills, args.out)
    if "gemini" in providers:
        results["gemini"] = _run_gemini(
            env["GEMINI_API_KEY"], env.get(_PROVIDER_MODEL_VAR["gemini"], ""), stills, args.out)

    for model in args.openrouter_gemini:
        results[f"gemini-openrouter:{model}"] = _run_gemini(
            env["OPENROUTER_API_KEY"], model, stills, args.out, via_openrouter=True)

    _write_comparison_table(results, args.out / "comparison.md")
    print(f"wrote {args.out / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
