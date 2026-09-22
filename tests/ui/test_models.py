from copy import deepcopy
import pytest
from sentinel.ui.models import empty_snapshot, validate_snapshot


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


@pytest.mark.parametrize('age', [-1, float('nan'), True])
def test_invalid_pose_age_rejected(age):
    snapshot = empty_snapshot('s', 'r', 'REHEARSAL')
    snapshot['localization']['age_ms'] = age
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)
