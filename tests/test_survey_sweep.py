import json
from pathlib import Path
import subprocess
import sys
import hashlib
import sqlite3

import pytest


ROOT = Path(__file__).resolve().parents[1]
SURVEY_ROOT = next((path for path in (ROOT.parent / 'survey-counting', ROOT / '.worktrees/survey-counting')
                    if (path / 'src/sentinel/survey/loop.py').exists()), ROOT.parent / 'survey-counting')
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


def test_final_databases_and_append_evidence_survive_runner_exit(tmp_path):
    report = run(tmp_path, '--approve-sweeps', '--modes', 'F')
    row = report['modes']['F']
    database = Path(row['database']['path'])
    assert database.is_file()
    assert hashlib.sha256(database.read_bytes()).hexdigest() == row['database']['sha256']
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM reports').fetchone()[0] == 1
    assert len(row['database']['snapshot']['reports']) == 1
    stream = Path(report['append_stream_path']).read_text().splitlines()
    assert json.loads(stream[0])['mode'] == 'F'
    assert report['option_generation'] == 'independent_manifest_adapter_not_production_generate_options'


def test_review_records_actual_request_and_fresh_results(tmp_path):
    report = run(tmp_path, '--approve-sweeps', '--modes', 'E')
    decisions = report['modes']['E']['decisions']
    assert decisions[0]['request']['request_text'] == 'Count the scene objects'
    assert decisions[0]['request']['site']['options']
    review = next(d for d in decisions if d['point'] == 'REVIEW_RESULTS')
    assert review['context']['results']['flights_used'] == 1
    assert review['context']['results']['counts']['phone']['uncertain'] == 1
    assert review['context']['results']['unanalysed'] == 0
    assert review['context']['legal_candidates']


def test_automatic_reads_are_not_claimed_as_dispatchable_tools(tmp_path):
    report = run(tmp_path, '--approve-sweeps', '--modes', 'F')
    assert report['read_dispatch_supported'] is False
    assert report['read_dispatch_evidence'] == 'source_reviewed_not_dispatched'
    assert report['modes']['F']['reads']['get_site'] > 0
    assert all('survey.get_site' not in d['legal_tool_names'] and 'survey.get_results' not in d['legal_tool_names']
               for d in report['modes']['F']['decisions'])


def test_existing_report_is_not_overwritten(tmp_path):
    output = tmp_path / 'survey.json'
    output.write_text('original evidence')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/survey_sweep.py'),
        '--survey-root', str(SURVEY_ROOT), '--scene',
        str(ROOT / 'fixtures/survey_eval/synthetic-off-axis-occlusion-closer-followup.json'),
        '--output', str(output)], capture_output=True, text=True)
    assert result.returncode != 0
    assert output.read_text() == 'original evidence'
    assert not list(tmp_path.glob('survey-run-*'))


def test_unexpected_loop_failure_retains_all_arm_records(tmp_path):
    script = '''
import argparse, json, runpy, sys
sys.path.insert(0, sys.argv[1] + '/src')
from sentinel.survey import loop
original_reads = loop.get_site, loop.get_results
def broken(**kwargs):
    raise RuntimeError('sensitive exception details')
loop.run_survey = broken
runner = runpy.run_path(sys.argv[2])
result = runner['execute'](argparse.Namespace(survey_root=sys.argv[1], scene=sys.argv[3],
    output=sys.argv[4], modes='F,E,J', approve_sweeps=True, max_flights=2))
assert (loop.get_site, loop.get_results) == original_reads
print(json.dumps(result))
'''
    result = subprocess.run([sys.executable, '-c', script, str(SURVEY_ROOT),
        str(ROOT / 'scripts/survey_sweep.py'),
        str(ROOT / 'fixtures/survey_eval/synthetic-off-axis-occlusion-closer-followup.json'),
        str(tmp_path / 'report.json')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['status'] == 'ERROR'
    assert set(report['modes']) == {'F', 'E', 'J'}
    assert all(row['status'] == 'ERROR' and row['error_category'] == 'RuntimeError'
               and Path(row['database']['path']).is_file() for row in report['modes'].values())
    assert len(Path(report['append_stream_path']).read_text().splitlines()) == 3
    assert 'sensitive exception details' not in result.stdout
