import pytest

from sentinel.room import semantic


@pytest.fixture(autouse=True)
def _no_home_offset_between_tests():
    """A session's measured-home offset is process state (one per site);
    each supervisor clears it on creation, and each test starts without one."""
    semantic._HOME_OFFSETS.clear()
    yield
    semantic._HOME_OFFSETS.clear()
