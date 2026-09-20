"""Run an external production survey loop against independent synthetic replay.

E/J use deterministic advisor doubles. No paid calls, images, hardware or real
filming-consent claims are involved. Source snapshots detect concurrent changes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile


def snapshot(root):
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                              text=True, check=True).stdout.strip()
    return {'commit': git('rev-parse', 'HEAD'), 'dirty': git('status', '--porcelain'),
            'source_sha256': {str(p.relative_to(root)).replace('\\', '/'):
                hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / 'src').rglob('*'))
                if p.is_file() and p.suffix in {'.py', '.sql', '.json'}}}


def evaluator():
    path = Path(__file__).resolve().parents[1] / 'src/sentinel/survey_eval.py'
    spec = importlib.util.spec_from_file_location('independent_survey_evaluator', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def execute(args):
    root = Path(args.survey_root).resolve()
    before = snapshot(root)
    ev = evaluator()
    scene = ev.load_scene(args.scene)
    sys.path.insert(0, str(root / 'src'))
    from sentinel.survey import loop, observability
    from sentinel.survey.detector import ScriptedDetector
    from sentinel.survey.envelope import SweepOption
    from sentinel.survey.flight import FlightFrame
    from sentinel.survey.proposer import RuleProposer, LlmProposer, JevProposer
    from sentinel.survey.report import check_report
    from sentinel.survey.store import SurveyStore

    expected = {'width_px': observability.IMAGE_WIDTH, 'height_px': observability.IMAGE_HEIGHT,
                'fx_px': observability.FOCAL_PX, 'fy_px': observability.FOCAL_PX,
                'cx_px': observability.PRINCIPAL_X, 'cy_px': observability.PRINCIPAL_Y,
                'minimum_object_width_px': observability.MIN_DETECTABLE_PX}
    calibration_error = any(scene.camera[k] != value for k, value in expected.items())
    indexed = {f['frame_id']: f for f in scene.frames}
    options = []
    for name, ids in scene.options.items():
        frames = [indexed[i] for i in ids if i in indexed]
        if not frames:
            # Explicit unavailable candidate. No position/observability is invented.
            options.append(SweepOption(name, 'UNEVALUABLE: replay geometry absent', (), 0, (), 0, 0, (), 0))
            continue
        poses = [f['pose'] for f in frames]
        depths = [p['axial_depth_cm'] for obj in scene.objects for pose in poses
                  if (p := ev.project_object(scene, obj, pose)) is not None]
        distance = round(statistics.median(depths)) if depths else 1
        stops = tuple(dict.fromkeys((p['x_cm'], p['y_cm']) for p in poses))
        span = max(p[0] for p in stops) - min(p[0] for p in stops)
        options.append(SweepOption(name, 'synthetic manifest replay', stops, poses[0]['z_cm'], (0,),
            round(span + scene.camera['width_px'] * distance / scene.camera['fx_px']), len(stops) * 22,
            observability.resolvable_classes(distance), distance))

    result = {'evidence_kind': 'synthetic_production_loop_with_scripted_advisors',
              'production_commit': before['commit'], 'source_before': before,
              'scene_id': scene.scene_id, 'scene_sha256': hashlib.sha256(Path(args.scene).read_bytes()).hexdigest(),
              'paid_calls': 0, 'hardware_flights': 0, 'modes': {}}
    original_site, original_results = loop.get_site, loop.get_results
    with tempfile.TemporaryDirectory(prefix='sentinel-survey-eval-') as temporary:
        for mode in args.modes.split(','):
            reads = {'get_site': 0, 'get_results': 0}
            approvals, flown, decisions, routes = [], [], [], []
            def site_read(*a, **kw):
                reads['get_site'] += 1
                return original_site(*a, **kw)
            def results_read(*a, **kw):
                reads['get_results'] += 1
                return original_results(*a, **kw)
            loop.get_site, loop.get_results = site_read, results_read

            class Sink:
                def write(self, record):
                    routes.append(record)

            class Advisor:
                def label(self, labels):
                    if not flown:
                        return next(iter(labels))
                    return next((label for label in labels if label.startswith('follow_up:')
                                 and label.split(':', 1)[1] not in flown), 'report')
                def choice(self, state, instruction, *, criteria, name):
                    chosen = self.label(criteria)
                    return {'value': chosen, 'confidence': 0.95,
                            'probabilities': {label: 0.95 if label == chosen else 0.01 for label in criteria}}
                def propose(self, request, context):
                    options_by_label = {c.label: c for c in context.legal.candidates if c.label != 'escalate'}
                    candidate = options_by_label[self.label(options_by_label)]
                    arguments = dict(candidate.arguments)
                    if 'reason' in arguments:
                        arguments['reason'] = 'Explicit synthetic replay comparison choice'
                    return {'status': 'TOOL_CALLS', 'tool_calls': [{'tool_name': candidate.tool_name,
                                                                 'arguments': arguments}]}

            policy = {'F': lambda: RuleProposer(), 'E': lambda: LlmProposer(Advisor(), Sink()),
                      'J': lambda: JevProposer(Advisor(), Sink())}[mode]()
            class RecordedPolicy:
                def decide(self, request, context):
                    output = policy.decide(request, context)
                    decisions.append({'point': context.legal.decision_point, 'result': output,
                        'legal_tool_names': sorted({c.tool_name for c in context.legal.candidates}),
                        'site_in_request': 'site' in request})
                    return output

            script = {}
            for frame in scene.frames:
                items = []
                for detection in frame['detections']:
                    item = {'label': detection['class'], 'confidence': 'low' if detection.get('band') == 'UNCERTAIN' else 'high'}
                    if detection.get('bbox'):
                        item.update({k: v / scene.camera['width_px' if k in {'x', 'width'} else 'height_px']
                                     for k, v in detection['bbox'].items()})
                    items.append(item)
                script[frame['frame_id'] + '.synthetic'] = items

            class StrictFlight:
                def fly(self, option):
                    if calibration_error:
                        raise ev.Unevaluable('manifest camera calibration differs from production constants')
                    frames = ev.replay_option(scene, option.option_id)
                    for frame in frames:
                        if any(frame['pose']['z_cm'] - obj['surface_z_cm'] != loop.DELTA_Z_CM for obj in scene.objects):
                            raise ev.Unevaluable('production fixed delta_z does not support this scene surface')
                    flown.append(option.option_id)
                    return [FlightFrame(stop_index=f['stop_index'], look_deg=0, **f['pose'],
                        position_kind='COMMANDED', image_path=f['frame_id'] + '.synthetic',
                        artifact_id='synthetic:' + f['frame_id'],
                        sha256=hashlib.sha256(json.dumps(f, sort_keys=True).encode()).hexdigest()) for f in frames]

            def approve(call):
                approved = bool(args.approve_sweeps)
                approvals.append({'call': call, 'approved': approved, 'kind': 'scripted_synthetic_only'})
                return approved
            def writer(results):
                clauses = []
                for row in results['class_counts']:
                    if row['method'] == 'NOT_OBSERVABLE':
                        continue
                    if row['method'] == 'SINGLE_VIEW_LOWER_BOUND':
                        clauses.append(f"At least {row['uncertain']} {row['class']} observations.")
                    else:
                        clauses.append(f"{row['confirmed']} {row['class']} confirmed; {row['uncertain']} uncertain sightings.")
                return ' '.join(clauses) or 'No classes observable.'

            store = SurveyStore.open(Path(temporary) / (mode + '.sqlite3'))
            try:
                outcome = loop.run_survey(store=store, run_id=mode, request_text='Count the scene objects',
                    options=options, policy=RecordedPolicy(), flight=StrictFlight(), detector=ScriptedDetector(script),
                    on_approval=approve, writer=writer, max_flights=args.max_flights)
                objects = [dict(row) for row in store.connection.execute('SELECT * FROM objects WHERE run_id=?', (mode,))]
                counts = original_results(store, mode, options)['class_counts']
                intervals = [{'class': c['class'],
                    'lower': c['uncertain'] if c['method'] == 'SINGLE_VIEW_LOWER_BOUND' else c['confirmed'],
                    'upper': c['confirmed'] + c['uncertain'] if c['method'] == 'MERGED_MULTI_VIEW' else None} for c in counts]
                report = None
                if outcome.get('report') is not None:
                    check_report(outcome['report'], counts)
                    report = {'body': outcome['report'], 'class_counts': intervals, 'objects': objects}
                illegal = sum(call['tool_name'] not in decision['legal_tool_names'] for decision in decisions
                              for call in decision['result'].get('tool_calls', []))
                score = ev.score_episode(scene, {**outcome, 'report': report,
                    'report_valid': True if report else None, 'illegal_calls': illegal},
                    reported_objects=objects, class_counts=intervals, selected_option_ids=flown)
                result['modes'][mode] = {'status': 'EVALUATED', 'outcome': outcome, 'score': score,
                    'reads': reads, 'approvals': approvals, 'decisions': decisions, 'routes': routes,
                    'flown_option_ids': flown, 'class_counts': counts,
                    'approval_descriptions_complete': all(bool(a['call'].get('description')) for a in approvals)}
            except ev.Unevaluable as error:
                result['modes'][mode] = {'status': 'UNEVALUABLE', 'reason': str(error),
                    'reads': reads, 'approvals': approvals, 'decisions': decisions, 'flown_option_ids': flown}
            finally:
                store.connection.close()
    loop.get_site, loop.get_results = original_site, original_results
    result['source_after'] = snapshot(root)
    result['source_stable'] = result['source_before'] == result['source_after']
    result['status'] = ('SOURCE_CHANGED' if not result['source_stable'] else
                        'UNEVALUABLE' if all(r['status'] == 'UNEVALUABLE' for r in result['modes'].values()) else 'EVALUATED')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--survey-root', required=True)
    parser.add_argument('--scene', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--approve-sweeps', action='store_true')
    parser.add_argument('--max-flights', type=int, choices=(1, 2), default=2)
    parser.add_argument('--modes', default='F,E,J')
    args = parser.parse_args()
    if not args.modes or any(mode not in {'F', 'E', 'J'} for mode in args.modes.split(',')):
        parser.error('modes must be F,E,J labels')
    result = execute(args)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
