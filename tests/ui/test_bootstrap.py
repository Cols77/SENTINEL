from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import time
import pytest
from sentinel.ui import bootstrap
from sentinel.ui.models import validate_snapshot
from sentinel.survey.store import SurveyStore
from sentinel.survey.consent import record_consent


def test_bare_live_readiness_is_ordered_actionable_and_secret_free(monkeypatch):
    from sentinel.ui.readiness import evaluate_live_readiness

    monkeypatch.setenv('GEMINI_API_KEY', 'sentinel-secret-api-value')
    monkeypatch.setenv('GEMINI_MODEL', 'sentinel-secret-model-value')
    readiness = evaluate_live_readiness(bootstrap.SessionConfig(mode='LIVE'))

    assert readiness.state == 'NOT_READY'
    assert [check.capability for check in readiness.checks] == [
        'planning', 'flight', 'detection', 'emergency']
    assert all(not check.ready and check.reason_code and check.remediation
               for check in readiness.checks)
    public = json.dumps(readiness.public())
    for required_name in ('--site', '--tag-map', '--pose-db', '--start-x-cm',
                          '--start-y-cm', '--surface-z-cm', '--confirm',
                          '--local-ip', 'GEMINI_API_KEY', 'GEMINI_MODEL'):
        assert required_name in public
    assert 'sentinel-secret-api-value' not in public
    assert 'sentinel-secret-model-value' not in public

@pytest.fixture
def live_config(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-secret-never-public')
    monkeypatch.setenv('GEMINI_MODEL','test-model')
    profile=json.loads(Path('fixtures/sentinel/v0/provider-profile.json').read_text())
    profile.update(provider='gemini',model='test-model')
    profile_path=tmp_path/'profile.json';profile_path.write_text(json.dumps(profile))
    store=SurveyStore.open(tmp_path/'survey.sqlite')
    record_consent(store,'lab','2026-09-21','operator');store.connection.close()
    return bootstrap.SessionConfig(data_dir=tmp_path,mode='LIVE',confirm=True,
        site_path=Path('docs/wp5-flight1-site.json'),tag_map_path=Path('docs/wp5-flight3-map.json'),
        pose_db=tmp_path/'pose.sqlite',detector_name='gemini',provider_profile=profile_path,
        authorise_paid_calls=True,
        location='lab',consent_date='2026-09-21',local_ip='192.168.10.2',surface_z_cm=50,start_x_cm=250,start_y_cm=190)

def test_bare_live_host_is_inert_and_never_falls_back(tmp_path, monkeypatch):
    from sentinel.ui import live
    calls = []
    monkeypatch.setattr(bootstrap, 'build_rehearsal_adapters',
                        lambda *args: calls.append(('rehearsal', args)))
    monkeypatch.setattr(live, 'make_client',
                        lambda config: calls.append(('client', config)))
    monkeypatch.setattr(live, 'check_emergency_transport',
                        lambda config: calls.append(('emergency', config)))
    service = bootstrap.build_service(
        bootstrap.SessionConfig(data_dir=tmp_path, mode='LIVE'))
    try:
        snapshot = service.read_snapshot('mission')
        validate_snapshot(snapshot)
        assert service.session()['mode'] == 'LIVE'
        assert snapshot['source']['kind'] == 'LIVE'
        assert snapshot['mission']['route'] == []
        assert snapshot['mission']['envelope'] is None
        assert not service.worker.active
        assert calls == []
    finally:
        service.close()


def _chat_command(service, text='count the computers'):
    return dict(session_id=service.session_id, run_id='mission', command_id='chat',
                correlation_id='corr', idempotency_key='chat-key',
                expected_version=service.store.read_run('mission')['decision_version'],
                text=text)


def _approve_current(service):
    assert service.approval.changed.wait(2)
    offer = service.approval.current('mission')
    return service.submit_decision('mission', dict(
        session_id=service.session_id, run_id='mission', proposal_id=offer['id'],
        proposal_sha256=offer['sha256'], expected_version=offer['decision_version'],
        decision='APPROVE', command_id='approve', correlation_id='corr',
        attempt_id='attempt', idempotency_key='approve-key'))


def test_live_composition_defers_every_operational_boundary(live_config, monkeypatch):
    from sentinel.ui import live
    calls = []
    monkeypatch.setattr(live, 'make_client', lambda config: calls.append('client'))
    monkeypatch.setattr(live, 'check_emergency_transport',
                        lambda config: calls.append('emergency'))
    monkeypatch.setattr(live.LiveFlight, '_new_supervisor',
                        lambda self, option: calls.append('provider'))
    service = bootstrap.build_service(live_config)
    try:
        service.read_snapshot('mission')
        assert calls == []
        assert service.adapters.source_kind == 'LIVE'
        assert service.adapters.flight.__class__.__name__ == 'LiveFlight'
        assert service.adapters.flight.coverage is not None
        assert service.safety.sender is None
    finally:
        service.close()
    with pytest.raises(FrozenInstanceError):
        live_config.mode = 'REHEARSAL'


def test_live_dispatch_refuses_before_client_without_flight_confirmation(live_config,
                                                                          monkeypatch):
    from sentinel.ui import live
    calls = []
    monkeypatch.setattr(live, 'make_client', lambda config: calls.append('client'))
    monkeypatch.setattr(live, 'check_emergency_transport',
                        lambda config: calls.append('emergency'))
    service = bootstrap.build_service(replace(live_config, confirm=False))
    try:
        assert service.submit_chat('mission', _chat_command(service))['kind'] == 'PROPOSE'
        assert calls == []
        assert _approve_current(service)['status'] == 'ACKNOWLEDGED'
        deadline = time.monotonic() + 2
        while service.worker.active and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls == []
    finally:
        service.close()


def test_live_dispatch_preflights_only_after_approval(live_config, monkeypatch):
    from sentinel.ui import live
    from sentinel.dry_run import DryRunClient
    calls = []
    client = DryRunClient()

    class Supervisor:
        frames_dir = live_config.data_dir / 'frames'
        cleanup_pending = False
        def start(self):
            calls.append('supervisor-start')
        def finish(self, frames):
            from sentinel.survey.live_detect import DetectionCache
            return DetectionCache('mission', ())
        def close(self):
            return True

    class Bridge:
        start_x_cm = live_config.start_x_cm
        start_y_cm = live_config.start_y_cm
        def __init__(self, **kwargs):
            pass
        def fly(self, option):
            calls.append('flight')
            return []

    class Preflight:
        def __init__(self, *args, **kwargs):
            pass
        def preflight(self):
            calls.append('preflight')

    monkeypatch.setattr(live, 'make_client',
                        lambda config: calls.append('client') or client)
    monkeypatch.setattr(live, 'check_emergency_transport',
                        lambda config: calls.append('emergency') or (lambda: 9))
    monkeypatch.setattr(live, 'FlightTestRunner', Preflight)
    monkeypatch.setattr(live, 'CubeFlight', Bridge)
    monkeypatch.setattr(live.LiveFlight, '_new_supervisor',
                        lambda self, option: calls.append('supervisor-create') or Supervisor())
    service = bootstrap.build_service(live_config)
    try:
        assert service.submit_chat('mission', _chat_command(service))['kind'] == 'PROPOSE'
        assert service.approval.changed.wait(2)
        assert calls == []
        assert _approve_current(service)['status'] == 'ACKNOWLEDGED'
        deadline = time.monotonic() + 2
        while 'preflight' not in calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls[:3] == ['emergency', 'client', 'preflight']
        assert calls.index('supervisor-create') > calls.index('preflight')
        assert calls.index('supervisor-start') > calls.index('supervisor-create')
    finally:
        service.close()


def test_cli_config_and_root_dispatch(monkeypatch):
    from sentinel.ui.__main__ import parse_config
    from sentinel import cli
    import sentinel.ui.__main__ as entry
    config=parse_config(['--mode','live','--site','site.json','--pose','passive','--confirm'])
    assert config.mode=='LIVE' and config.confirm and config.site_path==Path('site.json')
    calls=[]
    monkeypatch.setattr(entry,'main',lambda argv:calls.append(argv) or 0)
    assert cli.main(['mission-control','--mode','rehearsal'])==0
    assert calls==[['--mode','rehearsal']]


@pytest.mark.parametrize('change', ['consent', 'profile'])
def test_invalid_profile_and_consent_refuse_at_dispatch_before_client(
        live_config, monkeypatch, change):
    from sentinel.ui import live
    calls = []
    monkeypatch.setattr(live, 'make_client', lambda config: calls.append('client'))
    config = live_config
    if change == 'consent':
        config = replace(config, location='other')
    else:
        profile = json.loads(config.provider_profile.read_text())
        profile['approval_status'] = 'UNAPPROVED'
        config.provider_profile.write_text(json.dumps(profile))
    service = bootstrap.build_service(config)
    try:
        assert service.submit_chat('mission', _chat_command(service))['kind'] == 'PROPOSE'
        assert _approve_current(service)['status'] == 'ACKNOWLEDGED'
        deadline = time.monotonic() + 2
        while service.worker.active and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls == []
    finally:
        service.close()


def test_independent_transport_failure_is_deferred_to_dispatch(live_config, monkeypatch):
    from sentinel.ui import live
    calls = []
    monkeypatch.setattr(live, 'make_client', lambda config: calls.append('client'))
    def fail(config):
        calls.append('emergency')
        raise OSError('interface missing')
    monkeypatch.setattr(live, 'check_emergency_transport', fail)
    service = bootstrap.build_service(live_config)
    try:
        assert calls == []
        assert service.submit_chat('mission', _chat_command(service))['kind'] == 'PROPOSE'
        assert _approve_current(service)['status'] == 'ACKNOWLEDGED'
        deadline = time.monotonic() + 2
        while service.worker.active and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls == ['emergency']
    finally:
        service.close()


def test_rehearsal_restart_cannot_acknowledge_an_orphaned_offer(tmp_path):
    config=bootstrap.SessionConfig(data_dir=tmp_path)
    service=bootstrap.build_service(config)
    service.close()
    restarted=bootstrap.build_service(config)
    try:
        assert restarted.read_snapshot('mission')['run']['status']=='ABORTED'
        assert restarted.approval.current('mission') is None
    finally:restarted.close()
def test_live_coverage_mismatch_composes_inert_planning(live_config, monkeypatch):
    from sentinel.ui import readiness
    monkeypatch.setattr(readiness, 'check_tag_map_matches_site',
                        lambda *args: (_ for _ in ()).throw(ValueError('mismatch')))
    service = bootstrap.build_service(live_config)
    try:
        assert service.readiness.state == 'NOT_READY'
        assert service.adapters.options == ()
        assert service.adapters.source_kind == 'LIVE'
    finally:
        service.close()
