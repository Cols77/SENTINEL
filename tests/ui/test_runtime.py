from pathlib import Path
from sentinel.ui.scenarios import build_rehearsal_adapters
from sentinel.ui.runtime import SurveyRuntime
from sentinel.ui.approval import ApprovalBroker
from sentinel.ui.store import UIStore
from sentinel.ui.safety import SafetyRegistry
from sentinel.ui.mission_worker import MissionWorker


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
