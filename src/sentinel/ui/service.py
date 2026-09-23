"""Application service shared by Rehearsal and Live adapter sets."""
from io import BytesIO
from pathlib import Path
import hashlib
from uuid import uuid4
from PIL import Image
from .store import UIStore
from .approval import ApprovalBroker
from .mission_worker import MissionWorker
from .runtime import SurveyRuntime
from .safety import SafetyRegistry, RunnerSafetyAdapter
from .models import EvidenceCapability
from .projection import ProjectionService

class MissionControlService:
    def __init__(self, *, config, adapters, readiness=None):
        self.config,self.adapters=config,adapters
        self.readiness=readiness
        self._readiness_overlays={}
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
            base = evaluate_live_readiness(self.config)
            base_checks = {check.capability: check for check in base.checks}
            for check in readiness.checks:
                baseline = base_checks[check.capability]
                if check != baseline:
                    self._readiness_overlays[check.capability] = (baseline, check)
                else:
                    self._readiness_overlays.pop(check.capability, None)
        self.readiness = readiness

    def require_run(self, run_id):
        if run_id != self.config.run_id:
            raise KeyError(run_id)

    def session(self):
        return dict(id=self.session_id,mode=self.config.mode,active_run_id=self.config.run_id)

    def read_snapshot(self, run_id):
        if self.config.mode == 'LIVE':
            from .readiness import LiveReadiness, evaluate_live_readiness
            base = evaluate_live_readiness(self.config)
            checks = []
            for check in base.checks:
                overlay = self._readiness_overlays.get(check.capability)
                if overlay is not None and overlay[0] == check:
                    check = overlay[1]
                elif overlay is not None:
                    self._readiness_overlays.pop(check.capability, None)
                checks.append(check)
            state = ('READY_TO_DISPATCH' if all(check.ready for check in checks)
                     else 'READY_TO_PLAN' if checks[0].ready else 'NOT_READY')
            self.readiness = LiveReadiness(state, tuple(checks),
                                           base.command_template)
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
