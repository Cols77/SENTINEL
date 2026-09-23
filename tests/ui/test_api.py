from pathlib import Path
from contextlib import closing
import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sentinel.ui.bootstrap import SessionConfig, build_service
from sentinel.ui.app import build_app
from sentinel.ui.models import validate_snapshot


def _profile_service(tmp_path):
    root = Path(__file__).resolve().parents[2]
    shutil.copyfile(root / 'docs/wp5-flight1-site.json', tmp_path / 'first-site.json')
    second_site = json.loads((tmp_path / 'first-site.json').read_text())
    second_site['cube']['max_x_m'] = 3.6
    (tmp_path / 'second-site.json').write_text(json.dumps(second_site))
    shutil.copyfile(root / 'docs/wp5-flight3-map.json', tmp_path / 'tags.json')
    def row(profile_id, site):
        return dict(id=profile_id, label=profile_id, enabled=True,
                    setup_status='Ready', site_path=site,
                    tag_map_path='tags.json', pose_db='pose.sqlite',
                    pose_mode='off', surface_z_cm=50,
                    start_x_cm=250, start_y_cm=190)
    profiles = [row('First', 'first-site.json'),
                dict(row('Second', 'second-site.json'), local_ip='192.168.10.2'),
                dict(id='Disabled', label='Disabled', enabled=False,
                     setup_status='Needs setup'),
                dict(row('Invalid', 'missing-site.json'))]
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps(dict(version=1, profiles=profiles)))
    service = build_service(SessionConfig(
        mode='LIVE', data_dir=tmp_path / 'data',
        site_profiles_path=path, site_profile_id='First'))
    return service


def test_site_profile_catalogue_and_multiple_idle_switches_are_atomic(tmp_path, monkeypatch):
    from sentinel.ui import live, readiness

    monkeypatch.setattr(readiness, 'discover_local_ipv4', lambda *_a: None)
    service = _profile_service(tmp_path)
    operational = []
    monkeypatch.setattr(live, 'make_client', lambda *_a: operational.append('drone'))
    monkeypatch.setattr(live, 'check_emergency_transport',
                        lambda *_a: operational.append('emergency'))
    monkeypatch.setattr(live.LiveFlight, '_new_supervisor',
                        lambda *_a: operational.append('provider'))
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            initial = client.get('/api/v1/site-profiles')
            assert initial.status_code == 200
            assert initial.json() == {
                'version': 1, 'selected_profile_id': 'First',
                'switch_allowed': True, 'switch_reason': None,
                'profiles': [
                    dict(id='First', label='First', enabled=True, setup_status='Ready'),
                    dict(id='Second', label='Second', enabled=True, setup_status='Ready'),
                    dict(id='Disabled', label='Disabled', enabled=False,
                         setup_status='Needs setup'),
                    dict(id='Invalid', label='Invalid', enabled=True,
                         setup_status='Ready'),
                ],
            }
            before = client.get('/api/v1/runs/mission/snapshot').json()
            assert validate_snapshot(before) == before
            assert before['readiness']['checks'][1]['ready'] is False
            prior_flight, prior_feed = service.adapters.flight, service.pose_feed
            changed = client.post('/api/v1/site-profiles/select',
                                  json={'profile_id': 'Second'})
            assert changed.status_code == 200
            assert changed.json()['selected_profile_id'] == 'Second'
            after = client.get('/api/v1/runs/mission/snapshot').json()
            assert validate_snapshot(after) == after
            assert after['session']['startup_summary']['site_id'] == 'second-site'
            assert after['mission']['envelope'] != before['mission']['envelope']
            assert after['mission']['route'] != before['mission']['route']
            assert max(stop['x'] for stop in after['mission']['route']) > max(
                stop['x'] for stop in before['mission']['route'])
            assert after['readiness']['checks'][1]['ready'] is True
            assert after['readiness']['checks'][3]['ready'] is True
            assert service.runtime.adapters is service.adapters
            assert service.pose_feed is service.adapters.pose_feed
            assert service.adapters.flight is not prior_flight
            assert service.pose_feed is not prior_feed
            assert service.adapters.flight.on_emergency_ready == service.safety.admit_emergency_sender
            assert service.adapters.flight.on_readiness_update == service._update_readiness
            assert client.post('/api/v1/site-profiles/select',
                               json={'profile_id': 'First'}).status_code == 200
            restored = client.get('/api/v1/runs/mission/snapshot').json()
            assert validate_snapshot(restored) == restored
            assert restored['mission']['route'] == before['mission']['route']
            assert restored['readiness']['checks'][1]['ready'] is False
            assert client.get('/api/v1/site-profiles').json()['switch_allowed'] is True
            assert [event['type'] for event in service.read_events('mission', 0, 200)] == [
                'SITE_PROFILE_CHANGED', 'SITE_PROFILE_CHANGED']
            assert all(event['summary'] == 'Site profile changed'
                       for event in service.read_events('mission', 0, 200))
            assert operational == []
    finally:
        service.close()


@pytest.mark.parametrize('body,reason', [
    ({}, 'INVALID_PROFILE_REQUEST'),
    ({'profile_id': ''}, 'INVALID_PROFILE_REQUEST'),
    ({'profile_id': 3}, 'INVALID_PROFILE_REQUEST'),
    ({'profile_id': 'Second', 'run_id': 'other'}, 'INVALID_PROFILE_REQUEST'),
    ({'profile_id': 'Unknown'}, 'SITE_PROFILE_NOT_FOUND'),
    ({'profile_id': 'Disabled'}, 'SITE_PROFILE_DISABLED'),
    ({'profile_id': 'Invalid'}, 'INVALID_LIVE_SITE_OR_COVERAGE'),
])
def test_profile_switch_rejects_invalid_selection_without_mutation(tmp_path, body, reason):
    service = _profile_service(tmp_path)
    original = (service.config, service.adapters, service.runtime.adapters,
                service.readiness, service.pose_feed, service.artifacts)
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select', json=body)
            assert response.status_code == 422
            assert response.json()['status'] == 'INVALID'
            assert response.json()['reason_code'] == reason
            assert (service.config, service.adapters, service.runtime.adapters,
                    service.readiness, service.pose_feed, service.artifacts) == original
            assert service.read_events('mission', 0, 200) == []
    finally:
        service.close()


@pytest.mark.parametrize('activity', [
    'offer', 'decision_command', 'worker', 'runner', 'runtime',
    'survey', 'terminal', 'recovery', 'refusal_event', 'artifacts',
    'pose_artifacts', 'pose_latest', 'pose_health', 'evidence_status',
    'pose_events',
])
def test_profile_switch_locks_after_any_mission_activity(tmp_path, activity):
    from sentinel.pose.persistence import ensure_schema
    from sentinel.survey.store import SurveyStore

    service = _profile_service(tmp_path)
    run = service.config.run_id
    try:
        if activity == 'offer':
            with service.store.connection(write=True) as conn:
                conn.execute("INSERT INTO ui_offers VALUES ('p',?,'{}','CANCELLED',NULL)", (run,))
        elif activity == 'decision_command':
            service.store.claim_command(dict(command_id='c', run_id=run,
                                             idempotency_key='k'))
        elif activity == 'worker':
            with service.worker._lock:
                service.worker._active = True
        elif activity == 'runner':
            service.registry.bind(run, object())
        elif activity == 'runtime':
            service.runtime.state[run] = {'phase': 'PROPOSAL'}
        elif activity == 'survey':
            survey = SurveyStore.open(service.runtime.survey_db)
            try:
                survey.create_run(run, 'count computers')
            finally:
                survey.connection.close()
        elif activity == 'artifacts':
            service.artifacts['frame'] = {'path': tmp_path / 'frame.png'}
        elif activity == 'pose_artifacts':
            service.pose_feed.artifacts['pose'] = {'path': tmp_path / 'pose.png'}
        elif activity == 'pose_latest':
            service.pose_feed.latest = object()
        elif activity == 'pose_health':
            service.pose_feed.health = {'status': 'DEGRADED'}
        elif activity == 'evidence_status':
            service.evidence_status['frame'] = 'REVOKED'
        elif activity == 'pose_events':
            with closing(sqlite3.connect(service.config.pose_db)) as pose:
                with pose:
                    ensure_schema(pose)
                    pose.execute(
                        'INSERT INTO pose_events VALUES (?,?,?,?,?,?)',
                        (run, 1, 'DRONE_POSE_REPORTED', '{}', 'sha',
                         '2026-09-23T00:00:00+00:00'))
        elif activity in {'terminal', 'recovery'}:
            with service.store.connection(write=True) as conn:
                conn.execute('UPDATE ui_runs SET status=? WHERE run_id=?',
                             ('COMPLETE' if activity == 'terminal' else
                              'RECOVERY_REQUIRED', run))
        else:
            service.store.append_event(run, 'CHAT', {'summary': 'Request refused'})
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            previous = (service.config, service.adapters, service.pose_feed,
                        service.artifacts, service.evidence_status)
            catalogue = client.get('/api/v1/site-profiles').json()
            assert catalogue['switch_allowed'] is False
            assert catalogue['switch_reason'] == 'MISSION_NOT_IDLE'
            response = client.post('/api/v1/site-profiles/select',
                                   json={'profile_id': 'Second'})
            assert response.status_code == 409
            assert response.json()['status'] == 'CONFLICT'
            assert service.config.site_profile_id == 'First'
            assert (service.config, service.adapters, service.pose_feed,
                    service.artifacts, service.evidence_status) == previous
            assert all(event['type'] != 'SITE_PROFILE_CHANGED'
                       for event in service.read_events(run, 0, 200))
    finally:
        if activity == 'worker':
            with service.worker._lock:
                service.worker._active = False
        if activity == 'runner':
            service.registry.unbind(run, service.registry.get(run))
        service.close()


@pytest.mark.parametrize('activity', ['runtime', 'artifacts', 'pose_events'])
def test_profile_switch_rechecks_after_composition_and_closes_candidate(
        tmp_path, monkeypatch, activity):
    from sentinel.ui import live
    from sentinel.pose.persistence import ensure_schema

    service = _profile_service(tmp_path)
    original_compose = live.compose
    candidates = []
    def compose_then_activate(config, readiness=None):
        candidate = original_compose(config, readiness)
        candidates.append(candidate)
        if activity == 'runtime':
            service.runtime.state[service.config.run_id] = {'phase': 'PROPOSAL'}
        elif activity == 'artifacts':
            service.artifacts['frame'] = {'path': tmp_path / 'frame.png'}
        else:
            with closing(sqlite3.connect(service.config.pose_db)) as pose:
                with pose:
                    ensure_schema(pose)
                    pose.execute('INSERT INTO pose_events VALUES (?,?,?,?,?,?)',
                        (service.config.run_id, 1, 'DRONE_POSE_REPORTED',
                         '{}', 'sha', '2026-09-23T00:00:00+00:00'))
        return candidate
    monkeypatch.setattr(live, 'compose', compose_then_activate)
    monkeypatch.setattr(live.LiveFlight, 'close',
                        lambda self: setattr(self, 'was_closed', True))
    prior = service.adapters
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                                   json={'profile_id': 'Second'})
            assert response.status_code == 409
            assert service.adapters is prior
            assert candidates[0].flight.was_closed is True
            assert service.read_events('mission', 0, 200) == []
    finally:
        service.close()


def test_profile_switch_pose_history_is_run_scoped_and_read_only(tmp_path):
    from sentinel.pose.persistence import ensure_schema

    service = _profile_service(tmp_path)
    pose_path = service.config.pose_db
    try:
        assert not pose_path.exists()
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            assert client.get('/api/v1/site-profiles').json()['switch_allowed'] is True
            assert not pose_path.exists()
            with closing(sqlite3.connect(pose_path)) as pose:
                with pose:
                    pose.execute('CREATE TABLE unrelated (value TEXT)')
            assert client.get('/api/v1/site-profiles').json()['switch_allowed'] is True
            with closing(sqlite3.connect(pose_path)) as pose:
                with pose:
                    ensure_schema(pose)
                    pose.execute('INSERT INTO pose_events VALUES (?,?,?,?,?,?)',
                        ('other-run', 1, 'DRONE_POSE_REPORTED', '{}', 'sha',
                         '2026-09-23T00:00:00+00:00'))
            assert client.get('/api/v1/site-profiles').json()['switch_allowed'] is True
            assert client.post('/api/v1/site-profiles/select',
                               json={'profile_id': 'Second'}).status_code == 200
    finally:
        service.close()


def test_profile_switch_checks_candidate_pose_database_before_commit(
        tmp_path, monkeypatch):
    from dataclasses import replace
    from sentinel.pose.persistence import ensure_schema
    from sentinel.ui import live

    service = _profile_service(tmp_path)
    candidate_pose_path = tmp_path / 'candidate-pose.sqlite'
    registry = service.site_profile_registry
    service.site_profile_registry = replace(registry, profiles=tuple(
        replace(profile, pose_db=candidate_pose_path)
        if profile.id == 'Second' else profile
        for profile in registry.profiles))
    with closing(sqlite3.connect(candidate_pose_path)) as pose:
        with pose:
            ensure_schema(pose)
            pose.execute('INSERT INTO pose_events VALUES (?,?,?,?,?,?)',
                (service.config.run_id, 1, 'DRONE_POSE_REPORTED', '{}', 'sha',
                 '2026-09-23T00:00:00+00:00'))

    original_compose = live.compose
    candidates = []
    closed = []
    def capture_compose(*args, **kwargs):
        candidate = original_compose(*args, **kwargs)
        candidates.append(candidate)
        return candidate
    monkeypatch.setattr(live, 'compose', capture_compose)
    monkeypatch.setattr(live.LiveFlight, 'close', lambda self: closed.append(self))
    monkeypatch.setattr(live.LivePoseFeed, 'close', lambda self: closed.append(self))
    prior = (service.config, service.adapters, service.runtime.adapters,
             service.pose_feed, service.artifacts, service.readiness,
             service._readiness_overlays.copy())
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                                   json={'profile_id': 'Second'})
            assert response.status_code == 409
            assert response.json()['status'] == 'CONFLICT'
            assert response.json()['reason_code'] == 'MISSION_NOT_IDLE'
            assert (service.config, service.adapters, service.runtime.adapters,
                    service.pose_feed, service.artifacts, service.readiness,
                    service._readiness_overlays) == prior
            assert service.site_profiles()['selected_profile_id'] == 'First'
            assert service.read_events('mission', 0, 200) == []
            assert candidates and candidates[0].flight in closed
            assert candidates[0].pose_feed in closed
    finally:
        service.close()


def test_profile_switch_final_unavailable_recheck_uses_unavailable_status(
        tmp_path, monkeypatch):
    from sentinel.ui import live

    service = _profile_service(tmp_path)
    original_reason = service._profile_switch_reason
    closed = []
    monkeypatch.setattr(live.LiveFlight, 'close', lambda self: closed.append(self))
    def unavailable_at_final_recheck(*args, **kwargs):
        conn = args[0] if args else kwargs.get('conn')
        if conn is not None:
            return 'PROFILE_SWITCH_UNAVAILABLE'
        return original_reason(*args, **kwargs)
    monkeypatch.setattr(service, '_profile_switch_reason',
                        unavailable_at_final_recheck)
    prior = (service.config, service.adapters, service.runtime.adapters,
             service.pose_feed, service.artifacts, service.readiness)
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                                   json={'profile_id': 'Second'})
            assert response.status_code == 503
            assert response.json()['status'] == 'UNAVAILABLE'
            assert response.json()['reason_code'] == 'PROFILE_SWITCH_UNAVAILABLE'
            assert (service.config, service.adapters, service.runtime.adapters,
                    service.pose_feed, service.artifacts, service.readiness) == prior
            assert closed and prior[1].flight not in closed
            assert service.read_events('mission', 0, 200) == []
    finally:
        service.close()


def test_profile_switch_commit_failure_rolls_back_state_and_closes_candidate(
        tmp_path, monkeypatch):
    from sentinel.ui import live, store

    service = _profile_service(tmp_path)
    original_connect = store.sqlite3.connect

    class CommitFailureConnection(sqlite3.Connection):
        def commit(self):
            changed = self.execute(
                "SELECT 1 FROM ui_events WHERE event_type='SITE_PROFILE_CHANGED' "
                'LIMIT 1').fetchone()
            if changed is not None:
                raise sqlite3.OperationalError('injected commit failure')
            return super().commit()

    def connect_with_commit_failure(path, *args, **kwargs):
        if Path(path) == service.store.path:
            kwargs['factory'] = CommitFailureConnection
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(store.sqlite3, 'connect', connect_with_commit_failure)
    closed = []
    monkeypatch.setattr(live.LiveFlight, 'close', lambda self: closed.append(self))
    prior = (service.config, service.adapters, service.runtime.adapters,
             service.pose_feed, service.artifacts, service.readiness,
             service._readiness_overlays.copy())
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                                   json={'profile_id': 'Second'})
            assert response.status_code == 503
            assert response.json()['status'] == 'UNAVAILABLE'
            assert response.json()['reason_code'] == 'PROFILE_SWITCH_UNAVAILABLE'
            assert (service.config, service.adapters, service.runtime.adapters,
                    service.pose_feed, service.artifacts, service.readiness,
                    service._readiness_overlays) == prior
            assert service.site_profiles()['selected_profile_id'] == 'First'
            assert closed and prior[1].flight not in closed
            assert service.read_events('mission', 0, 200) == []
    finally:
        service.close()


def test_profile_select_does_not_block_event_loop(tmp_path, monkeypatch):
    from threading import Event, Thread, Timer

    service = _profile_service(tmp_path)
    entered = Event()
    release = Event()
    responses = []
    errors = []

    def blocked_selection(command):
        entered.set()
        if not release.wait(timeout=3):
            raise AssertionError('controlled profile selection was not released')
        return {'status': 'ACKNOWLEDGED', 'reason_code': None}

    monkeypatch.setattr(service, 'select_site_profile', blocked_selection)
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            def select_profile():
                try:
                    responses.append(client.post('/api/v1/site-profiles/select',
                                                 json={'profile_id': 'Second'}))
                except BaseException as exc:
                    errors.append(exc)

            request = Thread(target=select_profile)
            request.start()
            assert entered.wait(timeout=1)
            watchdog = Timer(1, release.set)
            watchdog.start()
            try:
                session = client.get('/api/v1/session')
                assert session.status_code == 200
                assert not release.is_set()
            finally:
                release.set()
                request.join(timeout=3)
                watchdog.cancel()
            assert not request.is_alive()
            assert not errors
            assert responses and responses[0].status_code == 200
    finally:
        service.close()


def test_profile_switch_event_failure_preserves_selection_and_closes_candidate(
        tmp_path, monkeypatch):
    from sentinel.ui import live

    service = _profile_service(tmp_path)
    prior = (service.config, service.adapters, service.readiness,
             service.pose_feed, service.artifacts)
    closed = []
    original_close = live.LiveFlight.close
    monkeypatch.setattr(live.LiveFlight, 'close',
                        lambda self: closed.append(self))
    def fail_event(*_args):
        raise RuntimeError('private database failure')
    monkeypatch.setattr(service.store, 'event_in', fail_event)
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                                   json={'profile_id': 'Second'})
            assert response.status_code == 503
            assert response.json()['status'] == 'UNAVAILABLE'
            assert response.json()['reason_code'] == 'PROFILE_SWITCH_UNAVAILABLE'
            assert 'private database failure' not in response.text
            assert (service.config, service.adapters, service.readiness,
                    service.pose_feed, service.artifacts) == prior
            assert closed and prior[1].flight not in closed
            assert service.read_events('mission', 0, 200) == []
    finally:
        monkeypatch.setattr(live.LiveFlight, 'close', original_close)
        service.close()


def test_profile_switch_ignores_replaced_resource_close_failure(tmp_path, monkeypatch):
    service = _profile_service(tmp_path)
    old_flight = service.adapters.flight
    def fail_close():
        raise RuntimeError('private close failure')
    monkeypatch.setattr(old_flight, 'close', fail_close)
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                                   json={'profile_id': 'Second'})
            assert response.status_code == 200
            assert service.config.site_profile_id == 'Second'
            assert [event['type'] for event in service.read_events('mission', 0, 200)] == [
                'SITE_PROFILE_CHANGED']
    finally:
        service.close()


def test_profile_switch_closes_pose_resource_when_composition_raises(tmp_path, monkeypatch):
    from sentinel.ui import live

    service = _profile_service(tmp_path)
    prior = service.adapters
    feeds = []
    original_feed_init = live.LivePoseFeed.__init__
    def capture_feed(self, *args):
        original_feed_init(self, *args)
        feeds.append(self)
    monkeypatch.setattr(live.LivePoseFeed, '__init__', capture_feed)
    monkeypatch.setattr(live.LivePoseFeed, 'close',
                        lambda self: setattr(self, 'was_closed', True))
    monkeypatch.setattr(live.LiveFlight, '__init__',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            RuntimeError('private constructor failure')))
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                                   json={'profile_id': 'Second'})
            assert response.status_code == 422
            assert response.json()['reason_code'] == 'SITE_PROFILE_INCOMPLETE'
            assert service.adapters is prior
            assert feeds and feeds[0].was_closed is True
            assert service.read_events('mission', 0, 200) == []
    finally:
        service.close()


def test_profile_switch_rejects_malformed_json(tmp_path):
    service = _profile_service(tmp_path)
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                content='{', headers={'content-type': 'application/json'})
            assert response.status_code == 422
            assert response.json()['reason_code'] == 'INVALID_PROFILE_REQUEST'
    finally:
        service.close()


@pytest.mark.parametrize(('content_type', 'origin'), [
    ('text/plain', 'https://untrusted.example'),
    ('text/plain', None),
    ('application/x-www-form-urlencoded', None),
    (None, None),
])
def test_profile_selection_requires_json_content_type_without_side_effects(
        tmp_path, monkeypatch, content_type, origin):
    service = _profile_service(tmp_path)
    selection_calls = []
    select_site_profile = service.select_site_profile
    monkeypatch.setattr(service, 'select_site_profile',
                        lambda command: (selection_calls.append(command),
                                         select_site_profile(command))[1])
    original = (service.config, service.adapters, service.runtime.adapters,
                service.readiness, service.pose_feed, service.artifacts)
    headers = {}
    if content_type is not None:
        headers['content-type'] = content_type
    if origin is not None:
        headers['origin'] = origin
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post('/api/v1/site-profiles/select',
                                   content='{"profile_id":"Second"}',
                                   headers=headers)
            assert response.status_code == 422
            body = response.json()
            assert body['status'] == 'INVALID'
            assert body['reason_code'] == 'INVALID_PROFILE_REQUEST'
            assert body['selected_profile_id'] == 'First'
            assert set(body) == {
                'status', 'reason_code', 'version', 'selected_profile_id',
                'profiles', 'switch_allowed', 'switch_reason'}
            assert selection_calls == []
            assert (service.config, service.adapters, service.runtime.adapters,
                    service.readiness, service.pose_feed, service.artifacts) == original
            assert service.read_events('mission', 0, 200) == []
    finally:
        service.close()


def test_profile_selection_accepts_json_content_type_parameters(tmp_path):
    service = _profile_service(tmp_path)
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            response = client.post(
                '/api/v1/site-profiles/select',
                content='{"profile_id":"Second"}',
                headers={'content-type': 'application/json; charset=utf-8'})
            assert response.status_code == 200
            assert response.json()['selected_profile_id'] == 'Second'
    finally:
        service.close()


def test_profile_routes_fail_closed_for_rehearsal_and_legacy_live(tmp_path):
    rehearsal = build_service(SessionConfig(data_dir=tmp_path / 'rehearsal'))
    legacy = build_service(SessionConfig(
        data_dir=tmp_path / 'legacy', mode='LIVE',
        site_path=Path('docs/wp5-flight1-site.json'),
        tag_map_path=Path('docs/wp5-flight3-map.json'),
        pose_db=tmp_path / 'pose.sqlite', surface_z_cm=50,
        start_x_cm=250, start_y_cm=190))
    try:
        for service in (rehearsal, legacy):
            with TestClient(build_app(service, repository_root=Path.cwd())) as client:
                catalogue = client.get('/api/v1/site-profiles').json()
                assert catalogue['switch_allowed'] is False
                assert catalogue['switch_reason'] == 'PROFILE_SWITCH_UNAVAILABLE'
                response = client.post('/api/v1/site-profiles/select',
                                       json={'profile_id': 'First'})
                assert response.status_code == 503
                assert response.json()['status'] == 'UNAVAILABLE'
                assert response.json()['reason_code'] == 'PROFILE_SWITCH_UNAVAILABLE'
                assert service.read_events('mission', 0, 200) == []
    finally:
        rehearsal.close()
        legacy.close()


def test_host_projection_and_capability_boundaries(tmp_path):
    config=SessionConfig(data_dir=tmp_path,scenario_path=Path('fixtures/mission-control/clean-computer.json'))
    service=build_service(config)
    try:
        with TestClient(build_app(service,repository_root=Path.cwd())) as client:
            assert client.get('/',follow_redirects=False).headers['location']=='/console/'
            for path in ['/console/','/console/app.mjs','/console/console.css','/assets/sentinel-ui/tokens/tokens.css']:
                assert client.get(path).status_code==200
            session=client.get('/api/v1/session').json()
            run=session['active_run_id']
            snapshot=client.get(f'/api/v1/runs/{run}/snapshot').json()
            validate_snapshot(snapshot)
            assert snapshot['source']['mode']=='REHEARSAL'
            assert snapshot['evidence']['provider'] == {
                'name': 'scripted', 'model': 'scenario', 'source_kind': 'SIMULATED'}
            assert client.get(f'/api/v1/runs/{run}/events?limit=201').status_code==422
            assert client.get('/api/v1/runs/other/snapshot').status_code==404
            assert client.get(f'/api/v1/runs/{run}/evidence/unknown').status_code==404
    finally:
        service.close()


def test_configured_live_api_projects_planning_without_hardware(tmp_path):
    service = build_service(SessionConfig(
        data_dir=tmp_path, mode='LIVE',
        site_path=Path('docs/wp5-flight1-site.json'),
        tag_map_path=Path('docs/wp5-flight3-map.json'),
        pose_db=tmp_path / 'pose.sqlite', surface_z_cm=50,
        start_x_cm=250, start_y_cm=190))
    try:
        with TestClient(build_app(service, repository_root=Path.cwd())) as client:
            snapshot = client.get('/api/v1/runs/mission/snapshot').json()
            assert snapshot['readiness']['state'] == 'READY_TO_PLAN'
            assert [check['capability'] for check in snapshot['readiness']['checks']] == [
                'planning', 'flight', 'detection', 'emergency']
            assert snapshot['mission']['route']
            assert validate_snapshot(snapshot) == snapshot
    finally:
        service.close()


def test_projection_uses_return_route_id_for_none_stop_index(tmp_path):
    config=SessionConfig(data_dir=tmp_path,scenario_path=Path('fixtures/mission-control/clean-computer.json'))
    service=build_service(config)
    try:
        run=config.run_id
        return_stop={
            'stop_id':'RETURN', 'option_id':'RETURN_OPTION', 'stop_index':None,
            'x_cm':0.0, 'y_cm':0.0, 'return_leg':True, 'status':'ACTIVE',
            'coverage':'OK', 'coverage_reason':'return leg',
            'predicted_tag_count':1, 'predicted_fix_error_cm':0.0,
            'leg_speed_cm_s':20.0, 'trim_budget':0, 'trim_progress':0,
            'arrival_error_cm':0.0, 'tolerance_cm':15.0,
            'tolerance_verdict':'INSIDE',
        }
        service.runtime.state[run]={
            'request_text':'return to start', 'phase':'FLIGHT',
            'updated_at':'2026-09-22T00:00:00+00:00',
            'stop':return_stop, 'stops':{'RETURN':return_stop},
        }
        snapshot=service.read_snapshot(run)
        assert snapshot['mission']['active_stop_id']=='RETURN'
        assert validate_snapshot(snapshot)==snapshot
    finally:
        service.close()


def test_follow_up_projection_keeps_prior_frame_routes_resolvable(tmp_path):
    config=SessionConfig(data_dir=tmp_path,scenario_path=Path('fixtures/mission-control/uncertain-followup.json'))
    service=build_service(config)
    run=config.run_id
    future=service.worker.start(dict(run_id=run,request_text='count the computers',correlation_id='corr'))
    try:
        assert service.approval.changed.wait(2)
        first=service.approval.current(run)
        service.approval.changed.clear()
        result=service.approval.decide(dict(
            session_id=service.session_id,run_id=run,proposal_id=first['id'],
            proposal_sha256=first['sha256'],expected_version=first['decision_version'],
            decision='APPROVE',command_id='first-command',correlation_id='corr',
            attempt_id='first-attempt',idempotency_key='first-key'))
        assert result['status']=='ACKNOWLEDGED'
        assert service.approval.changed.wait(5)
        follow_up=service.approval.current(run)
        assert follow_up and follow_up['id'] != first['id']
        snapshot=service.read_snapshot(run)
        route_ids={stop['stop_id'] for stop in snapshot['mission']['route']}
        assert {frame['stop_id'] for frame in snapshot['evidence']['frames']} <= route_ids
        assert {stop['id'] for stop in snapshot['proposal']['stops']} <= route_ids
        assert snapshot['mission']['sweep']['option_id']==first['candidate']['arguments']['option_id']
        follow_up_ids={stop['id'] for stop in snapshot['proposal']['stops']}
        assert all(stop['status']=='PLANNED' for stop in snapshot['mission']['route'] if stop['stop_id'] in follow_up_ids)
        assert validate_snapshot(snapshot)==snapshot
    finally:
        service.approval.cancel(run)
        future.result(timeout=5)
        service.close()
