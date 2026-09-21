import pytest
from sentinel.ui.pose_projection import freshness_status

@pytest.mark.parametrize('age,expected',[(None,'UNAVAILABLE'),(0,'FRESH'),(999,'FRESH'),(1000,'AGING'),(2000,'AGING'),(2001,'STALE')])
def test_backend_freshness_boundaries(age,expected):
    assert freshness_status(age,2000)==expected
