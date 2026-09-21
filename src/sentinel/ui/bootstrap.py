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


def build_service(config):
    from .service import MissionControlService
    if config.mode != 'REHEARSAL':
        raise StartupError('LIVE_ADAPTERS_NOT_CONFIGURED')
    adapters=build_rehearsal_adapters(config.scenario_path,config.data_dir/'images')
    return MissionControlService(config=config,adapters=adapters)
