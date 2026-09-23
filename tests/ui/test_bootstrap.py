from dataclasses import FrozenInstanceError, replace
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
from pathlib import Path
import sqlite3
from threading import Event, get_ident
import time
import pytest
from sentinel.ui import bootstrap
from sentinel.survey.store import SurveyStore
from sentinel.survey.consent import record_consent


def test_bare_live_readiness_is_ordered_actionable_and_secret_free(monkeypatch):
    from sentinel.ui.readiness import evaluate_live_readiness

    monkeypatch.setenv('OPENROUTER_API_KEY', 'sentinel-secret-api-value')
    monkeypatch.setenv('GEMINI_OPENROUTER_MODEL', 'sentinel-secret-model-value')
    readiness = evaluate_live_readiness(bootstrap.SessionConfig(mode='LIVE'))

    assert readiness.state == 'NOT_READY'
    assert [check.capability for check in readiness.checks] == [
        'planning', 'flight', 'detection', 'emergency']
    assert all(check.reason_code and check.remediation
               for check in readiness.checks if not check.ready)
    assert not readiness.checks[0].ready
    assert not readiness.checks[2].ready
    public = json.dumps(readiness.public())
    for required_name in ('--site', '--tag-map', '--pose-db', '--start-x-cm',
                          '--start-y-cm', '--surface-z-cm'):
        assert required_name in public
    assert readiness.command_template == ''
    assert 'LIVE_CONFIRM_REQUIRED' not in public
    assert 'LIVE_PAID_CALL_AUTH_REQUIRED' not in public
    assert '--confirm' not in public
    assert '--i-authorise-paid-calls' not in public
    assert 'sentinel-secret-api-value' not in public
    assert 'sentinel-secret-model-value' not in public


def test_live_defaults_to_openrouter_gemini_and_loads_nearest_env(
        tmp_path, monkeypatch):
    from sentinel.ui.__main__ import parse_config
    from sentinel.ui.readiness import evaluate_live_readiness

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_OPENROUTER_MODEL', raising=False)
    (tmp_path / '.env').write_text(
        'OPENROUTER_API_KEY=secret-from-env\n'
        'GEMINI_OPENROUTER_MODEL=google/gemini-2.5-flash\n')

    config = parse_config(['--mode', 'live'])
    readiness = evaluate_live_readiness(config)
    detection = next(check for check in readiness.checks
                     if check.capability == 'detection')

    assert config.detector_name == 'gemini-openrouter'
    assert detection.reason_code == 'APPROVED_PROVIDER_PROFILE_REQUIRED'
    assert readiness.command_template == ''
    assert 'secret-from-env' not in json.dumps(readiness.public())
    assert parse_config(['--mode', 'rehearsal']).detector_name == 'scripted'

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


def test_provider_readiness_names_only_missing_variables(live_config, monkeypatch,
                                                         tmp_path):
    from sentinel.ui.readiness import evaluate_live_readiness

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    detection = next(check for check in evaluate_live_readiness(live_config).checks
                     if check.capability == 'detection')
    assert detection.reason_code == 'LIVE_PROVIDER_ENV_REQUIRED'
    assert 'GEMINI_API_KEY' in detection.remediation
    assert 'GEMINI_MODEL' not in detection.remediation
    monkeypatch.setenv('GEMINI_API_KEY', 'hidden-key')
    detection = next(check for check in evaluate_live_readiness(live_config).checks
                     if check.capability == 'detection')
    assert detection.ready
    assert 'hidden-key' not in json.dumps(detection.public())


def test_provider_variable_names_are_absent_for_other_blockers(live_config):
    from sentinel.ui.readiness import evaluate_live_readiness

    for config in (replace(live_config, detector_name='scripted'),
                   replace(live_config, provider_profile=None)):
        detection = next(check for check in evaluate_live_readiness(config).checks
                         if check.capability == 'detection')
        assert detection.reason_code != 'LIVE_PROVIDER_ENV_REQUIRED'
        public = json.dumps(detection.public())
        assert 'GEMINI_API_KEY' not in public
        assert 'GEMINI_MODEL' not in public
        assert 'OPENROUTER_API_KEY' not in public
        assert 'GEMINI_OPENROUTER_MODEL' not in public


def test_route_discovery_uses_udp_connect_without_sending(monkeypatch):
    from sentinel.ui import readiness
    calls = []

    class RouteSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls.append('close')

        def connect(self, destination):
            calls.append(('connect', destination))

        def getsockname(self):
            calls.append('getsockname')
            return ('192.168.10.2', 43100)

        def send(self, *_args):
            pytest.fail('readiness must never send a packet')

    monkeypatch.setattr(readiness.socket, 'socket',
                        lambda family, kind: RouteSocket())
    assert readiness.discover_local_ipv4('192.168.10.1') == '192.168.10.2'
    assert calls == [('connect', ('192.168.10.1', 8889)), 'getsockname', 'close']


def test_route_discovery_rejects_loopback_and_failure(monkeypatch):
    from sentinel.ui import readiness

    class RouteSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def connect(self, _destination):
            pass

        def getsockname(self):
            return ('127.0.0.1', 43100)

    monkeypatch.setattr(readiness.socket, 'socket',
                        lambda family, kind: RouteSocket())
    assert readiness.discover_local_ipv4('192.168.10.1') is None

    def unavailable(*_args):
        raise OSError('no route')

    monkeypatch.setattr(readiness.socket, 'socket', unavailable)
    assert readiness.discover_local_ipv4('192.168.10.1') is None


def test_snapshot_refreshes_route_and_provider_facts(live_config, monkeypatch):
    from sentinel.ui import readiness

    route = {'address': None}
    monkeypatch.setattr(readiness, 'discover_local_ipv4',
                        lambda tello_ip: route['address'])
    service = bootstrap.build_service(replace(live_config, local_ip=None,
                                              confirm=False,
                                              authorise_paid_calls=False))
    try:
        def checks():
            snapshot = service.read_snapshot('mission')
            assert snapshot['schema_version'] == 3
            assert snapshot['readiness']['command_template'] == ''
            assert 'resolved_local_ip' not in snapshot['readiness']
            return {row['capability']: row for row in snapshot['readiness']['checks']}

        assert checks()['flight']['reason_code'] == 'LIVE_NETWORK_CONFIG_REQUIRED'
        route['address'] = '192.168.10.2'
        assert all(checks()[capability]['ready']
                   for capability in ('flight', 'emergency', 'detection'))
        monkeypatch.delenv('GEMINI_API_KEY')
        assert checks()['detection']['reason_code'] == 'LIVE_PROVIDER_ENV_REQUIRED'
        monkeypatch.setenv('GEMINI_API_KEY', 'hidden-key')
        assert checks()['detection']['ready']
        route['address'] = None
        assert checks()['emergency']['reason_code'] == 'LIVE_NETWORK_CONFIG_REQUIRED'
        assert checks()['flight']['reason_code'] == 'LIVE_NETWORK_CONFIG_REQUIRED'
    finally:
        service.close()


def test_explicit_local_ip_takes_precedence_over_route_discovery(live_config,
                                                                 monkeypatch):
    from sentinel.ui import readiness

    def fail_if_discovered(_tello_ip):
        pytest.fail('explicit local IP should take precedence')

    monkeypatch.setattr(readiness, 'discover_local_ipv4', fail_if_discovered)
    checks = readiness.evaluate_live_readiness(live_config).checks
    assert next(check for check in checks if check.capability == 'flight').ready
    assert next(check for check in checks if check.capability == 'emergency').ready


def test_flight_and_emergency_share_one_route_observation(live_config,
                                                          monkeypatch):
    from sentinel.ui import readiness

    calls = []
    def discover(_tello_ip):
        calls.append('route')
        return '192.168.10.2' if len(calls) == 1 else None

    monkeypatch.setattr(readiness, 'discover_local_ipv4', discover)
    checks = readiness.evaluate_live_readiness(replace(live_config, local_ip=None)).checks
    assert calls == ['route']
    assert next(check for check in checks if check.capability == 'flight').ready
    assert next(check for check in checks if check.capability == 'emergency').ready


def test_dispatch_failure_overlay_clears_when_underlying_route_changes(
        live_config, monkeypatch):
    from sentinel.ui import readiness

    route = {'address': '192.168.10.2'}
    monkeypatch.setattr(readiness, 'discover_local_ipv4',
                        lambda tello_ip: route['address'])
    service = bootstrap.build_service(replace(live_config, local_ip=None))
    try:
        from sentinel.ui.readiness import LiveReadiness, ReadinessCheck

        base = readiness.evaluate_live_readiness(service.config)
        failure = ReadinessCheck('flight', False, 'LIVE_PREFLIGHT_FAILED',
                                 'Resolve the no-takeoff preflight failure.')
        service._update_readiness(LiveReadiness(
            'READY_TO_PLAN', tuple(failure if row.capability == 'flight' else row
                                   for row in base.checks), '', base.resolved_local_ip))
        def flight():
            return next(row for row in service.read_snapshot('mission')['readiness']['checks']
                        if row['capability'] == 'flight')

        assert flight()['reason_code'] == 'LIVE_PREFLIGHT_FAILED'
        assert flight()['reason_code'] == 'LIVE_PREFLIGHT_FAILED'
        route['address'] = None
        assert flight()['reason_code'] == 'LIVE_NETWORK_CONFIG_REQUIRED'
        route['address'] = '192.168.10.2'
        assert flight()['ready']
    finally:
        service.close()


def test_stale_successful_callback_cannot_mask_route_loss(live_config,
                                                           monkeypatch):
    from sentinel.ui import readiness

    route = {'address': '192.168.10.2'}
    monkeypatch.setattr(readiness, 'discover_local_ipv4',
                        lambda tello_ip: route['address'])
    service = bootstrap.build_service(replace(live_config, local_ip=None))
    try:
        stale = readiness.evaluate_live_readiness(service.config)
        assert next(row for row in stale.checks if row.capability == 'flight').ready
        route['address'] = None
        service._update_readiness(stale)
        checks = {row['capability']: row for row in
                  service.read_snapshot('mission')['readiness']['checks']}
        assert checks['flight']['reason_code'] == 'LIVE_NETWORK_CONFIG_REQUIRED'
        assert checks['emergency']['reason_code'] == 'LIVE_NETWORK_CONFIG_REQUIRED'
    finally:
        service.close()


@pytest.mark.parametrize('capability,reason_code', [
    ('flight', 'LIVE_PREFLIGHT_FAILED'),
    ('emergency', 'INDEPENDENT_EMERGENCY_UNAVAILABLE'),
])
def test_dispatch_failure_overlay_clears_when_route_address_changes(
        live_config, monkeypatch, capability, reason_code):
    from sentinel.ui import readiness
    from sentinel.ui.readiness import LiveReadiness, ReadinessCheck

    route = {'address': '192.168.10.2'}
    monkeypatch.setattr(readiness, 'discover_local_ipv4',
                        lambda tello_ip: route['address'])
    service = bootstrap.build_service(replace(live_config, local_ip=None))
    try:
        base = readiness.evaluate_live_readiness(service.config)
        failure = ReadinessCheck(capability, False, reason_code,
                                 'Restore dispatch capability.')
        service._update_readiness(LiveReadiness(
            'READY_TO_PLAN', tuple(failure if row.capability == capability else row
                                   for row in base.checks), '', base.resolved_local_ip))
        def selected_check():
            return next(row for row in service.read_snapshot('mission')['readiness']['checks']
                        if row['capability'] == capability)

        assert selected_check()['reason_code'] == reason_code
        route['address'] = '192.168.10.3'
        assert selected_check()['ready']
    finally:
        service.close()

def test_bare_live_host_refuses_before_adapter_construction(tmp_path, monkeypatch):
    from sentinel.ui import live
    calls = []
    monkeypatch.setattr(bootstrap, 'build_rehearsal_adapters',
                        lambda *args: calls.append(('rehearsal', args)))
    monkeypatch.setattr(live, 'make_client',
                        lambda config: calls.append(('client', config)))
    monkeypatch.setattr(live, 'check_emergency_transport',
                        lambda config: calls.append(('emergency', config)))
    with pytest.raises(bootstrap.StartupError,
                       match='^SITE_PROFILE_INCOMPLETE$'):
        bootstrap.build_service(
            bootstrap.SessionConfig(data_dir=tmp_path, mode='LIVE'))
    assert calls == []


def test_invalid_consent_database_is_failed_readiness_without_file_mutation(
        live_config):
    consent_db = live_config.data_dir / 'invalid-consent.sqlite'
    consent_db.write_bytes(b'not a sqlite database')
    before_bytes = consent_db.read_bytes()
    before_mtime = consent_db.stat().st_mtime_ns

    service = bootstrap.build_service(
        replace(live_config, survey_db=consent_db))
    try:
        snapshot = service.read_snapshot('mission')
        detection = next(check for check in snapshot['readiness']['checks']
                         if check['capability'] == 'detection')
        assert detection['ready'] is False
        assert detection['reason_code'] == 'CONSENT_DATABASE_UNREADABLE'
        assert snapshot['readiness']['state'] == 'READY_TO_PLAN'
    finally:
        service.close()

    assert consent_db.read_bytes() == before_bytes
    assert consent_db.stat().st_mtime_ns == before_mtime
    assert not Path(str(consent_db) + '-wal').exists()
    assert not Path(str(consent_db) + '-shm').exists()


def test_live_consent_inspection_preserves_existing_bytes_mtime_and_schema(
        live_config):
    consent_db = live_config.data_dir / 'minimal-consent.sqlite'
    with sqlite3.connect(consent_db) as conn:
        conn.execute('CREATE TABLE filming_consent('
                     'location TEXT NOT NULL, consent_date TEXT NOT NULL, '
                     'recorded_by TEXT NOT NULL, '
                     'PRIMARY KEY(location, consent_date))')
        conn.execute('INSERT INTO filming_consent VALUES (?,?,?)',
                     ('lab', '2026-09-21', 'operator'))

    def schema_rows():
        uri = consent_db.resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as conn:
            return conn.execute(
                "SELECT type,name,sql FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()

    before_bytes = consent_db.read_bytes()
    before_mtime = consent_db.stat().st_mtime_ns
    before_schema = schema_rows()
    service = bootstrap.build_service(
        replace(live_config, survey_db=consent_db))
    try:
        snapshot = service.read_snapshot('mission')
        detection = next(check for check in snapshot['readiness']['checks']
                         if check['capability'] == 'detection')
        assert detection['ready'] is True
    finally:
        service.close()

    assert schema_rows() == before_schema
    assert consent_db.read_bytes() == before_bytes
    assert consent_db.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize('query_fails', [False, True])
def test_readonly_consent_connection_closes_after_query(tmp_path, monkeypatch,
                                                         query_fails):
    from sentinel.ui import readiness

    calls = []
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls.append('exit')

        def execute(self, statement, parameters):
            calls.append('execute')
            if query_fails:
                raise sqlite3.OperationalError('query unavailable')
            return self

        def fetchone(self):
            return (1,)

        def close(self):
            calls.append('close')

    def connect(path, *, uri, timeout):
        assert path.endswith('?mode=ro')
        assert uri is True
        return Connection()

    monkeypatch.setattr(readiness.sqlite3, 'connect', connect)
    if query_fails:
        with pytest.raises(sqlite3.OperationalError):
            readiness._has_recorded_consent_readonly(tmp_path / 'survey.sqlite',
                                                     'lab', '2026-09-21')
    else:
        assert readiness._has_recorded_consent_readonly(
            tmp_path / 'survey.sqlite', 'lab', '2026-09-21')
    assert calls[0] == 'execute'
    assert calls[-1] == 'close'


def test_older_poll_cannot_discard_newer_dispatch_failure(live_config, monkeypatch):
    from sentinel.ui import readiness
    from sentinel.ui.readiness import LiveReadiness, ReadinessCheck

    route = {'address': '192.168.10.2'}
    monkeypatch.setattr(readiness, 'discover_local_ipv4',
                        lambda tello_ip: route['address'])
    service = bootstrap.build_service(replace(live_config, local_ip=None))
    original_evaluate = readiness.evaluate_live_readiness
    poll_captured = Event()
    resume_poll = Event()
    callback_started = Event()
    poll_thread = {'id': None}

    def evaluate(config):
        observed = original_evaluate(config)
        if get_ident() == poll_thread['id']:
            poll_captured.set()
            assert resume_poll.wait(5), 'timed out resuming the paused poll'
        return observed

    monkeypatch.setattr(readiness, 'evaluate_live_readiness', evaluate)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            def read_poll():
                poll_thread['id'] = get_ident()
                return service.read_snapshot('mission')

            poll = pool.submit(read_poll)
            assert poll_captured.wait(5)
            route['address'] = '192.168.10.3'
            current = original_evaluate(service.config)
            failure = ReadinessCheck('flight', False, 'LIVE_PREFLIGHT_FAILED',
                                     'Resolve the no-takeoff preflight failure.')
            update = LiveReadiness(
                'READY_TO_PLAN', tuple(failure if row.capability == 'flight' else row
                                       for row in current.checks), '',
                current.resolved_local_ip)

            def publish_failure():
                callback_started.set()
                service._update_readiness(update)

            callback = pool.submit(publish_failure)
            assert callback_started.wait(5)
            try:
                callback.result(timeout=1)
            except FutureTimeout:
                pass  # Serialized callback waits for the paused poll.
            finally:
                resume_poll.set()
            poll.result(timeout=5)
            callback.result(timeout=5)
        flight = next(row for row in service.read_snapshot('mission')['readiness']['checks']
                      if row['capability'] == 'flight')
        assert flight['reason_code'] == 'LIVE_PREFLIGHT_FAILED'
    finally:
        resume_poll.set()
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


def test_live_dispatch_does_not_require_startup_confirmation(live_config,
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
        assert calls == ['emergency', 'client']
    finally:
        service.close()


@pytest.mark.parametrize('decision,expected_status', [
    ('APPROVE', 'ACKNOWLEDGED'),
    ('REJECT', 'REJECTED'),
])
def test_ready_to_plan_proposal_allows_current_approval_and_rejection(
        live_config, monkeypatch, decision, expected_status):
    from sentinel.ui import live
    from sentinel.ui import readiness

    calls = []
    monkeypatch.setattr(live, 'make_client',
                        lambda config: calls.append('client'))
    monkeypatch.setattr(readiness, 'discover_local_ipv4', lambda tello_ip: None)
    service = bootstrap.build_service(replace(live_config, confirm=False,
                                              local_ip=None))
    try:
        assert service.submit_chat(
            'mission', _chat_command(service))['kind'] == 'PROPOSE'
        snapshot = service.read_snapshot('mission')
        assert snapshot['readiness']['state'] == 'READY_TO_PLAN'
        assert snapshot['proposal']['eligibility'] == {
            'consent': None,
            'envelope': True,
            'preflight': None,
            'current': True,
        }
        assert snapshot['proposal']['disabled_reasons'] == []
        legal = {row['action']: row['enabled']
                 for row in snapshot['legal_actions']}
        assert legal['APPROVE'] is True
        assert legal['REJECT'] is True

        offer = service.approval.current('mission')
        result = service.submit_decision('mission', dict(
            session_id=service.session_id, run_id='mission',
            proposal_id=offer['id'], proposal_sha256=offer['sha256'],
            expected_version=offer['decision_version'], decision=decision,
            command_id=f'{decision.lower()}-command', correlation_id='corr',
            attempt_id=f'{decision.lower()}-attempt',
            idempotency_key=f'{decision.lower()}-key'))
        assert result['status'] == expected_status
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


def test_live_preflight_failure_is_projected_as_unarmed_readiness(live_config,
                                                                   monkeypatch):
    from sentinel.ui import live
    from sentinel.dry_run import DryRunClient

    class BrokenPreflight:
        def __init__(self, *args, **kwargs):
            pass

        def preflight(self):
            raise RuntimeError('battery unavailable')

    monkeypatch.setattr(live, 'check_emergency_transport',
                        lambda config: (lambda: 9))
    monkeypatch.setattr(live, 'make_client', lambda config: DryRunClient())
    monkeypatch.setattr(live, 'FlightTestRunner', BrokenPreflight)
    service = bootstrap.build_service(live_config)
    try:
        assert service.submit_chat('mission', _chat_command(service))['kind'] == 'PROPOSE'
        assert _approve_current(service)['status'] == 'ACKNOWLEDGED'
        deadline = time.monotonic() + 2
        flight = None
        while time.monotonic() < deadline:
            flight = next(check for check in service.readiness.checks
                          if check.capability == 'flight')
            if flight.reason_code == 'LIVE_PREFLIGHT_FAILED':
                break
            time.sleep(0.01)
        snapshot = service.read_snapshot('mission')
        flight = next(check for check in snapshot['readiness']['checks']
                      if check['capability'] == 'flight')
        assert snapshot['readiness']['state'] == 'READY_TO_PLAN'
        assert flight == {
            'capability': 'flight',
            'ready': False,
            'reason_code': 'LIVE_PREFLIGHT_FAILED',
            'remediation': 'Resolve the no-takeoff preflight failure before dispatch.',
        }
        assert next(check for check in service.read_snapshot('mission')['readiness']['checks']
                    if check['capability'] == 'flight')['reason_code'] == 'LIVE_PREFLIGHT_FAILED'
        assert service.registry.get('mission') is None
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


@pytest.mark.parametrize('flag,value,field,expected', [
    ('--site', 'operator-site.json', 'site_path', Path('operator-site.json')),
    ('--tag-map', 'operator-map.json', 'tag_map_path', Path('operator-map.json')),
    ('--pose-db', 'operator-pose.sqlite', 'pose_db', Path('operator-pose.sqlite')),
    ('--pose', 'passive', 'pose_mode', 'passive'),
    ('--surface-z-cm', '40', 'surface_z_cm', 40.0),
    ('--start-x-cm', '123', 'start_x_cm', 123),
    ('--start-y-cm', '234', 'start_y_cm', 234),
])
def test_any_legacy_physical_cli_flag_preserves_compatibility_path(
        flag, value, field, expected):
    from sentinel.ui.__main__ import parse_config

    config = parse_config(['--mode', 'live', flag, value])
    assert config.site_profiles_path is None
    assert getattr(config, field) == expected


@pytest.mark.parametrize('flag,value', [
    ('--tag-m', 'operator-map.json'),
    ('--surface-z', '40'),
    ('--start-x', '123'),
])
def test_abbreviated_legacy_physical_flags_are_rejected(flag, value):
    from sentinel.ui.__main__ import parse_config

    with pytest.raises(SystemExit) as exc:
        parse_config(['--mode', 'live', flag, value])
    assert exc.value.code == 2


@pytest.mark.parametrize('argument,field,expected', [
    ('--site=operator-site.json', 'site_path', Path('operator-site.json')),
    ('--tag-map=operator-map.json', 'tag_map_path', Path('operator-map.json')),
    ('--pose-db=operator-pose.sqlite', 'pose_db', Path('operator-pose.sqlite')),
    ('--pose=off', 'pose_mode', 'off'),
    ('--surface-z-cm=40', 'surface_z_cm', 40.0),
    ('--start-x-cm=123', 'start_x_cm', 123),
    ('--start-y-cm=234', 'start_y_cm', 234),
])
def test_full_equals_legacy_flags_preserve_compatibility_path(
        argument, field, expected):
    from sentinel.ui.__main__ import parse_config

    config = parse_config(['--mode', 'live', argument])
    assert config.site_profiles_path is None
    assert getattr(config, field) == expected


def test_complete_legacy_cli_physical_fields_reach_service(tmp_path):
    from sentinel.ui.__main__ import parse_config

    site = Path('docs/wp5-flight1-site.json')
    tag_map = Path('docs/wp5-flight3-map.json')
    pose_db = tmp_path / 'operator-pose.sqlite'
    config = parse_config([
        '--mode', 'live', '--data-dir', str(tmp_path / 'data'),
        '--site', str(site), '--tag-map', str(tag_map),
        '--pose-db', str(pose_db), '--pose', 'off',
        '--surface-z-cm', '50', '--start-x-cm', '250',
        '--start-y-cm', '190'])
    assert config.site_profiles_path is None
    service = bootstrap.build_service(config)
    try:
        assert service.config.site_path == site
        assert service.config.tag_map_path == tag_map
        assert service.config.pose_db == pose_db
        assert service.config.surface_z_cm == 50
        assert (service.config.start_x_cm, service.config.start_y_cm) == (250, 190)
    finally:
        service.close()


def test_incomplete_legacy_cli_physical_fields_fail_without_registry_fallback(
        tmp_path):
    from sentinel.ui.__main__ import parse_config

    config = parse_config(['--mode', 'live', '--data-dir', str(tmp_path / 'data'),
                           '--site', 'operator-site.json'])
    assert config.site_profiles_path is None
    with pytest.raises(bootstrap.StartupError,
                       match='^SITE_PROFILE_INCOMPLETE$'):
        bootstrap.build_service(config)
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('change', ['consent', 'profile'])
def test_invalid_profile_and_consent_refuse_at_dispatch_before_client(
        live_config, monkeypatch, change):
    from sentinel.ui import live
    calls = []
    monkeypatch.setattr(live, 'make_client', lambda config: calls.append('client'))
    config = replace(live_config, confirm=False, authorise_paid_calls=False)
    if change == 'consent':
        config = replace(config, location='other')
    else:
        profile = json.loads(config.provider_profile.read_text())
        profile['approval_status'] = 'UNAPPROVED'
        config.provider_profile.write_text(json.dumps(profile))
    service = bootstrap.build_service(config)
    try:
        detection = next(check for check in service.read_snapshot('mission')['readiness']['checks']
                         if check['capability'] == 'detection')
        assert detection['reason_code'] == (
            'RECORDED_CONSENT_REQUIRED' if change == 'consent'
            else 'APPROVED_PROVIDER_PROFILE_REQUIRED')
        assert service.submit_chat('mission', _chat_command(service))['kind'] == 'PROPOSE'
        assert service.approval.changed.wait(2)
        snapshot = service.read_snapshot('mission')
        assert snapshot['proposal']['eligibility']['consent'] is None
        assert snapshot['proposal']['disabled_reasons'] == []
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
        for _ in range(2):
            emergency = next(check for check in service.read_snapshot('mission')['readiness']['checks']
                             if check['capability'] == 'emergency')
            assert emergency['reason_code'] == 'INDEPENDENT_EMERGENCY_UNAVAILABLE'
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


def test_graceful_idle_configured_live_restart_remains_active(live_config):
    config = live_config
    service = bootstrap.build_service(config)
    assert service.read_snapshot('mission')['run']['status'] == 'ACTIVE'
    service.close()

    restarted = bootstrap.build_service(config)
    try:
        snapshot = restarted.read_snapshot('mission')
        assert snapshot['run']['status'] == 'ACTIVE'
        assert snapshot['readiness']['checks'][0]['ready'] is True
        assert restarted.approval.current('mission') is None
    finally:
        restarted.close()


def test_live_restart_with_interrupted_proposal_requires_recovery(live_config):
    from sentinel.ui.approval import ApprovalBroker
    from sentinel.ui.store import UIStore

    store = UIStore.open(live_config.data_dir / 'ui.sqlite')
    store.ensure_run('interrupted-session', 'mission', 'LIVE')
    ApprovalBroker(store).offer(
        'mission', {'arguments': {'option_id': 'RAIL_A'},
                    'description': 'Interrupted proposal'}, 'corr')

    restarted = bootstrap.build_service(live_config)
    try:
        assert (restarted.read_snapshot('mission')['run']['status'] ==
                'RECOVERY_REQUIRED')
        assert restarted.approval.current('mission') is None
    finally:
        restarted.close()


def test_live_coverage_mismatch_refuses_before_service_creation(live_config, monkeypatch):
    from sentinel.ui import site_profiles
    monkeypatch.setattr(site_profiles, 'check_tag_map_matches_site',
                        lambda *args: (_ for _ in ()).throw(ValueError('mismatch')))
    with pytest.raises(bootstrap.StartupError,
                       match='^INVALID_LIVE_SITE_OR_COVERAGE$'):
        bootstrap.build_service(live_config)
