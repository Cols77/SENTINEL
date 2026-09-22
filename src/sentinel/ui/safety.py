"""Independent safety path: session latch, runner latch, then transport, then audit."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, RLock
from sentinel.client import EmergencyAborted
from sentinel.mission_observer import RunnerLifecycleEvent
from .store import canonical

class SafetyRegistry:
    def __init__(self):
        self._lock = RLock()
        self._runners = {}
        self.emergency = Event()
        self.closing = False

    def bind(self, run_id, runner):
        with self._lock:
            if self.closing or self.emergency.is_set():
                runner.latch_emergency()
                raise EmergencyAborted('SESSION_EMERGENCY')
            if self._runners and self._runners.get(run_id) is not runner:
                raise RuntimeError('RUNNER_ALREADY_ACTIVE')
            self._runners[run_id] = runner

    def unbind(self, run_id, runner):
        with self._lock:
            if self._runners.get(run_id) is runner:
                del self._runners[run_id]

    def get(self, run_id):
        with self._lock:
            return self._runners.get(run_id)

    def observe(self, event) -> None:
        """Consume runner lifecycle telemetry as the safety binding boundary."""
        if not isinstance(event, RunnerLifecycleEvent) or event.runner is None:
            return
        if event.status == 'STARTED':
            self.bind(event.run_id, event.runner)
        elif event.status in {'PASS', 'COMPLETE', 'FAILED', 'FAIL', 'EMERGENCY_ABORTED'}:
            self.unbind(event.run_id, event.runner)

    def stop_admission(self):
        """Close admission atomically without suppressing controlled landing."""
        with self._lock:
            self.closing = True
            return tuple(self._runners.values())

    def latch_all(self):
        with self._lock:
            self.emergency.set()
            for runner in self._runners.values():
                runner.latch_emergency()


class RunnerSafetyAdapter:
    def __init__(self, registry, emergency_sender, session_id, audit_sink=None):
        self.registry, self.sender, self.session_id = registry, emergency_sender, session_id
        self.audit_sink = audit_sink
        self._lock = RLock()
        self._ledger = {}
        self._ids = {}
        self.audit_error = False
        self._audit_pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='sentinel-safety-audit')
        self.state = {name: dict(available=False,status='UNAVAILABLE',reason='No active runner',command_id=None) for name in ('land','emergency')}

    def _claim(self, action, scope, identity):
        if set(identity) != {'command_id','correlation_id','idempotency_key'} or any(not isinstance(v,str) or not v for v in identity.values()):
            raise ValueError('INVALID_COMMAND_IDENTITY')
        key = identity['idempotency_key']
        fingerprint = canonical([action,scope,identity])
        with self._lock:
            prior = self._ledger.get(key)
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError('COMMAND_IDENTITY_CONFLICT')
                return deepcopy(prior[1])
            if identity['command_id'] in self._ids:
                raise ValueError('COMMAND_IDENTITY_CONFLICT')
            self._ids[identity['command_id']] = key
            self._ledger[key] = (fingerprint,dict(status='IN_DOUBT',message='Safety request in progress'))
        return None

    def _finish(self, action, identity, result):
        with self._lock:
            key=identity['idempotency_key']
            self._ledger[key]=(self._ledger[key][0],deepcopy(result))
            self.state[action] = dict(available=True,status=result['status'],reason=result.get('reason_code'),command_id=identity['command_id'])
        if self.audit_sink:
            self._audit_pool.submit(self._audit, {**result,'action':action,**identity})
        return result

    def _audit(self, result):
        try:
            self.audit_sink(result)
        except Exception:
            self.audit_error=True

    def trigger_emergency(self, session_id, command_identity, active_run_id=None):
        if session_id != self.session_id:
            raise ValueError('SESSION_MISMATCH')
        prior = self._claim('emergency',session_id,command_identity)
        if prior:
            return prior
        self.registry.latch_all()
        try:
            if self.sender is None:
                result=dict(status='UNAVAILABLE',reason_code='NO_EMERGENCY_TRANSPORT')
            else:
                sent=self.sender() if callable(self.sender) else self.sender.send()
                result=dict(status='ACKNOWLEDGED',bytes_sent=sent)
        except Exception:
            result=dict(status='FAILED',reason_code='EMERGENCY_SEND_FAILED')
        return self._finish('emergency',command_identity,result)

    def request_land(self, run_id, command_identity):
        prior=self._claim('land',run_id,command_identity)
        if prior:
            return prior
        runner=self.registry.get(run_id)
        if runner is None:
            result=dict(status='UNAVAILABLE',reason_code='NO_ACTIVE_RUNNER')
        else:
            try:
                runner.request_controlled_land()
                result=dict(status=runner.land_status)
            except Exception:
                result=dict(status='FAILED',reason_code='LAND_SEND_FAILED')
        return self._finish('land',command_identity,result)

    def close(self):
        self._audit_pool.shutdown(wait=True)
