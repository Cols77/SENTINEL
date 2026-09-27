"""Prepare everything a Mission Control Live room-agent flight needs, in one offline command.

Task M. For a CanningSt ``count_return`` acceptance (TRIAL) run this does, in
order, what ``docs/room-agent-live-runbook.md`` (L0 steps 1-2, L3 "Issue the
TRIAL grant", L3 steps 1-2) and the README's pack example do by hand:

1. check (or, with ``--consent-confirmed``, record) today's CanningSt filming
   consent in ``<run-root>/consent.sqlite3``;
2. create or reuse ``<run-root>/registry.json`` (``room_capability.py init``);
3. pick the run: the current pack's run is reused while it has not flown and
   its grant has more than an hour left, otherwise a fresh ``l3-<time>``;
4. issue the ``count_return`` TRIAL grant bound to the committed CanningSt
   profiles and site (``room_capability.py issue-trial``), or reuse the
   run's existing unexpired matching grant;
5. preview the live manifest (``room_session.py preview --manifest-out``);
6. write both site assurances, valid for the site's 30 minutes
   (``room_assurance.write_assurances``);
7. write ``<run-root>/<RUN>.pack.json`` and the same bytes to
   ``<run-root>/live.pack.json``, the stable path Mission Control is started
   with, so a re-run needs no restart (the pack is re-read at each approval).

It calls those scripts' functions in-process; it opens no socket, contacts no
drone and makes no provider call. ``git rev-parse HEAD`` (local) is its only
subprocess.

The manifest does not bind the request text: ``room_session.py live`` takes
the request separately (Mission Control passes the request typed in the
panel), so any request typed at approval time flies under the previewed
``count_return`` manifest. ``--request`` only labels the pack.

Usage::

    $env:PYTHONPATH = "src;."
    python scripts/room_live_prepare.py --run-root runs [--local-ip 192.168.10.2] [--consent-confirmed]
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import room_assurance, room_capability, room_session  # noqa: E402
from sentinel.room import artifacts, capabilities, live_session  # noqa: E402
from sentinel.room.store import RoomStore  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "room_agent" / "v1"
SITE_PATH = FIXTURES / "canningst-site.json"
PROFILES = FIXTURES / "profiles"
DRIFT_PATH = PROFILES / "canningst-drift.json"
ENERGY_PATH = PROFILES / "canningst-energy.json"
LIMITS_PATH = PROFILES / "canningst-command-limits.json"
FLIGHT_PROFILE_PATH = PROFILES / "canningst-flight-profile.json"
TAG_MAP_PATH = REPO_ROOT / "docs" / "wp5-flight3-map.json"
class FlightScenario(NamedTuple):
    """One selectable demo configuration: its geometry, its drift budget and
    the tags it flies against.

    The tag map belongs to the scenario, not to the installation: a venue's
    AprilTags are at that venue's measured positions and height, so moving
    the demo to another room is choosing another scenario here rather than
    editing a shared map (2026-09-26 operator request -- the lecture room's
    tags sit at 49.5 cm, CanningSt's at 53).
    """
    site: Path
    drift: Path
    tag_map: Path
    #: The venue this scenario flies in, and so the location today's filming
    #: consent must be recorded for. A flight in another room is another
    #: scenario with its own consent, never CanningSt's record reused.
    location: str


#: Flight scenarios: geometry, drift profile and tag map. ``wide`` spreads
#: a 3x3 tag-facing viewpoint grid over most of the flight volume, declares
#: home where the operator places the drone (~180 cm from the tag wall) and
#: allows the aircraft further from a tag fix (see the wide site PROVENANCE).
#: ``demo`` is the compact site with the operator-declared 60 cm predicted
#: position error limit (2026-09-25: 30 cm refused the left/right rear zones
#: at look 2 -- single-tag fixes hold, never tighten, the xy budget).
#: ``demo360`` is ``demo`` plus one wall cell each on the left, rear and right
#: walls, observed by three pure rotations over the pad
#: (center-middle-middle-{left,backward,right}); those looks face away from
#: the tags, so losing the fix and recovering (or refusing) is an accepted
#: outcome of this scenario.
#: ``demo5`` flies fewer regions (2026-09-26 operator request): demo360's site
#: and cells, observed from five top-height poses instead of nine -- tag-facing
#: looks from the right and left, left and right wall looks, and a backward
#: look, all from the pad's own row. Each pose declares the cells its demo360
#: middle-height counterpart sees (hand-authored: the coverage generator
#: currently finds no pose that sees any cell). The poses sat one row further
#: back until live l3-20260926-142659, where 30 cm to the flight volume's edge
#: refused every travelling zone on its fixless route home.
#: ``lecture`` is the same five viewpoints in the lecture room the operator
#: measured on 2026-09-26: 400 cm wide by 350 cm deep from the tag plane,
#: tags 69.4 cm apart at 49.5 cm, so it carries its own site and tag map
#: (see fixtures/room_agent/v1/lecture-room-site.PROVENANCE.md). Every
#: CanningSt scenario keeps the CanningSt map, unchanged.
#: ``lecture4`` is the lecture room with four viewpoints (2026-09-26 evening
#: operator request): one board look from the validated pad spot, then the
#: aircraft travels to the left, right and rear walls and faces each --
#: ~67 cm left/right and ~27 cm back, every endpoint at least 80 cm from the
#: flight edges. Same venue, tags, drift and profiles as ``lecture``.
DEMO_DRIFT_PATH = PROFILES / "canningst-demo-drift.json"
LECTURE_TAG_MAP_PATH = REPO_ROOT / "docs" / "lecture-room-map.json"
LOCATION = "CanningSt"
LECTURE_LOCATION = "LectureRoom"
SCENARIOS = {
    "canningst": FlightScenario(SITE_PATH, DRIFT_PATH, TAG_MAP_PATH, LOCATION),
    "wide": FlightScenario(FIXTURES / "canningst-wide-site.json",
                           PROFILES / "canningst-wide-drift.json", TAG_MAP_PATH, LOCATION),
    "demo": FlightScenario(SITE_PATH, DEMO_DRIFT_PATH, TAG_MAP_PATH, LOCATION),
    "demo360": FlightScenario(FIXTURES / "canningst-demo360-site.json", DEMO_DRIFT_PATH, TAG_MAP_PATH, LOCATION),
    "demo5": FlightScenario(FIXTURES / "canningst-demo5-site.json", DEMO_DRIFT_PATH, TAG_MAP_PATH, LOCATION),
    "lecture": FlightScenario(FIXTURES / "lecture-room-site.json", DEMO_DRIFT_PATH,
                              LECTURE_TAG_MAP_PATH, LECTURE_LOCATION),
    "lecture4": FlightScenario(FIXTURES / "lecture4-site.json", DEMO_DRIFT_PATH,
                               LECTURE_TAG_MAP_PATH, LECTURE_LOCATION),
}
_scenario = "canningst"

CAPABILITY = "count_return"
TRIAL_PROTOCOL = "l3-canningst"
AIRCRAFT_ID = "tello-canningst"
DEFAULT_REQUEST = "count the phones and computers in the room"
CURRENT_PACK_NAME = "live.pack.json"
GRANT_LIFETIME = timedelta(hours=12)  # demo day 2026-09-27: cover the whole day on one prepare
#: A run is reused only while its grant has at least this long left.
GRANT_REUSE_MARGIN = timedelta(hours=1)
#: Run events that leave a run reusable (the motors-off L0 check on its manifest).
_REUSABLE_EVENTS = {"CONNECTIVITY_CHECKED"}


class PrepareError(RuntimeError):
    """A refusal, printed as ``refused: ...``."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _quiet(func, argv):
    """Run one of the existing CLIs' ``main`` in-process; return (code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = func(argv)
    return code, out.getvalue(), err.getvalue()


def _location() -> str:
    return SCENARIOS[_scenario].location


def _flight_files() -> dict[str, str]:
    scenario = SCENARIOS[_scenario]
    return dict(site=str(scenario.site), drift_profile=str(scenario.drift), energy_profile=str(ENERGY_PATH),
                command_limits=str(LIMITS_PATH), profile_file=str(FLIGHT_PROFILE_PATH),
                tag_map=str(scenario.tag_map))


def _flight_file_argv() -> list[str]:
    files = _flight_files()
    return ["--site", files["site"], "--drift-profile", files["drift_profile"],
            "--energy-profile", files["energy_profile"], "--command-limits", files["command_limits"],
            "--profile-file", files["profile_file"]]


# -- consent ------------------------------------------------------------------
def ensure_consent(consent_db: Path, day: str, *, confirmed: bool, location: str = LOCATION) -> str:
    from sentinel.survey.consent import has_consent, record_consent
    from sentinel.survey.store import SurveyStore

    if not confirmed and not consent_db.exists():
        raise PrepareError(
            f"no filming consent recorded for {location} on {day} ({consent_db} does not exist); re-run with "
            "--consent-confirmed only when everyone who may appear in frame today has agreed")
    store = SurveyStore.open(consent_db)
    try:
        if confirmed:
            record_consent(store, location, day, "operator")
            return (f"NOTICE: filming consent recorded for {location} on {day}. By passing --consent-confirmed "
                    "you, the operator, confirm that everyone who may appear in frame today has agreed.")
        if not has_consent(store, location, day):
            raise PrepareError(
                f"no filming consent recorded for {location} on {day}; re-run with --consent-confirmed only "
                "when everyone who may appear in frame today has agreed")
        return f"consent: reusing today's recorded filming consent for {location} ({day})"
    finally:
        store.connection.close()


# -- registry and grant ---------------------------------------------------------
def ensure_registry(registry_path: Path) -> tuple[capabilities.GrantRegistry, str]:
    if registry_path.exists():
        try:
            return capabilities.load_registry(registry_path), f"registry: reusing {registry_path.as_posix()}"
        except (capabilities.GrantRegistryError, OSError) as exc:
            raise PrepareError(f"{registry_path} is damaged: {exc}") from exc
    code, _, err = _quiet(room_capability.main, ["init", "--registry", str(registry_path)])
    if code != 0:
        raise PrepareError(err.strip() or "room_capability init failed")
    return capabilities.load_registry(registry_path), f"registry: created {registry_path.as_posix()}"


def _binding():
    files = _flight_files()
    return live_session.load_flight_binding(
        site_path=files["site"], drift_profile_path=files["drift_profile"],
        energy_profile_path=files["energy_profile"], command_limits_path=files["command_limits"],
        profile_paths=(files["profile_file"],))


def _evidence_destination(run_root: Path, run_id: str) -> str:
    return (run_root / run_id).as_posix()


def matching_grant(registry, binding, run_id: str, *, evidence_destination: str, now: datetime):
    """The run's unexpired TRIAL grant bound to exactly these files (``admit_live``'s test), or None."""
    acceptance = room_capability._acceptance_manifest(run_id, CAPABILITY)
    matches = [
        grant for grant in registry.grants
        if grant.state == "TRIAL" and grant.allows(
            capability=CAPABILITY, site_sha256=binding.site.cube_site_sha256, aircraft_id=AIRCRAFT_ID,
            profile_sha256s=binding.sha256s, now=now, session_id=run_id, acceptance_manifest=acceptance)
    ]
    if len(matches) > 1:
        raise PrepareError(f"{len(matches)} TRIAL grants match run {run_id}; prepare a fresh run (--new-run)")
    if not matches:
        return None
    trial = registry.trial_binding_for(matches[0].grant_sha256)
    if trial is not None and (trial.trial_protocol != TRIAL_PROTOCOL
                              or trial.evidence_destination != evidence_destination):
        raise PrepareError(f"run {run_id}'s grant was issued for another protocol or evidence destination; "
                           "prepare a fresh run (--new-run)")
    return matches[0]


def ensure_grant(registry_path: Path, binding, run_id: str, *, evidence_destination: str, now: datetime):
    registry = capabilities.load_registry(registry_path)
    grant = matching_grant(registry, binding, run_id, evidence_destination=evidence_destination, now=now)
    if grant is not None:
        return grant, f"grant: reusing TRIAL grant {grant.grant_sha256[:12]} (expires {grant.expires_at})"
    expires_at = (now + GRANT_LIFETIME).strftime("%Y-%m-%dT%H:%M:%SZ")
    code, out, err = _quiet(room_capability.main, [
        "issue-trial", "--capability", CAPABILITY, "--trial-protocol", TRIAL_PROTOCOL,
        "--aircraft-id", AIRCRAFT_ID, "--evidence-destination", evidence_destination,
        "--expires-at", expires_at, "--session-id", run_id, "--registry", str(registry_path),
        *_flight_file_argv()])
    if code != 0:
        raise PrepareError(err.strip() or "room_capability issue-trial failed")
    grant_sha256 = out.strip().splitlines()[-1]
    registry = capabilities.load_registry(registry_path)
    grant = next(g for g in registry.grants if g.grant_sha256 == grant_sha256)
    return grant, f"grant: issued TRIAL grant {grant_sha256[:12]} (expires {expires_at})"


# -- run choice -----------------------------------------------------------------
def run_is_unflown(run_root: Path, run_id: str) -> bool:
    """True while ``<run-root>/<run>`` holds nothing but (optionally) motors-off L0 checks."""
    run_dir = run_root / run_id
    if (run_dir / "terminal.json").exists():
        return False
    database = run_dir / "session.sqlite3"
    if not database.exists():
        return True
    store = RoomStore.open(database)
    try:
        return {event.event_type for event in store.events(run_id)} <= _REUSABLE_EVENTS
    finally:
        store.close()


def _current_pack(run_root: Path) -> dict:
    try:
        pack = json.loads((run_root / CURRENT_PACK_NAME).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return pack if isinstance(pack, dict) else {}


def choose_run(run_root: Path, registry_path: Path, binding, *, explicit: str | None, new_run: bool,
               now: datetime) -> str:
    if explicit:
        if not run_is_unflown(run_root, explicit):
            raise PrepareError(f"run {explicit} already flew (or started to); pick another --run-id")
        return explicit
    if not new_run and registry_path.exists():
        run_id = _current_pack(run_root).get("run_id")
        if isinstance(run_id, str) and run_id and run_is_unflown(run_root, run_id):
            registry = capabilities.load_registry(registry_path)
            try:
                grant = matching_grant(registry, binding, run_id,
                                       evidence_destination=_evidence_destination(run_root, run_id),
                                       now=now + GRANT_REUSE_MARGIN)
            except PrepareError:
                grant = None  # not cleanly reusable: a fresh run below
            if grant is not None:
                return run_id
    local = now.astimezone()
    run_id = f"l3-{local:%Y%m%d-%H%M%S}"
    suffix = 1
    while (run_root / run_id).exists() or (run_root / f"{run_id}.manifest.json").exists():
        suffix += 1
        run_id = f"l3-{local:%Y%m%d-%H%M%S}-{suffix}"
    return run_id


# -- manifest -------------------------------------------------------------------
def _code_revision() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                                timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def ensure_manifest(run_root: Path, run_id: str, grant, registry_path: Path, *, day: str,
                    evidence_destination: str, now: datetime) -> tuple[Path, str, str]:
    manifest_out = run_root / f"{run_id}.manifest.json"
    opened = run_root / run_id / "manifest.json"
    if opened.exists():
        # The run was already opened by a motors-off check: its manifest is fixed.
        raw = opened.read_bytes()
        artifacts.atomic_write_bytes(manifest_out, raw)
        note = "manifest: reusing the manifest this run was opened with"
    else:
        revision = _code_revision()
        code, _, err = _quiet(room_session.main, [
            "preview", "--target-mode", "live", "--run-id", run_id,
            "--capability", CAPABILITY, "--grant-registry", str(registry_path),
            "--capability-grant-sha256", grant.grant_sha256, "--capability-grant-state", "TRIAL",
            "--trial-protocol", TRIAL_PROTOCOL, "--evidence-destination", evidence_destination,
            "--code-revision", revision, "--integration-revision", revision,
            "--request-revision", "l3", "--goal-parser-revision", "l3",
            *_flight_file_argv(),
            "--aircraft-id", AIRCRAFT_ID, "--firmware-version", "UNVERIFIED",
            "--requested-jev-identity", "jev", "--requested-detector-identity", "gemini-openrouter",
            "--max-duration-s", "600", "--max-distance-cm", "2000", "--max-recovery-attempts", "3",
            "--consent-reference", f"{_location()}/{day}", "--manifest-out", str(manifest_out)])
        gates = [line for line in err.splitlines() if line.startswith(("missing gate:", "refused:"))]
        if code != 0 or gates:
            raise PrepareError("manifest preview failed: " + ("; ".join(gates) or f"exit {code}"))
        raw = manifest_out.read_bytes()
        note = f"manifest: previewed {manifest_out.as_posix()}"
    manifest = artifacts.parse_manifest(raw)
    try:
        capabilities.admit_live(capabilities.load_registry(registry_path), manifest=manifest, now=now)
    except capabilities.GrantRegistryError as exc:
        raise PrepareError(f"the manifest is not admitted: {exc}; prepare a fresh run (--new-run)") from exc
    return manifest_out, manifest.to_document()[artifacts.DIGEST_FIELD], note


# -- main -----------------------------------------------------------------------
def prepare(*, run_root: Path, local_ip: str | None, consent_confirmed: bool, run_id: str | None = None,
            new_run: bool = False, request: str = DEFAULT_REQUEST, today: str | None = None,
            now: datetime | None = None, scenario: str = "lecture") -> dict:
    global _scenario
    if scenario not in SCENARIOS:
        raise PrepareError(f"unknown scenario {scenario!r}; choose one of {', '.join(SCENARIOS)}")
    _scenario = scenario
    now = now or utc_now()
    day = today or date.today().isoformat()
    run_root = Path(run_root)
    if not local_ip:
        local_ip = _current_pack(run_root).get("local_ip")  # keep the address a previous run was given
    notes = [ensure_consent(run_root / "consent.sqlite3", day, confirmed=consent_confirmed,
                            location=_location())]
    binding = _binding()
    registry_path = run_root / "registry.json"
    run_root.mkdir(parents=True, exist_ok=True)
    _, note = ensure_registry(registry_path)
    notes.append(note)
    run = choose_run(run_root, registry_path, binding, explicit=run_id, new_run=new_run, now=now)
    evidence_destination = _evidence_destination(run_root, run)
    grant, note = ensure_grant(registry_path, binding, run, evidence_destination=evidence_destination, now=now)
    notes.append(note)
    manifest_path, digest, note = ensure_manifest(run_root, run, grant, registry_path, day=day,
                                                  evidence_destination=evidence_destination, now=now)
    notes.append(note)
    clock_domain_id = f"operator-{run}"
    assurance_paths = room_assurance.write_assurances(
        site_path=_flight_files()["site"], session_id=run, clock_domain_id=clock_domain_id, out_dir=run_root,
        prefix=run,
        evidence_ref="operator-floor-check")
    valid_ms = min(entry.max_assurance_age_ms for entry in binding.site.terminal_contingencies)
    expires_local = (datetime.now().astimezone() + timedelta(milliseconds=valid_ms))
    files = _flight_files()
    pack = dict(
        run_id=run, scenario=scenario, manifest=str(manifest_path.resolve()), authorization_digest=digest,
        grant_registry=str(registry_path.resolve()), grant_sha256=grant.grant_sha256,
        consent_db=str((run_root / "consent.sqlite3").resolve()), location=_location(), date=day,
        clock_domain_id=clock_domain_id, assurances=[str(Path(p).resolve()) for p in assurance_paths],
        assurances_expire_at=expires_local.isoformat(timespec="seconds"),
        run_root=str(run_root.resolve()), site=files["site"], tag_map=files["tag_map"],
        drift_profile=files["drift_profile"], energy_profile=files["energy_profile"],
        command_limits=files["command_limits"], profile_files=[files["profile_file"]],
        previewed_request=request, prepared_at=now.isoformat(timespec="seconds"))
    if local_ip:
        pack["local_ip"] = local_ip
    raw = (json.dumps(pack, indent=2) + "\n").encode("utf-8")
    run_pack = run_root / f"{run}.pack.json"
    current_pack = run_root / CURRENT_PACK_NAME
    artifacts.atomic_write_bytes(run_pack, raw)
    artifacts.atomic_write_bytes(current_pack, raw)
    return dict(pack=pack, run_pack=run_pack, current_pack=current_pack, notes=notes, expires_local=expires_local,
                manifest=manifest_path, digest=digest)


def _report(result, run_root: Path, local_ip: str | None) -> str:
    pack = result["pack"]
    current = result["current_pack"].as_posix()
    expires = result["expires_local"]
    ip = f" --local-ip {local_ip}" if local_ip else ""
    lines = [*result["notes"],
             f"assurances: {', '.join(Path(p).name for p in pack['assurances'])}",
             "",
             f"run:  {pack['run_id']} (scenario {pack.get('scenario', 'canningst')})",
             f"site: {Path(pack['site']).name}  tags: {Path(pack['tag_map']).name}",
             f"pack: {result['run_pack'].as_posix()}",
             f"      (also {current}; Mission Control re-reads it at each approval)",
             f"site assurances expire at {expires:%H:%M:%S} local time (UTC{expires:%z}); "
             "re-run this script after that, and before every new Live flight.",
             f"previewed request: \"{pack['previewed_request']}\" (the manifest does not bind the request "
             "text; the request typed in the panel is the one flown)",
             "",
             "Motors-off check (L0) on this manifest (optional, keeps this run reusable):",
             f"  python scripts/room_session.py live --execute --manifest {result['manifest'].as_posix()} "
             f"--authorization-digest {result['digest']} --capability {CAPABILITY} "
             f"--grant-registry {(run_root / 'registry.json').as_posix()} "
             f"--consent-db {(run_root / 'consent.sqlite3').as_posix()} --location {pack['location']} "
             f"--date {pack['date']} --i-authorise-paid-calls --run-root {run_root.as_posix()} "
             f"--connectivity-timeout-s 20 --connectivity-max-age-s 30 --check-connectivity-only{ip}",
             "",
             "Launch Mission Control Live:",
             '  $env:PYTHONPATH="src;."',
             # The site profile carries the venue's cube site and tag map for
             # Mission Control's own startup validation, so it follows the
             # scenario's venue rather than naming CanningSt for every room.
             f"  python -m sentinel.ui --mode live --site-profile {pack['location']} "
             "--data-dir artifacts/room-live "
             f"--room-live-config {current} --i-authorise-paid-calls"]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="room_live_prepare", description=__doc__.splitlines()[0])
    parser.add_argument("--run-root", required=True, help="where the registry, consent, manifests and packs live")
    parser.add_argument("--local-ip", default=None, help="the laptop's address on the Tello Wi-Fi (pack default)")
    parser.add_argument("--consent-confirmed", action="store_true",
                        help="record today's CanningSt filming consent: everyone who may appear in frame agreed")
    parser.add_argument("--run-id", default=None, help="use this run id (default: reuse the unflown current run "
                                                       "or make a fresh l3-<time>)")
    parser.add_argument("--new-run", action="store_true", help="always prepare a fresh run")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="lecture",
                        help="flight scenario: lecture (the measured lecture room, five top-height poses, its "
                             "own tags at 49.5 cm; the default, and what Mission Control's prepare "
                             "uses -- the venue the demo is in, so an untouched drop-down cannot "
                             "prepare another room's geometry), "
                             "demo360 (compact CanningSt site + left/backward/right looks from the pad, "
                             "60 cm error limit; the default, also what Mission Control's prepare uses), "
                             "demo5 (demo360's site from five top-height poses), "
                             "lecture (demo5's five poses in the measured lecture room, its own "
                             "tags at 49.5 cm), demo (compact, 60 cm), canningst (compact, 30 cm) "
                             "or wide (3x3 zones)")
    parser.add_argument("--request", default=DEFAULT_REQUEST,
                        help="label only: the manifest does not bind the request text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_root = Path(args.run_root)
    try:
        result = prepare(run_root=run_root, local_ip=args.local_ip, consent_confirmed=args.consent_confirmed,
                         run_id=args.run_id, new_run=args.new_run, request=args.request, scenario=args.scenario)
    except (PrepareError, live_session.LiveSessionError, capabilities.GrantRegistryError, OSError,
            ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(_report(result, run_root, result["pack"].get("local_ip")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
