from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import shutil

import pytest

from sentinel.ui.bootstrap import SessionConfig, StartupError, build_service


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / 'config/mission-control-sites.v1.json'


def _registry(tmp_path, profiles):
    shutil.copyfile(ROOT / 'docs/wp5-flight1-site.json', tmp_path / 'site.json')
    shutil.copyfile(ROOT / 'docs/wp5-flight3-map.json', tmp_path / 'tags.json')
    path = tmp_path / 'sites.json'
    path.write_text(json.dumps({'version': 1, 'profiles': profiles}))
    return path


def _enabled(profile_id='Lab'):
    return dict(id=profile_id, label='Lab setup', enabled=True,
                setup_status='Ready', site_path='site.json',
                tag_map_path='tags.json', pose_db='pose.sqlite',
                pose_mode='off', surface_z_cm=50,
                start_x_cm=250, start_y_cm=190)


def test_default_registry_resolves_canning_st_and_lists_disabled_demo():
    from sentinel.ui.site_profiles import load_site_profiles

    registry = load_site_profiles(DEFAULT_REGISTRY)
    canning = registry.require('CanningSt')
    assert canning.site_path == ROOT / 'docs/wp5-flight1-site.json'
    assert canning.tag_map_path == ROOT / 'docs/wp5-flight3-map.json'
    assert canning.pose_db == ROOT / 'artifacts/canning-st/pose.sqlite'
    assert (canning.pose_mode, canning.surface_z_cm,
            canning.start_x_cm, canning.start_y_cm) == ('off', 50, 250, 190)
    assert registry.public('CanningSt') == {
        'version': 1,
        'selected_profile_id': 'CanningSt',
        'profiles': [
            {'id': 'CanningSt', 'label': 'Canning St', 'enabled': True,
             'setup_status': 'Ready'},
            {'id': 'Demo', 'label': 'Demo', 'enabled': False,
             'setup_status': 'Venue calibration required'},
        ],
    }
    assert 'site.json' not in json.dumps(registry.public('CanningSt'))
    with pytest.raises(StartupError, match='^SITE_PROFILE_DISABLED$'):
        registry.require('Demo')
    with pytest.raises(FrozenInstanceError):
        canning.label = 'changed'


def test_registry_resolves_relative_paths_and_accepts_new_entries(tmp_path):
    from sentinel.ui.site_profiles import apply_site_profile, load_site_profiles

    path = _registry(tmp_path, [_enabled('First'), _enabled('Second')])
    registry = load_site_profiles(path)
    second = registry.require('Second')
    assert second.site_path == tmp_path / 'site.json'
    assert second.tag_map_path == tmp_path / 'tags.json'
    assert second.pose_db == tmp_path / 'pose.sqlite'
    config = apply_site_profile(SessionConfig(mode='LIVE'), second)
    assert config.site_path == second.site_path
    assert config.pose_db == second.pose_db
    assert config.start_x_cm == 250
    assert [row['id'] for row in registry.public('Second')['profiles']] == [
        'First', 'Second']


@pytest.mark.parametrize('profile_id,profiles,code', [
    ('Absent', [_enabled()], 'SITE_PROFILE_NOT_FOUND'),
    ('Draft', [{'id': 'Draft', 'label': 'Draft', 'enabled': False,
                'setup_status': 'Venue calibration required'}],
     'SITE_PROFILE_DISABLED'),
    ('Lab', [dict(_enabled(), start_y_cm=None)],
     'SITE_PROFILE_INCOMPLETE'),
])
def test_selected_missing_disabled_or_incomplete_profile_refuses_startup(
        tmp_path, monkeypatch, profile_id, profiles, code):
    from sentinel.ui import live

    path = _registry(tmp_path, profiles)
    monkeypatch.setattr(live, 'compose', lambda *_a, **_k: pytest.fail(
        'adapters constructed before site gate'))
    with pytest.raises(StartupError, match=f'^{code}$'):
        build_service(SessionConfig(mode='LIVE', data_dir=tmp_path / 'data',
                                    site_profiles_path=path,
                                    site_profile_id=profile_id))
    assert not (tmp_path / 'data').exists()


def test_mismatched_tag_map_refuses_before_adapter_construction(tmp_path, monkeypatch):
    from sentinel.ui import live

    path = _registry(tmp_path, [_enabled()])
    doc = json.loads((tmp_path / 'tags.json').read_text())
    doc['tags'][0]['x_units'] += 100
    (tmp_path / 'tags.json').write_text(json.dumps(doc))
    monkeypatch.setattr(live, 'compose', lambda *_a, **_k: pytest.fail(
        'adapters constructed before geometry gate'))
    with pytest.raises(StartupError,
                       match='^INVALID_LIVE_SITE_OR_COVERAGE$'):
        build_service(SessionConfig(mode='LIVE', data_dir=tmp_path / 'data',
                                    site_profiles_path=path,
                                    site_profile_id='Lab'))


def test_nonfinite_or_unrepresentable_surface_uses_allowlisted_error(tmp_path):
    from sentinel.ui.site_profiles import load_site_profiles

    path = _registry(tmp_path, [dict(_enabled(), surface_z_cm=10 ** 1000)])
    with pytest.raises(StartupError, match='^SITE_PROFILE_INCOMPLETE$'):
        load_site_profiles(path).require('Lab')


@pytest.mark.parametrize('pose_mode', [[], {}])
def test_collection_pose_mode_uses_allowlisted_error(tmp_path, pose_mode):
    from sentinel.ui.site_profiles import load_site_profiles

    path = _registry(tmp_path, [dict(_enabled(), pose_mode=pose_mode)])
    with pytest.raises(StartupError, match='^SITE_PROFILE_INCOMPLETE$'):
        load_site_profiles(path).require('Lab')


def test_integer_local_ip_uses_allowlisted_error(tmp_path):
    from sentinel.ui.site_profiles import load_site_profiles

    path = _registry(tmp_path, [dict(_enabled(), local_ip=3232238082)])
    with pytest.raises(StartupError, match='^SITE_PROFILE_INCOMPLETE$'):
        load_site_profiles(path).require('Lab')


@pytest.mark.parametrize('version', [True, 1.0])
def test_registry_rejects_noninteger_version(tmp_path, version):
    from sentinel.ui.site_profiles import load_site_profiles

    path = _registry(tmp_path, [_enabled()])
    doc = json.loads(path.read_text())
    doc['version'] = version
    path.write_text(json.dumps(doc))
    with pytest.raises(StartupError, match='^SITE_PROFILE_INCOMPLETE$'):
        load_site_profiles(path)


def test_registry_loading_and_startup_gate_do_not_construct_operational_boundaries(
        tmp_path, monkeypatch):
    from sentinel.ui import live
    from sentinel.ui.site_profiles import load_site_profiles

    path = _registry(tmp_path, [_enabled()])
    calls = []
    monkeypatch.setattr(live, 'make_client', lambda *_a: calls.append('drone'))
    monkeypatch.setattr(live, 'check_emergency_transport',
                        lambda *_a: calls.append('emergency'))
    monkeypatch.setattr(live.LiveFlight, '_new_supervisor',
                        lambda *_a: calls.append('provider'))
    registry = load_site_profiles(path)
    registry.require('Lab')
    service = build_service(SessionConfig(mode='LIVE',
        data_dir=tmp_path / 'data', site_profiles_path=path,
        site_profile_id='Lab'))
    try:
        assert service.adapters.flight.coverage is not None
        assert calls == []
    finally:
        service.close()


def test_live_cli_defaults_to_registry_and_applies_canning_st(tmp_path):
    from sentinel.ui.__main__ import parse_config

    config = parse_config(['--mode', 'live', '--data-dir', str(tmp_path)])
    assert config.site_profiles_path == DEFAULT_REGISTRY
    assert config.site_profile_id == 'CanningSt'
    service = build_service(config)
    try:
        assert service.config.site_path == ROOT / 'docs/wp5-flight1-site.json'
        assert service.config.pose_db == ROOT / 'artifacts/canning-st/pose.sqlite'
        assert service.config.site_profile_id == 'CanningSt'
        assert service.readiness.checks[0].ready is True
    finally:
        service.close()
    assert parse_config(['--mode', 'rehearsal']).site_profiles_path is None
    custom = parse_config(['--mode', 'live', '--site-profiles',
                           str(tmp_path / 'custom.json'), '--site-profile', 'Venue2'])
    assert custom.site_profiles_path == tmp_path / 'custom.json'
    assert custom.site_profile_id == 'Venue2'


def test_registry_selection_overrides_legacy_physical_fields(tmp_path):
    path = _registry(tmp_path, [_enabled()])
    service = build_service(SessionConfig(
        mode='LIVE', data_dir=tmp_path / 'data', site_profiles_path=path,
        site_profile_id='Lab', site_path=Path('wrong-site.json'),
        tag_map_path=Path('wrong-map.json'), pose_db=Path('wrong-pose.sqlite'),
        surface_z_cm=None))
    try:
        assert service.config.site_path == tmp_path / 'site.json'
        assert service.config.tag_map_path == tmp_path / 'tags.json'
        assert service.config.pose_db == tmp_path / 'pose.sqlite'
    finally:
        service.close()


def test_bare_live_config_refuses_before_service_or_adapter_creation(
        tmp_path, monkeypatch):
    from sentinel.ui import live

    monkeypatch.setattr(live, 'compose', lambda *_a, **_k: pytest.fail(
        'adapters constructed before site gate'))
    with pytest.raises(StartupError, match='^SITE_PROFILE_INCOMPLETE$'):
        build_service(SessionConfig(mode='LIVE', data_dir=tmp_path / 'data'))
    assert not (tmp_path / 'data').exists()
