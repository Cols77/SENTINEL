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


@pytest.mark.parametrize('age', [-1, float('nan'), True])
def test_invalid_pose_age_rejected(age):
    snapshot = empty_snapshot('s', 'r', 'REHEARSAL')
    snapshot['localization']['age_ms'] = age
    with pytest.raises(ValueError):
        validate_snapshot(snapshot)
