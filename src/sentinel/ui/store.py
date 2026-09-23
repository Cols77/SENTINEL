"""Durable UI journal. Every operation owns its connection and short transaction."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class CommandConflict(ValueError):
    pass


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS ui_runs (
 run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, mode TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'ACTIVE', version INTEGER NOT NULL DEFAULT 0,
 decision_version INTEGER NOT NULL DEFAULT 0, snapshot_json TEXT);
CREATE TABLE IF NOT EXISTS ui_events (
 run_id TEXT NOT NULL REFERENCES ui_runs(run_id), sequence_no INTEGER NOT NULL,
 event_id TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
 occurred_at TEXT NOT NULL, PRIMARY KEY(run_id,sequence_no));
CREATE TABLE IF NOT EXISTS ui_commands (
 command_id TEXT PRIMARY KEY, run_id TEXT, idempotency_key TEXT UNIQUE NOT NULL,
 request_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT);
CREATE TABLE IF NOT EXISTS ui_messages (
 message_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ui_runs(run_id),
 sequence_no INTEGER NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL,
 status TEXT NOT NULL, snapshot_version INTEGER NOT NULL, UNIQUE(run_id,sequence_no));
CREATE TABLE IF NOT EXISTS ui_offers (
 proposal_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ui_runs(run_id),
 payload_json TEXT NOT NULL, status TEXT NOT NULL, decision TEXT);
'''


class UIStore:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def open(cls, path):
        self = cls(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(_SCHEMA)
        return self

    @contextmanager
    def connection(self, *, write=False):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_run(self, session_id, run_id, mode):
        with self.connection(write=True) as conn:
            conn.execute('INSERT OR IGNORE INTO ui_runs(run_id,session_id,mode) VALUES (?,?,?)',
                         (run_id, session_id, mode))
            row = conn.execute('SELECT * FROM ui_runs WHERE run_id=?', (run_id,)).fetchone()
            if row['session_id'] != session_id or row['mode'] != mode:
                raise CommandConflict('RUN_IDENTITY_CONFLICT')
            if row['status'] == 'IDLE_CLOSED':
                conn.execute("UPDATE ui_runs SET status='ACTIVE' WHERE run_id=?",
                             (run_id,))
        return self.read_run(run_id)

    def mark_idle_live_closed(self, run_id):
        """Record a graceful unarmed close without weakening crash recovery."""
        with self.connection(write=True) as conn:
            row = conn.execute(
                'SELECT mode,status FROM ui_runs WHERE run_id=?',
                (run_id,)).fetchone()
            offered = conn.execute(
                'SELECT 1 FROM ui_offers WHERE run_id=? LIMIT 1',
                (run_id,)).fetchone()
            if (row is None or row['mode'] != 'LIVE' or
                    row['status'] != 'ACTIVE' or offered is not None):
                return False
            conn.execute("UPDATE ui_runs SET status='IDLE_CLOSED' WHERE run_id=?",
                         (run_id,))
            return True

    def read_run(self, run_id):
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM ui_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def append_event(self, run_id, event_type, payload):
        with self.connection(write=True) as conn:
            return self.event_in(conn, run_id, event_type, payload)

    @staticmethod
    def event_in(conn, run_id, event_type, payload):
        sequence = conn.execute('SELECT COALESCE(MAX(sequence_no),0)+1 FROM ui_events WHERE run_id=?', (run_id,)).fetchone()[0]
        identity, timestamp = str(uuid4()), now()
        conn.execute('INSERT INTO ui_events VALUES (?,?,?,?,?,?)',
                     (run_id, sequence, identity, event_type, canonical(payload), timestamp))
        return {'id': identity, 'run_id': run_id, 'sequence': sequence,
                'type': event_type, 'occurred_at': timestamp,
                'summary': payload.get('summary', event_type), 'refs': payload.get('refs', [])}

    def read_events(self, run_id, after, limit):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('INVALID_CURSOR')
        self.read_run(run_id)
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM ui_events WHERE run_id=? AND sequence_no>? ORDER BY sequence_no LIMIT ?', (run_id, after, limit)).fetchall()
        return [dict(id=r['event_id'], run_id=run_id, sequence=r['sequence_no'],
                     type=r['event_type'], occurred_at=r['occurred_at'],
                     summary=json.loads(r['payload']).get('summary', r['event_type']),
                     refs=json.loads(r['payload']).get('refs', [])) for r in rows]

    @staticmethod
    def claim_in(conn, command):
        request = canonical(command)
        rows = conn.execute('SELECT * FROM ui_commands WHERE command_id=? OR idempotency_key=?',
                            (command['command_id'], command['idempotency_key'])).fetchall()
        if rows:
            if len(rows) != 1 or rows[0]['request_json'] != request:
                raise CommandConflict('COMMAND_IDENTITY_CONFLICT')
            row = dict(rows[0])
            row['result'] = json.loads(row['result_json']) if row['result_json'] else None
            return {'created': False, 'command': row}
        conn.execute('INSERT INTO ui_commands VALUES (?,?,?,?,?,NULL)',
                     (command['command_id'], command.get('run_id'), command['idempotency_key'], request, 'PENDING'))
        return {'created': True, 'command': {'result': None, 'status': 'PENDING'}}

    def claim_command(self, command):
        with self.connection(write=True) as conn:
            return self.claim_in(conn, command)

    def settle_command(self, command_id, result):
        with self.connection(write=True) as conn:
            conn.execute('UPDATE ui_commands SET status=?,result_json=? WHERE command_id=?',
                         (result['status'], canonical(result), command_id))
        return result

    def read_command(self, command_id):
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM ui_commands WHERE command_id=?', (command_id,)).fetchone()
        if row is None:
            raise KeyError(command_id)
        return {**dict(row), 'result': json.loads(row['result_json']) if row['result_json'] else None}

    def publish_snapshot(self, run_id, snapshot):
        candidate = deepcopy(snapshot)
        with self.connection(write=True) as conn:
            row = conn.execute('SELECT * FROM ui_runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            watermark = conn.execute('SELECT COALESCE(MAX(sequence_no),0) FROM ui_events WHERE run_id=?', (run_id,)).fetchone()[0]
            candidate['events']['watermark'] = watermark
            candidate['run']['version'] = row['version']
            if row['snapshot_json'] and canonical(candidate) == row['snapshot_json']:
                return candidate
            candidate['run']['version'] += 1
            conn.execute('UPDATE ui_runs SET version=?,snapshot_json=? WHERE run_id=?',
                         (candidate['run']['version'], canonical(candidate), run_id))
        return candidate

    def read_snapshot(self, run_id):
        raw = self.read_run(run_id)['snapshot_json']
        return json.loads(raw) if raw else None

    def add_message(self, run_id, role, body, status='COMPLETE', snapshot_version=0):
        with self.connection(write=True) as conn:
            sequence = conn.execute('SELECT COALESCE(MAX(sequence_no),0)+1 FROM ui_messages WHERE run_id=?', (run_id,)).fetchone()[0]
            conn.execute('INSERT INTO ui_messages VALUES (?,?,?,?,?,?,?)',
                         (str(uuid4()), run_id, sequence, role, body, status, snapshot_version))

    def messages(self, run_id):
        with self.connection() as conn:
            rows = conn.execute('SELECT * FROM ui_messages WHERE run_id=? ORDER BY sequence_no', (run_id,)).fetchall()
        return [dict(id=r['message_id'], run_id=run_id, sequence=r['sequence_no'], role=r['role'], body=r['body'], status=r['status'], snapshot_version=r['snapshot_version']) for r in rows]
