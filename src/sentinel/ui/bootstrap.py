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


def validate_live_startup(config):
    from .live import validate_config
    validate_config(config)


def build_live_adapters(config):
    from .live import compose
    return compose(config)


def build_service(config):
    from .service import MissionControlService
    if config.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise StartupError('LOCALHOST_REQUIRED')
    if config.mode == 'REHEARSAL':
        adapters=build_rehearsal_adapters(config.scenario_path,config.artifact_root or config.data_dir/'images')
    elif config.mode == 'LIVE':
        validate_live_startup(config)
        adapters=build_live_adapters(config)
    else:
        raise StartupError('INVALID_MODE')
    return MissionControlService(config=config,adapters=adapters)
