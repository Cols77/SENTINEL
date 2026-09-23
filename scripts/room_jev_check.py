"""Task V: does the REAL Jev model make sensible choices in the room loop?

Runs ``sentinel.room.controller.RoomController`` with the real ``JevJudge``
(the same construction ``sentinel.coordinator.proposer_factory.build_proposer``
uses for the ``jev`` proposer arm: OpenRouter, ``JEV_PROPOSER_MODEL``) against
a FAKE flight stack and FAKE supervisor environment -- an offline
``SafetySupervisor``/stub executor pair, exactly the "safety" test fixture's
own construction, bound to the real CanningSt site fixture instead of a
synthetic one -- and a fake detector that reports a phone only from a chosen
region. The drone never leaves the process: no real Tello client, no real
detector, nothing but Jev's own judgment calls leaves the machine.

Four cases (search, staleness/re-anchor, count, read-only); see
``docs/reports/room-agent-jev-check.md`` for the results this produced.

Paid calls are gated exactly the way ``build_proposer`` gates them: the
``SENTINEL_JEV_PROPOSER_CONSENT`` environment variable must be set (any
non-blank value) and ``OPENROUTER_API_KEY`` must resolve, or this refuses
before making any request. Both are read from the nearest ``.env`` file
(walking up from the current working directory) the same way the rest of
this codebase's live-model scripts already do (see
``scripts/live_llm_arm_retry.py``), never printed or persisted.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from sentinel.camera import CameraModel  # noqa: E402
from sentinel.coordinator.proposer_factory import JEV_PROPOSER_MODEL, require_jev_proposer_consent  # noqa: E402
from sentinel.cube.checkpoint import CheckpointPlan, CommandLimits, PlannedCommand  # noqa: E402
from sentinel.cube.energy import EnergyProfile, EnergySample, ExecutionMode, ReserveGuard  # noqa: E402
from sentinel.cube.localization import DriftProfile, LocalizationTracker, PoseFix  # noqa: E402
from sentinel.cube.step_executor import CheckpointOutcome, CommandState, ExecutorState, TerminalOutcome  # noqa: E402
from sentinel.judgment import ChoiceJudge, JevJudge, JudgeUnavailable  # noqa: E402
from sentinel.models import Position, Telemetry  # noqa: E402
from sentinel.room.controller import RoomController  # noqa: E402
from sentinel.room.goals import GoalLedger, GoalObjective  # noqa: E402
from sentinel.room.grounding import CountGeometry  # noqa: E402
from sentinel.room.ingest import DetectionEvidence, PerceptionRequest, PerceptionResult, detection_digest  # noqa: E402
from sentinel.room.models import CapabilityGrant, CapabilityProfile, GoalKind, GoalStatus  # noqa: E402
from sentinel.room.models import TerminalState, canonical_grant_sha256  # noqa: E402
from sentinel.room.semantic import resolve_pose_id  # noqa: E402
from sentinel.room.site import RoomSite, parse_room_site  # noqa: E402
from sentinel.room.store import RoomStore  # noqa: E402
from sentinel.room.supervisor import SafetySupervisor, calibration_hashes  # noqa: E402
from sentinel.room.wiring import make_count_geometry  # noqa: E402

SESSION = "jev-check"
JEV_CALL_BUDGET = 40
ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "room-agent" / "jev-check"
DEFAULT_SITE = REPO_ROOT / "fixtures" / "room_agent" / "v1" / "canningst-site.json"
FALLBACK_SITE = REPO_ROOT / "fixtures" / "room_agent" / "v1" / "semantic-site.json"


# --------------------------------------------------------------------------
# Site / consent / dotenv plumbing
# --------------------------------------------------------------------------


def load_site(path: Path | None = None) -> tuple[RoomSite, Path]:
    """The CanningSt site, or the semantic-site fixture if it is missing."""
    candidate = path or (DEFAULT_SITE if DEFAULT_SITE.exists() else FALLBACK_SITE)
    document = json.loads(candidate.read_text(encoding="utf-8"))
    return parse_room_site(document), candidate


def load_dotenv_nearest(start: Path | None = None) -> dict[str, str]:
    """The nearest ``.env``, walking up from *start* (default: cwd).

    Never raises: a run with no ``.env`` anywhere above it just finds
    nothing, and the consent/key gates below refuse in the usual way.
    """
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            env: dict[str, str] = {}
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                env[name.strip()] = value.strip()
            return env
    return {}


def apply_nearest_dotenv() -> None:
    for name, value in load_dotenv_nearest().items():
        os.environ.setdefault(name, value)


class JevCheckRefused(RuntimeError):
    """The real-model gate refused before any request could be made."""


def build_real_jev_judge(*, observation_callback=None) -> JevJudge:
    """The same construction ``build_proposer('jev', ...)`` uses.

    Refuses fail-closed, exactly like ``build_proposer``, unless
    ``SENTINEL_JEV_PROPOSER_CONSENT`` is set (fresh, explicit operator
    consent -- plan gate G-live-jev-proposer) AND ``OPENROUTER_API_KEY``
    resolves. Both come from the nearest ``.env`` if the environment does
    not already have them.
    """
    apply_nearest_dotenv()
    try:
        require_jev_proposer_consent()
    except PermissionError as exc:
        raise JevCheckRefused(str(exc)) from exc
    if not os.environ.get("OPENROUTER_API_KEY", "").strip():
        raise JevCheckRefused(
            "OPENROUTER_API_KEY is not set (checked the environment and the nearest .env); "
            "a real Jev run cannot be made without it")
    return JevJudge(api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai",
                    model=JEV_PROPOSER_MODEL, observation_callback=observation_callback)


class BudgetedJudge:
    """Wraps a ``ChoiceJudge``, refusing once *budget* calls have been made.

    Every call (successful or not) is logged verbatim -- state, instruction,
    criteria and the raw response -- so the check's report can quote Jev's
    own reasoning per case.
    """

    def __init__(self, inner: ChoiceJudge, *, budget: int = JEV_CALL_BUDGET) -> None:
        self._inner = inner
        self.budget = budget
        self.calls: list[dict] = []

    def choice(self, state: dict, instruction: str, *, criteria, name: str) -> dict:
        if len(self.calls) >= self.budget:
            raise JudgeUnavailable(f"room_jev_check budget of {self.budget} Jev calls exhausted")
        try:
            answer = self._inner.choice(state, instruction, criteria=criteria, name=name)
        except Exception as exc:
            self.calls.append({"criteria": dict(criteria), "error": f"{type(exc).__name__}: {exc}"})
            raise
        self.calls.append({"criteria": dict(criteria), "answer": answer})
        return answer


# --------------------------------------------------------------------------
# Fake supervisor / flight stack -- the "safety" test fixture, on the real site
# --------------------------------------------------------------------------


def _drift_profile() -> DriftProfile:
    # Generous tolerances and near-zero drift RATES (Task V is a fake,
    # offline supervisor environment over the real site's geometry -- see
    # the task report's "known gaps": the real CanningSt drift profile's
    # measured rates, applied to this harness's generic ~60s worst-case
    # home-route budget, project xy_uncertainty past even that profile's
    # own 30 cm ceiling before any turn is ever taken, refusing home_clear
    # admission outright. Jev's own choices, not this drift model, are what
    # Task V checks -- matching the existing "safety" test fixture's own
    # convention of generous/near-static tolerances over a fixed fixture
    # pose. Nothing here drifts to COASTING/LOST except where a case forces
    # it via ``coast()``.
    return DriftProfile(
        schema_version="pose-drift-v1", profile_id="room-jev-check", validated=True,
        evidence_refs=("room-jev-check",), max_camera_age_s=2000, max_fix_age_s=2000,
        max_estimator_failures=3, max_look_away_s=300, max_translation_cm=2000,
        max_yaw_deg=180, max_xy_uncertainty_cm=2000, max_heading_uncertainty_deg=2000,
        xy_drift_cm_s=0.001, xy_drift_per_cm=0.001, xy_drift_per_yaw_deg=0.001,
        heading_drift_deg_s=0.001, heading_drift_per_cm=0.001, heading_drift_per_yaw_deg=0.001,
        max_fix_age_on_arrival_s=1, max_fix_jump_cm=30, max_fix_heading_jump_deg=20,
        recovery_deadline_s=8, max_recovery_attempts=4, min_burst_reports=3,
        max_burst_spread_cm=10, max_burst_heading_spread_deg=8,
        max_height_error_cm=10, rotation_radius_cm=15,
        recovery_action_upper_bound_s=1, canonical_heading_tolerance_deg=2,
    )


class _Clock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Executor:
    """The stub flight executor the room controller test suite uses,
    reporting whatever pose ``resolve_checkpoint`` last targeted."""

    def __init__(self, store: RoomStore, clock: Callable[[], float]) -> None:
        self.store, self.clock = store, clock
        self.state = ExecutorState.AIRBORNE
        self.calls: list[str] = []
        self.reported_pose = Position(250, 190, 120)

    def execute(self, plan: CheckpointPlan) -> CheckpointOutcome:
        self.calls.extend(c.kind for c in plan.commands)
        states = tuple(CommandState(c, "COMPLETED", 0.0, 1.0) for c in plan.commands)
        return CheckpointOutcome("COMPLETED", states, ("observation",), Telemetry(76, 100),
                                 self.reported_pose, True)

    def return_home(self, plan: CheckpointPlan) -> CheckpointOutcome:
        self.state = ExecutorState.RETURNING
        return self.execute(plan)

    def apply_reanchor(self, pose, *, persist) -> None:
        persist()

    def land(self, *, upper_bound_s=None, deadline=None) -> TerminalOutcome:
        self.calls.append("LAND")
        self.state = ExecutorState.TERMINAL
        return TerminalOutcome("LANDED", "REQUESTED", self.reported_pose, 75, self.clock())


@dataclass
class Environment:
    """Everything one case needs: a fake, offline supervisor/store/executor
    pair bound to the real site, plus the fake observation collaborators."""

    site: RoomSite
    site_path: Path
    supervisor: SafetySupervisor
    store: RoomStore
    clock: _Clock
    executor: Executor
    tracker: LocalizationTracker
    samples: dict
    count_geometry: CountGeometry
    detections: dict[str, PerceptionResult] = field(default_factory=dict)
    captures: list[str] = field(default_factory=list)


#: All three CanningSt regions this fixture declares (Task V adapts the
#: brief's illustrative "front-left-top" wording to the regions this site
#: actually has -- see the report's "known gaps" section).
def declared_regions(site: RoomSite) -> tuple[str, ...]:
    return tuple(sorted(pose_id for pose_id, _cells in site.pose_visibility))


def build_environment(*, db_path: Path, site: RoomSite | None = None) -> Environment:
    site, site_path = (site, DEFAULT_SITE) if site is not None else load_site()
    drift = _drift_profile()
    energy_profile = EnergyProfile("room-jev-check-energy", site.cube_site_sha256, "aircraft",
                                   .01, 1, 20, 2, "SIMULATION_ONLY")
    limits = CommandLimits(trim_rounds=0, localise=False)
    clock = _Clock()
    # Every checkpoint this site's pose_visibility offers declares the same
    # yaw (all "toward_targets"), so one fixed fix at that yaw satisfies
    # ``RoomController._measured_pose_matches_declared_pose`` (a direct,
    # unconverted comparison against ``resolved.yaw_deg``) for every region
    # without ever needing to move the tracker between observations. A site
    # whose declared poses face different directions would need a resolver
    # that also updates the tracker's heading per pose -- out of this
    # harness's scope (see the task report).
    regions = declared_regions(site)
    declared_yaw = resolve_pose_id(site, regions[0]).yaw_deg if regions else site.home.heading_deg
    home = site.home.nominal_pose
    tracker = LocalizationTracker(drift, clock=clock)
    tracker.accept_fix(PoseFix(Position(home.x_cm, home.y_cm, 120), float(declared_yaw), clock.now, 1, 1, (1, 2)))

    hashes = calibration_hashes(energy_profile, drift, limits, site)
    grant = CapabilityGrant("ADAPTIVE_FLIGHT", "SIMULATION_ONLY", site.cube_site_sha256, "aircraft",
                            hashes, ("simulation",), "2099-01-01T00:00:00Z", "")
    grant = replace(grant, grant_sha256=canonical_grant_sha256(grant))
    capabilities = CapabilityProfile((grant,))

    store = RoomStore.open(db_path)
    store.create_session(SESSION, "room jev check", capabilities)

    samples = {"pose": tracker.health, "energy": EnergySample(80, clock.now)}

    def refresh():
        return samples["pose"], samples["energy"]

    def emergency(executor):
        executor.state = ExecutorState.TERMINAL
        return TerminalOutcome("EMERGENCY_ABORTED", "NAVIGATION_UNAVAILABLE", None, None, clock())

    supervisor = SafetySupervisor(
        store=store, session_id=SESSION, site=site, tracker=tracker,
        reserve=ReserveGuard(energy_profile, clock), limits=limits, aircraft_id="aircraft",
        mode=ExecutionMode.SIMULATION, clock=clock, refresh=refresh, emergency=emergency,
        wall_clock=lambda: datetime(2026, 9, 23, tzinfo=timezone.utc))
    supervisor.validate_bindings = lambda site: None

    from sentinel.room.site import ContingencyAssurance
    for entry in site.terminal_contingencies:
        store.append_contingency_assurance(
            ContingencyAssurance(
                assurance_id=f"jev-check-{entry.contingency_id}", contingency_id=entry.contingency_id,
                session_id=SESSION, clock_domain_id=supervisor.clock_domain_id, state="CURRENT",
                assured_at="2026-09-23T00:00:00Z", assured_monotonic_ms=0,
                expires_monotonic_ms=10_000_000, evidence_refs=("room-jev-check",)),
            expected_version=store.session_version(SESSION))

    executor = Executor(store, clock)
    executor.reported_pose = Position(home.x_cm, home.y_cm, 120)
    count_geometry = make_count_geometry(site)
    return Environment(site=site, site_path=site_path, supervisor=supervisor, store=store, clock=clock,
                       executor=executor, tracker=tracker, samples=samples, count_geometry=count_geometry)


def live_refresh(env: Environment) -> None:
    """Wire ``supervisor.refresh`` to the tracker's live health, and give it
    a recovery adapter (Task B item 2c's convention,
    ``tests/room/test_reanchor.py``'s ``live_like``) -- without one, REANCHOR
    is never even offered, coasting or not. Only the staleness case needs
    this; the others keep the static default."""
    from sentinel.cube.recovery import PoseReport, RecoveryController

    def refresh():
        return env.supervisor.tracker.health, env.samples["energy"]
    env.supervisor.refresh = refresh
    home = env.site.home.nominal_pose
    reanchor_position = Position(home.x_cm, home.y_cm, 120)

    def burst_source(action):
        return tuple(PoseReport(str(i), PoseFix(reanchor_position, env.supervisor.tracker.health.heading_deg,
                                                env.clock.now, 1, 1, (1, 2))) for i in range(3))

    env.supervisor.recovery = RecoveryController(env.supervisor.tracker, clock=env.clock,
                                                  burst_source=burst_source, energy_admits=lambda action: True)
    env.supervisor.mapped_heading_tag_ids = ((90, (1, 2)),)


def coast(env: Environment, *, failures: int = 1):
    for _ in range(failures):
        env.tracker.note_camera(env.clock.now, estimator_failed=True)
    return env.tracker.health


# --------------------------------------------------------------------------
# Fake observation collaborators (fake resolver, fake detector)
# --------------------------------------------------------------------------


def make_resolve_checkpoint(env: Environment):
    """Targets whichever pose the candidate's own ID declares, the way
    ``tests/room/test_count_return.py``'s ``make_resolve_checkpoint`` does --
    real semantic-pose-to-plan geometry is Task 1/2's own tested
    responsibility (see that module's docstring); this only needs a
    checkpoint that plausibly reaches the declared pose."""
    def resolve(candidate, snapshot):
        pose = env.supervisor.tracker.health
        pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
        resolved = resolve_pose_id(env.site, pose_id)
        target = Position(resolved.x_cm, resolved.y_cm, resolved.z_cm) if resolved else pose.position
        heading = resolved.yaw_deg if resolved else pose.heading_deg
        env.executor.reported_pose = target
        command = PlannedCommand.from_mapping("CAPTURE", {}, upper_bound_s=1, interruptible=True)
        return CheckpointPlan(candidate.candidate_id, pose.position, target, pose.heading_deg, heading,
                              (command,), 1.0)
    return resolve


#: A generic, always-in-frame bottom-centre point: well below the horizon
#: for TELLO_MEASURED's 8.6-degree pitch (or a level camera) at any range,
#: so a fake detection always grounds to *some* valid room position without
#: needing per-target inverse-projection arithmetic. Task V's detector fakes
#: *presence*, not photogrammetric accuracy -- the room's grounding/counting
#: pipeline has its own dedicated test suite for that.
def _generic_bbox(camera: CameraModel) -> tuple[float, float, float, float]:
    width, height = 30.0, 40.0
    centre_u, centre_v = camera.cx, min(camera.cy + 0.2 * camera.fy, camera.height - height - 1)
    return (centre_u - width / 2.0, centre_v - height, width, height)


def _detection(class_name: str, band: str, camera: CameraModel, *, index: int = 0) -> DetectionEvidence:
    evidence = DetectionEvidence(f"d{index}", class_name, band, None, "VLM_CONFIDENCE_BAND",
                                 _generic_bbox(camera), "FULL_FRAME_PIXELS", camera.width, camera.height,
                                 None, "")
    return replace(evidence, evidence_sha256=detection_digest(evidence))


def make_result(session_id: str, pose_id: str, tmp_dir: Path, detections: tuple[DetectionEvidence, ...],
                *, captured_at: str) -> PerceptionResult:
    frame = tmp_dir / f"{pose_id}.png"
    frame.write_bytes(b"room-jev-check-frame:" + pose_id.encode())
    import hashlib
    request = PerceptionRequest("room-perception-v1", session_id, "observe-action", pose_id,
        f"frame-{pose_id}", "room-jev-check-run", 0, "a" * 64, str(frame),
        hashlib.sha256(frame.read_bytes()).hexdigest(), captured_at, "pose1", "pinned-model")
    return PerceptionResult(request, "SUCCEEDED", "fixture", "pinned-model", "VERIFIED", captured_at, detections)


class FakeDetector:
    """The perception collaborators: captures a frame per dispatched
    checkpoint and reports whatever this case declared for that region."""

    def __init__(self, env: Environment, session_id: str, tmp_dir: Path) -> None:
        self.env, self.session_id, self.tmp_dir = env, session_id, tmp_dir
        self.results: dict[str, PerceptionResult] = {}
        self.captures: list[str] = []

    def set_region(self, pose_id: str, detections: tuple[DetectionEvidence, ...], *,
                   captured_at: str = "2026-09-23T00:00:00+00:00") -> None:
        self.results[pose_id] = make_result(self.session_id, pose_id, self.tmp_dir, detections,
                                            captured_at=captured_at)

    def capture(self, candidate, checkpoint_outcome, snapshot) -> PerceptionRequest:
        pose_id = candidate.candidate_id.rsplit(":", 1)[-1]
        self.captures.append(pose_id)
        if pose_id not in self.results:
            self.set_region(pose_id, ())
        request = self.results[pose_id].request
        return replace(request, snapshot_version=self.env.store.session_version(self.session_id))

    def provide(self, request: PerceptionRequest) -> PerceptionResult:
        result = self.results[request.observation_id]
        return replace(result, request=request)


# --------------------------------------------------------------------------
# Ledger / drone-state seeding
# --------------------------------------------------------------------------


def set_ledger(env: Environment, objectives: tuple[GoalObjective, ...], *, request_text: str) -> None:
    ledger = GoalLedger(request_text, objectives, GoalStatus.PENDING)
    version = env.store.session_version(SESSION)
    env.store.append_event(session_id=SESSION, expected_version=version, event_type="GOAL_LEDGER_ACCEPTED",
                           payload={"goal_revision": 1, "ledger": asdict(ledger)}, correlation_id="jev-check",
                           causation_id=None, idempotency_key=f"jev-check:{SESSION}:{version}:ledger")


def seed_drone_state(env: Environment, *, battery: int) -> None:
    version = env.store.session_version(SESSION)
    env.store.append_event(session_id=SESSION, expected_version=version, event_type="DRONE_STATE_RECORDED",
                           payload={"observed_at": "2026-09-23T00:00:00+00:00", "scope": "aircraft:jev-check",
                                    "uncertainty": "last recorded telemetry; present state unknown",
                                    "state": {"battery_percent": battery, "flight_state": "LANDED"}},
                           correlation_id="jev-check", causation_id=None,
                           idempotency_key=f"jev-check:{SESSION}:{version}:drone")


def find_objective(*, objective_id: str = "g1", classes=("phone",), scope="ROOM",
                   terminal="OBSERVED") -> GoalObjective:
    return GoalObjective(objective_id, 1, GoalKind.FIND_OBSERVE, classes, None, None, scope, terminal,
                        None, None, ())


def count_objective(*, objective_id: str = "g1", classes=("phone",), scope="ROOM") -> GoalObjective:
    return GoalObjective(objective_id, 1, GoalKind.COUNT, classes, None, None, scope, "REPORTED",
                        None, None, ())


def answer_state_objective(*, objective_id: str = "g1") -> GoalObjective:
    return GoalObjective(objective_id, 1, GoalKind.ANSWER_STATE, (), None, None, "ROOM", "REPORTED",
                        None, None, ())


# --------------------------------------------------------------------------
# Case result / event logging
# --------------------------------------------------------------------------


@dataclass
class CaseResult:
    name: str
    passed: bool
    reason: str
    terminal_state: str | None
    jev_calls: int
    answer: str | None = None


def write_run_log(case_name: str, env: Environment, judge: BudgetedJudge) -> Path:
    run_dir = ARTIFACT_ROOT / case_name
    run_dir.mkdir(parents=True, exist_ok=True)
    events = [asdict(e) for e in env.store.events(SESSION)]
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, default=str) for e in events) + ("\n" if events else ""), encoding="utf-8")
    (run_dir / "jev_calls.jsonl").write_text(
        "\n".join(json.dumps(c, default=str) for c in judge.calls) + ("\n" if judge.calls else ""),
        encoding="utf-8")
    return run_dir


def _invalid_choice_events(env: Environment) -> list[dict]:
    bad_codes = {"ILLEGAL_OR_MALFORMED", "MISSING_OR_NON_MAX_SELECTION", "INVALID_CONFIDENCE",
                "MALFORMED_ANSWER", "INVALID_CONFIGURATION"}
    return [asdict(e) for e in env.store.events(SESSION)
           if e.event_type == "ROOM_TOOL_OUTCOME" and e.payload.get("refusal_code") in bad_codes]


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------


def run_search_case(judge_factory: Callable[[], ChoiceJudge], *, tmp_dir: Path,
                    found_region: str | None = None) -> CaseResult:
    env = build_environment(db_path=tmp_dir / "search.sqlite3")
    try:
        regions = declared_regions(env.site)
        found_region = found_region or regions[0]
        detector = FakeDetector(env, SESSION, tmp_dir)
        camera = env.count_geometry.camera
        for pose_id in regions:
            detections = (_detection("phone", "low", camera),) if pose_id == found_region else ()
            detector.set_region(pose_id, detections)
        set_ledger(env, (find_objective(),), request_text="find the phone")

        judge = BudgetedJudge(judge_factory())
        controller = RoomController(env.store, judge, supervisor=env.supervisor, flight_executor=env.executor,
                                    resolve_checkpoint=make_resolve_checkpoint(env),
                                    perception_capture=detector.capture, perception_provider=detector.provide,
                                    count_geometry=env.count_geometry)
        try:
            terminal = controller.run_until_terminal(SESSION)
        except JudgeUnavailable as exc:
            return CaseResult("search", False, f"Jev call budget exhausted: {exc}", None, len(judge.calls))
        finally:
            write_run_log("search", env, judge)

        answer_event = next((e for e in reversed(env.store.events(SESSION))
                            if e.event_type == "ROOM_TOOL_OUTCOME" and e.payload.get("answer")), None)
        answer = answer_event.payload.get("answer") if answer_event else None
        invalid = _invalid_choice_events(env)
        reasons = []
        if terminal != TerminalState.COMPLETED:
            reasons.append(f"terminal_state={terminal!r}, expected COMPLETED")
        if invalid:
            reasons.append(f"{len(invalid)} invalid/malformed Jev choice(s): {invalid[0].get('refusal_code')}")
        if answer is None or found_region.split("-toward")[0] not in answer.replace("_", "-"):
            reasons.append(f"answer does not name the found region {found_region!r}: {answer!r}")
        if "LAND" not in env.executor.calls:
            reasons.append("the aircraft never landed before the answer was recorded")
        passed = not reasons
        return CaseResult("search", passed, "; ".join(reasons) or "search completed, phone named, home returned",
                          terminal.value if terminal else None, len(judge.calls), answer)
    finally:
        env.store.close()


def run_staleness_case(judge_factory: Callable[[], ChoiceJudge], *, tmp_dir: Path) -> CaseResult:
    env = build_environment(db_path=tmp_dir / "staleness.sqlite3")
    try:
        regions = declared_regions(env.site)
        found_region = regions[-1]
        detector = FakeDetector(env, SESSION, tmp_dir)
        camera = env.count_geometry.camera
        for pose_id in regions:
            detections = (_detection("phone", "low", camera),) if pose_id == found_region else ()
            detector.set_region(pose_id, detections)
        set_ledger(env, (find_objective(),), request_text="find the phone")
        live_refresh(env)

        judge = BudgetedJudge(judge_factory())
        controller = RoomController(env.store, judge, supervisor=env.supervisor, flight_executor=env.executor,
                                    resolve_checkpoint=make_resolve_checkpoint(env),
                                    perception_capture=detector.capture, perception_provider=detector.provide,
                                    count_geometry=env.count_geometry)
        observed = 0
        coasted = False
        terminal = None
        try:
            while terminal is None:
                outcome = controller.step(SESSION)
                terminal = outcome.terminal_state
                if outcome.accepted and str(outcome.candidate_id).startswith("OBSERVE:"):
                    observed += 1
                if observed == 2 and not coasted:
                    coast(env, failures=2)
                    coasted = True
        except JudgeUnavailable as exc:
            return CaseResult("staleness", False, f"Jev call budget exhausted: {exc}", None, len(judge.calls))
        finally:
            write_run_log("staleness", env, judge)

        events = env.store.events(SESSION)
        reanchored = any(e.event_type == "POSE_REANCHORED" for e in events)
        forced_recover = any(e.event_type == "ROOM_TOOL_OUTCOME" and e.payload.get("candidate_id") == "RECOVER"
                             for e in events)
        which = "Jev chose REANCHOR" if reanchored else ("supervisor forced RECOVER" if forced_recover else "neither")
        answer_event = next((e for e in reversed(events)
                            if e.event_type == "ROOM_TOOL_OUTCOME" and e.payload.get("answer")), None)
        answer = answer_event.payload.get("answer") if answer_event else None
        invalid = _invalid_choice_events(env)
        reasons = []
        if not coasted:
            reasons.append("the harness never reached a second observation to inject staleness from")
        if not (reanchored or forced_recover):
            reasons.append("neither a voluntary REANCHOR nor a forced RECOVER happened after staleness")
        if terminal != TerminalState.COMPLETED:
            reasons.append(f"terminal_state={terminal!r}, expected COMPLETED (search resumed and completed)")
        if invalid:
            reasons.append(f"{len(invalid)} invalid/malformed Jev choice(s): {invalid[0].get('refusal_code')}")
        passed = not reasons
        reason = f"{which}, then " + ("; ".join(reasons) or "the search resumed and completed")
        return CaseResult("staleness", passed, reason, terminal.value if terminal else None, len(judge.calls), answer)
    finally:
        env.store.close()


def run_count_case(judge_factory: Callable[[], ChoiceJudge], *, tmp_dir: Path) -> CaseResult:
    env = build_environment(db_path=tmp_dir / "count.sqlite3")
    try:
        regions = declared_regions(env.site)
        if len(regions) < 2:
            return CaseResult("count", False, "the site declares fewer than two observable regions", None, 0)
        detector = FakeDetector(env, SESSION, tmp_dir)
        camera = env.count_geometry.camera
        bands = {regions[0]: "high", regions[1]: "low"}
        for pose_id in regions:
            band = bands.get(pose_id)
            detections = (_detection("phone", band, camera),) if band else ()
            detector.set_region(pose_id, detections)
        set_ledger(env, (count_objective(),), request_text="count the phones in the room")

        judge = BudgetedJudge(judge_factory())
        controller = RoomController(env.store, judge, supervisor=env.supervisor, flight_executor=env.executor,
                                    resolve_checkpoint=make_resolve_checkpoint(env),
                                    perception_capture=detector.capture, perception_provider=detector.provide,
                                    count_geometry=env.count_geometry)
        try:
            terminal = controller.run_until_terminal(SESSION)
        except JudgeUnavailable as exc:
            return CaseResult("count", False, f"Jev call budget exhausted: {exc}", None, len(judge.calls))
        finally:
            write_run_log("count", env, judge)

        events = env.store.events(SESSION)
        answer_event = next((e for e in reversed(events)
                            if e.event_type == "ROOM_TOOL_OUTCOME" and e.payload.get("answer")), None)
        answer = answer_event.payload.get("answer") if answer_event else None
        invalid = _invalid_choice_events(env)
        reasons = []
        if terminal != TerminalState.COMPLETED:
            reasons.append(f"terminal_state={terminal!r}, expected COMPLETED")
        if invalid:
            reasons.append(f"{len(invalid)} invalid/malformed Jev choice(s): {invalid[0].get('refusal_code')}")
        if answer is None or "phone: 1 confirmed; 1 uncertain" not in answer:
            reasons.append("count answer was not 'phone: 1 confirmed; 1 uncertain' "
                          f"(see docs/reports/room-agent-jev-check.md for what it actually said): {answer!r}")
        if answer is not None and "coverage" not in answer.lower():
            reasons.append("no coverage note in the answer")
        if "LAND" not in env.executor.calls:
            reasons.append("the aircraft never landed before the answer was recorded")
        passed = not reasons
        return CaseResult("count", passed, "; ".join(reasons) or "counted correctly, coverage noted, home returned",
                          terminal.value if terminal else None, len(judge.calls), answer)
    finally:
        env.store.close()


def run_readonly_case(judge_factory: Callable[[], ChoiceJudge], *, tmp_dir: Path) -> CaseResult:
    env = build_environment(db_path=tmp_dir / "readonly.sqlite3")
    try:
        set_ledger(env, (answer_state_objective(),), request_text="what is your battery level?")
        seed_drone_state(env, battery=61)
        env.executor.state = ExecutorState.NEW  # never took off: a read-only session must not appear airborne

        judge = BudgetedJudge(judge_factory())
        controller = RoomController(env.store, judge, supervisor=env.supervisor, flight_executor=env.executor)
        try:
            terminal = controller.run_until_terminal(SESSION)
        except JudgeUnavailable as exc:
            return CaseResult("readonly", False, f"Jev call budget exhausted: {exc}", None, len(judge.calls))
        finally:
            write_run_log("readonly", env, judge)

        events = env.store.events(SESSION)
        answer_event = next((e for e in reversed(events)
                            if e.event_type == "ROOM_TOOL_OUTCOME" and e.payload.get("answer")), None)
        answer = answer_event.payload.get("answer") if answer_event else None
        invalid = _invalid_choice_events(env)
        reasons = []
        if terminal != TerminalState.COMPLETED:
            reasons.append(f"terminal_state={terminal!r}, expected COMPLETED")
        if invalid:
            reasons.append(f"{len(invalid)} invalid/malformed Jev choice(s): {invalid[0].get('refusal_code')}")
        if env.executor.calls:
            reasons.append(f"zero movement expected, executor was called: {env.executor.calls}")
        if answer is None or "battery_percent=61" not in answer:
            reasons.append(f"answer does not report the battery level directly: {answer!r}")
        passed = not reasons
        return CaseResult("readonly", passed, "; ".join(reasons) or "zero movement, direct battery answer",
                          terminal.value if terminal else None, len(judge.calls), answer)
    finally:
        env.store.close()


CASES: dict[str, Callable[..., CaseResult]] = {
    "search": run_search_case, "staleness": run_staleness_case,
    "count": run_count_case, "readonly": run_readonly_case,
}


def run_all(judge_factory: Callable[[], ChoiceJudge], *, tmp_dir: Path,
           cases: tuple[str, ...] = tuple(CASES)) -> list[CaseResult]:
    return [CASES[name](judge_factory, tmp_dir=tmp_dir) for name in cases]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=tuple(CASES), default=None,
                        help="run only this case (repeatable); default: all four")
    parser.add_argument("--run-root", default=None,
                        help="scratch directory for the run's sqlite stores (default: a fresh temp dir)")
    args = parser.parse_args(argv)

    import tempfile
    run_root = Path(args.run_root) if args.run_root else Path(tempfile.mkdtemp(prefix="room-jev-check-"))
    run_root.mkdir(parents=True, exist_ok=True)

    try:
        judge_factory = lambda: build_real_jev_judge()  # noqa: E731
        judge_factory()  # fail fast, before any case's fixture is built
    except JevCheckRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    cases = tuple(args.case) if args.case else tuple(CASES)
    ok = True
    for name in cases:
        result = CASES[name](judge_factory, tmp_dir=run_root / name)
        status = "PASS" if result.passed else "FAIL"
        ok = ok and result.passed
        print(f"{status} {result.name}: {result.reason} "
              f"(terminal={result.terminal_state}, jev_calls={result.jev_calls})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
