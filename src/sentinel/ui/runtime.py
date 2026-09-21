"""Shared execution of the production survey loop; boundary adapters select mode."""
from threading import RLock
from sentinel.survey.store import SurveyStore
from sentinel.survey.loop import run_survey, get_results
from sentinel.client import EmergencyAborted
from sentinel.runner import ControlledLandRequested
from .store import now

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

    def execute(self, request):
        run_id=request['run_id']
        if self.registry.emergency.is_set():
            raise EmergencyAborted('SESSION_EMERGENCY')
        with self.lock:
            self.state[run_id]=dict(request_text=request['request_text'],phase='PROPOSAL',updated_at=now())
        store=SurveyStore.open(self.survey_db)
        flight=self.adapters.flight
        if hasattr(flight,'registry'):
            flight.registry,flight.run_id=self.registry,run_id
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
                location=a.location,consent_date=a.consent_date,on_approval=approve,emit=lambda kind,payload:self._publish_event(run_id,kind,payload))
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
            store.connection.execute('BEGIN')
            return dict(run=store.read_run(run_id),sweeps=store.read_sweeps(run_id),frames=store.read_frames_public(run_id),
                sightings=store.read_sightings_public(run_id),objects=store.read_objects_public(run_id),report=store.read_report(run_id),results=get_results(store,run_id,self.adapters.options))
        finally:
            store.connection.close()
