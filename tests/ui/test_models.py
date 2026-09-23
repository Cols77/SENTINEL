from copy import deepcopy
import pytest
from sentinel.ui.models import empty_snapshot, validate_snapshot


def test_live_readiness_public_shape_contains_only_allowlisted_fields():
    from sentinel.ui.bootstrap import SessionConfig
    from sentinel.ui.readiness import evaluate_live_readiness

    public = evaluate_live_readiness(SessionConfig(mode='LIVE')).public()
    assert set(public) == {'state', 'checks', 'command_template'}
    assert all(set(check) == {'capability', 'ready', 'reason_code', 'remediation'}
               for check in public['checks'])
    assert [check['capability'] for check in public['checks']] == [
        'planning', 'flight', 'detection', 'emergency']


def readiness_document(state='NOT_READY'):
    ready_capabilities = (
        set() if state == 'NOT_READY' else
        {'planning'} if state == 'READY_TO_PLAN' else
        {'planning', 'flight', 'detection', 'emergency'})
    return {
        'state': state,
        'checks': [
            {'capability': capability, 'ready': capability in ready_capabilities,
             'reason_code': (None if capability in ready_capabilities else
                             f'LIVE_{capability.upper()}_REQUIRED'),
             'remediation': (None if capability in ready_capabilities else
                             f'Supply --{capability}.')}
            for capability in ('planning', 'flight', 'detection', 'emergency')
        ],
        'command_template': 'sentinel mission-control --mode live --site <SITE_JSON>',
    }


@pytest.mark.parametrize('state',
                         ['NOT_READY', 'READY_TO_PLAN', 'READY_TO_DISPATCH', 'ACTIVE'])
def test_schema_v3_accepts_closed_readiness_states_and_legacy_omission(state):
    snapshot = empty_snapshot('s', 'r', 'LIVE')
    snapshot['readiness'] = readiness_document(state)
    assert validate_snapshot(snapshot) == snapshot
    del snapshot['readiness']
    assert validate_snapshot(snapshot) == snapshot


@pytest.mark.parametrize('mutate', [
    lambda readiness: readiness['checks'].reverse(),
    lambda readiness: readiness['checks'].__setitem__(
        1, dict(readiness['checks'][0])),
    lambda readiness: readiness['checks'][0].update(
        ready=True, reason_code='STILL_BLOCKED', remediation=None),
    lambda readiness: readiness.update(command_template=7),
    lambda readiness: readiness.update(secret='private'),
])
def test_readiness_rejects_malformed_order_pairing_and_cross_types(mutate):
    snapshot = empty_snapshot('s', 'r', 'LIVE')
    snapshot['readiness'] = readiness_document()
    mutate(snapshot['readiness'])
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_snapshot_rejects_private_unknown_and_cross_run_facts():
    snapshot = empty_snapshot('s', 'r', 'REHEARSAL')
    assert validate_snapshot(snapshot) == snapshot
    for field in ('image_path', 'raw_response', 'secret'):
        bad = deepcopy(snapshot)
        bad['evidence'][field] = 'private'
        with pytest.raises(ValueError):
            validate_snapshot(bad)
    bad = deepcopy(snapshot)
    bad['report']['run_id'] = 'other'
    with pytest.raises(ValueError):
        validate_snapshot(bad)


def test_schema_v3_snapshot_exposes_operational_stop_and_detection_facts():
    snapshot = empty_snapshot('s', 'r', 'REHEARSAL')
    assert snapshot['schema_version'] == 3
    stop = snapshot['mission']['route']
    assert stop == []
    assert snapshot['localization']['vertical_consistency']['status'] == 'UNAVAILABLE'

    # A populated stop must carry explicit coverage and arrival metadata rather
    # than leaving the browser to infer completion from animation.
    from sentinel.ui.projection import stop_view, vertical_consistency_view
    class Option:
        option_id = 'OPT'
        range_cm = viewed_width_cm = estimated_seconds = 100
        z_cm = 100
        resolvable_classes = ()
        stops = ((1, 2),)
    row = stop_view(Option(), 0)
    for key in ('coverage', 'coverage_reason', 'predicted_tag_count',
                'predicted_fix_error_cm', 'leg_speed_cm_s', 'trim_budget',
                'trim_progress', 'return_leg', 'arrival_error_cm',
                'tolerance_cm', 'tolerance_verdict'):
        assert key in row
    assert stop_view(Option(), 0, operational={'status':'FAILED'})['status'] == 'FAILED'
    assert vertical_consistency_view(None)['status'] == 'UNAVAILABLE'
    assert vertical_consistency_view({'tag_height_cm':100,'height_cm':100,'vertical_mismatch_cm':10})['status'] == 'CONSISTENT'
    assert vertical_consistency_view({'tag_height_cm':100,'height_cm':100,'vertical_mismatch_cm':20})['status'] == 'MARGINAL'
    assert vertical_consistency_view({'tag_height_cm':100,'height_cm':100,'vertical_mismatch_cm':30})['status'] == 'INCONSISTENT'


@pytest.mark.parametrize('raw,expected',[('UNLOCALISED','FAILED'),('IMPLAUSIBLE','FAILED'),('ABORTED_EN_ROUTE','ABORTED'),('mystery','UNKNOWN')])
def test_cube_stop_statuses_are_closed(raw, expected):
    from sentinel.ui.projection import stop_view
    class Option:
        option_id='OPT';range_cm=viewed_width_cm=estimated_seconds=100;z_cm=100;resolvable_classes=();stops=((1,2),)
    assert stop_view(Option(),0,operational={'status':raw})['status'] == expected


def test_proposer_interfaces_expose_explicit_provenance():
    from sentinel.survey.proposer import RuleProposer, JevProposer, LlmProposer
    from sentinel.coordinator.proposer import JevProposer as CoordinatorJev, LlmProposer as CoordinatorLlm
    assert (RuleProposer.provenance_mode, RuleProposer.provenance_source) == ('F','RULES')
    assert (JevProposer.provenance_mode, JevProposer.provenance_source) == ('J','JUDGE')
    assert (LlmProposer.provenance_mode, LlmProposer.provenance_source) == ('E','AGENT')
    assert (CoordinatorJev.provenance_mode, CoordinatorJev.provenance_source) == ('J','JUDGE')
    assert (CoordinatorLlm.provenance_mode, CoordinatorLlm.provenance_source) == ('E','AGENT')


def test_aborted_sweep_status_is_closed_and_not_active():
    from sentinel.ui.projection import sweep_status_view
    assert sweep_status_view('ABORTED') == 'ABORTED'
    assert sweep_status_view('FLOWN') == 'COMPLETE'
    assert sweep_status_view('OPEN') == 'ACTIVE'


def test_schema_v3_rejects_cross_run_and_unresolved_operational_references():
    snapshot = empty_snapshot('s', 'r', 'REHEARSAL')
    snapshot['evidence']['frames'].append({
        'id': 'f', 'stop_id': 'missing', 'captured_at': None,
        'commanded_pose': None, 'measured_pose': None, 'analysis': 'PENDING',
        'detection_status': 'CAPTURED', 'detection_latency_ms': None,
        'detection_error': None, 'detection_count': None, 'evidence': 'MISSING',
        'gap_reason': 'provider unavailable', 'failure': None,
        'merge_eligible': False, 'artifact_id': None, 'sha256': None,
        'width': None, 'height': None, 'source_kind': 'SIMULATED'})
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)
    snapshot['evidence']['frames'][0]['run_id'] = 'other'
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_provenance_source_is_closed():
    snapshot = empty_snapshot('s', 'r', 'REHEARSAL')
    snapshot['decision']['source'] = 'PRIVATE_PROVIDER'
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_return_active_stop_uses_real_route_identity_not_none_suffix():
    snapshot = empty_snapshot('s', 'r', 'REHEARSAL')
    snapshot['mission']['route'] = [{
        'stop_id': 'RETURN', 'x': 0.0, 'y': 0.0, 'coverage': 'OK',
        'coverage_reason': 'return leg', 'predicted_tag_count': None,
        'predicted_fix_error_cm': None, 'leg_speed_cm_s': 20.0,
        'trim_budget': 0, 'trim_progress': 0, 'return_leg': True,
        'arrival_error_cm': 0.0, 'tolerance_cm': 15.0,
        'tolerance_verdict': 'INSIDE', 'status': 'ACTIVE',
    }]
    snapshot['mission']['active_stop_id'] = 'RETURN'
    assert validate_snapshot(snapshot) == snapshot
    snapshot['mission']['active_stop_id'] = 'RETURN:None'
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)


def test_rehearsal_and_live_adapters_expose_detector_identity_separately(tmp_path):
    from sentinel.ui.scenarios import ScenarioAdapters, build_rehearsal_adapters
    rehearsal = build_rehearsal_adapters('fixtures/mission-control/clean-computer.json', tmp_path / 'images')
    assert (rehearsal.source_kind, rehearsal.detector_name, rehearsal.detector_model) == (
        'SIMULATED', 'scripted', 'scenario')
    fake_live = ScenarioAdapters((), object(), object(), object(), object(), object(), 'LIVE',
                                 detector_name='approved-detector', detector_model='approved-model')
    assert (fake_live.source_kind, fake_live.detector_name, fake_live.detector_model) == (
        'LIVE', 'approved-detector', 'approved-model')


@pytest.mark.parametrize('age', [-1, float('nan'), True])
def test_invalid_pose_age_rejected(age):
    snapshot = empty_snapshot('s', 'r', 'REHEARSAL')
    snapshot['localization']['age_ms'] = age
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)
