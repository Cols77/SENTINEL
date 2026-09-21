from concurrent.futures import ThreadPoolExecutor
import pytest
from sentinel.ui.store import UIStore, CommandConflict


def test_claim_is_atomic_and_payload_bound(tmp_path):
    path = tmp_path / 'ui.sqlite'
    UIStore.open(path).ensure_run('s', 'r', 'REHEARSAL')
    command = dict(command_id='c', session_id='s', run_id='r', idempotency_key='key', kind='CHAT', text='count computers')
    def claim(_):
        return UIStore.open(path).claim_command(command)['created']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(8))) == 1
    with pytest.raises(CommandConflict):
        UIStore.open(path).claim_command({**command, 'text': 'take off'})


def test_event_sequences_and_snapshot_versions_survive_reload(tmp_path):
    path = tmp_path / 'ui.sqlite'
    store = UIStore.open(path)
    store.ensure_run('s', 'r', 'REHEARSAL')
    def append(i):
        return UIStore.open(path).append_event('r', 'OBSERVED', {'summary': f'observation {i}'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(20)))
    assert [e['sequence'] for e in store.read_events('r', 0, 200)] == list(range(1, 21))
    first = store.publish_snapshot('r', {'run': {'version': 0}, 'events': {'watermark': 0}})
    second = store.publish_snapshot('r', {'run': {'version': 0}, 'events': {'watermark': 0}})
    assert first['run']['version'] == second['run']['version'] == 1
    assert UIStore.open(path).read_snapshot('r')['events']['watermark'] == 20
    with pytest.raises(ValueError):
        store.read_events('r', 0, 201)


def test_command_result_is_durable(tmp_path):
    path = tmp_path / 'ui.sqlite'
    store = UIStore.open(path)
    store.ensure_run('s', 'r', 'REHEARSAL')
    command = dict(command_id='c', session_id='s', run_id='r', idempotency_key='key', kind='CHAT')
    store.claim_command(command)
    store.settle_command('c', {'status': 'ACKNOWLEDGED'})
    assert UIStore.open(path).claim_command(command)['command']['result']['status'] == 'ACKNOWLEDGED'
