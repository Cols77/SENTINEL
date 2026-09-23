"""Public models and reference integrity checks."""
from dataclasses import dataclass
import json
from jsonschema import Draft202012Validator
from .schema import SCHEMA

@dataclass(frozen=True)
class EvidenceCapability:
    status: str
    run_id: str
    artifact_id: str
    media_type: str | None = None
    payload: bytes | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    reason_code: str | None = None


def validate_snapshot(value):
    try:
        json.dumps(value, allow_nan=False)
        Draft202012Validator(SCHEMA).validate(value)
        run_id = value['run']['id']
        for row in [value['report'], *value['chat']['messages']]:
            if row['run_id'] != run_id:
                raise ValueError('cross-run reference')
        for row in [value['proposal'], value['localization']['latest_report'], value['localization']['pose_evidence']]:
            if row and row['run_id'] != run_id:
                raise ValueError('cross-run reference')
        if value['source']['mode'] != value['session']['mode']:
            raise ValueError('source mode mismatch')
        readiness = value.get('readiness')
        if readiness is not None:
            expected = ('planning','flight','detection','emergency')
            checks = readiness['checks']
            if tuple(row['capability'] for row in checks) != expected:
                raise ValueError('readiness checks must be ordered and unique')
            for row in checks:
                if row['ready'] != (row['reason_code'] is None and row['remediation'] is None):
                    raise ValueError('readiness reason/remediation mismatch')
            ready = {row['capability']: row['ready'] for row in checks}
            expected_state = ('NOT_READY' if not ready['planning'] else
                              'READY_TO_DISPATCH' if all(ready.values()) else
                              'READY_TO_PLAN')
            if readiness['state'] == 'ACTIVE':
                if not all(ready.values()):
                    raise ValueError('active readiness has unavailable capability')
            elif readiness['state'] != expected_state:
                raise ValueError('readiness state/check mismatch')
        evidence = value['evidence']
        ids = {}
        for key in ('frames','sightings','objects'):
            ids[key] = {r['id'] for r in evidence[key]}
            if len(ids[key]) != len(evidence[key]):
                raise ValueError('duplicate identity')
        for row in evidence['sightings']:
            if row['frame_id'] not in ids['frames']:
                raise ValueError('unresolved frame')
        for row in [*evidence['objects'], *value['results']['counts']]:
            if not set(row['sighting_ids']) <= ids['sightings']:
                raise ValueError('unresolved sighting')
        for frame in evidence['frames']:
            if frame['evidence'] == 'AVAILABLE' and (not frame['artifact_id'] or not frame['sha256'] or not frame['width'] or not frame['height']):
                raise ValueError('unbound evidence')
        route = value['mission']['route']
        route_ids = {row['stop_id'] for row in route}
        if len(route_ids) != len(route):
            raise ValueError('duplicate stop identity')
        if value['mission']['active_stop_id'] and value['mission']['active_stop_id'] not in route_ids:
            raise ValueError('unresolved active stop')
        for frame in evidence['frames']:
            if frame['stop_id'] not in route_ids:
                raise ValueError('unresolved stop')
        if value['proposal']:
            proposal_ids = {row['id'] for row in value['proposal']['stops']}
            if len(proposal_ids) != len(value['proposal']['stops']):
                raise ValueError('duplicate proposal stop identity')
            if not proposal_ids <= route_ids:
                raise ValueError('unresolved proposal stop')
        return value
    except Exception as exc:
        raise ValueError('INVALID_PROJECTION') from exc


def empty_snapshot(session_id, run_id, mode):
    return dict(schema_version=3,
        session=dict(id=session_id,mode=mode,startup_summary=dict(site_id=None,pose_mode='off',detector_name='scripted')),
        run=dict(id=run_id,request_text='',status='ACTIVE',phase='REQUEST',version=0,decision_version=0,updated_at='1970-01-01T00:00:00+00:00'),
        source=dict(mode=mode,kind='SIMULATED' if mode=='REHEARSAL' else 'LIVE',freshness='FRESH'),
        safety={key:dict(available=False,status='UNAVAILABLE',reason='No active runner',command_id=None) for key in ('land','emergency')},
        chat=dict(messages=[],pending_command_id=None),proposal=None,
        decision=dict(mode='F',source='RULES',escalation_reason=None),
        mission=dict(flights=dict(used=0,max=2),sweep=None,envelope=None,route=[],active_stop_id=None,commanded_position=None,return_to_start=False,return_error_cm=None),
        localization=dict(status='UNAVAILABLE',age_ms=None,stale_after_ms=2000,latest_report=None,recent_stream=dict(frames_seen=0,reports_emitted=0,no_tag_frames=0,gated_frames=0,timeouts=0),containment='UNAVAILABLE',vertical_consistency=dict(status='UNAVAILABLE',measured_m=None,expected_m=None,error_m=None,reason='No pose observation'),pose_evidence=None,map_position=None),
        evidence=dict(frames=[],sightings=[],objects=[],analysis=dict(analysed=0,total=0,dropped_labels=[],unknown_labels=[]),provider=dict(name='scripted',model='scenario',source_kind='SIMULATED'),failures=[]),
        results=dict(counts=[]),report=dict(status='ABSENT',text=None,reason=None,run_id=run_id,id=None),events=dict(watermark=0),legal_actions=[])
