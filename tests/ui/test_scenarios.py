import pytest
from sentinel.ui.scenarios import build_rehearsal_adapters
from sentinel.survey.store import SurveyStore
from sentinel.survey.loop import run_survey,get_results
from sentinel.client import EmergencyAborted
from sentinel.mission_observer import AbortEvent, RunnerLifecycleEvent


def test_uncertain_scenario_requires_second_approval(tmp_path):
    a=build_rehearsal_adapters('fixtures/mission-control/uncertain-followup.json',tmp_path/'images')
    store=SurveyStore.open(tmp_path/'survey.sqlite');offers=[]
    try:
        result=run_survey(store=store,run_id='r',request_text='count computers',options=a.options,policy=a.policy,flight=a.flight,detector=a.detector,writer=a.writer,on_approval=lambda p:offers.append(p) or True)
        assert len(offers)==2
        assert result['status']=='REPORTED'
        assert a.flight.flights==2
    finally:store.connection.close()


def test_missing_analysis_does_not_erase_other_observations(tmp_path):
    a=build_rehearsal_adapters('fixtures/mission-control/missing-analysis.json',tmp_path/'images')
    store=SurveyStore.open(tmp_path/'survey.sqlite')
    try:
        run_survey(store=store,run_id='r',request_text='count computers',options=a.options,policy=a.policy,flight=a.flight,detector=a.detector,writer=a.writer,on_approval=lambda p:True)
        assert get_results(store,'r',a.options)['unanalysed']==1
        assert any(f['analysis_status']=='OK' for f in store.frames('r'))
    finally:store.connection.close()


def test_scenario_interruption_emits_abort_and_failed_lifecycle_not_pass(tmp_path):
    a=build_rehearsal_adapters('fixtures/mission-control/clean-computer.json',tmp_path/'images')
    class Observer:
        def __init__(self): self.events=[]
        def observe(self,event): self.events.append(event)
    observer=Observer(); a.flight.observer=observer
    class InterruptingRunner:
        def check_interruption(self): raise EmergencyAborted('SESSION_EMERGENCY')
    import sentinel.ui.scenarios as scenarios
    old=scenarios.FlightTestRunner; scenarios.FlightTestRunner=lambda *args,**kwargs: InterruptingRunner()
    try:
        with pytest.raises(EmergencyAborted):
            a.flight.fly(a.options[0])
    finally:
        scenarios.FlightTestRunner=old
    assert any(isinstance(e, AbortEvent) for e in observer.events)
    assert any(isinstance(e, RunnerLifecycleEvent) and e.status in {'FAILED','EMERGENCY_ABORTED'} for e in observer.events)
    assert not any(isinstance(e, RunnerLifecycleEvent) and e.status=='PASS' for e in observer.events)
