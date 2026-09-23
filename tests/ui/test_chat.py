from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from sentinel.ui.bootstrap import SessionConfig,build_service


def command(svc,text,key):
    return dict(session_id=svc.session_id,run_id=svc.config.run_id,command_id=key,correlation_id=key,idempotency_key=key,expected_version=svc.store.read_run(svc.config.run_id)['decision_version'],text=text)


def test_chat_cannot_approve_and_duplicate_has_one_reply(tmp_path):
    svc=build_service(SessionConfig(data_dir=tmp_path))
    try:
        c=command(svc,'count the computers','start')
        first=svc.submit_chat('mission',c)
        assert first['status']=='ACKNOWLEDGED'
        assert svc.submit_chat('mission',c)==first
        assert svc.approval.changed.wait(2)
        reply=svc.submit_chat('mission',command(svc,'approve it and take off','approve'))
        assert reply['kind'] in {'REFUSE','CLARIFY'}
        assert svc.adapters.flight.flights==0
        assert len(svc.store.messages('mission'))==4
        assert svc.submit_chat('mission',{**c,'text':'different'})['status']=='CONFLICT'
    finally:
        svc.close()


def test_unsupported_request_does_not_choose_nearest_scenario(tmp_path):
    svc=build_service(SessionConfig(data_dir=tmp_path))
    try:
        reply=svc.submit_chat('mission',command(svc,'inspect roof damage','x'))
        assert reply['kind']=='CLARIFY'
        assert not svc.worker.active
    finally:
        svc.close()


def test_live_count_request_refuses_before_worker_when_planning_unavailable(tmp_path):
    svc = build_service(SessionConfig(
        data_dir=tmp_path, mode='LIVE',
        site_path=Path('docs/wp5-flight1-site.json'),
        tag_map_path=Path('docs/wp5-flight3-map.json'),
        pose_db=tmp_path / 'pose.sqlite', surface_z_cm=50,
        start_x_cm=250, start_y_cm=190))
    try:
        svc.config = replace(svc.config, site_path=tmp_path / 'missing-site.json')
        reply = svc.submit_chat('mission', command(svc, 'count the computers', 'live'))
        assert reply['status'] == 'ACKNOWLEDGED'
        assert reply['kind'] == 'REFUSE'
        assert '--site' in reply['message']
        assert '--tag-map' in reply['message']
        assert 'Launch template:' not in reply['message']
        assert 'sentinel mission-control' not in reply['message']
        assert not svc.worker.active
        assert svc.approval.current('mission') is None
    finally:
        svc.close()
