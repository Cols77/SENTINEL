import importlib
import json
from pathlib import Path

import pytest


def api():
    return importlib.import_module('sentinel.survey_eval')


def manifest():
    return {
        'schema_version': 1, 'scene_id': 'synthetic-unit', 'provenance': 'synthetic',
        'bounds_cm': {'min_x': -1000, 'max_x': 1000, 'min_y': 0, 'max_y': 1000},
        'camera': {'width_px': 960, 'height_px': 720, 'fx_px': 683, 'fy_px': 683,
                   'cx_px': 480, 'cy_px': 360, 'minimum_object_width_px': 20},
        'objects': [{'object_id': 'phone', 'class': 'phone', 'x_cm': 150, 'y_cm': 600,
                     'surface_z_cm': 0, 'width_cm': 15}],
        'options': {'far': ['f0'], 'near': ['n0']},
        'frames': [
            {'frame_id': 'f0', 'option_id': 'far', 'stop_index': 0, 'status': 'OK',
             'pose': {'x_cm': 0, 'y_cm': 0, 'z_cm': 55, 'heading_deg': 0},
             'occluded_object_ids': [], 'detections': []},
            {'frame_id': 'n0', 'option_id': 'near', 'stop_index': 0, 'status': 'OK',
             'pose': {'x_cm': 0, 'y_cm': 300, 'z_cm': 55, 'heading_deg': 0},
             'occluded_object_ids': [], 'detections': []}]}


def scene(tmp_path, doc=None):
    path = tmp_path / 'scene.json'
    path.write_text(json.dumps(doc or manifest()), encoding='utf-8')
    return api().load_scene(path)


def test_module_exists():
    assert importlib.util.find_spec('sentinel.survey_eval') is not None


def test_independent_projection_uses_axial_depth(tmp_path):
    s = scene(tmp_path)
    p = api().project_object(s, s.objects[0], s.frames[1]['pose'])
    assert p['u_px'] == pytest.approx(821.5)
    assert p['v_px'] == pytest.approx(485.2166666667)
    assert p['axial_depth_cm'] == 300


def test_coverage_tracks_new_resolvable_truth_not_nominal_range(tmp_path):
    s = scene(tmp_path)
    assert api().coverage(s, ['far'])['visible_resolvable_ids'] == []
    c = api().coverage(s, ['near'], previous_option_ids=['far'])
    assert c['new_object_ids'] == ['phone']
    assert c['whole_scene_fraction'] == 1


@pytest.mark.parametrize('mutation', ['absent', 'unanalysed'])
def test_missing_replay_never_falls_back(tmp_path, mutation):
    doc = manifest()
    if mutation == 'absent':
        doc['options']['near'].append('missing-frame')
    else:
        doc['frames'][1]['status'] = 'UNANALYSED'
    s = scene(tmp_path, doc)
    with pytest.raises(api().Unevaluable):
        api().replay_option(s, 'near')


def test_occlusion_excludes_coverage(tmp_path):
    doc = manifest()
    doc['frames'][1]['occluded_object_ids'] = ['phone']
    assert api().coverage(scene(tmp_path, doc), ['near'])['visible_resolvable_ids'] == []


def test_no_report_is_not_consistent_or_complete(tmp_path):
    score = api().score_episode(scene(tmp_path), {'status': 'ESCALATED'})
    assert score['report_valid'] is None
    assert score['report_completed'] is False
    assert score['escalated'] is True
    assert score['count_error'] is None


def test_matching_is_one_to_one_and_order_independent(tmp_path):
    doc = manifest()
    doc['objects'] = [dict(doc['objects'][0], object_id='a', x_cm=0),
                      dict(doc['objects'][0], object_id='b', x_cm=80)]
    s = scene(tmp_path, doc)
    rows = [{'class': 'phone', 'x_cm': 40, 'y_cm': 600, 'band': 'CONFIRMED'},
            {'class': 'phone', 'x_cm': 0, 'y_cm': 600, 'band': 'CONFIRMED'}]
    outcome = {'status': 'REPORTED', 'report_valid': True}
    for order in [rows, list(reversed(rows))]:
        result = api().score_episode(s, outcome, reported_objects=order)
        assert result['localisation'] == 1
        assert result['count_error'] == {'phone': 0}
    result = api().score_episode(s, outcome, reported_objects=rows + [rows[1]])
    assert result['duplicate_count'] == 1
    assert result['precision'] == pytest.approx(2 / 3)


def test_lower_bound_is_not_scored_as_exact_zero(tmp_path):
    result = api().score_episode(scene(tmp_path), {'status': 'REPORTED', 'report_valid': True},
        class_counts=[{'class': 'phone', 'kind': 'LOWER_BOUND', 'lower': 0, 'upper': None}])
    assert result['count_error'] == {}
    assert result['count_bounds']['phone'] == {'lower': 0, 'upper': None, 'contains_truth': True}


def test_compare_modes_scores_supplied_episodes_without_claiming_integration(tmp_path):
    scores = api().compare_modes(scene(tmp_path), {'F': {'outcome': {'status': 'ESCALATED'}},
                                                'E': {'outcome': {'status': 'REPORTED', 'report_valid': True}}})
    assert scores['F']['report_completed'] is False
    assert scores['E']['report_completed'] is True


@pytest.mark.parametrize('change', ['version', 'duplicate', 'bounds', 'provenance'])
def test_scene_rejects_invalid_truth(tmp_path, change):
    doc = manifest()
    if change == 'version': doc['schema_version'] = 2
    if change == 'duplicate': doc['objects'] *= 2
    if change == 'bounds': doc['objects'][0]['x_cm'] = 1001
    if change == 'provenance': doc['provenance'] = 'invented-real'
    with pytest.raises(ValueError):
        scene(tmp_path, doc)


def test_committed_acceptance_scenes_are_explicitly_synthetic():
    paths = list(Path('fixtures/survey_eval').glob('*.json'))
    assert len(paths) >= 3
    for path in paths:
        assert api().load_scene(path).provenance == 'synthetic'


def test_duplicate_replay_frames_do_not_increase_coverage():
    s = api().load_scene('fixtures/survey_eval/synthetic-close-same-frame-and-duplicate-replay.json')
    assert api().coverage(s, ['rail'])['visible_resolvable_ids'] == ['left', 'right']


def test_uncertain_objects_form_an_interval_not_an_exact_count(tmp_path):
    result = api().score_episode(scene(tmp_path), {'status': 'REPORTED'},
        reported_objects=[{'class': 'phone', 'band': 'UNCERTAIN', 'x_cm': 150, 'y_cm': 600}])
    assert result['count_error'] == {}
    assert result['count_bounds']['phone'] == {'lower': 0, 'upper': 1, 'contains_truth': True}
