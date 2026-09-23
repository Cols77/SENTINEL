"""Immutable startup configuration and adapter composition."""
from dataclasses import dataclass
from pathlib import Path
from .scenarios import build_rehearsal_adapters

class StartupError(ValueError):
    pass

@dataclass(frozen=True)
class SessionConfig:
    data_dir: Path = Path('artifacts/mission-control')
    scenario_path: Path = Path('fixtures/mission-control/clean-computer.json')
    mode: str = 'REHEARSAL'
    host: str = '127.0.0.1'
    port: int = 8771
    run_id: str = 'mission'
    detector_name: str = 'scripted'
    site_path: Path | None = None
    tag_map_path: Path | None = None
    pose_db: Path | None = None
    pose_mode: str = 'off'
    location: str | None = None
    consent_date: str | None = None
    confirm: bool = False
    authorise_paid_calls: bool = False
    artifact_root: Path | None = None
    ui_db: Path | None = None
    survey_db: Path | None = None
    provider_profile: Path | None = None
    local_ip: str | None = None
    tello_ip: str = '192.168.10.1'
    surface_z_cm: float | None = None
    start_x_cm: int | None = None
    start_y_cm: int | None = None
    sr063_amended: bool = False
    site_profiles_path: Path | None = None
    site_profile_id: str = 'CanningSt'


def validate_live_startup(config):
    from .readiness import evaluate_live_readiness
    return evaluate_live_readiness(config)


def build_live_adapters(config, readiness=None):
    from .live import compose
    return compose(config, readiness=readiness)


def build_service(config):
    from .service import MissionControlService
    if config.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise StartupError('LOCALHOST_REQUIRED')
    if config.mode == 'REHEARSAL':
        adapters=build_rehearsal_adapters(config.scenario_path,config.artifact_root or config.data_dir/'images')
        readiness=None
    elif config.mode == 'LIVE':
        from .site_profiles import (SiteProfile, apply_site_profile,
                                    load_site_profiles, validate_site_profile)
        if config.site_profiles_path is not None:
            profile = load_site_profiles(config.site_profiles_path).require(
                config.site_profile_id)
            config = apply_site_profile(config, profile)
        else:
            validate_site_profile(SiteProfile(
                id='Legacy', label='Legacy', enabled=True,
                setup_status='Ready', site_path=config.site_path,
                tag_map_path=config.tag_map_path, pose_db=config.pose_db,
                pose_mode=config.pose_mode, surface_z_cm=config.surface_z_cm,
                start_x_cm=config.start_x_cm, start_y_cm=config.start_y_cm,
                local_ip=config.local_ip))
        readiness=validate_live_startup(config)
        adapters=build_live_adapters(config,readiness)
    else:
        raise StartupError('INVALID_MODE')
    return MissionControlService(config=config,adapters=adapters,readiness=readiness)
