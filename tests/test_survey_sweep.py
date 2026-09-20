import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SURVEY_ROOT = ROOT.parent / 'survey-counting'
pytestmark = pytest.mark.skipif(not (SURVEY_ROOT / 'src/sentinel/survey/loop.py').exists(),
                               reason='external survey production checkout is required')


def run(tmp_path, *args):
    output = tmp_path / 'survey.json'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/survey_sweep.py'),
        '--survey-root', str(SURVEY_ROOT), '--scene',
        str(ROOT / 'fixtures/survey_eval/synthetic-off-axis-occlusion-closer-followup.json'),
        '--output', str(output), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text())


def test_real_production_loop_runs_all_scripted_modes_with_approval_and_budget(tmp_path):
    report = run(tmp_path, '--approve-sweeps', '--max-flights', '2')
    assert report['production_commit']
    assert report['evidence_kind'] == 'synthetic_production_loop_with_scripted_advisors'
    assert set(report['modes']) == {'F', 'E', 'J'}
    for row in report['modes'].values():
        assert row['outcome']['status'] == 'REPORTED'
        assert 1 <= row['outcome']['flights'] <= 2
        assert row['reads']['get_site'] >= 1
        assert row['reads']['get_results'] >= 1
        assert row['approvals'] and all(a['approved'] for a in row['approvals'])
        assert row['score']['report_valid'] is True


def test_default_refuses_synthetic_sweeps(tmp_path):
    report = run(tmp_path)
    assert all(row['outcome']['status'] == 'REFUSED' for row in report['modes'].values())
    assert all(row['outcome']['flights'] == 0 for row in report['modes'].values())


def test_production_bridge_marks_missing_replay_unevaluable(tmp_path):
    report = run(tmp_path, '--approve-sweeps', '--scene',
        str(ROOT / 'fixtures/survey_eval/synthetic-missing-unanalysed.json'))
    assert report['status'] == 'UNEVALUABLE'
    assert all(row['status'] == 'UNEVALUABLE' for row in report['modes'].values())


def test_missing_nonselected_option_does_not_block_rule_arm(tmp_path):
    scene = json.loads((ROOT / 'fixtures/survey_eval/synthetic-off-axis-occlusion-closer-followup.json').read_text())
    scene['options']['absent'] = ['not-recorded']
    path = tmp_path / 'scene.json'
    path.write_text(json.dumps(scene))
    report = run(tmp_path, '--approve-sweeps', '--modes', 'F', '--scene', str(path))
    assert report['modes']['F']['status'] == 'EVALUATED'


def test_calibration_mismatch_is_unevaluable(tmp_path):
    scene = json.loads((ROOT / 'fixtures/survey_eval/synthetic-off-axis-occlusion-closer-followup.json').read_text())
    scene['camera']['fx_px'] = 700
    path = tmp_path / 'scene.json'
    path.write_text(json.dumps(scene))
    report = run(tmp_path, '--approve-sweeps', '--scene', str(path))
    assert report['status'] == 'UNEVALUABLE'
