"""Persist proposals before exposing controls; only exact decisions release a waiter."""
from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
import json
from threading import Event, RLock
from uuid import uuid4
from .store import CommandConflict, canonical

@dataclass
class PendingApproval:
    proposal: dict
    ready: Event = field(default_factory=Event)
    decision: bool = False

    def wait(self, timeout=None):
        if not self.ready.wait(timeout):
            raise TimeoutError('APPROVAL_PENDING')
        return self.decision


class ApprovalBroker:
    def __init__(self, store):
        self.store = store
        self._lock = RLock()
        self._waiters = {}
        self.changed = Event()

    def offer(self, run_id, candidate, correlation_id):
        with self._lock, self.store.connection(write=True) as conn:
            row = conn.execute('SELECT * FROM ui_runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None or row['status'] != 'ACTIVE':
                raise CommandConflict('RUN_NOT_ACTIVE')
            version = row['decision_version'] + 1
            proposal = dict(id=str(uuid4()),run_id=run_id,
                sha256=hashlib.sha256(canonical(candidate).encode()).hexdigest(),
                decision_version=version,correlation_id=correlation_id,candidate=candidate)
            conn.execute("UPDATE ui_offers SET status='REPLACED' WHERE run_id=? AND status='PENDING'", (run_id,))
            conn.execute('UPDATE ui_runs SET decision_version=? WHERE run_id=?', (version,run_id))
            conn.execute('INSERT INTO ui_offers VALUES (?,?,?,?,NULL)', (proposal['id'],run_id,canonical(proposal),'PENDING'))
            self.store.event_in(conn, run_id, 'PROPOSAL', {'summary': candidate.get('description','Proposal awaiting approval')})
            for pending in self._waiters.values():
                if pending.proposal['run_id'] == run_id:
                    pending.ready.set()
            pending = PendingApproval(proposal)
            self._waiters[proposal['id']] = pending
        self.changed.set()
        return pending

    def current(self, run_id):
        with self.store.connection() as conn:
            row = conn.execute("SELECT payload_json FROM ui_offers WHERE run_id=? AND status='PENDING'", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def decide(self, attempt):
        required = {'session_id','run_id','proposal_id','proposal_sha256','expected_version','decision','command_id','correlation_id','attempt_id','idempotency_key'}
        identity = attempt.get('attempt_id','invalid')
        conflict = dict(attempt_id=identity,status='CONFLICT',message='Proposal or command identity changed')
        if set(attempt) != required or attempt['decision'] not in {'APPROVE','REJECT'} or type(attempt['expected_version']) is not int:
            return conflict
        if any(not isinstance(attempt[k],str) or not attempt[k] for k in required-{'expected_version'}):
            return conflict
        try:
            with self._lock, self.store.connection(write=True) as conn:
                claim = self.store.claim_in(conn, attempt)
                if not claim['created']:
                    return claim['command']['result'] or dict(attempt_id=identity,status='IN_DOUBT',message='Decision awaiting reconciliation')
                row = conn.execute('SELECT * FROM ui_runs WHERE run_id=?', (attempt['run_id'],)).fetchone()
                offer = conn.execute('SELECT * FROM ui_offers WHERE proposal_id=?', (attempt['proposal_id'],)).fetchone()
                proposal = json.loads(offer['payload_json']) if offer else {}
                legal = (row is not None and row['status']=='ACTIVE' and row['session_id']==attempt['session_id']
                    and row['decision_version']==attempt['expected_version'] and offer and offer['status']=='PENDING'
                    and offer['run_id']==attempt['run_id'] and proposal['sha256']==attempt['proposal_sha256']
                    and proposal['correlation_id']==attempt['correlation_id'])
                result = dict(attempt_id=identity,status='ACKNOWLEDGED' if attempt['decision']=='APPROVE' else 'REJECTED',message='Decision recorded') if legal else conflict
                if legal:
                    conn.execute("UPDATE ui_offers SET status='SETTLED',decision=? WHERE proposal_id=?", (attempt['decision'],attempt['proposal_id']))
                    conn.execute('UPDATE ui_runs SET decision_version=decision_version+1 WHERE run_id=?', (attempt['run_id'],))
                    self.store.event_in(conn, attempt['run_id'], 'DECISION', {'summary': attempt['decision']})
                conn.execute('UPDATE ui_commands SET status=?,result_json=? WHERE command_id=?', (result['status'],canonical(result),attempt['command_id']))
            # Commit precedes wake-up. No callback runs while the SQL transaction is open.
            if legal:
                with self._lock:
                    waiter = self._waiters.pop(attempt['proposal_id'], None)
                    if waiter:
                        waiter.decision = attempt['decision']=='APPROVE'
                        waiter.ready.set()
            return result
        except CommandConflict:
            return conflict

    def cancel(self, run_id):
        with self._lock, self.store.connection(write=True) as conn:
            conn.execute("UPDATE ui_offers SET status='CANCELLED' WHERE run_id=? AND status='PENDING'", (run_id,))
            conn.execute('UPDATE ui_runs SET decision_version=decision_version+1 WHERE run_id=?', (run_id,))
            for pending in self._waiters.values():
                if pending.proposal['run_id']==run_id:
                    pending.ready.set()

    def recover_interrupted_runs(self):
        with self.store.connection(write=True) as conn:
            rows = conn.execute("SELECT run_id,mode FROM ui_runs WHERE status='ACTIVE'").fetchall()
            for row in rows:
                conn.execute("UPDATE ui_runs SET status=?,decision_version=decision_version+1 WHERE run_id=?", ('RECOVERY_REQUIRED' if row[1]=='LIVE' else 'ABORTED',row[0]))
                conn.execute("UPDATE ui_offers SET status='CANCELLED' WHERE run_id=?", (row[0],))
        for row in rows:
            self.cancel(row[0])
