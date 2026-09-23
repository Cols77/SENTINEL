"""Application service shared by Rehearsal and Live adapter sets."""
from io import BytesIO
from pathlib import Path
import hashlib
import sqlite3
from threading import RLock
from uuid import uuid4
from PIL import Image
from .store import UIStore
from .approval import ApprovalBroker
from .mission_worker import MissionWorker
from .runtime import SurveyRuntime
from .safety import SafetyRegistry, RunnerSafetyAdapter
from .models import EvidenceCapability
from .projection import ProjectionService

_DISPATCH_FAILURES = {
    'planning': {'POSE_DATABASE_UNAVAILABLE'},
    'flight': {'LIVE_PREFLIGHT_FAILED'},
    'emergency': {'INDEPENDENT_EMERGENCY_UNAVAILABLE'},
}

class MissionControlService:
    def __init__(self, *, config, adapters, readiness=None,
                 site_profile_registry=None, profile_base_config=None):
        self.config,self.adapters=config,adapters
        self.site_profile_registry = site_profile_registry
        self.profile_base_config = profile_base_config or config
        self.readiness=readiness
        self._readiness_overlays={}
        self._readiness_lock=RLock()
        self.store=UIStore.open(config.ui_db or config.data_dir/'ui.sqlite')
        try:
            self.session_id=self.store.read_run(config.run_id)['session_id']
        except KeyError:
            self.session_id=str(uuid4())
        self.approval=ApprovalBroker(self.store)
        self.approval.recover_interrupted_runs()
        self.store.ensure_run(self.session_id,config.run_id,config.mode)
        self.registry=SafetyRegistry()
        self.safety=RunnerSafetyAdapter(self.registry,(lambda:9) if config.mode=='REHEARSAL' else adapters.emergency_sender,self.session_id,self._audit_safety)
        if config.mode == 'LIVE' and hasattr(adapters.flight, 'on_emergency_ready'):
            adapters.flight.on_emergency_ready=self.safety.admit_emergency_sender
        if config.mode == 'LIVE' and hasattr(adapters.flight, 'on_readiness_update'):
            adapters.flight.on_readiness_update=self._update_readiness
        self.runtime=SurveyRuntime(config.survey_db or config.data_dir/'survey.sqlite',self.store,adapters,self.approval,self.registry)
        self.worker=MissionWorker(self.runtime.execute)
        self.projection=ProjectionService(self)
        self.artifacts=getattr(adapters.flight,'artifacts',{})
        self.evidence_status={}
        self.pose_feed=adapters.pose_feed
        if config.mode=='REHEARSAL':
            from .rehearsal_pose import RehearsalPoseFeed
            fixture_root=Path(__file__).resolve().parents[3]/'fixtures/mission-control'
            self.pose_feed=RehearsalPoseFeed(config.run_id,config.data_dir/'pose-images',fixture_root)
        from .chat import ChatService
        self.chat=ChatService(self)
        self.closed=False

    def _update_readiness(self, readiness):
        if self.config.mode == 'LIVE':
            from .readiness import evaluate_live_readiness
            with self._readiness_lock:
                base = evaluate_live_readiness(self.config)
                base_checks = {check.capability: check for check in base.checks}
                for check in readiness.checks:
                    baseline = base_checks[check.capability]
                    route_current = (check.capability not in {'flight', 'emergency'} or
                                     readiness.resolved_local_ip == base.resolved_local_ip)
                    if (not check.ready and
                            check.reason_code in _DISPATCH_FAILURES.get(check.capability, ())
                            and baseline.ready and route_current):
                        self._readiness_overlays[check.capability] = (
                            baseline, base.resolved_local_ip, check)
                    else:
                        self._readiness_overlays.pop(check.capability, None)
                self._refresh_live_readiness(base)
        else:
            self.readiness = readiness

    def _refresh_live_readiness(self, base):
        from .readiness import LiveReadiness
        checks = []
        for check in base.checks:
            overlay = self._readiness_overlays.get(check.capability)
            route_current = (check.capability not in {'flight', 'emergency'} or
                             overlay is not None and
                             overlay[1] == base.resolved_local_ip)
            if overlay is not None and overlay[0] == check and route_current:
                check = overlay[2]
            elif overlay is not None:
                self._readiness_overlays.pop(check.capability, None)
            checks.append(check)
        state = ('READY_TO_DISPATCH' if all(check.ready for check in checks)
                 else 'READY_TO_PLAN' if checks[0].ready else 'NOT_READY')
        self.readiness = LiveReadiness(state, tuple(checks),
                                       base.command_template,
                                       base.resolved_local_ip)

    def require_run(self, run_id):
        if run_id != self.config.run_id:
            raise KeyError(run_id)

    def session(self):
        return dict(id=self.session_id,mode=self.config.mode,active_run_id=self.config.run_id)

    def _profile_switch_reason(self, conn=None):
        """Shared catalogue/admission predicate; caller holds readiness lock."""
        if self.config.mode != 'LIVE' or self.site_profile_registry is None or self.closed:
            return 'PROFILE_SWITCH_UNAVAILABLE'
        run_id = self.config.run_id
        with self.runtime.lock:
            try:
                reason = self.store.profile_switch_reason(run_id, conn)
                if reason:
                    return reason
                if run_id in self.runtime.state or self.worker.active or \
                        self.registry.get(run_id) is not None or \
                        self.registry.emergency.is_set() or self.registry.closing or \
                        self.safety._ledger or any(
                            row.get('command_id') is not None
                            for row in self.safety.state.values()):
                    return 'MISSION_NOT_IDLE'
                survey_path = Path(self.runtime.survey_db)
                if survey_path.is_file():
                    uri = survey_path.resolve().as_uri() + '?mode=ro'
                    with sqlite3.connect(uri, uri=True, timeout=1.0) as survey:
                        exists = survey.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='survey_runs'").fetchone()
                        if exists and survey.execute(
                                'SELECT 1 FROM survey_runs WHERE run_id=? LIMIT 1',
                                (run_id,)).fetchone():
                            return 'MISSION_NOT_IDLE'
            except (OSError, sqlite3.Error):
                return 'PROFILE_SWITCH_UNAVAILABLE'
        return None

    def _site_profile_catalogue(self, reason=None):
        if self.site_profile_registry is None:
            public = dict(version=None, selected_profile_id=None, profiles=[])
        else:
            public = self.site_profile_registry.public(self.config.site_profile_id)
        if reason is None:
            reason = self._profile_switch_reason()
        return {**public, 'switch_allowed': reason is None,
                'switch_reason': reason}

    def site_profiles(self):
        with self._readiness_lock:
            return self._site_profile_catalogue()

    @staticmethod
    def _close_local_adapters(adapters):
        for resource in (adapters.flight, adapters.pose_feed):
            if resource is not None:
                try:
                    close = getattr(resource, 'close', None)
                    if close is not None:
                        close()
                except Exception:
                    # The committed selection cannot be undone by inert cleanup.
                    pass

    def select_site_profile(self, command):
        if (not isinstance(command, dict) or set(command) != {'profile_id'} or
                not isinstance(command['profile_id'], str) or
                not command['profile_id'].strip()):
            return dict(status='INVALID', reason_code='INVALID_PROFILE_REQUEST',
                        **self.site_profiles())
        with self._readiness_lock:
            reason = self._profile_switch_reason()
            if reason:
                status = ('UNAVAILABLE' if reason == 'PROFILE_SWITCH_UNAVAILABLE'
                          else 'CONFLICT')
                return dict(status=status, reason_code=reason,
                            **self._site_profile_catalogue(reason))
            from .bootstrap import StartupError
            from .site_profiles import apply_site_profile
            from .readiness import evaluate_live_readiness
            from .live import compose
            try:
                profile = self.site_profile_registry.require(command['profile_id'])
                candidate_config = apply_site_profile(self.profile_base_config, profile)
                from dataclasses import replace
                candidate_config = replace(candidate_config, site_profile_id=profile.id)
                candidate_readiness = evaluate_live_readiness(candidate_config)
                candidate_adapters = compose(candidate_config, candidate_readiness)
            except StartupError as exc:
                code = str(exc)
                if code not in {'SITE_PROFILE_NOT_FOUND', 'SITE_PROFILE_DISABLED',
                                'SITE_PROFILE_INCOMPLETE',
                                'INVALID_LIVE_SITE_OR_COVERAGE'}:
                    code = 'SITE_PROFILE_INCOMPLETE'
                return dict(status='INVALID', reason_code=code,
                            **self._site_profile_catalogue())
            except Exception:
                return dict(status='INVALID', reason_code='SITE_PROFILE_INCOMPLETE',
                            **self._site_profile_catalogue())

            previous_adapters = self.adapters
            previous = (self.config, self.adapters, self.runtime.adapters,
                        self.pose_feed, self.artifacts, self.readiness,
                        self._readiness_overlays.copy())
            reason = None
            with (self.runtime.lock, self.worker._lock,
                  self.registry._lock, self.safety._lock):
                try:
                    with self.store.connection(write=True) as conn:
                        reason = self._profile_switch_reason(conn)
                        if reason is None:
                            self.store.event_in(conn, self.config.run_id,
                                'SITE_PROFILE_CHANGED',
                                {'summary': 'Site profile changed'})
                            candidate_adapters.flight.on_emergency_ready = self.safety.admit_emergency_sender
                            candidate_adapters.flight.on_readiness_update = self._update_readiness
                            self.config = candidate_config
                            self.adapters = candidate_adapters
                            self.runtime.adapters = candidate_adapters
                            self.pose_feed = candidate_adapters.pose_feed
                            self.artifacts = getattr(candidate_adapters.flight,
                                                     'artifacts', {})
                            self._readiness_overlays.clear()
                            self.readiness = candidate_readiness
                except Exception:
                    (self.config, self.adapters, self.runtime.adapters,
                     self.pose_feed, self.artifacts, self.readiness,
                     self._readiness_overlays) = previous
                    self._close_local_adapters(candidate_adapters)
                    return dict(status='UNAVAILABLE',
                                reason_code='PROFILE_SWITCH_UNAVAILABLE',
                                **self._site_profile_catalogue(
                                    'PROFILE_SWITCH_UNAVAILABLE'))
            if reason is not None:
                self._close_local_adapters(candidate_adapters)
                return dict(status='CONFLICT', reason_code=reason,
                            **self._site_profile_catalogue(reason))
            self._close_local_adapters(previous_adapters)
            return dict(status='ACKNOWLEDGED', reason_code=None,
                        **self._site_profile_catalogue())

    def read_snapshot(self, run_id):
        if self.config.mode == 'LIVE':
            from .readiness import evaluate_live_readiness
            with self._readiness_lock:
                self._refresh_live_readiness(evaluate_live_readiness(self.config))
                return self.projection.snapshot(run_id)
        return self.projection.snapshot(run_id)

    def read_events(self, run_id, after_sequence, limit):
        self.require_run(run_id)
        return self.store.read_events(run_id,after_sequence,limit)

    def read_evidence(self, run_id, artifact_id):
        self.require_run(run_id)
        frames=self.runtime.facts(run_id)['frames']
        bound=next((f for f in frames if f['artifact_id']==artifact_id),None)
        status=self.evidence_status.get(artifact_id,'AVAILABLE')
        if status in {'FORBIDDEN','REVOKED'}:
            return EvidenceCapability(status,run_id,artifact_id,reason_code=status)
        artifact=self.artifacts.get(artifact_id)
        if self.pose_feed:
            with self.pose_feed.lock:
                pose_artifact=self.pose_feed.artifacts.get(artifact_id)
                if pose_artifact:
                    artifact=dict(pose_artifact)
                    bound={'sha256':artifact['sha256']}
        if not bound or not artifact:
            return EvidenceCapability('NOT_FOUND',run_id,artifact_id,reason_code='NOT_FOUND')
        try:
            path=artifact['path']
            if path.stat().st_size>10*1024*1024:
                raise ValueError('IMAGE_TOO_LARGE')
            payload=path.read_bytes()
            if hashlib.sha256(payload).hexdigest()!=bound['sha256']:
                raise ValueError('HASH_MISMATCH')
            with Image.open(BytesIO(payload)) as image:
                if image.format not in {'PNG','JPEG'} or image.width*image.height>20_000_000:
                    raise ValueError('INVALID_IMAGE')
                width,height=image.size
                media='image/png' if image.format=='PNG' else 'image/jpeg'
                image.verify()
            return EvidenceCapability('AVAILABLE',run_id,artifact_id,media,payload,bound['sha256'],width,height)
        except (OSError,ValueError):
            return EvidenceCapability('NOT_FOUND',run_id,artifact_id,reason_code='INVALID_ARTIFACT')

    def submit_decision(self, run_id, command):
        self.require_run(run_id)
        if command.get('run_id')!=run_id:
            return dict(attempt_id=command.get('attempt_id','invalid'),status='CONFLICT',message='Run identity mismatch')
        return self.approval.decide(command)

    def submit_chat(self, run_id, command):
        self.require_run(run_id)
        return self.chat.submit(run_id,command)

    def request_land(self, run_id, command):
        self.require_run(run_id)
        return self.safety.request_land(run_id,command)

    def trigger_emergency(self, command):
        return self.safety.trigger_emergency(command['session_id'],command['identity'],command.get('active_run_id'))

    def _audit_safety(self, result):
        if result['status'] != 'UNAVAILABLE':
            self.approval.cancel(self.config.run_id)
        self.store.append_event(self.config.run_id,'SAFETY',{'summary':f"{result['action']}: {result['status']}"})

    def close(self):
        if not self.closed:
            self.closed=True
            graceful_idle_live = (
                self.config.mode == 'LIVE' and not self.worker.active and
                self.registry.get(self.config.run_id) is None and
                self.approval.current(self.config.run_id) is None)
            runners=self.registry.stop_admission()
            for runner in runners:
                if not self.registry.emergency.is_set():
                    try:
                        runner.request_controlled_land()
                    except Exception:
                        # Failed controlled landing must not become a silent latch.
                        identity=str(uuid4())
                        self.safety.trigger_emergency(self.session_id,dict(command_id=identity,
                            correlation_id=identity,idempotency_key=identity),self.config.run_id)
            self.approval.cancel(self.config.run_id)
            self.worker.close()
            flight_close = getattr(self.adapters.flight, 'close', None)
            if flight_close is not None:
                flight_close()
            self.safety.close()
            if graceful_idle_live:
                self.store.mark_idle_live_closed(self.config.run_id)
            if self.pose_feed:self.pose_feed.close()
