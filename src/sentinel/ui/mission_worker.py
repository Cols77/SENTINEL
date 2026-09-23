"""Single active mission, with refusal rather than executor queueing."""
from concurrent.futures import ThreadPoolExecutor
from threading import RLock

class MissionBusy(RuntimeError):
    pass

class MissionWorker:
    def __init__(self, run_callable):
        self._run = run_callable
        self._lock = RLock()
        self._active = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sentinel-mission')

    @property
    def active(self):
        with self._lock:
            return self._active

    def start(self, request):
        with self._lock:
            if self._active:
                raise MissionBusy('MISSION_BUSY')
            self._active = True
        try:
            return self._pool.submit(self._execute, request)
        except BaseException:
            with self._lock:
                self._active = False
            raise

    def _execute(self, request):
        try:
            return self._run(request)
        finally:
            with self._lock:
                self._active = False

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)
