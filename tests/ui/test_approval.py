import pytest
from sentinel.ui.approval import ApprovalBroker
from sentinel.ui.store import UIStore


def setup(tmp_path):
    store = UIStore.open(tmp_path / 'ui.sqlite')
    store.ensure_run('s', 'r', 'REHEARSAL')
    broker = ApprovalBroker(store)
    offer = broker.offer('r', {'arguments': {'option_id':'rail'}, 'description':'Four stops'}, 'corr')
    attempt = dict(session_id='s',run_id='r',proposal_id=offer.proposal['id'],proposal_sha256=offer.proposal['sha256'],expected_version=offer.proposal['decision_version'],decision='APPROVE',command_id='cmd',correlation_id='corr',attempt_id='a',idempotency_key='key')
    return store, broker, offer, attempt


def test_decision_survives_response_loss_and_display_refresh(tmp_path):
    store, broker, offer, attempt = setup(tmp_path)
    store.publish_snapshot('r', {'run':{'version':0},'events':{'watermark':0}})
    result = broker.decide(attempt)
    assert result['status'] == 'ACKNOWLEDGED'
    assert offer.wait(timeout=0.1) is True
    assert ApprovalBroker(store).decide(attempt) == result
    assert broker.decide({**attempt,'decision':'REJECT'})['status'] == 'CONFLICT'


def test_replaced_offer_cannot_approve(tmp_path):
    store, broker, old, attempt = setup(tmp_path)
    broker.offer('r', {'arguments':{'option_id':'other'},'description':'New'}, 'new-corr')
    assert broker.decide(attempt)['status'] == 'CONFLICT'
    assert old.wait(timeout=0.1) is False


def test_restart_does_not_resume_live_offer(tmp_path):
    store, broker, offer, attempt = setup(tmp_path)
    with store.connection(write=True) as conn:
        conn.execute("UPDATE ui_runs SET mode='LIVE'")
    broker.recover_interrupted_runs()
    assert store.read_run('r')['status'] == 'RECOVERY_REQUIRED'
    assert broker.decide(attempt)['status'] == 'CONFLICT'
