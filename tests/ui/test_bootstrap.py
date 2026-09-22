from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import pytest
from sentinel.ui import bootstrap
from sentinel.ui.models import validate_snapshot
from sentinel.survey.store import SurveyStore
from sentinel.survey.consent import record_consent

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
        site_path=Path('docs/wp5-flight1-site.json'),tag_map_path=Path('calibration/tag-map-calibrated.json'),
        pose_db=tmp_path/'pose.sqlite',detector_name='gemini',provider_profile=profile_path,
        authorise_paid_calls=True,
        location='lab',consent_date='2026-09-21',local_ip='192.168.10.2',surface_z_cm=50,start_x_cm=250,start_y_cm=190)

@pytest.mark.parametrize('field,value', [('confirm',False),('site_path',None),('tag_map_path',None),('pose_db',None),('location',None),('consent_date',None),('detector_name','replay'),('provider_profile',None),('local_ip',None),('surface_z_cm',None)])
def test_live_failure_never_constructs_rehearsal(live_config,monkeypatch,field,value):
    calls=[]
    monkeypatch.setattr(bootstrap,'build_rehearsal_adapters',lambda *a:calls.append(a))
    with pytest.raises(bootstrap.StartupError):bootstrap.build_service(replace(live_config,**{field:value}))
    assert calls==[]

def test_live_requires_explicit_paid_call_authorisation_before_preflight(live_config, monkeypatch):
    from sentinel.ui import live
    monkeypatch.setattr(live, 'make_client', lambda c: pytest.fail('must refuse before preflight'))
    with pytest.raises(bootstrap.StartupError, match='PAID_CALL_AUTH'):
        bootstrap.build_service(replace(live_config, authorise_paid_calls=False))

def test_live_composition_no_flight_and_restart_requires_recovery(live_config,monkeypatch):
    from sentinel.ui import live
    from sentinel.dry_run import DryRunClient
    clients=[]
    def factory(config):
        client=DryRunClient();clients.append(client);return client
    monkeypatch.setattr(live,'make_client',factory)
    monkeypatch.setattr(live,'check_emergency_transport',lambda c:lambda:9)
    service=bootstrap.build_service(live_config)
    try:
        snapshot=service.read_snapshot('mission');validate_snapshot(snapshot)
        assert snapshot['run']['status']=='ACTIVE'
        assert snapshot['source']['kind']=='LIVE'
        assert 'test-secret' not in json.dumps(snapshot)
        assert service.adapters.flight.__class__.__name__=='LiveFlight'
        assert service.safety.sender is not None
        assert not service.worker.active
        assert all('takeoff' not in c.commands for c in clients)
    finally:service.close()
    service=bootstrap.build_service(live_config)
    try:assert service.read_snapshot('mission')['run']['status']=='RECOVERY_REQUIRED'
    finally:service.close()
    with pytest.raises(FrozenInstanceError):live_config.mode='REHEARSAL'

def test_preflight_failure_no_fallback(live_config,monkeypatch):
    from sentinel.ui import live
    from sentinel.dry_run import DryRunClient
    def low_battery(c):
        client=DryRunClient();client.battery=lambda:1;return client
    monkeypatch.setattr(live,'make_client',low_battery)
    monkeypatch.setattr(live,'check_emergency_transport',lambda c:lambda:9)
    with pytest.raises(bootstrap.StartupError,match='PREFLIGHT'):bootstrap.build_service(live_config)


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


def test_invalid_profile_and_consent_refuse_before_client(live_config,monkeypatch):
    from sentinel.ui import live
    monkeypatch.setattr(live,'make_client',lambda c:pytest.fail('must refuse before connecting'))
    with pytest.raises(bootstrap.StartupError,match='CONSENT'):
        bootstrap.build_service(replace(live_config,location='other'))
    profile=json.loads(live_config.provider_profile.read_text());profile['approval_status']='UNAPPROVED'
    live_config.provider_profile.write_text(json.dumps(profile))
    with pytest.raises(bootstrap.StartupError,match='PROFILE'):bootstrap.build_service(live_config)


def test_independent_transport_failure_refuses_startup(live_config,monkeypatch):
    from sentinel.ui import live
    monkeypatch.setattr(live,'make_client',lambda c:pytest.fail('must refuse before connecting'))
    def fail(c):raise OSError('interface missing')
    monkeypatch.setattr(live,'check_emergency_transport',fail)
    with pytest.raises(bootstrap.StartupError,match='EMERGENCY'):bootstrap.build_service(live_config)


def test_rehearsal_restart_cannot_acknowledge_an_orphaned_offer(tmp_path):
    config=bootstrap.SessionConfig(data_dir=tmp_path)
    service=bootstrap.build_service(config)
    service.close()
    restarted=bootstrap.build_service(config)
    try:
        assert restarted.read_snapshot('mission')['run']['status']=='ABORTED'
        assert restarted.approval.current('mission') is None
    finally:restarted.close()
