import pytest
from sentinel.runner import FlightTestRunner, ControlledLandRequested
from sentinel.client import EmergencyAborted
from sentinel.ui.safety import SafetyRegistry, RunnerSafetyAdapter
from tests.test_runner import _FakeClient


def test_emergency_latches_before_independent_send_and_survives_failure():
    runner = FlightTestRunner(_FakeClient(), 'dry-run')
    registry = SafetyRegistry()
    registry.bind('r', runner)
    calls = []
    def send():
        assert runner.emergency_triggered
        calls.append('sent')
        raise OSError('offline')
    adapter = RunnerSafetyAdapter(registry, send, 's')
    identity = dict(command_id='c',correlation_id='x',idempotency_key='k')
    result = adapter.trigger_emergency('s', identity, 'r')
    assert result['status'] == 'FAILED'
    assert adapter.trigger_emergency('s', identity, 'r') == result
    assert calls == ['sent']
    with pytest.raises(EmergencyAborted): runner.move_checked('forward',20)
    with pytest.raises(EmergencyAborted): registry.bind('new', FlightTestRunner(_FakeClient(),'dry-run'))


def test_land_is_idempotent_and_does_not_become_emergency():
    runner = FlightTestRunner(_FakeClient(),'dry-run')
    runner.request_controlled_land()
    runner.request_controlled_land()
    assert not runner.emergency_triggered
    assert [r.command for r in runner.client.records] == ['land']
    with pytest.raises(ControlledLandRequested): runner.move_checked('forward',20)


def test_emergency_without_runner_still_sends_and_latches_session():
    calls=[]
    registry=SafetyRegistry()
    adapter=RunnerSafetyAdapter(registry,lambda:calls.append('sent') or 9,'s')
    assert adapter.trigger_emergency('s',dict(command_id='c',correlation_id='x',idempotency_key='k'))['status']=='ACKNOWLEDGED'
    assert calls==['sent'] and registry.emergency.is_set()


def test_blocked_audit_does_not_block_emergency():
    from threading import Event
    entered, release = Event(), Event()
    def audit(result):
        entered.set()
        assert release.wait(2)
    registry=SafetyRegistry()
    adapter=RunnerSafetyAdapter(registry,lambda:9,'s',audit)
    try:
        result=adapter.trigger_emergency('s',dict(command_id='a',correlation_id='x',idempotency_key='a'))
        assert result['status']=='ACKNOWLEDGED'
        assert entered.wait(1)
        result=adapter.trigger_emergency('s',dict(command_id='b',correlation_id='x',idempotency_key='b'))
        assert result['status']=='ACKNOWLEDGED'
    finally:
        release.set()
        adapter.close()


def test_service_shutdown_lands_active_runner_before_cleanup(tmp_path):
    from sentinel.ui.bootstrap import SessionConfig,build_service
    service=build_service(SessionConfig(data_dir=tmp_path))
    runner=FlightTestRunner(_FakeClient(),'dry-run')
    service.registry.bind('mission',runner)
    service.close()
    assert [r.command for r in runner.client.records]==['land']
    assert not runner.emergency_triggered
    with pytest.raises(EmergencyAborted):
        service.registry.bind('mission',FlightTestRunner(_FakeClient(),'dry-run'))


# -- merge integration: Mission Control safety over the room agent's StepExecutor --

def test_independent_emergency_latch_terminates_the_shared_step_executor():
    """The UI emergency path latches runners without calling trigger_emergency;
    the connected step executor must still become terminal so no further
    checkpoint or supervisor work is admitted."""
    from sentinel.cube.step_executor import ExecutorState
    runner = FlightTestRunner(_FakeClient(), 'dry-run')
    runner.step_executor.start_legacy()
    assert runner.step_executor.state is ExecutorState.AIRBORNE
    registry = SafetyRegistry()
    registry.bind('r', runner)
    adapter = RunnerSafetyAdapter(registry, lambda: 9, 's')
    identity = dict(command_id='c', correlation_id='x', idempotency_key='k')
    assert adapter.trigger_emergency('s', identity, 'r')['status'] == 'ACKNOWLEDGED'
    assert runner.step_executor.state is ExecutorState.TERMINAL
    runner.step_executor.close()
    assert 'land' not in [r.command for r in runner.client.records]


def test_controlled_land_is_sent_once_even_when_the_step_executor_cleans_up():
    runner = FlightTestRunner(_FakeClient(), 'dry-run')
    runner.step_executor.start_legacy()
    runner.request_controlled_land()
    assert runner.land_status == 'ACKNOWLEDGED'
    runner.step_executor.close()
    assert [r.command for r in runner.client.records].count('land') == 1
    assert not runner.emergency_triggered
    assert runner.client.closed
