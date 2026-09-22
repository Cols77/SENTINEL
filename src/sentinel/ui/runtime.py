"""Shared execution of the production survey loop; boundary adapters select mode."""
from threading import RLock
from sentinel.survey.store import SurveyStore
from sentinel.survey.loop import run_survey, get_results
from sentinel.survey.read_model import SurveyReadModel
from sentinel.client import EmergencyAborted
from sentinel.runner import ControlledLandRequested
from sentinel.mission_observer import (AbortEvent, DetectorResultEvent, FrameCapturedEvent,
                                        MergeEvent, PoseEstimateEvent, PoseHealthEvent,
                                        PublicEscalationEvent, ReportEvent,
                                        RunnerLifecycleEvent, StopProgressEvent)
from .store import now


class RuntimeMissionObserver:
    """Runtime-owned adapter from typed mission telemetry to UI state/journal."""
    def __init__(self, runtime, run_id):
        self.runtime, self.run_id = runtime, run_id

    def observe(self, event):
        if getattr(event, 'run_id', self.run_id) not in ('', self.run_id):
            return
        if isinstance(event, RunnerLifecycleEvent):
            # Safety admission is handled by the same lifecycle, atomically with
            # the registry's existing latch/close checks.
            if event.status == 'STARTED':
                self.runtime._publish_structured(self.run_id, 'RUN_STARTED', 'FLIGHT', event)
            elif event.status not in {'STARTED'}:
                self.runtime._publish_structured(self.run_id, 'RUN_TERMINAL', 'REPORT', event)
            return
        if isinstance(event, StopProgressEvent):
            self.runtime._publish_structured(self.run_id, 'STOP_ARRIVED', 'FLIGHT', event)
        elif isinstance(event, FrameCapturedEvent):
            self.runtime._publish_structured(self.run_id, 'FRAME_CAPTURED', 'ANALYSIS', event)
        elif isinstance(event, DetectorResultEvent):
            self.runtime._publish_structured(self.run_id, 'DETECTOR_RESULT', 'ANALYSIS', event)
        elif isinstance(event, MergeEvent):
            self.runtime._publish_structured(self.run_id, 'MERGE_COMPLETED', 'REVIEW', event)
        elif isinstance(event, ReportEvent):
            self.runtime._publish_structured(self.run_id, 'REPORT_ACCEPTED' if event.status == 'ACCEPTED' else 'REPORT_REJECTED', 'REPORT', event)
        elif isinstance(event, PublicEscalationEvent):
            self.runtime._publish_structured(self.run_id, 'ESCALATION', 'REVIEW', event)
        elif isinstance(event, AbortEvent):
            self.runtime._publish_structured(self.run_id, 'RUN_ABORTED', 'REPORT', event)
        elif isinstance(event, (PoseEstimateEvent, PoseHealthEvent)):
            # Preserve the original object, including exact encoded PNG bytes and
            # AprilTag diagnostics, for the projection adapter. Never serialize
            # those bytes into the UI journal.
            feed = getattr(self.runtime.adapters, 'pose_feed', None)
            if feed is not None:
                if isinstance(event, PoseEstimateEvent) and event.estimate is not None:
                    feed.accept(event.estimate)
                elif isinstance(event, PoseHealthEvent) and event.value is not None:
                    feed.accept_health(event.value)
            return

    def runner_started(self, run_id, runner):
        self.runtime.registry.runner_started(run_id, runner)

    def runner_finished(self, run_id, runner):
        self.runtime.registry.runner_finished(run_id, runner)

class SurveyRuntime:
    def __init__(self, survey_db, ui_store, adapters, approval, safety_registry):
        self.survey_db=survey_db
        self.store,self.adapters,self.approval,self.registry=ui_store,adapters,approval,safety_registry
        self.lock=RLock()
        self.state={}

    def _publish_event(self, run_id, event_type, payload):
        if self.registry.emergency.is_set():
            raise EmergencyAborted('SESSION_EMERGENCY')
        phase={'SWEEP_STARTED':'FLIGHT','STOP_ARRIVED':'FLIGHT','FRAME_CAPTURED':'ANALYSIS','ANALYSIS_COMPLETED':'ANALYSIS','MERGE_COMPLETED':'REVIEW','REPORT_ACCEPTED':'REPORT'}.get(event_type)
        with self.lock:
            state=self.state.setdefault(run_id,{})
            if phase: state['phase']=phase
            state['updated_at']=now()
            if event_type=='STOP_ARRIVED': state['stop']=payload
            self.store.append_event(run_id,event_type,{'summary':payload.get('summary',event_type.replace('_',' ').capitalize())})

    def _publish_structured(self, run_id, event_type, phase, event):
        payload = {'summary': event_type.replace('_', ' ').capitalize()}
        if isinstance(event, StopProgressEvent):
            payload['stop'] = {
                'option_id': event.option_id or self._option_id(event.stop),
                'stop_index': event.stop_index if event.stop_index is not None else self._stop_index(event.stop),
                'x_cm': event.x_cm,
                'y_cm': event.y_cm,
            }
        elif isinstance(event, FrameCapturedEvent):
            payload['refs'] = [event.artifact_id] if event.artifact_id else []
        elif isinstance(event, DetectorResultEvent):
            payload.update(status=event.status, latency_ms=event.latency_ms, error=event.error)
        elif isinstance(event, (AbortEvent, PublicEscalationEvent)):
            payload.update(reason=getattr(event, 'reason', ''), point=getattr(event, 'point', ''),
                           stage=getattr(event, 'stage', ''))
        with self.lock:
            state=self.state.setdefault(run_id,{})
            state['phase']=phase; state['updated_at']=now()
            if isinstance(event, StopProgressEvent): state['stop']=payload['stop']
            self.store.append_event(run_id,event_type,payload)

    @staticmethod
    def _option_id(stop):
        if not stop:
            return None
        parts = stop.rsplit('_', 1)
        return parts[0] if len(parts) == 2 and parts[1].isdigit() else stop

    @staticmethod
    def _stop_index(stop):
        try:
            return int(stop.rsplit('_', 1)[1])
        except (AttributeError, IndexError, ValueError):
            return None

    def execute(self, request):
        run_id=request['run_id']
        if self.registry.emergency.is_set():
            raise EmergencyAborted('SESSION_EMERGENCY')
        with self.lock:
            self.state[run_id]=dict(request_text=request['request_text'],phase='PROPOSAL',updated_at=now())
        store=SurveyStore.open(self.survey_db)
        flight=self.adapters.flight
        observer = RuntimeMissionObserver(self, run_id)
        if hasattr(flight, 'observer'):
            flight.observer = observer
        if hasattr(flight,'run_id'):
            flight.run_id=run_id
        # Legacy adapters retain their emit hook for browser-v2 event names;
        # lifecycle admission is now owned by RuntimeMissionObserver.
        if hasattr(flight,'emit'):
            flight.emit=lambda kind,payload:self._publish_event(run_id,kind,payload)
        def approve(candidate):
            pending=self.approval.offer(run_id,candidate,request['correlation_id'])
            with self.lock:
                self.state[run_id]['phase']='APPROVAL'
            result=pending.wait()
            if self.registry.emergency.is_set():
                raise EmergencyAborted('SESSION_EMERGENCY')
            return result
        try:
            a=self.adapters
            outcome=run_survey(store=store,run_id=run_id,request_text=request['request_text'],options=a.options,
                policy=a.policy,flight=a.flight,detector=a.detector,writer=a.writer,camera=a.camera,
                location=a.location,consent_date=a.consent_date,on_approval=approve,
                # Typed observer events are the authoritative journal path. The
                # legacy emit stream is retained for direct/no-observer callers
                # and is disabled here to avoid duplicate concurrent writes.
                emit=None,observer=observer)
        except (EmergencyAborted,ControlledLandRequested):
            outcome=dict(status='ABORTED',counts=store.counts(run_id),report=None)
        except Exception:
            outcome=dict(status='FAILED',counts=store.counts(run_id),report=None)
            self.store.append_event(run_id,'RUN_FAILED',{'summary':'Runtime failed; inspect local diagnostic logs'})
            raise
        finally:
            store.connection.close()
            with self.lock:
                status=locals().get('outcome',{}).get('status','FAILED')
                self.state[run_id].update(outcome=locals().get('outcome',{}),phase='REPORT',updated_at=now())
                with self.store.connection(write=True) as conn:
                    conn.execute('UPDATE ui_runs SET status=?,decision_version=decision_version+1 WHERE run_id=?', ('COMPLETE' if status=='REPORTED' else 'ABORTED',run_id))
                self.store.append_event(run_id,'RUN_TERMINAL',{'summary':status})
        return outcome

    def facts(self, run_id):
        store=SurveyStore.open(self.survey_db)
        try:
            facts = SurveyReadModel(store).read(run_id)
            if facts is None:
                return dict(run=None, sweeps=[], frames=[], sightings=[], objects=[],
                            counts={}, report=None,
                            results=get_results(store, run_id, self.adapters.options))
            # Browser schema v2 still consumes derived decision facts. The
            # backend read model remains the sole source for stored entities;
            # this compatibility view contains only computed review values.
            facts['results'] = get_results(store, run_id, self.adapters.options)
            return facts
        finally:
            store.connection.close()
