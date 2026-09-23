"""Versioned physical site profiles for Live Mission Control."""
from __future__ import annotations

from dataclasses import dataclass, replace
import ipaddress
import json
import math
from pathlib import Path
import re
from typing import TYPE_CHECKING

from sentinel.camera import TELLO_MEASURED
from sentinel.cube.coverage import CoverageModel, check_tag_map_matches_site
from sentinel.cube.site import load_site
from sentinel.pose.tagmap import load_tag_map
from sentinel.survey.cube_bridge import envelope_from_site
from sentinel.survey.options import generate_options

from .bootstrap import StartupError

if TYPE_CHECKING:
    from .bootstrap import SessionConfig


@dataclass(frozen=True)
class SiteProfile:
    id: str
    label: str
    enabled: bool
    setup_status: str
    site_path: Path | None = None
    tag_map_path: Path | None = None
    pose_db: Path | None = None
    pose_mode: str | None = None
    surface_z_cm: float | None = None
    start_x_cm: int | None = None
    start_y_cm: int | None = None
    local_ip: str | None = None

    def public(self) -> dict:
        return {'id': self.id, 'label': self.label,
                'enabled': self.enabled, 'setup_status': self.setup_status}


@dataclass(frozen=True)
class SiteProfileRegistry:
    profiles: tuple[SiteProfile, ...]
    version: int = 1

    def require(self, profile_id: str) -> SiteProfile:
        profile = next((p for p in self.profiles if p.id == profile_id), None)
        if profile is None:
            raise StartupError('SITE_PROFILE_NOT_FOUND')
        if not profile.enabled:
            raise StartupError('SITE_PROFILE_DISABLED')
        validate_site_profile(profile)
        return profile

    def public(self, selected_id: str) -> dict:
        return {'version': self.version, 'selected_profile_id': selected_id,
                'profiles': [profile.public() for profile in self.profiles]}


def _path(base: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return (base / value).resolve()


def load_site_profiles(path: Path) -> SiteProfileRegistry:
    """Load catalogue data only; operational boundaries are never constructed."""
    path = Path(path).resolve()
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(doc, dict) or type(doc.get('version')) is not int or
                doc['version'] != 1 or
                not isinstance(doc.get('profiles'), list)):
            raise ValueError('unsupported registry')
        profiles = []
        ids = set()
        for row in doc['profiles']:
            if not isinstance(row, dict):
                raise ValueError('invalid profile')
            profile_id, label = row.get('id'), row.get('label')
            if (not isinstance(profile_id, str) or
                    not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', profile_id) or
                    profile_id in ids or not isinstance(label, str) or
                    not label.strip() or not isinstance(row.get('enabled'), bool)):
                raise ValueError('invalid identity')
            ids.add(profile_id)
            status = row.get('setup_status', 'Ready' if row['enabled'] else None)
            if not isinstance(status, str) or not status.strip():
                raise ValueError('missing setup status')
            profiles.append(SiteProfile(
                id=profile_id, label=label, enabled=row['enabled'],
                setup_status=status,
                site_path=_path(path.parent, row.get('site_path')),
                tag_map_path=_path(path.parent, row.get('tag_map_path')),
                pose_db=_path(path.parent, row.get('pose_db')),
                pose_mode=row.get('pose_mode'),
                surface_z_cm=row.get('surface_z_cm'),
                start_x_cm=row.get('start_x_cm'),
                start_y_cm=row.get('start_y_cm'),
                local_ip=row.get('local_ip')))
    except (OSError, ValueError, TypeError) as exc:
        raise StartupError('SITE_PROFILE_INCOMPLETE') from exc
    return SiteProfileRegistry(tuple(profiles))


def validate_site_profile(profile: SiteProfile) -> None:
    """Refuse incomplete or geometrically invalid physical configurations."""
    required_paths = (profile.site_path, profile.tag_map_path, profile.pose_db)
    if (any(path is None for path in required_paths) or
            not isinstance(profile.pose_mode, str) or
            profile.pose_mode not in {'off', 'passive', 'reanchor'} or
            not _valid_surface_height(profile.surface_z_cm) or
            (profile.start_x_cm is None) != (profile.start_y_cm is None) or
            (profile.pose_mode == 'off' and profile.start_x_cm is None) or
            any(value is not None and (not isinstance(value, int) or
                                      isinstance(value, bool))
                for value in (profile.start_x_cm, profile.start_y_cm))):
        raise StartupError('SITE_PROFILE_INCOMPLETE')
    if profile.local_ip is not None:
        if not isinstance(profile.local_ip, str) or not profile.local_ip.strip():
            raise StartupError('SITE_PROFILE_INCOMPLETE')
        try:
            address = ipaddress.ip_address(profile.local_ip)
            if address.version != 4 or address.is_unspecified or address.is_loopback:
                raise ValueError('invalid local IPv4')
        except (ValueError, TypeError) as exc:
            raise StartupError('SITE_PROFILE_INCOMPLETE') from exc
    try:
        site = load_site(profile.site_path)
        tag_map = load_tag_map(str(profile.tag_map_path))
        check_tag_map_matches_site(tag_map, site, tolerance_cm=5.0)
        CoverageModel(site, tag_map.doc['camera_calibration'])
        envelope = envelope_from_site(site, 300)
        options = generate_options(envelope, TELLO_MEASURED)
        if not options:
            raise ValueError('no survey options')
        if profile.start_x_cm is not None and not (
                envelope.min_x_cm <= profile.start_x_cm <= envelope.max_x_cm and
                envelope.min_y_cm <= profile.start_y_cm <= envelope.max_y_cm):
            raise ValueError('start outside envelope')
        if profile.surface_z_cm >= envelope.min_z_cm:
            raise ValueError('surface must be below survey')
    except Exception as exc:
        raise StartupError('INVALID_LIVE_SITE_OR_COVERAGE') from exc


def _valid_surface_height(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except (OverflowError, ValueError):
        return False


def apply_site_profile(config: SessionConfig, profile: SiteProfile) -> SessionConfig:
    return replace(config, site_path=profile.site_path,
                   tag_map_path=profile.tag_map_path, pose_db=profile.pose_db,
                   pose_mode=profile.pose_mode, surface_z_cm=profile.surface_z_cm,
                   start_x_cm=profile.start_x_cm, start_y_cm=profile.start_y_cm,
                   local_ip=profile.local_ip if profile.local_ip is not None
                   else config.local_ip)
