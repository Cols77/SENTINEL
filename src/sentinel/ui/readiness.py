"""Pure, allowlisted readiness evaluation for an inert Live host."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import ipaddress
import json
import math
import os
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from jsonschema import validate

from sentinel.camera import TELLO_MEASURED
from sentinel.cube.coverage import CoverageModel, check_tag_map_matches_site
from sentinel.cube.site import load_site
from sentinel.pose.tagmap import load_tag_map
from sentinel.survey.consent import consent_policy
from sentinel.survey.cube_bridge import envelope_from_site
from sentinel.survey.options import generate_options
from sentinel.survey.store import SurveyStore

if TYPE_CHECKING:
    from .bootstrap import SessionConfig


Capability = Literal['planning', 'flight', 'detection', 'emergency']
ReadinessState = Literal['NOT_READY', 'READY_TO_PLAN',
                         'READY_TO_DISPATCH', 'ACTIVE']


@dataclass(frozen=True)
class ReadinessCheck:
    capability: Capability
    ready: bool
    reason_code: str | None
    remediation: str | None

    def public(self) -> dict:
        return {
            'capability': self.capability,
            'ready': self.ready,
            'reason_code': self.reason_code,
            'remediation': self.remediation,
        }


@dataclass(frozen=True)
class LiveReadiness:
    state: ReadinessState
    checks: tuple[ReadinessCheck, ...]
    command_template: str

    def public(self) -> dict:
        return {
            'state': self.state,
            'checks': [check.public() for check in self.checks],
            'command_template': self.command_template,
        }


COMMAND_TEMPLATE = (
    'Set GEMINI_API_KEY and GEMINI_MODEL, then run: sentinel mission-control '
    '--mode live --site <SITE_JSON> --tag-map <TAG_MAP_JSON> '
    '--pose-db <POSE_DB> --pose off --start-x-cm <X_CM> '
    '--start-y-cm <Y_CM> --surface-z-cm <Z_CM> --detector gemini '
    '--provider-profile <APPROVED_PROFILE_JSON> --survey-db <SURVEY_DB> '
    '--location <LOCATION> --consent-date <YYYY-MM-DD> '
    '--local-ip <LOCAL_IP> --confirm --i-authorise-paid-calls'
)


def _check(capability: Capability, ready: bool, reason_code: str,
           remediation: str) -> ReadinessCheck:
    return ReadinessCheck(capability, ready,
                          None if ready else reason_code,
                          None if ready else remediation)


def _planning(config: SessionConfig) -> ReadinessCheck:
    required = ('site_path', 'tag_map_path', 'pose_db', 'surface_z_cm')
    coordinates_missing = (config.start_x_cm is None or
                           config.start_y_cm is None)
    if any(getattr(config, name) is None for name in required) or (
            config.pose_mode == 'off' and coordinates_missing):
        return _check(
            'planning', False, 'LIVE_PLANNING_CONFIG_REQUIRED',
            'Supply --site, --tag-map, --pose-db, --surface-z-cm and, '
            'for --pose off, both --start-x-cm and --start-y-cm.')
    if config.pose_mode not in {'off', 'passive', 'reanchor'}:
        return _check('planning', False, 'INVALID_POSE_MODE',
                      'Use --pose off, --pose passive, or --pose reanchor.')
    if config.pose_mode == 'reanchor' and not config.sr063_amended:
        return _check('planning', False, 'SR063_AMENDMENT_REQUIRED',
                      'Use --sr063-amended only after the amendment is approved.')
    if (config.start_x_cm is None) != (config.start_y_cm is None):
        return _check('planning', False, 'BOTH_START_COORDINATES_REQUIRED',
                      'Supply both --start-x-cm and --start-y-cm.')
    try:
        if (not math.isfinite(config.surface_z_cm) or
                config.surface_z_cm < 0):
            raise ValueError('invalid surface')
        site = load_site(config.site_path)
        tag_map = load_tag_map(str(config.tag_map_path))
        check_tag_map_matches_site(tag_map, site, tolerance_cm=5.0)
        CoverageModel(site, tag_map.doc['camera_calibration'])
        envelope = envelope_from_site(site, 300)
        generate_options(envelope, TELLO_MEASURED)
        if config.start_x_cm is not None and not (
                envelope.min_x_cm <= config.start_x_cm <= envelope.max_x_cm and
                envelope.min_y_cm <= config.start_y_cm <= envelope.max_y_cm):
            raise ValueError('start outside envelope')
        if config.surface_z_cm >= envelope.min_z_cm:
            raise ValueError('surface must be below survey')
    except Exception:
        return _check(
            'planning', False, 'INVALID_LIVE_SITE_OR_COVERAGE',
            'Check --site, --tag-map, --surface-z-cm, --start-x-cm and '
            '--start-y-cm against the measured room configuration.')
    return _check('planning', True, '', '')


def _network_ready(config: SessionConfig) -> bool:
    try:
        local = ipaddress.ip_address(config.local_ip)
        tello = ipaddress.ip_address(config.tello_ip)
        return (local.version == tello.version == 4 and
                not local.is_unspecified and not local.is_loopback)
    except (TypeError, ValueError):
        return False


def _flight(config: SessionConfig) -> ReadinessCheck:
    if not config.confirm:
        return _check('flight', False, 'LIVE_CONFIRM_REQUIRED',
                      'Restart with --confirm before dispatching a real flight.')
    if not _network_ready(config):
        return _check('flight', False, 'LIVE_NETWORK_CONFIG_REQUIRED',
                      'Supply a non-loopback IPv4 address with --local-ip.')
    return _check('flight', True, '', '')


def _detection(config: SessionConfig) -> ReadinessCheck:
    if config.detector_name not in {'gemini', 'moondream'}:
        return _check(
            'detection', False, 'LIVE_PROVIDER_REQUIRED',
            'Select --detector gemini or --detector moondream; set '
            'GEMINI_API_KEY and GEMINI_MODEL, or MOONDREAM_API_KEY and '
            'MOONDREAM_MODEL.')
    prefix = config.detector_name.upper()
    if not os.environ.get(prefix + '_API_KEY') or not os.environ.get(prefix + '_MODEL'):
        return _check(
            'detection', False, 'LIVE_PROVIDER_ENV_REQUIRED',
            f'Set {prefix}_API_KEY and {prefix}_MODEL; secret values are never displayed.')
    if not config.authorise_paid_calls:
        return _check(
            'detection', False, 'LIVE_PAID_CALL_AUTH_REQUIRED',
            'Restart with --i-authorise-paid-calls after reviewing provider cost.')
    if config.provider_profile is None:
        return _check(
            'detection', False, 'APPROVED_PROVIDER_PROFILE_REQUIRED',
            'Supply --provider-profile with an approved provider-profile.v1 document.')
    if not config.location or not config.consent_date:
        return _check(
            'detection', False, 'LIVE_CONSENT_CONFIG_REQUIRED',
            'Supply --location and --consent-date for previously recorded consent.')
    try:
        date.fromisoformat(config.consent_date)
        profile = json.loads(config.provider_profile.read_text())
        root = Path(__file__).resolve().parents[3]
        schema = json.loads((root / 'docs/superpowers/specs/contracts/'
                             'provider-profile-v1.schema.json').read_text())
        validate(profile, schema)
        if (profile['approval_status'] != 'APPROVED' or
                profile['provider'] != config.detector_name or
                profile['model'] != os.environ[prefix + '_MODEL']):
            raise ValueError('profile mismatch')
    except Exception:
        return _check(
            'detection', False, 'APPROVED_PROVIDER_PROFILE_REQUIRED',
            'Check --provider-profile against the selected provider and '
            f'{prefix}_MODEL.')
    survey_db = config.survey_db or config.data_dir / 'survey.sqlite'
    if not survey_db.is_file():
        return _check(
            'detection', False, 'CONSENT_DATABASE_REQUIRED',
            'Supply --survey-db containing recorded consent for the location and date.')
    store = SurveyStore.open(survey_db)
    try:
        consent_policy(store, config.location, config.consent_date)
    except Exception:
        return _check(
            'detection', False, 'RECORDED_CONSENT_REQUIRED',
            'Record consent for the exact --location and --consent-date before dispatch.')
    finally:
        store.connection.close()
    return _check('detection', True, '', '')


def _emergency(config: SessionConfig) -> ReadinessCheck:
    return _check(
        'emergency', _network_ready(config), 'LIVE_NETWORK_CONFIG_REQUIRED',
        'Supply a non-loopback IPv4 address with --local-ip; transport is '
        'initialized only at dispatch.')


def evaluate_live_readiness(config: SessionConfig) -> LiveReadiness:
    """Return safe readiness facts without constructing operational adapters."""
    checks = (_planning(config), _flight(config), _detection(config),
              _emergency(config))
    planning_ready = checks[0].ready
    state: ReadinessState = (
        'READY_TO_DISPATCH' if all(check.ready for check in checks) else
        'READY_TO_PLAN' if planning_ready else
        'NOT_READY')
    return LiveReadiness(state, checks, COMMAND_TEMPLATE)
