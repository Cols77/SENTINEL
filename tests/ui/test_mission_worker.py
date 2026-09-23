from threading import Event
import pytest
from sentinel.ui.mission_worker import MissionWorker, MissionBusy


def test_second_mission_is_refused_not_queued():
    entered, release = Event(), Event()
    def run(request):
        entered.set()
        assert release.wait(2)
        return request
    worker = MissionWorker(run)
    first = worker.start({'run_id':'r'})
    assert entered.wait(1)
    try:
        with pytest.raises(MissionBusy): worker.start({'run_id':'other'})
    finally:
        release.set()
        worker.close()
    assert first.result()['run_id'] == 'r'
