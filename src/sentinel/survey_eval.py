"""Independent synthetic survey acceptance arithmetic; no production loop adapter.

Camera convention: heading zero looks along +y, x points right, z points up.
Poses are level cameras; truth positions describe centres on stated surface planes.
Replay records are explicit per option, never substituted across options.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Mapping


class Unevaluable(ValueError):
    """The requested replay lacks complete recorded observations."""


@dataclass(frozen=True)
class Scene:
    schema_version: int
    scene_id: str
    provenance: str
    bounds_cm: dict
    camera: dict
    objects: tuple[dict, ...]
    options: dict[str, list[str]]
    frames: tuple[dict, ...]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('expected finite number')
    return value


def load_scene(path) -> Scene:
    """Load v1 truth; real captures require separate provenance/evidence support."""
    doc = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if doc.get('schema_version') != 1 or doc.get('provenance') != 'synthetic':
        raise ValueError('only explicitly synthetic schema version 1 is supported')
    if not isinstance(doc.get('scene_id'), str) or not doc['scene_id']:
        raise ValueError('scene_id required')
    bounds, camera = doc['bounds_cm'], doc['camera']
    for axis in ('x', 'y'):
        if _number(bounds['min_' + axis]) >= _number(bounds['max_' + axis]):
            raise ValueError('invalid surveyed bounds')
    for key in ('width_px', 'height_px', 'fx_px', 'fy_px', 'minimum_object_width_px'):
        if _number(camera[key]) <= 0:
            raise ValueError('camera dimensions must be positive')
    for key in ('cx_px', 'cy_px'):
        _number(camera[key])
    ids = set()
    for obj in doc['objects']:
        if not obj.get('object_id') or obj['object_id'] in ids or not obj.get('class'):
            raise ValueError('unique truth ids and class names required')
        ids.add(obj['object_id'])
        for axis in ('x', 'y'):
            if not bounds['min_' + axis] <= _number(obj[axis + '_cm']) <= bounds['max_' + axis]:
                raise ValueError('truth outside surveyed bounds')
        _number(obj['surface_z_cm'])
        if _number(obj['width_cm']) <= 0:
            raise ValueError('truth width must be positive')
    frame_ids = set()
    for frame in doc['frames']:
        if not frame.get('frame_id') or frame['frame_id'] in frame_ids:
            raise ValueError('unique frame ids required')
        frame_ids.add(frame['frame_id'])
        if frame['option_id'] not in doc['options']:
            raise ValueError('frame option not declared')
        if not isinstance(frame['stop_index'], int) or frame['stop_index'] < 0:
            raise ValueError('invalid stop index')
        for key in ('x_cm', 'y_cm', 'z_cm', 'heading_deg'):
            _number(frame['pose'][key])
        if not set(frame['occluded_object_ids']) <= ids:
            raise ValueError('unknown occlusion truth id')
        if not isinstance(frame['detections'], list):
            raise ValueError('recorded detections required')
    return Scene(1, doc['scene_id'], doc['provenance'], bounds, camera,
                 tuple(doc['objects']), doc['options'], tuple(doc['frames']))


def project_object(scene: Scene, obj: dict, pose: dict) -> dict | None:
    """Independent pinhole forward projection using optical-axis depth."""
    theta = math.radians(pose['heading_deg'])
    dx, dy = obj['x_cm'] - pose['x_cm'], obj['y_cm'] - pose['y_cm']
    depth = dx * math.sin(theta) + dy * math.cos(theta)
    if depth <= 0:
        return None
    lateral = dx * math.cos(theta) - dy * math.sin(theta)
    camera = scene.camera
    return {'u_px': camera['cx_px'] + camera['fx_px'] * lateral / depth,
            'v_px': camera['cy_px'] + camera['fy_px'] * (pose['z_cm'] - obj['surface_z_cm']) / depth,
            'width_px': camera['fx_px'] * obj['width_cm'] / depth,
            'axial_depth_cm': depth}


def replay_option(scene: Scene, option_id: str) -> tuple[dict, ...]:
    ids = scene.options.get(option_id)
    if not ids:
        raise Unevaluable(f'no observations for option {option_id}')
    indexed = {frame['frame_id']: frame for frame in scene.frames}
    result = []
    for frame_id in ids:
        frame = indexed.get(frame_id)
        if frame is None or frame['option_id'] != option_id or frame['status'] != 'OK':
            raise Unevaluable(f'missing or unanalysed {option_id}/{frame_id}')
        result.append(frame)
    return tuple(result)


def _visible(scene, frame):
    result = set()
    for obj in scene.objects:
        if obj['object_id'] in frame['occluded_object_ids']:
            continue
        p = project_object(scene, obj, frame['pose'])
        if p and 0 <= p['u_px'] < scene.camera['width_px'] and 0 <= p['v_px'] < scene.camera['height_px'] \
                and p['width_px'] >= scene.camera['minimum_object_width_px']:
            result.add(obj['object_id'])
    return result


def coverage(scene: Scene, option_ids, *, previous_option_ids=()) -> dict:
    def union(options):
        return set().union(*(_visible(scene, f) for option in options for f in replay_option(scene, option)))
    current, previous = union(option_ids), union(previous_option_ids)
    return {'visible_resolvable_ids': sorted(current), 'new_object_ids': sorted(current - previous),
            'whole_scene_fraction': len(current) / len(scene.objects) if scene.objects else None,
            'cumulative_whole_scene_fraction': len(current | previous) / len(scene.objects) if scene.objects else None}


def _matching(truth, reported, radius):
    """Maximum-cardinality bipartite matching, independent of greedy row order."""
    neighbours = []
    for obj in truth:
        neighbours.append([i for i, row in enumerate(reported)
            if row.get('class') == obj['class'] and row.get('x_cm') is not None and row.get('y_cm') is not None
            and math.hypot(row['x_cm'] - obj['x_cm'], row['y_cm'] - obj['y_cm']) <= radius])
    assigned = {}
    def augment(index, seen):
        for candidate in neighbours[index]:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate not in assigned or augment(assigned[candidate], seen):
                assigned[candidate] = index
                return True
        return False
    for index in range(len(truth)):
        augment(index, set())
    duplicates = sum(i not in assigned and any(i in row for row in neighbours) for i in range(len(reported)))
    return len(assigned), duplicates


def score_episode(scene: Scene, outcome: dict, *, reported_objects=(), class_counts=(), match_radius_cm=50.0) -> dict:
    """Score supplied episode evidence, not a simulated or implicitly run policy.

    Counts without explicit bounds derive confirmed/uncertain intervals. Exact
    errors exist only for exact intervals; lower bounds never imply exact zero.
    Precision/recall refer to supplied room-localized object hypotheses, not
    provider bbox accuracy. Empty denominators produce None.
    """
    if _number(match_radius_cm) <= 0:
        raise ValueError('matching radius must be positive')
    completed = outcome.get('status') in {'REPORTED', 'REPORT_SUBMITTED', 'COMPLETE'}
    matched, duplicates = _matching(scene.objects, reported_objects, match_radius_cm)
    true_counts = {name: sum(o['class'] == name for o in scene.objects)
                   for name in {o['class'] for o in scene.objects} | {o['class'] for o in reported_objects}}
    bounds, errors = {}, {}
    if completed:
        counts = list(class_counts)
        if not counts:
            for name in true_counts:
                rows = [r for r in reported_objects if r['class'] == name]
                confirmed = sum(r.get('band') == 'CONFIRMED' for r in rows)
                counts.append({'class': name, 'lower': confirmed, 'upper': len(rows)})
        for row in counts:
            lower, upper = row['lower'], row.get('upper')
            if _number(lower) < 0 or int(lower) != lower or upper is not None and (_number(upper) < lower or int(upper) != upper):
                raise ValueError('invalid count interval')
            truth = true_counts.get(row['class'], 0)
            bounds[row['class']] = {'lower': lower, 'upper': upper,
                                   'contains_truth': lower <= truth and (upper is None or truth <= upper)}
            if upper == lower:
                errors[row['class']] = lower - truth
    return {'scene_id': scene.scene_id, 'provenance': scene.provenance, 'status': outcome.get('status'),
            'report_completed': completed, 'report_valid': outcome.get('report_valid') if completed else None,
            'escalated': outcome.get('status') == 'ESCALATED',
            'count_error': errors if completed else None, 'count_bounds': bounds,
            'matched_objects': matched, 'duplicate_count': duplicates,
            'unique_count_error': len(reported_objects) - len(scene.objects),
            'precision': matched / len(reported_objects) if reported_objects else None,
            'recall': matched / len(scene.objects) if scene.objects else None,
            'localisation': matched / len(scene.objects) if scene.objects else None,
            'flights': outcome.get('flights'), 'illegal_calls': outcome.get('illegal_calls'),
            'decision_quality': outcome.get('decision_quality')}


def compare_modes(scene: Scene, episodes: Mapping[str, dict]) -> dict[str, dict]:
    """Compare explicit recorded episode payloads; does not execute policies."""
    return {mode: score_episode(scene, **episode) for mode, episode in episodes.items()}
