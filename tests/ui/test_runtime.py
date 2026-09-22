from pathlib import Path
from sentinel.ui.scenarios import build_rehearsal_adapters
from sentinel.ui.runtime import RuntimeMissionObserver, SurveyRuntime
from sentinel.ui.approval import ApprovalBroker
from sentinel.ui.store import UIStore
from sentinel.ui.safety import SafetyRegistry
from sentinel.ui.mission_worker import MissionWorker
from sentinel.mission_observer import AbortEvent, PublicEscalationEvent, StopProgressEvent


def test_clean_run_waits_for_operator_then_counts_real_artifacts(tmp_path):
    store=UIStore.open(tmp_path/'ui.sqlite')
    store.ensure_run('s','r','REHEARSAL')
    broker=ApprovalBroker(store)
    adapters=build_rehearsal_adapters(Path('fixtures/mission-control/clean-computer.json'),tmp_path/'artifacts')
    runtime=SurveyRuntime(tmp_path/'survey.sqlite',store,adapters,broker,SafetyRegistry())
    worker=MissionWorker(runtime.execute)
    future=worker.start(dict(run_id='r',request_text='count the computers',correlation_id='corr'))
    try:
        assert broker.changed.wait(2)
        offer=broker.current('r')
        assert adapters.flight.flights==0
        result=broker.decide(dict(session_id='s',run_id='r',proposal_id=offer['id'],proposal_sha256=offer['sha256'],expected_version=offer['decision_version'],decision='APPROVE',command_id='c',correlation_id='corr',attempt_id='a',idempotency_key='k'))
        assert result['status']=='ACKNOWLEDGED'
        outcome=future.result(timeout=5)
        assert outcome['status']=='REPORTED'
        assert outcome['counts']['computer']['confirmed']==1
        facts=runtime.facts('r')
        assert facts['report']['body']==outcome['report']
        assert len(facts['frames'])==4
        assert all(len(f['sha256'])==64 and f['sha256']!='0'*64 for f in facts['frames'])
    finally:
        broker.cancel('r')
        worker.close()


def test_rejection_never_flies(tmp_path):
    store=UIStore.open(tmp_path/'ui.sqlite');store.ensure_run('s','r','REHEARSAL')
    broker=ApprovalBroker(store)
    adapters=build_rehearsal_adapters('fixtures/mission-control/clean-computer.json',tmp_path/'images')
    worker=MissionWorker(SurveyRuntime(tmp_path/'survey.sqlite',store,adapters,broker,SafetyRegistry()).execute)
    future=worker.start(dict(run_id='r',request_text='count computers',correlation_id='corr'))
    try:
        assert broker.changed.wait(2)
        p=broker.current('r')
        broker.decide(dict(session_id='s',run_id='r',proposal_id=p['id'],proposal_sha256=p['sha256'],expected_version=p['decision_version'],decision='REJECT',command_id='c',correlation_id='corr',attempt_id='a',idempotency_key='k'))
        assert future.result(2)['status']=='REFUSED'
        assert adapters.flight.flights==0
    finally:
        broker.cancel('r');worker.close()


def test_runtime_journals_explicit_abort_and_escalation_events(tmp_path):
    store=UIStore.open(tmp_path/'ui.sqlite');store.ensure_run('s','r','REHEARSAL')
    adapters=build_rehearsal_adapters('fixtures/mission-control/clean-computer.json',tmp_path/'images')
    runtime=SurveyRuntime(tmp_path/'survey.sqlite',store,adapters,ApprovalBroker(store),SafetyRegistry())
    observer=RuntimeMissionObserver(runtime, 'r')
    observer.observe(PublicEscalationEvent(run_id='r', point='REVIEW', reason='operator decision'))
    observer.observe(AbortEvent(run_id='r', stage='FLIGHT', reason='controlled landing'))
    events=store.read_events('r', 0, 10)
    assert [event['type'] for event in events] == ['ESCALATION', 'RUN_ABORTED']


def test_runtime_retains_allowlisted_vertical_facts_and_durable_escalation(tmp_path):
    store=UIStore.open(tmp_path/'ui.sqlite');store.ensure_run('s','r','REHEARSAL')
    adapters=build_rehearsal_adapters('fixtures/mission-control/clean-computer.json',tmp_path/'images')
    runtime=SurveyRuntime(tmp_path/'survey.sqlite',store,adapters,ApprovalBroker(store),SafetyRegistry())
    observer=RuntimeMissionObserver(runtime,'r')
    observer.observe(StopProgressEvent(run_id='r', stop='GRID_0', stop_index=0, option_id='GRID', status='ACTIVE', payload={
        'tag_height_cm': 132.0, 'height_cm': 130.0, 'vertical_mismatch_cm': 2.0,
        'private_path':'must not persist'}))
    observer.observe(PublicEscalationEvent(run_id='r', point='PROPOSAL', reason='judge unavailable'))
    state=runtime.state['r']
    assert state['stops']['GRID:0']['tag_height_cm'] == 132.0
    assert 'private_path' not in state['stops']['GRID:0']
    assert state['decision']['source'] == 'RULES'
    assert state['decision']['escalation_reason'] == 'judge unavailable'
