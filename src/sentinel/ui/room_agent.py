"""Mission Control's room-agent architecture (Task C): Jev adaptive room loop.

The operator selects **Room agent (Jev adaptive)** next to the survey
architecture, picks a run mode, types a request, approves it and watches it
run. Approval is dispatch, exactly as for the survey: nothing that can fly,
call a paid provider or open a drone link is constructed before the
operator's APPROVE.

Run modes:

- ``REHEARSAL``: the production composition (``live_session.run_count_return``
  over ``live_stack.TelloFlightStackFactory`` and the real ``DjitellopyClient``)
  on a fake CanningSt aircraft, a scene detector and a scripted Jev
  (``room_rehearsal``).
- ``JEV_REHEARSAL``: the same fake aircraft and detector, the REAL Jev over
  OpenRouter (``live_stack.default_jev``), built only after approval and only
  when Mission Control was started with ``--i-authorise-paid-calls``.
- ``LIVE``: ``scripts/room_session.py live`` with every one of its gates
  (manifest + authorization digest, capability admission of the count_return
  TRIAL grant, consent, site assurances, calibration binding, connectivity
  preflight), fed from the operator's live pack (``--room-live-config``).

Land and Emergency: the run's flight stack is registered in the session's
``SafetyRegistry`` as the active runner, so the existing Mission Control
Land/Emergency endpoints act on it. A controlled Land goes through the room
``StepExecutor.land()`` (its own operation and SDK locks, never around them),
so it is recorded LANDED; Emergency latches the session and sends the
runner's direct motor stop.
"""

import contextlib
from dataclasses import replace
from datetime import datetime, timezone
import io
import ipaddress
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

from .room_agent_projection import RoomAgentProjection
from . import room_rehearsal
from .room_review import RoomReview

ARCHITECTURES = (
    dict(id="survey", label="Survey (rules proposer)",
         description="The existing survey loop: a rules proposer offers a sweep, you approve it."),
    dict(id="room_agent", label="Room agent (Jev adaptive)",
         description="Jev chooses each observation, re-anchor and answer turn by turn; the safety "
                     "supervisor bounds every move and returns home."),
)
MODES = {
    "REHEARSAL": "Rehearsal: fake drone, fake detector, scripted Jev",
    "JEV_REHEARSAL": "Jev rehearsal: fake drone and detector, real Jev (paid calls after approval)",
    "LIVE": "Live: real Tello, real detector, real Jev",
}
DEFAULT_REQUEST = "count the phones and computers in the room"
PERCEPTION_REVIEWS = (
    dict(id="STANDARD", label="Standard: live perception only"),
    dict(id="RECORDED_REVIEW", label="Standard + recorded post flight review"),
)
SITE_LINE = "Site: CanningSt (fixtures/room_agent/v1/canningst-site.json)"
def _now():
    return datetime.now(timezone.utc).isoformat()


class RoomRunnerSafety:
    """The active-runner face of one room run for ``SafetyRegistry``.

    ``request_controlled_land`` lands through the room executor itself
    (``StepExecutor.land``: it takes the executor's operation lock after the
    in-flight command, then the SDK lock), so the terminal outcome is LANDED,
    never a LANDING_FAILED race. ``latch_emergency`` is the runner's direct
    motor stop (``FlightTestRunner.trigger_emergency``)."""

    def __init__(self, on_note=None):
        self.stack = None
        self.land_status = "IDLE"
        self.land_outcome = None
        self.emergency_sent = False
        self._on_note = on_note or (lambda *a, **k: None)
        self._lock = threading.RLock()

    def attach(self, stack):
        with self._lock:
            self.stack = stack

    def request_controlled_land(self):
        with self._lock:
            stack = self.stack
        if stack is None:
            raise RuntimeError("NO_FLIGHT_STACK")
        self.land_status = "REQUESTED"
        self._on_note("OPERATOR", "Operator Land requested: landing through the room executor")
        outcome = stack.executor.land(upper_bound_s=15.0)
        self.land_outcome = outcome
        self.land_status = "ACKNOWLEDGED" if outcome.state == "LANDED" else "FAILED"
        self._on_note("OPERATOR", f"Operator Land: {outcome.state} ({outcome.reason})")
        if self.land_status != "ACKNOWLEDGED":
            raise RuntimeError(f"LAND_{outcome.state}")

    def latch_emergency(self):
        with self._lock:
            stack = self.stack
        self.emergency_sent = True
        self._on_note("OPERATOR", "EMERGENCY: motor stop sent")
        if stack is not None and stack.trigger_emergency is not None:
            stack.trigger_emergency()
        elif stack is not None:
            stack.executor.note_emergency()

    def send_emergency(self):
        """Mission Control's emergency transport for a Live room run."""
        self.latch_emergency()
        return 1


class RoomAgentRun:
    def __init__(self, *, mode, scenario, request_text, local_ip, perception_review="STANDARD"):
        self.id = f"room-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        self.initial_id = self.id
        self.proposal_id = uuid4().hex
        self.mode, self.scenario, self.request_text, self.local_ip = mode, scenario, request_text, local_ip
        self.perception_review = perception_review
        self.status = "AWAITING_APPROVAL"
        self.created_at = _now()
        self.started_at = None
        self.finished_at = None
        self.error = None
        self.projection = RoomAgentProjection(home_xy_cm=room_rehearsal.HOME_XY_CM)
        self.safety = RoomRunnerSafety(on_note=self.projection.note)
        self.tracker = None
        self.clock = time.monotonic
        self.store = None
        self.store_path = None
        self.frame_root = None
        self.camera_source = None
        self.capture_records = {}
        self.review = None
        self.world = None
        self.thread = None
        self.done = threading.Event()
        self.lock = threading.RLock()

    def sync(self):
        """Fold new ledger events, then the tracker's live localization."""
        with self.lock:
            store = self.store
            if store is None and self.store_path is not None and Path(self.store_path).is_file():
                from sentinel.room.store import RoomStore
                try:
                    store = self.store = RoomStore.open(self.store_path)
                except Exception:
                    store = None
            if store is not None:
                try:
                    self.projection.fold(store.events(self.id, after_sequence=self.projection.after_sequence))
                except Exception:
                    pass
            tracker = self.tracker
        if tracker is not None and not self.done.is_set():
            try:
                health = tracker.health
                state = getattr(health.state, "value", str(health.state))
                fix_age = max(0.0, float(self.clock()) - float(health.measured_at))
                position = (health.position.x_cm, health.position.y_cm, health.position.z_cm)
                self.projection.set_live_localization(state=state, fix_age_s=round(fix_age, 2),
                                                      heading_deg=health.heading_deg, position=position)
            except Exception:
                pass

    def proposal(self):
        flies = self.mode == "LIVE"
        lines = [MODES[self.mode], getattr(self, "site_line", SITE_LINE),
                 "Capability: count_return TRIAL grant, CanningSt calibration profiles"]
        if self.mode in ("JEV_REHEARSAL", "LIVE"):
            lines.append("Approving authorises paid Jev (OpenRouter) calls" +
                         (" and paid detector calls" if flies else ""))
        if flies:
            lines.append("Approving is dispatch: the real Tello takes off after the live gates and preflight pass")
            if self.perception_review == "RECORDED_REVIEW":
                lines.append("Record the Tello video for optional local review after landing")
            if self.local_ip:
                lines.append(f"Tello link local IP: {self.local_ip}")
        return dict(id=self.proposal_id, mode=self.mode, scenario=self.scenario, request_text=self.request_text,
                     perception_review=self.perception_review,
                    flies=flies, paid_calls=self.mode != "REHEARSAL", lines=lines)

    def public(self):
        self.sync()
        view = self.projection.public()
        review = None
        if self.perception_review == "RECORDED_REVIEW":
            active = self.review_session()
            review = active.status() if active is not None else {
                "recording": {"status": "PENDING" if not self.done.is_set() else "MISSING"},
                "analysis": {"status": "UNAVAILABLE", "reason_code": "RUN_NOT_FINISHED"},
            }
        return dict(id=self.id, mode=self.mode, mode_label=MODES[self.mode], scenario=self.scenario,
                     perception_review=self.perception_review,
                     review=review,
                    request_text=self.request_text, status=self.status, created_at=self.created_at,
                    started_at=self.started_at, finished_at=self.finished_at, error=self.error,
                    proposal=self.proposal() if self.status == "AWAITING_APPROVAL" else None,
                     land_status=self.safety.land_status, emergency_sent=self.safety.emergency_sent, **view)

    def review_session(self):
        if self.perception_review != "RECORDED_REVIEW" or not self.done.is_set() or self.frame_root is None:
            return None
        with self.lock:
            if self.review is None:
                self.review = RoomReview(self.frame_root, room_rehearsal.REPO_ROOT / "fixtures" / "perception")
            return self.review


class RoomAgentService:
    """One room-agent run at a time, bound to one Mission Control service."""

    def __init__(self, mission, *, live_config_path=None, data_dir=None, pace=True):
        self.mission = mission
        config = mission.config
        self.live_config_path = Path(live_config_path) if live_config_path else getattr(config, "room_live_config", None)
        self.data_dir = Path(data_dir or config.data_dir) / "room-agent"
        self.pace = pace
        self.run: RoomAgentRun | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._preparing = False
        self._port_checked_at = 0.0
        self._port_snapshot = None
        self._live_link_used = False

    # -- catalogue ------------------------------------------------------------
    def modes(self):
        available = ["REHEARSAL", "JEV_REHEARSAL"]
        if self.live_config_path is not None:
            available.append("LIVE")
        return available

    def catalogue(self):
        paid_calls_authorised = bool(getattr(self.mission.config, "authorise_paid_calls", False))
        def mode_entry(mode):
            if mode == "LIVE" and self.live_config_path is None:
                reason = "Live Tello needs a room Live flight pack (--room-live-config)."
            elif mode in ("JEV_REHEARSAL", "LIVE") and not paid_calls_authorised:
                reason = "Jev needs paid calls authorised (--i-authorise-paid-calls)."
            else:
                reason = None
            return dict(id=mode, label=MODES[mode], available=reason is None, reason=reason)

        return dict(
            architectures=list(ARCHITECTURES),
            modes=[mode_entry(m) for m in MODES],
            perception_reviews=list(PERCEPTION_REVIEWS),
            scenarios=[dict(id=s.scenario_id, label=s.label, description=s.description)
                       for s in room_rehearsal.SCENARIOS.values()],
            default_request=DEFAULT_REQUEST,
            site="CanningSt",
            local_ip=self._default_local_ip(),
            paid_calls_authorised=paid_calls_authorised,
        )

    def state(self):
        with self._lock:
            run = self.run
        return dict(catalogue=self.catalogue(), run=run.public() if run else None,
                    preparation=self.preparation_state())

    def _prepare_blocker(self):
        if self.live_config_path is None:
            return "ROOM_LIVE_CONFIG_REQUIRED"
        if self._closed:
            return "CLOSED"
        if self._preparing:
            return "PREPARATION_ACTIVE"
        run = self.run
        if run is not None and (run.status == "RUNNING" or
                                (run.thread is not None and not run.done.is_set())):
            return "ROOM_RUN_ACTIVE"
        return self._mission_busy()

    def _command_port_status(self, local_ip):
        now = time.monotonic()
        with self._lock:
            if self._port_snapshot is not None and now - self._port_checked_at < 3:
                return self._port_snapshot
        try:
            import psutil
            occupants = [connection for connection in psutil.net_connections(kind="udp")
                         if connection.laddr and connection.laddr.port == 8889
                         and (local_ip is None or connection.laddr.ip in (local_ip, "0.0.0.0", "::"))]
            status = (dict(status="BUSY", owner_pid=occupants[0].pid,
                           owner_is_this_server=occupants[0].pid == os.getpid())
                      if occupants else dict(status="FREE"))
        except Exception:
            status = dict(status="UNKNOWN")
        with self._lock:
            self._port_snapshot = status
            self._port_checked_at = now
        return status

    def preparation_state(self):
        pack = self._live_pack(optional=True) or {}
        expiry = pack.get("assurances_expire_at")
        try:
            expires_at = datetime.fromisoformat(expiry)
            pack_expired = expires_at.tzinfo is None or expires_at <= datetime.now(expires_at.tzinfo)
        except (TypeError, ValueError):
            pack_expired = bool(pack)
        try:
            pack_used = (Path(pack["run_root"]) / pack["run_id"] / "terminal.json").exists()
        except (KeyError, TypeError, ValueError):
            pack_used = False
        with self._lock:
            blocker = self._prepare_blocker()
            preparing = self._preparing
        return dict(can_prepare=blocker is None, reason_code=blocker, preparing=preparing,
                    pack_run_id=pack.get("run_id"), assurances_expire_at=expiry,
                    pack_expired=pack_expired, pack_used=pack_used,
                    command_port=self._command_port_status(pack.get("local_ip") or self._default_local_ip()))

    def prepare(self, command):
        if not isinstance(command, dict):
            return dict(status="INVALID", reason_code="INVALID_REQUEST")
        local_ip = command.get("local_ip") or self._default_local_ip()
        if local_ip is not None:
            try:
                address = ipaddress.ip_address(local_ip)
                subnet = ipaddress.ip_network("192.168.10.0/24")
                if (address.version != 4 or address not in subnet or
                        address in (subnet.network_address, subnet.broadcast_address,
                                    ipaddress.ip_address("192.168.10.1"))):
                    raise ValueError
            except ValueError:
                return dict(status="INVALID", reason_code="INVALID_LOCAL_IP")
        with self._lock:
            blocker = self._prepare_blocker()
            if blocker:
                return dict(status="CONFLICT", reason_code=blocker)
            self._preparing = True
        try:
            try:
                from scripts import room_live_prepare
            except ImportError:
                sys.path.insert(0, str(room_rehearsal.REPO_ROOT))
                from scripts import room_live_prepare

            pack = self._live_pack(optional=True) or {}
            pack_path = Path(self.live_config_path).resolve()
            run_root = pack_path.parent
            if pack.get("run_root") and Path(pack["run_root"]).resolve() != run_root:
                return dict(status="UNAVAILABLE", reason_code="ROOM_PACK_ROOT_MISMATCH",
                            detail="The configured Live pack must be inside its run root.")
            result = room_live_prepare.prepare(run_root=run_root, local_ip=local_ip,
                                               consent_confirmed=False, new_run=True)
            with self._lock:
                self.live_config_path = result["current_pack"]
                self._port_checked_at = 0.0
                restart_server = (self._live_link_used and
                                  os.environ.get("SENTINEL_ROOM_UI_SUPERVISED") == "1")
                restart_required = self._live_link_used and not restart_server
                self.run = None
            new_pack = result["pack"]
            return dict(status="ACKNOWLEDGED", run_id=new_pack["run_id"],
                        assurances_expire_at=new_pack["assurances_expire_at"],
                        server_restarting=restart_server, restart_required=restart_required)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            return dict(status="UNAVAILABLE", reason_code="ROOM_PREPARATION_FAILED", detail=str(exc))
        finally:
            with self._lock:
                self._preparing = False

    def _default_local_ip(self):
        pack = self._live_pack(optional=True) or {}
        return pack.get("local_ip") or getattr(self.mission.config, "local_ip", None)

    # -- proposal and approval ------------------------------------------------
    def propose(self, command):
        if not isinstance(command, dict):
            return dict(status="INVALID", reason_code="INVALID_REQUEST")
        mode = command.get("mode", "REHEARSAL")
        perception_review = command.get("perception_review", "STANDARD")
        if perception_review not in {row["id"] for row in PERCEPTION_REVIEWS} or (
                perception_review == "RECORDED_REVIEW" and mode != "LIVE"):
            return dict(status="INVALID", reason_code="INVALID_PERCEPTION_REVIEW")
        scenario = command.get("scenario", "a")
        text = command.get("request_text")
        local_ip = command.get("local_ip") or None
        if mode not in MODES:
            return dict(status="INVALID", reason_code="UNKNOWN_MODE")
        if mode not in self.modes():
            return dict(status="UNAVAILABLE", reason_code="ROOM_LIVE_CONFIG_REQUIRED")
        if scenario not in room_rehearsal.SCENARIOS:
            return dict(status="INVALID", reason_code="UNKNOWN_SCENARIO")
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 4096:
            return dict(status="INVALID", reason_code="INVALID_REQUEST_TEXT")
        if local_ip is not None:
            try:
                address = ipaddress.ip_address(local_ip)
                if address.version != 4 or address.is_loopback or address.is_unspecified:
                    raise ValueError
            except ValueError:
                return dict(status="INVALID", reason_code="INVALID_LOCAL_IP")
        if mode in ("JEV_REHEARSAL", "LIVE") and not getattr(self.mission.config, "authorise_paid_calls", False):
            return dict(status="UNAVAILABLE", reason_code="PAID_CALLS_NOT_AUTHORISED")
        if mode == "LIVE":
            preparation = self.preparation_state()
            if not preparation["pack_run_id"]:
                return dict(status="UNAVAILABLE", reason_code="ROOM_LIVE_CONFIG_REQUIRED")
            if preparation["pack_used"]:
                return dict(status="UNAVAILABLE", reason_code="ROOM_PACK_ALREADY_USED")
            if preparation["pack_expired"]:
                return dict(status="UNAVAILABLE", reason_code="ROOM_PACK_EXPIRED")
            if preparation["command_port"]["status"] == "BUSY":
                return dict(status="UNAVAILABLE", reason_code="TELLO_COMMAND_PORT_IN_USE")
        with self._lock:
            if self._closed:
                return dict(status="UNAVAILABLE", reason_code="CLOSED")
            if self._preparing:
                return dict(status="CONFLICT", reason_code="PREPARATION_ACTIVE")
            if self.run is not None and self.run.status == "RUNNING":
                return dict(status="CONFLICT", reason_code="ROOM_RUN_ACTIVE")
            busy = self._mission_busy()
            if busy:
                return dict(status="CONFLICT", reason_code=busy)
            self.run = RoomAgentRun(mode=mode, scenario=scenario, request_text=text.strip(),
                                    local_ip=local_ip or (self._default_local_ip() if mode == "LIVE" else None),
                                    perception_review=perception_review)
            if mode == "LIVE":
                pack = self._live_pack(optional=True) or {}
                if pack.get("site"):
                    self.run.site_line = (f"Site: CanningSt, {pack.get('scenario', 'canningst')} scenario "
                                          f"({Path(pack['site']).name})")
            self.run.projection.note("PROPOSAL", f"Proposal ready: {MODES[mode]}")
            return dict(status="ACKNOWLEDGED", run=self.run.public())

    def _mission_busy(self):
        mission = self.mission
        run_id = mission.config.run_id
        if mission.registry.emergency.is_set():
            return "SESSION_EMERGENCY_LATCHED"
        if mission.worker.active or mission.registry.get(run_id) is not None:
            return "MISSION_ACTIVE"
        return None

    def decide(self, command):
        if not isinstance(command, dict) or command.get("decision") not in ("APPROVE", "REJECT"):
            return dict(status="INVALID", reason_code="INVALID_DECISION")
        with self._lock:
            run = self.run
            if run is None or run.proposal_id != command.get("proposal_id"):
                return dict(status="CONFLICT", reason_code="PROPOSAL_NOT_CURRENT")
            if run.status != "AWAITING_APPROVAL":
                return dict(status="CONFLICT", reason_code="PROPOSAL_ALREADY_DECIDED")
            if command["decision"] == "REJECT":
                run.status = "REJECTED"
                run.finished_at = _now()
                run.projection.note("PROPOSAL", "Proposal rejected by the operator")
                return dict(status="ACKNOWLEDGED", run=run.public())
            busy = self._mission_busy()
            if busy:
                return dict(status="CONFLICT", reason_code=busy)
            # Approval is dispatch: only now is anything constructed.
            run.status = "RUNNING"
            run.started_at = _now()
            run.projection.note("PROPOSAL", "Approved by the operator: dispatching")
            run.thread = threading.Thread(target=self._execute, args=(run,), name=f"room-agent-{run.id}",
                                          daemon=True)
            run.thread.start()
            return dict(status="ACKNOWLEDGED", run=run.public())

    def wait(self, timeout=None):
        run = self.run
        return run.done.wait(timeout) if run is not None else True

    def close(self):
        with self._lock:
            self._closed = True
            run = self.run
        if run is not None and run.thread is not None:
            run.thread.join(timeout=30)

    # -- execution ------------------------------------------------------------
    def _execute(self, run):
        stop_poll = threading.Event()

        def poll():
            while not stop_poll.wait(0.2):
                run.sync()

        poller = threading.Thread(target=poll, name=f"room-agent-poll-{run.id}", daemon=True)
        poller.start()
        try:
            if run.mode == "LIVE":
                self._run_live(run)
            else:
                self._run_rehearsal(run)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator, never swallowed silently
            run.status = "FAILED" if run.status == "RUNNING" else run.status
            run.error = f"{type(exc).__name__}: {exc}"
            run.projection.note("ERROR", f"Run failed: {run.error}")
        finally:
            stop_poll.set()
            poller.join(timeout=2)
            try:
                self.mission.registry.unbind(self.mission.config.run_id, run.safety)
            except Exception:
                pass
            run.sync()
            run.done.set()
            run.finished_at = _now()
            with run.lock:
                if run.store is not None:
                    try:
                        run.store.close()
                    except Exception:
                        pass
                    run.store = None

    def _attach(self, run, stack, *, tracker=None, clock=None, site=None, **_):
        """Observe the built stack: the active runner for Land/Emergency, the
        tracker for live localization, and each recovery burst for display."""
        run.tracker = tracker
        run.camera_source = stack.frame_source
        run.capture_records = stack.capture_records
        if clock is not None:
            run.clock = clock
        run.safety.attach(stack)
        self.mission.registry.bind(self.mission.config.run_id, run.safety)
        if run.mode == "LIVE":
            self.mission.safety.admit_emergency_sender(run.safety.send_emergency)
        run.projection.note("STACK", "Flight stack connected; Land and Emergency are armed for this run")
        refresh_inner = stack.refresh

        def refresh():
            health, energy = refresh_inner()
            run.projection.observe_localization(getattr(health.state, "value", str(health.state)),
                                                health.heading_deg)
            return health, energy

        stack = replace(stack, refresh=refresh)
        inner = stack.recovery_burst
        if inner is None:
            return stack

        def recovery_burst(action):
            reports = tuple(inner(action))
            run.sync()
            heading = None
            if reports:
                heading = reports[0].fix.heading_deg
            elif tracker is not None:
                heading = tracker.health.heading_deg
            tags = sorted({t for report in reports for t in report.fix.tag_ids})
            kind = getattr(action, "kind", "burst")
            run.projection.note_burst(heading_deg=float(heading or 0.0), tags=tags, reports=len(reports),
                                      purpose=f"recovery {str(kind).lower().replace('_', ' ')}")
            return reports

        return replace(stack, recovery_burst=recovery_burst)

    def _run_rehearsal(self, run):
        from sentinel.room import live_session, live_stack
        from sentinel.room.models import CapabilityProfile
        from sentinel.room.store import RoomStore

        scenario = room_rehearsal.SCENARIOS[run.scenario]
        run_dir = self.data_dir / run.id
        run.frame_root = run_dir
        binding = room_rehearsal.flight_binding()
        world = room_rehearsal.build_world(scenario, run_id=run.id, run_dir=run_dir, pace=self.pace)
        run.world = world
        if run.mode == "JEV_REHEARSAL":
            judge = live_stack.default_jev()
            run.projection.note("JEV", f"Real Jev connected ({judge.identity}); paid calls authorised by approval")
        else:
            judge = room_rehearsal.ScriptedJev(run.request_text, delay_s=0.4 if self.pace else 0.0)
        manifest = room_rehearsal.rehearsal_manifest(binding, run.id, world.detector.identity)
        grant = room_rehearsal.trial_grant(binding, run.id, manifest)
        store = RoomStore.open(run_dir / "session.sqlite3")
        run.store_path = run_dir / "session.sqlite3"
        store.create_session(run.id, run.request_text, CapabilityProfile((grant,)))
        with run.lock:
            run.store = store
        tag_map = live_stack.load_bound_tag_map(room_rehearsal.TAG_MAP_PATH,
                                               expected_sha256=binding.site.tag_map_sha256)
        factory = live_stack.TelloFlightStackFactory(
            link=world.link, tag_map=tag_map, run_id=run.id, run_dir=run_dir, pose_burst=world.world.burst,
            home_settle_s=0.5 if self.pace else 0.0)

        def observed_factory(**kwargs):
            return self._attach(run, factory(**kwargs), **kwargs)

        try:
            outcome = live_session.run_count_return(
                store=store, manifest=manifest, site=binding.site, drift_profile=binding.drift_profile,
                energy_profile=binding.energy_profile, limits=binding.limits,
                calibration_profiles=binding.calibration_profiles, flight_stack_factory=observed_factory,
                judge=judge, detector=world.detector,
                assurances=room_rehearsal.rehearsal_assurances(run.id, binding.site),
                request_text=run.request_text, frames_dir=run_dir / "frames", clock=time.monotonic,
                wall_clock=lambda: datetime.now(timezone.utc), before_takeoff=lambda: None)
        except live_session.LiveSessionError as exc:
            run.status = "REFUSED"
            run.error = str(exc)
            run.projection.note("REFUSED", f"Refused before takeoff: {exc}")
            return
        finally:
            world.link.close()
        run.sync()
        landing = outcome.landing
        pose = (landing.pose.x_cm, landing.pose.y_cm) if landing is not None and landing.pose is not None else None
        run.projection.finish(terminal_state=outcome.terminal_state.value,
                              landing_state=landing.state if landing else None,
                              landing_reason=landing.reason if landing else None,
                              landing_pose=pose, took_off=outcome.took_off, reason=outcome.reason)
        run.status = outcome.terminal_state.value

    # -- live -----------------------------------------------------------------
    def _live_pack(self, *, optional=False):
        path = self.live_config_path
        if path is None:
            if optional:
                return None
            raise RuntimeError("ROOM_LIVE_CONFIG_REQUIRED")
        try:
            # utf-8-sig: PowerShell 5.1's Set-Content -Encoding utf8 writes a BOM.
            pack = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            if optional:
                return None
            raise
        return pack if isinstance(pack, dict) else None

    def live_argv(self, run):
        """``scripts/room_session.py live`` arguments from the operator's pack."""
        pack = self._live_pack()
        base = Path(self.live_config_path).resolve().parent
        root = room_rehearsal.REPO_ROOT

        def resolve(value):
            p = Path(value)
            if p.is_absolute():
                return str(p)
            for candidate in (Path.cwd() / p, base / p, root / p):
                if candidate.exists():
                    return str(candidate)
            return str(root / p)

        def path(key, default=None):
            value = pack.get(key, default)
            if value is None:
                raise RuntimeError(f"room live pack is missing {key!r}")
            return resolve(value)

        required = ("manifest", "authorization_digest", "grant_registry", "consent_db", "location", "date",
                    "clock_domain_id", "run_root")
        missing = [key for key in required if not pack.get(key)]
        if missing or not pack.get("assurances"):
            raise RuntimeError(f"room live pack is missing {missing or ['assurances']}")
        argv = ["live", "--execute", "--manifest", path("manifest"),
                "--authorization-digest", str(pack["authorization_digest"]),
                "--capability", "count_return", "--grant-registry", path("grant_registry"),
                "--consent-db", path("consent_db"), "--location", str(pack["location"]),
                "--date", str(pack["date"]),
                "--site", path("site", str(room_rehearsal.SITE_PATH)),
                "--tag-map", path("tag_map", str(room_rehearsal.TAG_MAP_PATH)),
                "--clock-domain-id", str(pack["clock_domain_id"]),
                "--drift-profile", path("drift_profile", str(room_rehearsal.DRIFT_PATH)),
                "--energy-profile", path("energy_profile", str(room_rehearsal.ENERGY_PATH)),
                "--command-limits", path("command_limits", str(room_rehearsal.LIMITS_PATH)),
                "--run-root", path("run_root"),
                "--connectivity-timeout-s", str(pack.get("connectivity_timeout_s", 20)),
                "--connectivity-max-age-s", str(pack.get("connectivity_max_age_s", 90))]
        for assurance in pack["assurances"]:
            argv += ["--assurance", resolve(assurance)]
        for profile in pack.get("profile_files", [str(room_rehearsal.FLIGHT_PROFILE_PATH)]):
            argv += ["--profile-file", resolve(profile)]
        if getattr(self.mission.config, "authorise_paid_calls", False):
            argv.append("--i-authorise-paid-calls")
        if run.local_ip:
            argv += ["--local-ip", run.local_ip]
        if run.perception_review == "RECORDED_REVIEW":
            argv.append("--record-video")
        if pack.get("jev_acceptance_json"):
            argv += ["--jev-acceptance-json", json.dumps(pack["jev_acceptance_json"])]
        manifest_run_id = json.loads(Path(path("manifest")).read_text(encoding="utf-8"))["run_id"]
        run_dir = Path(path("run_root")) / manifest_run_id
        if (run_dir / "terminal.json").exists():
            # One pack is one flight: reopening a finished run would fly on its closed ledger.
            raise RuntimeError(f"the pack's run {manifest_run_id} already flew; re-run "
                               "scripts/room_live_prepare.py for a fresh run, then approve again")
        return argv, run_dir, manifest_run_id

    def _run_live(self, run):
        self._live_link_used = True
        argv, run_dir, manifest_run_id = self.live_argv(run)
        run.id = manifest_run_id
        run.frame_root = run_dir
        run.store_path = run_dir / "session.sqlite3"
        room_session = _import_room_session()
        buffer = io.StringIO()
        tee = _Tee(sys.stderr, buffer)
        with contextlib.redirect_stderr(tee):
            code = room_session.main(argv, request_text=run.request_text,
                                     on_stack=lambda stack, **kw: self._attach(run, stack, **kw))
        run.sync()
        terminal = {}
        try:
            terminal = json.loads((run_dir / "terminal.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        stderr_lines = buffer.getvalue().splitlines()
        refusals = [line[len("refused: "):] for line in stderr_lines if line.startswith("refused: ")]
        if code != 0:
            run.status = "REFUSED" if refusals else "FAILED"
            run.error = refusals[-1] if refusals else f"room_session live exited {code}"
            run.projection.note("REFUSED", f"Live session refused: {run.error}")
        state = terminal.get("state") or ("REFUSED" if code else "COMPLETED")
        if state == "ABORTED" and terminal.get("reason") == "START_FAILED":
            start_failures = [line[len("Live start failed: "):] for line in stderr_lines
                              if line.startswith("Live start failed: ")]
            if start_failures:
                run.error = start_failures[-1][:300]
                run.projection.note("START_FAILED", f"Flight start failed; landed: {run.error}")
        landing_pose = None
        outcome = run.safety.land_outcome
        if run.world is None and run.tracker is not None:
            position = run.tracker.health.position
            landing_pose = (position.x_cm, position.y_cm)
        if outcome is not None and outcome.pose is not None:
            landing_pose = (outcome.pose.x_cm, outcome.pose.y_cm)
        run.projection.finish(terminal_state=state, landing_state=terminal.get("landing_state"),
                              landing_reason=terminal.get("landing_reason"), landing_pose=landing_pose,
                              took_off=terminal.get("took_off"), reason=terminal.get("reason"))
        if code == 0:
            run.status = state


class _Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            try:
                stream.write(text)
            except Exception:
                pass
        return len(text)

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def _import_room_session():
    try:
        from scripts import room_session
    except ImportError:
        sys.path.insert(0, str(room_rehearsal.REPO_ROOT))
        from scripts import room_session
    return room_session


def attach_room_agent(mission, **kwargs) -> RoomAgentService:
    """Give a Mission Control service its room-agent architecture (idempotent)."""
    existing = getattr(mission, "room_agent", None)
    if existing is not None:
        return existing
    room = RoomAgentService(mission, **kwargs)
    mission.room_agent = room
    original_close = mission.close

    def close():
        # The session's own close lands every registered runner first.
        original_close()
        room.close()

    mission.close = close
    return room


def build_room_agent_router(room: RoomAgentService):
    from fastapi import APIRouter, Request
    from fastapi.responses import FileResponse, JSONResponse, Response
    from starlette.concurrency import run_in_threadpool

    router = APIRouter(prefix="/api/v1/room-agent")

    def restart_worker():
        # Let the acknowledgement reach the browser before the process closes
        # its HTTP connection and releases DJITelloPy's global UDP sockets.
        time.sleep(0.25)
        os._exit(75)

    def result(body):
        return JSONResponse(body, status_code={"CONFLICT": 409, "INVALID": 422, "UNAVAILABLE": 503}.get(
            body.get("status"), 200))

    async def body_of(request: Request):
        try:
            return await request.json()
        except ValueError:
            return None

    @router.get("")
    def state():
        return room.state()

    def current_run(run_id):
        with room._lock:
            run = room.run
        return run if run is not None and run_id in (run.id, run.initial_id) else None

    @router.get("/camera/live/{run_id}")
    def camera_live(run_id: str):
        from .room_camera import live_frame
        run = current_run(run_id)
        if run is None:
            return Response(status_code=404)
        try:
            frame = live_frame(run)
        except Exception:
            return Response(status_code=503, headers={"cache-control": "no-store"})
        if frame is None:
            return Response(status_code=204, headers={"cache-control": "no-store"})
        return Response(frame["payload"], media_type="image/jpeg", headers={
            "cache-control": "no-store", "x-frame-at": frame["observed_at"],
            "x-frame-width": str(frame["width"]), "x-frame-height": str(frame["height"]),
            "x-apriltags": json.dumps(frame["tags"], separators=(",", ":")),
        })

    @router.get("/camera/captures/{run_id}/latest")
    def camera_capture_latest(run_id: str):
        from .room_camera import capture_record
        run = current_run(run_id)
        if run is None:
            return Response(status_code=404)
        try:
            record = capture_record(run)
        except Exception:
            return Response(status_code=503, headers={"cache-control": "no-store"})
        if record is None:
            return Response(status_code=204, headers={"cache-control": "no-store"})
        return JSONResponse({key: value for key, value in record.items() if key not in ("path", "sha256")},
                            headers={"cache-control": "no-store"})

    @router.get("/camera/captures/{run_id}/{observation_id}")
    def camera_capture_image(run_id: str, observation_id: str):
        from .room_camera import capture_bytes, capture_record
        run = current_run(run_id)
        try:
            record = capture_record(run, observation_id) if run is not None else None
            payload = capture_bytes(record) if record is not None else None
        except Exception:
            return Response(status_code=503, headers={"cache-control": "no-store"})
        return (Response(payload, media_type="image/png", headers={"cache-control": "no-store"})
                if payload is not None else Response(status_code=404))

    @router.get("/runs/{run_id}/recording")
    def recording(run_id: str):
        run = current_run(run_id)
        review = run.review_session() if run is not None else None
        if review is None or not review.source.is_file():
            return Response(status_code=404)
        return FileResponse(review.source, filename="flight.h264", media_type="application/octet-stream")

    @router.post("/runs/{run_id}/analysis")
    def start_analysis(run_id: str):
        run = current_run(run_id)
        review = run.review_session() if run is not None else None
        if review is None:
            return Response(status_code=404)
        return result(review.start())

    @router.get("/runs/{run_id}/analysis/result")
    def analysis_result(run_id: str):
        run = current_run(run_id)
        review = run.review_session() if run is not None else None
        document = review.result() if review is not None else None
        return JSONResponse(document, headers={"cache-control": "no-store"}) if document else Response(status_code=404)

    @router.get("/runs/{run_id}/analysis/video")
    def analysis_video(run_id: str):
        run = current_run(run_id)
        review = run.review_session() if run is not None else None
        path = review.video_path() if review is not None else None
        return FileResponse(path, media_type="video/mp4") if path else Response(status_code=404)

    @router.post("/proposals")
    async def propose(request: Request):
        return result(await run_in_threadpool(room.propose, await body_of(request)))

    @router.post("/preparations")
    async def prepare(request: Request):
        from starlette.background import BackgroundTask

        body = await run_in_threadpool(room.prepare, await body_of(request))
        response = result(body)
        if body.get("server_restarting"):
            # The response is sent before the worker exits. The parent in
            # sentinel.ui.__main__ starts a clean receiver process.
            response.background = BackgroundTask(restart_worker)
        return response

    @router.post("/decisions")
    async def decide(request: Request):
        return result(await run_in_threadpool(room.decide, await body_of(request)))

    return router
